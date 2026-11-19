"""Save widget edits made in the preview into the Widget Blueprint .uasset.

Order: build the new bytes in memory, check them, copy the original aside, then
replace the file in one step. A failed check never touches the file.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from .paths_plugin import cache_root
from .uasset import ReadError, load_package
from .uasset.props import export_properties
from .uasset.writer import EditError, apply_edits

_BACKUP_DIR = "umg_backups"
_KEEP_BACKUPS = 20


def _backup_dir(file: Path) -> Path:
    import hashlib

    key = hashlib.sha256(str(file.resolve()).lower().encode()).hexdigest()[:16]
    path = cache_root(for_write=True) / _BACKUP_DIR / f"{file.stem}-{key}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backups(file: Path) -> list[Path]:
    return sorted(_backup_dir(file).glob("*.uasset"))


def check_rewrite(old: bytes, new: bytes, edited: set[str]) -> None:
    """Raise ``EditError`` unless ``new`` is ``old`` with only the edited objects changed."""
    try:
        before, after = load_package(old), load_package(new)
    except ReadError as exc:
        raise EditError(f"the rewritten file does not read back ({exc}); file not modified") from exc
    if len(before.exports) != len(after.exports) or len(before.imports) > len(after.imports):
        raise EditError("the rewritten file lost objects; file not modified")
    if before.names != after.names[: len(before.names)]:
        raise EditError("the rewritten file changed existing names; file not modified")
    if [(i.class_package, i.class_name, i.outer_index, i.object_name) for i in before.imports] != [
        (i.class_package, i.class_name, i.outer_index, i.object_name) for i in after.imports[: len(before.imports)]
    ]:
        raise EditError("the rewritten file changed its references; file not modified")
    for a, b in zip(before.exports, after.exports):
        same_identity = (a.object_name, a.class_index, a.outer_index, a.super_index, a.template_index, a.object_flags) == (
            b.object_name, b.class_index, b.outer_index, b.super_index, b.template_index, b.object_flags)
        if not same_identity or b.serial_offset + b.serial_size > len(new):
            raise EditError(f"object {a.object_name} moved wrongly; file not modified")
        old_bytes = old[a.serial_offset : a.serial_offset + a.serial_size]
        new_bytes = new[b.serial_offset : b.serial_offset + b.serial_size]
        if old_bytes != new_bytes:
            if a.object_name not in edited:
                raise EditError(f"object {a.object_name} changed although it was not edited; file not modified")
            try:
                export_properties(after, b)
            except ReadError as exc:
                raise EditError(f"object {a.object_name} does not read back ({exc}); file not modified") from exc
    # what follows the objects (bulk data, payload trailer) must be carried over untouched
    old_end = max(e.serial_offset + e.serial_size for e in before.exports)
    new_end = max(e.serial_offset + e.serial_size for e in after.exports)
    if old[old_end:] != new[new_end:]:
        raise EditError("the end of the file changed; file not modified")
    shift = new_end - old_end
    for name in ("bulk_data_start", "payload_toc_offset"):
        was, now = before.fields[name][1], after.fields[name][1]
        if was >= old_end and now != was + shift:
            raise EditError(f"{name} was not moved with the data; file not modified")


def _edited_objects(data: bytes, edits: list[dict[str, Any]]) -> set[str]:
    """Names of the widgets and of their slots (a slot edit changes the slot object)."""
    from .uasset.props import Ref

    pkg = load_package(data)
    names = {str(e.get("widget")) for e in edits}
    out = set(names)
    for exp in pkg.exports:
        if exp.object_name in names:
            ref = export_properties(pkg, exp).get("Slot")
            if isinstance(ref, Ref) and ref.index > 0:
                out.add(pkg.exports[ref.index - 1].object_name)
    return out


_IMAGE_CLASSES = ("Texture2D", "Material", "MaterialInstanceConstant")


def _content_dir(file: Path) -> Path | None:
    return next((p for p in file.parents if p.name == "Content"), None)


def _asset_file(content: Path, object_path: str) -> Path:
    parts = object_path.split(".", 1)[0].strip("/").split("/")
    return content.joinpath(*parts[1:]).with_suffix(".uasset")


def _with_asset_classes(file: Path, edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Image edits get the class of the asset they point to, read from that asset's file."""
    from .uasset import main_asset_class

    content = _content_dir(file)
    out = []
    for edit in edits:
        if isinstance(edit, dict) and edit.get("field") == "resource" and content is not None:
            target = _asset_file(content, str(edit.get("value") or ""))
            if not target.is_file():
                raise EditError(f"Image not found in this project: {edit.get('value')}")
            edit = {**edit, "asset_class": main_asset_class(target)}
        out.append(edit)
    return out


def find_images(file: Path, query: str, limit: int = 40) -> list[dict[str, str]]:
    """Textures and materials of the widget's project whose file name contains ``query``."""
    from .uasset import load_package, main_asset_class

    content = _content_dir(file)
    if content is None:
        return []
    mount = load_package(file).package_name.strip("/").split("/")[0]
    needle = (query or "").strip().lower()
    out: list[dict[str, str]] = []
    scanned = 0
    for path in sorted(content.rglob("*.uasset")):
        rel = path.relative_to(content)
        if rel.parts[0].startswith("__External") or (needle and needle not in path.stem.lower()):
            continue
        scanned += 1
        if scanned > 600:  # each candidate costs a file read: keep a vague search bounded
            break
        cls = main_asset_class(path)
        if cls in _IMAGE_CLASSES:
            out.append({"key": f"/{mount}/{rel.with_suffix('').as_posix()}.{path.stem}", "name": path.stem,
                        "kind": "texture" if cls == "Texture2D" else "material", "folder": rel.parent.as_posix()})
            if len(out) >= limit:
                break
    return out


def save_edits(file: Path, edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply ``edits`` to ``file``. Returns ``{"ok", "backup", ...}`` or ``{"ok": False, "error"}``."""
    if not isinstance(edits, list) or not edits:
        return {"ok": False, "error": "Nothing to save."}
    if len(edits) > 2000:
        return {"ok": False, "error": "Too many edits in one save."}
    try:
        old = file.read_bytes()
        edits = _with_asset_classes(file, edits)
        new = apply_edits(old, edits)
        check_rewrite(old, new, _edited_objects(old, edits))
    except (EditError, ReadError) as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"Could not read the file: {exc}"}
    if new == old:
        return {"ok": True, "unchanged": True}
    backup = _backup_dir(file) / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}.uasset"
    temp = file.with_name(file.name + ".ducky-tmp")
    try:
        shutil.copy2(file, backup)
        temp.write_bytes(new)
        os.replace(temp, file)
    except OSError as exc:
        for leftover in (temp, backup):  # nothing changed: no backup of an unchanged file either
            try:
                leftover.unlink()
            except OSError:
                pass
        return {"ok": False, "error": f"Could not write the file (is it open or read-only?): {exc}"}
    for stale in _backups(file)[:-_KEEP_BACKUPS]:
        try:
            stale.unlink()
        except OSError:
            pass
    return {"ok": True, "backup": str(backup), "bytes_before": len(old), "bytes_after": len(new), "edits": len(edits)}


def restore_last(file: Path) -> dict[str, Any]:
    """Put back the copy made by the most recent save."""
    backups = _backups(file)
    if not backups:
        return {"ok": False, "error": "No backup for this widget."}
    latest = backups[-1]
    try:
        shutil.copy2(latest, file)
        latest.unlink()
    except OSError as exc:
        return {"ok": False, "error": f"Could not restore: {exc}"}
    return {"ok": True, "restored": str(latest), "remaining": len(backups) - 1}


def _resolve(relative_path: str) -> Path:
    from frontend.ui_web import project_files as pf

    return Path(pf.resolve_project_file_path((relative_path or "").strip().replace("\\", "/")))


# UEFN keeps a loaded package's file open, so Windows refuses to replace it ("access denied").
# With UEFN open the save goes: UEFN lets go of the widget (refused if it has unsaved changes
# there, which would otherwise be lost or fight the file), the file is written, UEFN loads it again.
_RELEASE = """
import unreal
path = {path!r}
out = {{"loaded": unreal.find_package(path) is not None}}
dirty = [str(p.get_name()) for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
out["dirty"] = path in dirty
if out["dirty"] and {save_first!r}:
    # the person chose to keep UEFN's version: UEFN writes it, the edits then go on top of it
    out["saved_in_uefn"] = bool(unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False))
    out["dirty"] = path in [str(p.get_name()) for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
if out["loaded"] and not out["dirty"]:
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is not None:
        unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).close_all_editors_for_asset(asset)
    del asset
    out["unloaded"] = bool(unreal.EditorLoadingAndSavingUtils.unload_packages([unreal.find_package(path)])[0])
    out["still_loaded"] = unreal.find_package(path) is not None
result = out
"""
_RELOAD = """
import unreal
result = {{"reloaded": unreal.EditorAssetLibrary.load_asset({path!r}) is not None}}
"""


def _uefn_python(code: str) -> dict[str, Any]:
    from backend.bridge import post_command_to_listener
    from frontend.settings import PANEL_LISTENER_PORT

    out = post_command_to_listener(PANEL_LISTENER_PORT, "execute_python", {"code": code}, timeout=60.0)
    if not isinstance(out, dict) or out.get("stderr"):
        raise RuntimeError(str((out or {}).get("stderr") or "no answer")[-300:])
    return out.get("result") or {}


def save_umg_edits(relative_path: str, edits: Any, save_in_uefn_first: bool = False) -> dict[str, Any]:
    from .service import _listener_online

    try:
        file = _resolve(relative_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not file.is_file() or file.suffix.lower() != ".uasset":
        return {"ok": False, "error": f"Not a .uasset file: {relative_path}"}
    edits = edits if isinstance(edits, list) else []
    if not _listener_online():
        return save_edits(file, edits)
    from .service import asset_path_of

    path = asset_path_of(relative_path)
    try:
        state = _uefn_python(_RELEASE.format(path=path, save_first=bool(save_in_uefn_first)))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"UEFN is open but did not answer, so the widget was not written: {exc}"}
    if state.get("dirty"):
        # UEFN marks a widget changed as soon as its designer opens it, so this is common
        return {"ok": False, "dirty_in_uefn": True,
                "error": f"{file.stem} has unsaved changes in UEFN (opening it in UEFN's designer is enough). "
                         "Nothing was written. Use \"Save in UEFN, then apply\" to keep UEFN's version and put "
                         "these changes on top of it."}
    if state.get("loaded") and (not state.get("unloaded") or state.get("still_loaded")):
        try:
            _uefn_python(_RELOAD.format(path=path))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"UEFN keeps {file.stem} loaded (another open widget or level uses it). "
                                      "Close what uses it in UEFN, then save again; nothing was written."}
    result = save_edits(file, edits)
    if state.get("saved_in_uefn"):
        result["saved_in_uefn"] = True
    if state.get("loaded"):
        try:
            _uefn_python(_RELOAD.format(path=path))
            if result.get("ok"):
                result["note"] = "UEFN reloaded the widget with these changes."
        except Exception as exc:  # noqa: BLE001
            result["warning"] = f"Saved, but UEFN did not reload it ({exc}); reopen it in UEFN."
    return result


def restore_umg_backup(relative_path: str) -> dict[str, Any]:
    try:
        file = _resolve(relative_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return restore_last(file)


def find_umg_images(relative_path: str, query: str) -> dict[str, Any]:
    try:
        return {"ok": True, "images": find_images(_resolve(relative_path), str(query or ""))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "images": []}


def umg_backup_count(relative_path: str) -> dict[str, Any]:
    try:
        return {"ok": True, "count": len(_backups(_resolve(relative_path)))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "count": 0}
