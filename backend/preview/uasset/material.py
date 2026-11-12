"""Read a UI material's node graph from its .uasset, for the preview's shader builder.

Only the graph is read here (nodes, wires, parameters). The panel turns it into a
fragment shader; a node it does not know makes the whole material fall back to its
thumbnail, never to a guessed picture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .package import Package, load_package
from .props import Raw, Ref, export_properties
from .reader import ReadError

_PREFIX = "MaterialExpression"
MAX_NODES = 600
MAX_PARENTS = 8


def _plain(pkg: Package, value: Any, textures: dict[str, str]) -> Any:
    """Property value as JSON: wires stay dicts, texture references become asset paths."""
    if isinstance(value, Ref):
        if value.index < 0:
            path = pkg.object_path(value.index)
            cls = pkg.imports[-value.index - 1].class_name
            if cls.startswith("Texture"):
                textures[path] = cls
            return {"asset": path, "class": cls}
        return None
    if isinstance(value, Raw):
        return None
    if isinstance(value, dict):
        return {k: _plain(pkg, v, textures) for k, v in value.items() if not k.startswith("$")}
    if isinstance(value, (list, tuple)):
        return [_plain(pkg, v, textures) for v in value]
    return value


def _graph(pkg: Package) -> Optional[dict[str, Any]]:
    material = next((e for e in pkg.exports if e.outer_index == 0 and pkg.class_name(e) == "Material"), None)
    if material is None:
        return None
    props = export_properties(pkg, material)
    editor = props.get("EditorOnlyData")
    outputs = export_properties(pkg, pkg.exports[editor.index - 1]) if isinstance(editor, Ref) and editor.index > 0 else props
    textures: dict[str, str] = {}
    nodes: dict[str, Any] = {}
    for exp in pkg.exports:
        cls = pkg.class_name(exp)
        if not cls.startswith(_PREFIX) or cls == "MaterialExpressionComment":
            continue
        if len(nodes) >= MAX_NODES:
            raise ReadError("material graph is too large to preview")
        node_props = {k: v for k, v in export_properties(pkg, exp).items()
                      if k not in ("Material", "Function", "MaterialExpressionEditorX", "MaterialExpressionEditorY",
                                   "MaterialExpressionGuid", "ExpressionGUID", "Desc", "bCollapsed")}
        nodes[str(exp.index)] = {"op": cls[len(_PREFIX):], "p": _plain(pkg, node_props, textures)}
    pins = {}
    for name in ("EmissiveColor", "Opacity", "OpacityMask"):
        pin = outputs.get(name)
        if isinstance(pin, dict) and "node" in pin:
            pins[name] = {k: pin[k] for k in ("node", "output", "mask", "use_constant", "constant")}
    return {
        "domain": str(props.get("MaterialDomain") or "MD_Surface"),
        "blend": str(props.get("BlendMode") or "BLEND_Opaque"),
        "nodes": nodes,
        "pins": pins,
        "textures": textures,
    }


def _name(info: Any) -> str:
    return str(info.get("Name") or "") if isinstance(info, dict) else ""


def material_graph(path: Path | str, resolve: Callable[[str], Optional[Path]]) -> Optional[dict[str, Any]]:
    """Graph of the material at ``path``; an instance returns its parent's graph plus overrides.

    ``None`` when this is not a UI material or its parent chain cannot be read.
    """
    overrides: dict[str, dict[str, Any]] = {"scalar": {}, "vector": {}, "texture": {}}
    textures: dict[str, str] = {}
    current = Path(path)
    for _ in range(MAX_PARENTS):
        pkg = load_package(current)
        graph = _graph(pkg)
        if graph is not None:
            if graph["domain"] != "MD_UI":
                return None
            graph["overrides"] = overrides
            graph["textures"].update(textures)
            return graph
        instance = next((e for e in pkg.exports if e.outer_index == 0
                         and pkg.class_name(e) == "MaterialInstanceConstant"), None)
        if instance is None:
            return None
        props = export_properties(pkg, instance)
        # the instance closest to the widget wins: never overwrite a value already set
        for entry in props.get("ScalarParameterValues") or []:
            if isinstance(entry, dict) and isinstance(entry.get("ParameterValue"), (int, float)):
                overrides["scalar"].setdefault(_name(entry.get("ParameterInfo")), float(entry["ParameterValue"]))
        for entry in props.get("VectorParameterValues") or []:
            value = entry.get("ParameterValue") if isinstance(entry, dict) else None
            if isinstance(value, list) and len(value) == 4:
                overrides["vector"].setdefault(_name(entry.get("ParameterInfo")), [float(c) for c in value])
        for entry in props.get("TextureParameterValues") or []:
            ref = entry.get("ParameterValue") if isinstance(entry, dict) else None
            if isinstance(ref, Ref) and ref.index < 0:
                tex = pkg.object_path(ref.index)
                overrides["texture"].setdefault(_name(entry.get("ParameterInfo")), tex)
                textures[tex] = pkg.imports[-ref.index - 1].class_name
        parent = props.get("Parent")
        if not isinstance(parent, Ref) or parent.index >= 0:
            return None
        parent_file = resolve(pkg.object_path(parent.index))
        if parent_file is None:
            return None
        current = parent_file
    return None
