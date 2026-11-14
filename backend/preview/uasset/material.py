"""Read a UI material's node graph from its .uasset, for the preview's shader builder.

Only the graph is read here (nodes, wires, parameters). The panel turns it into a
fragment shader; a node it does not know makes the whole material fall back to its
thumbnail, never to a guessed picture.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Optional

from .package import Package, load_package
from .props import Raw, Ref, export_properties
from .reader import ReadError
from .resolve import with_relative

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
    for name in ("EmissiveColor", "Opacity", "OpacityMask", "BaseColor"):
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


def _walk(path: Path | str, resolve: Callable[[str], Optional[Path]]) -> tuple[Optional[dict[str, Any]], dict, dict, str]:
    """Follow an instance's parents up to the material with a graph.

    Returns (graph or None, overrides, textures, why the walk stopped without a graph).
    The instance nearest to ``path`` wins: a value already set is never overwritten.
    """
    overrides: dict[str, dict[str, Any]] = {"scalar": {}, "vector": {}, "texture": {}, "switch": {}}
    textures: dict[str, str] = {}
    current = Path(path)
    for _ in range(MAX_PARENTS):
        pkg = load_package(current)
        graph = _graph(pkg)
        if graph is not None:
            graph["textures"].update(textures)
            return graph, overrides, textures, ""
        instance = next((e for e in pkg.exports if e.outer_index == 0
                         and pkg.class_name(e) == "MaterialInstanceConstant"), None)
        if instance is None:
            return None, overrides, textures, "not a Material or a Material Instance"
        props = export_properties(pkg, instance)
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
        for holder in ("StaticParametersRuntime", "StaticParameters"):
            block = props.get(holder)
            for entry in (block.get("StaticSwitchParameters") or []) if isinstance(block, dict) else []:
                if isinstance(entry, dict) and isinstance(entry.get("Value"), bool):
                    overrides["switch"].setdefault(_name(entry.get("ParameterInfo")), entry["Value"])
        parent = props.get("Parent")
        if not isinstance(parent, Ref) or parent.index >= 0:
            return None, overrides, textures, "the material instance has no parent material"
        parent_path = pkg.object_path(parent.index)
        parent_file = with_relative(resolve, current, pkg.package_name)(parent_path)
        if parent_file is None:
            return None, overrides, textures, f"its parent {parent_path} is not a file of this project"
        current = parent_file
    return None, overrides, textures, "the chain of parent materials is too long"


def material_graph(path: Path | str, resolve: Callable[[str], Optional[Path]]) -> dict[str, Any]:
    """Graph of the material at ``path``; an instance returns its parent's graph plus overrides.

    Raises ``ReadError`` saying why when this is not a UI material or its parent chain
    cannot be read.
    """
    graph, overrides, _textures, why = _walk(path, resolve)
    if graph is None:
        raise ReadError(why)
    if graph["domain"] != "MD_UI":
        raise ReadError(f"{graph['domain']} material: only UI materials (MD_UI) are drawn live")
    graph["overrides"] = overrides
    return graph


# Texture / colour parameter names that carry a base colour when the graph can't be read
_BASE_NAME = re.compile(r"base ?colou?r|diffuse|albedo|^base ?texture$|^colou?r$", re.IGNORECASE)


def surface_look(path: Path | str, resolve: Callable[[str], Optional[Path]]) -> dict[str, Any]:
    """Best guess at a surface material's base colour, for a mesh preview.

    ``{"texture": object path}`` and/or ``{"color": [r, g, b]}`` (linear), plus ``how``:
    "graph" when found by following the BaseColor input, "parameter name" when the
    parent graph is outside the project and a parameter's name says base colour, and
    ``why`` when nothing was found.
    """
    graph, overrides, _textures, why = _walk(path, resolve)
    if graph is None:
        for name, tex in overrides["texture"].items():
            if _BASE_NAME.search(name):
                return {"texture": tex, "how": "parameter name"}
        for name, value in overrides["vector"].items():
            if _BASE_NAME.search(name):
                return {"color": value[:3], "how": "parameter name"}
        return {"why": why}
    pin = graph["pins"].get("BaseColor")
    if not pin:
        return {"why": "nothing is connected to its Base Color"}
    if pin.get("use_constant") or not pin.get("node"):
        return {"color": list(pin.get("constant") or [0.5, 0.5, 0.5])[:3], "how": "graph"}
    found: dict[str, Any] = {}
    seen: set[str] = set()

    def visit(node_id: str, depth: int) -> None:
        node = graph["nodes"].get(node_id)
        if node is None or node_id in seen or depth > 40 or "texture" in found:
            return
        seen.add(node_id)
        op, p = node["op"], node["p"]
        if op in ("TextureSample", "TextureSampleParameter2D"):
            tex = overrides["texture"].get(p.get("ParameterName") or "") or (p.get("Texture") or {}).get("asset")
            if tex:
                found["texture"] = tex
                return
        if op == "VectorParameter" and "color" not in found:
            found["color"] = (overrides["vector"].get(p.get("ParameterName") or "") or p.get("DefaultValue") or [0.5] * 4)[:3]
        if op in ("Constant3Vector", "Constant4Vector") and "color" not in found:
            found["color"] = list(p.get("Constant") or [0.5] * 4)[:3]
        for value in p.values():  # inputs, in the order the node lists them
            if isinstance(value, dict) and value.get("node", 0) > 0:
                visit(str(value["node"]), depth + 1)

    visit(str(pin["node"]), 0)
    if found:
        found["how"] = "graph"
        return found
    return {"why": "its Base Color comes from nodes the preview does not follow"}
