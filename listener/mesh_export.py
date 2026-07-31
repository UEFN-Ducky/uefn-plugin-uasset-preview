"""StaticMesh → FBX export for the panel 3D viewer (plugin-owned listener handler)."""

from __future__ import annotations

import os
from typing import Any, Optional

import unreal

from listener.asset_resolve import follow_redirector, load_asset_resolved
from listener.dispatch import register


def _asset_class_name(data: unreal.AssetData) -> str:
    if hasattr(data, "asset_class_path"):
        return str(data.asset_class_path.asset_name)
    return str(getattr(data, "asset_class", ""))


def _static_mesh_metadata(mesh: unreal.StaticMesh, asset_path: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "asset_path": asset_path,
        "name": mesh.get_name(),
        "path": mesh.get_path_name(),
        "asset_class": "StaticMesh",
    }
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if sub is not None:
            info["lod_count"] = int(sub.get_lod_count(mesh))
    except Exception:
        pass
    try:
        if hasattr(mesh, "get_editor_property"):
            nanite = mesh.get_editor_property("nanite_settings")
            enabled = False
            if nanite is not None:
                try:
                    enabled = bool(nanite.get_editor_property("enabled"))
                except Exception:
                    enabled = bool(nanite)
            info["has_nanite"] = enabled
            if enabled:
                info["preview_note"] = (
                    "Nanite mesh — preview uses fallback geometry; Open in UEFN for full detail."
                )
    except Exception:
        pass
    try:
        mats = mesh.get_editor_property("static_materials")
        info["material_slots"] = len(mats) if mats is not None else 0
    except Exception:
        pass
    try:
        body_setup = mesh.get_editor_property("body_setup")
        if body_setup is not None:
            info["collision_trace_flag"] = str(body_setup.get_editor_property("collision_trace_flag"))
    except Exception:
        pass
    return info


def _find_exported_fbx(output_directory: str, stem: str) -> Optional[str]:
    preferred = os.path.join(output_directory, f"{stem}.fbx")
    if os.path.isfile(preferred):
        return preferred
    preferred_upper = os.path.join(output_directory, f"{stem}.FBX")
    if os.path.isfile(preferred_upper):
        return preferred_upper
    try:
        candidates = [
            os.path.join(output_directory, name)
            for name in os.listdir(output_directory)
            if name.lower().endswith(".fbx")
        ]
    except OSError:
        return None
    if not candidates:
        return None
    stem_l = stem.lower()
    named = [p for p in candidates if stem_l in os.path.basename(p).lower()]
    pool = named or candidates
    pool.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return pool[0]


def _make_fbx_export_options():
    cls = getattr(unreal, "FbxExportOption", None)
    if cls is None:
        return None
    try:
        options = cls()
    except Exception:
        return None
    for prop, value in (
        ("collision", False),
        ("level_of_detail", False),
        ("vertex_color", True),
        ("ascii", False),
    ):
        try:
            if hasattr(options, prop):
                options.set_editor_property(prop, value)
        except Exception:
            try:
                setattr(options, prop, value)
            except Exception:
                pass
    return options


def _export_static_mesh_fbx(mesh: unreal.StaticMesh, asset_path: str, output_path: str) -> str:
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(output_path))[0] or mesh.get_name() or "mesh"

    exporter = None
    for cls_name in ("StaticMeshExporterFBX", "ExporterFBX"):
        cls = getattr(unreal, cls_name, None)
        if cls is None:
            continue
        try:
            exporter = cls()
            break
        except Exception:
            exporter = None

    task = unreal.AssetExportTask()
    task.object = mesh
    task.filename = output_path
    task.selected = False
    task.replace_identical = True
    task.prompt = False
    task.automated = True
    if exporter is not None:
        task.exporter = exporter
    options = _make_fbx_export_options()
    if options is not None:
        try:
            task.options = options
        except Exception:
            try:
                task.set_editor_property("options", options)
            except Exception:
                pass

    ok = False
    try:
        ok = bool(unreal.Exporter.run_asset_export_task(task))
    except Exception:
        ok = False
    if not ok:
        try:
            unreal.AssetToolsHelpers.get_asset_tools().export_asset_tasks([task])
            ok = True
        except Exception:
            ok = False
    if not ok:
        unreal.AssetToolsHelpers.get_asset_tools().export_assets([asset_path], out_dir)

    found = _find_exported_fbx(out_dir, stem)
    if found is None:
        raise RuntimeError(
            f"Static mesh export produced no FBX for {asset_path} (may be protected or unsupported)."
        )
    if found != output_path and not os.path.isfile(output_path):
        try:
            os.replace(found, output_path)
            found = output_path
        except OSError:
            pass
    return found


@register("preview_static_mesh")
def preview_static_mesh(asset_path: str, output_directory: str, filename: str = "model.fbx") -> dict:
    """Export a StaticMesh to a caller-owned cache directory for the panel 3D viewer."""
    path = (asset_path or "").strip()
    out_dir = (output_directory or "").strip()
    name = (filename or "model.fbx").strip() or "model.fbx"
    if not path:
        raise ValueError("asset_path is required")
    if not out_dir:
        raise ValueError("output_directory is required")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("filename must be a bare file name")
    if not name.lower().endswith(".fbx"):
        name = f"{name}.fbx"

    data = unreal.EditorAssetLibrary.find_asset_data(path)
    asset_class = ""
    if data is not None:
        try:
            valid = data.is_valid() if callable(getattr(data, "is_valid", None)) else True
        except Exception:
            valid = True
        if valid:
            asset_class = _asset_class_name(data)
    mesh, resolved_path = load_asset_resolved(path)
    if mesh is None:
        raise ValueError(f"Asset not found: {path}")
    mesh = follow_redirector(mesh)
    if mesh is None:
        raise ValueError(f"Asset not found: {path}")
    if not isinstance(mesh, unreal.StaticMesh):
        cls = type(mesh).__name__ or asset_class or "Unknown"
        raise ValueError(f"Not a StaticMesh (got {cls}). Only static meshes can be previewed.")

    export_path = resolved_path or path
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, name)
    exported = _export_static_mesh_fbx(mesh, export_path, output_path)
    meta = _static_mesh_metadata(mesh, export_path)
    siblings = []
    try:
        for fn in os.listdir(out_dir):
            full = os.path.join(out_dir, fn)
            if os.path.isfile(full):
                siblings.append(fn)
    except OSError:
        siblings = [os.path.basename(exported)]

    result = {
        "ok": True,
        "asset_path": export_path,
        "asset_class": "StaticMesh",
        "exported_file": exported,
        "output_directory": out_dir,
        "filename": os.path.basename(exported),
        "siblings": siblings,
        "metadata": meta,
    }
    if export_path != path:
        result["requested_path"] = path
        result["followed_redirector"] = True
    return result


try:
    from listener.tick import register_heavy

    register_heavy("preview_static_mesh")
except Exception:
    pass
