"""Panel RPCs for the sandboxed asset / model preview HTML panes."""

from __future__ import annotations

from typing import Any


def preview_asset(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import preview_project_asset

    return preview_project_asset(str(relative_path or ""))


def load_static_mesh_preview(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import load_static_mesh_preview as _load

    return _load(str(relative_path or ""))


def load_material_preview(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import load_material_preview as _load

    return _load(str(relative_path or ""))


def load_material_shader(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.material_preview import load_material_shader as _load

    return _load(str(relative_path or ""))


def load_sound_preview(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.sound_preview import load_sound_preview as _load

    return _load(str(relative_path or ""))


def load_texture_preview(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import load_texture_preview as _load

    return _load(str(relative_path or ""))


def load_widget_tree(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import load_widget_tree as _load

    return _load(str(relative_path or ""))


def save_umg_edits(relative_path: str = "", edits: Any = None, save_in_uefn_first: bool = False,
                   **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_edit import save_umg_edits as _save

    return _save(str(relative_path or ""), edits, bool(save_in_uefn_first))


def restore_umg_backup(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_edit import restore_umg_backup as _restore

    return _restore(str(relative_path or ""))


def find_umg_images(relative_path: str = "", query: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_edit import find_umg_images as _find

    return _find(str(relative_path or ""), str(query or ""))


def find_widget_blueprint(name: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_find import find_widget_blueprint as _find

    return _find(str(name or ""))


def umg_backup_count(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_edit import umg_backup_count as _count

    return _count(str(relative_path or ""))


def load_umg_preview(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview.umg_preview import load_umg_preview as _load

    return _load(str(relative_path or ""))


def open_asset_in_uefn(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from .preview import open_asset_in_uefn as _open

    return _open(str(relative_path or ""))


def read_hex(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    from frontend.ui_web.project_files import read_project_file

    result = read_project_file(str(relative_path or ""))
    return {
        "path": result.get("path") or relative_path,
        "content": result.get("content") or "",
        "binary_preview": result.get("binary_preview") or "",
        "kind": result.get("kind") or "",
        "mime": result.get("mime") or "",
    }


def model_media_url(relative_path: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Wrap host project_file_media_url for standalone .fbx/.glb panes."""
    from frontend.ui_web.project_files import read_project_file
    from frontend.ui_web.project_media import build_model_media_urls, build_project_media_url

    result = read_project_file(str(relative_path or ""))
    kind = result.get("kind") or ""
    url = result.get("media_url") or ""
    if not url and kind == "image":
        url = build_project_media_url(result.get("path") or relative_path)
    out: dict[str, Any] = {
        "path": result.get("path") or relative_path,
        "media_url": url,
        "mime": result.get("mime") or "",
        "kind": kind,
    }
    if kind == "model":
        if not url:
            urls = build_model_media_urls(result.get("path") or relative_path)
            out.update(urls)
        else:
            out["media_base_url"] = result.get("media_base_url") or ""
            out["media_filename"] = result.get("media_filename") or ""
    return out


def register_panel_rpcs(api: Any) -> None:
    api.register_panel_rpc("preview_asset", preview_asset)
    api.register_panel_rpc("load_static_mesh_preview", load_static_mesh_preview)
    api.register_panel_rpc("load_material_preview", load_material_preview)
    api.register_panel_rpc("load_material_shader", load_material_shader)
    api.register_panel_rpc("load_sound_preview", load_sound_preview)
    api.register_panel_rpc("load_texture_preview", load_texture_preview)
    api.register_panel_rpc("load_widget_tree", load_widget_tree)
    api.register_panel_rpc("load_umg_preview", load_umg_preview)
    api.register_panel_rpc("save_umg_edits", save_umg_edits)
    api.register_panel_rpc("restore_umg_backup", restore_umg_backup)
    api.register_panel_rpc("umg_backup_count", umg_backup_count)
    api.register_panel_rpc("find_umg_images", find_umg_images)
    api.register_panel_rpc("find_widget_blueprint", find_widget_blueprint)
    api.register_panel_rpc("open_asset_in_uefn", open_asset_in_uefn)
    api.register_panel_rpc("read_hex", read_hex)
    api.register_panel_rpc("model_media_url", model_media_url)
    api.log("uasset-preview panel RPCs registered")
