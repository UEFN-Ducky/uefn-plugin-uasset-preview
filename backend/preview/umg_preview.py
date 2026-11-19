"""Interactive Widget Blueprint preview: spec from the .uasset + URLs for its media.

The widget itself is read offline. Supported texture sources and WAV payloads
are decoded locally when Oodle is available; UEFN exports and thumbnails remain
fallbacks. Materials still use thumbnails, not a reconstruction of their shaders.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from .paths_plugin import PANEL_UI_HTTP_PORT, PLUGIN_ID, cache_root
from .thumbnail import embedded_thumbnail_png
from .uasset import ReadError, load_package
from .uasset.payload import oodle_available, sound_wav, texture_png
from .uasset.umg_spec import build_umg_spec, project_resolver

_MEDIA_DIR = "umg"


def _media_url(filename: str) -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/cache/{_MEDIA_DIR}/{filename}"


def _media_dir() -> Path:
    path = cache_root(for_write=True) / _MEDIA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_key(file: Path) -> str:
    stat = file.stat()
    return hashlib.sha256(f"{file}\0{stat.st_mtime_ns}\0{stat.st_size}".encode()).hexdigest()[:40]


def content_dir_of(file: Path) -> Optional[Path]:
    for parent in file.parents:
        if parent.name == "Content":
            return parent
    return None


def _listener(command: str, payload: dict[str, Any], timeout: float) -> Optional[dict[str, Any]]:
    try:
        from backend.bridge import post_command_to_listener
        from frontend.settings import PANEL_LISTENER_PORT

        res = post_command_to_listener(PANEL_LISTENER_PORT, command, payload, timeout=timeout)
        return res if isinstance(res, dict) else None
    except Exception:  # noqa: BLE001 - offline or failed export: the caller falls back
        return None


def _to_png(source: Path) -> Optional[bytes]:
    try:
        if source.suffix.lower() == ".png":
            return source.read_bytes()
        from PIL import Image

        with Image.open(source) as img:
            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _fill_image(asset: dict[str, Any], key: str, file: Path, mount: str, online: bool) -> None:
    media = _media_dir()
    name = _media_key(file)
    real = media / f"{name}.source-v3.png"
    if not real.is_file() and asset["kind"] == "texture":
        png = None
        try:
            png = texture_png(load_package(file))
        except (ReadError, OSError, ValueError, TypeError):
            png = None
        if png is None and online:
            res = _listener("preview_asset", {"asset_path": _asset_path(key, mount)}, 60.0)
            exported = str((res or {}).get("preview_file") or "")
            if exported and os.path.isfile(exported):
                png = _to_png(Path(exported))
        if png:
            real.write_bytes(png)
    if real.is_file():
        asset.update(url=_media_url(real.name), quality="full")
        from PIL import Image

        with Image.open(real) as image:
            asset.update(width=image.width, height=image.height)
        return
    target = media / f"{name}.thumb2.png"  # "thumb2": true colours (earlier files had red/blue exchanged)
    thumb = None if target.is_file() else embedded_thumbnail_png(file)
    if thumb or target.is_file():
        if thumb:
            target.write_bytes(thumb)
        asset.update(url=_media_url(target.name), quality="thumbnail")


def _fill_sound(asset: dict[str, Any], key: str, file: Path, mount: str, online: bool) -> None:
    target = _media_dir() / f"{_media_key(file)}.uefn.wav"  # UEFN's export only (earlier .wav files were decoded wrong here)
    if not target.is_file():
        wav = None
        try:
            wav = sound_wav(load_package(file))
        except (ReadError, OSError):
            wav = None
        if wav:
            target.write_bytes(wav)
        elif online:
            res = _listener("export_sound_wav", {"asset_path": _asset_path(key, mount)}, 60.0)
            exported = str((res or {}).get("exported_file") or "")
            if exported and os.path.isfile(exported):
                shutil.copyfile(exported, target)
    if target.is_file():
        asset.update(url=_media_url(target.name), quality="full")


def _asset_path(object_path: str, mount: str) -> str:
    """Object path under this project's mount (references keep a migrated asset's old one)."""
    parts = object_path.split("/", 2)
    return f"/{mount}/{parts[2]}" if mount and len(parts) == 3 else object_path


def build_preview(file: Path, *, online: bool = False) -> dict[str, Any]:
    """Spec + media URLs for the Widget Blueprint at ``file`` (absolute path)."""
    content = content_dir_of(file)
    if content is None:
        return {"ok": False, "error": "The widget is not inside a project Content folder."}
    try:
        spec = build_umg_spec(file, project_resolver(content))
    except ReadError as exc:
        return {"ok": False, "error": str(exc)}
    mount = str(spec.get("package") or "").strip("/").split("/", 1)[0]
    counts = {"images_full": 0, "images_thumbnail": 0, "images_missing": 0, "sounds": 0, "sounds_missing": 0}
    for key, asset in spec["assets"].items():
        path = Path(asset.pop("file", "") or "")
        kind = asset["kind"]
        if kind in ("texture", "material"):
            if path.is_file():
                _fill_image(asset, key, path, mount, online)
            if kind == "material" and key in spec.get("materials", {}):
                counts["materials_live"] = counts.get("materials_live", 0) + 1  # drawn by its own shader
                continue
            bucket = {"full": "images_full", "thumbnail": "images_thumbnail"}.get(asset.get("quality", ""), "images_missing")
            counts[bucket] += 1
        elif kind == "sound":
            if path.is_file() and asset.get("class") == "SoundWave":
                _fill_sound(asset, key, path, mount, online)
            counts["sounds" if asset.get("url") else "sounds_missing"] += 1
    return {"ok": True, "spec": spec, "media": counts, "listener_online": online,
            "oodle_available": oodle_available()}


def load_umg_preview(relative_path: str) -> dict[str, Any]:
    from frontend.ui_web import project_files as pf

    from .service import _listener_online

    rel = (relative_path or "").strip().replace("\\", "/")
    try:
        file = Path(pf.resolve_project_file_path(rel))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not file.is_file():
        return {"ok": False, "error": f"Not a file: {relative_path}"}
    try:
        return build_preview(file, online=_listener_online())
    except Exception as exc:  # noqa: BLE001 - the panel shows the reason instead of a blank pane
        return {"ok": False, "error": f"Could not read the widget: {exc}"}
