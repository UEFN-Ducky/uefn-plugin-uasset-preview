"""Live preview of a UI material asset: its node graph plus URLs for the textures it samples.

The panel compiles the graph into a shader (``ui/umg/material.js``, the same builder the
UMG preview uses), so the material is drawn as it really is, animated, without UEFN.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .uasset import ReadError, load_package
from .uasset.material import material_graph
from .uasset.umg_spec import project_resolver
from .umg_preview import _fill_image, content_dir_of


def build_material_shader(file: Path, *, online: bool = False) -> dict[str, Any]:
    """Graph + texture media for the material at ``file``; ``ok: False`` with the reason otherwise."""
    content = content_dir_of(file)
    if content is None:
        return {"ok": False, "error": "The material is not inside a project Content folder."}
    resolve = project_resolver(content)
    try:
        mount = load_package(file).package_name.strip("/").split("/", 1)[0]
        graph = material_graph(file, resolve)
    except (ReadError, OSError, KeyError, IndexError, TypeError) as exc:
        return {"ok": False, "error": str(exc) or "the material could not be read"}
    textures: dict[str, dict[str, Any]] = {}
    for path, cls in graph.pop("textures").items():
        asset: dict[str, Any] = {"kind": "texture", "class": cls, "name": path.rsplit(".", 1)[-1]}
        source = resolve(path)
        if source is not None:
            _fill_image(asset, path, source, mount, online)
        textures[path] = asset
    return {"ok": True, "graph": graph, "textures": textures, "listener_online": online}


def load_material_shader(relative_path: str) -> dict[str, Any]:
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
        return build_material_shader(file, online=_listener_online())
    except Exception as exc:  # noqa: BLE001 - the panel keeps the thumbnail and shows why
        return {"ok": False, "error": f"Could not read the material: {exc}"}
