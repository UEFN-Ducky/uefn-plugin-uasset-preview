"""StaticMesh preview without UEFN: the source geometry read from the .uasset, as a GLB.

Each material slot becomes a glTF material with its base colour (``surface_look``):
the texture is decoded from its own .uasset and embedded (at most 1024 px), or a flat
colour. Unreal is Z-up, left-handed, in centimetres; glTF is Y-up, right-handed, in
metres: the Y and Z axes are swapped (which also flips handedness) and lengths / 100.
"""

from __future__ import annotations

import array
import hashlib
import io
import json
import struct
from pathlib import Path
from typing import Any, Callable, Optional

from .paths_plugin import PANEL_UI_HTTP_PORT, PLUGIN_ID, cache_root
from .uasset import ReadError, load_package
from .uasset.material import surface_look
from .uasset.mesh_description import MeshDescription, read_mesh_description
from .uasset.payload import first_payload, texture_png
from .uasset.props import Ref, export_properties
from .uasset.resolve import with_relative
from .uasset.umg_spec import project_resolver
from .umg_preview import content_dir_of

MAX_TEXTURE_PX = 1024
_DIR = "mesh"


def _url(name: str) -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/cache/{_DIR}/{name}"


def _info(props: dict[str, Any], md: Optional[MeshDescription]) -> dict[str, Any]:
    bounds = props.get("ExtendedBounds") or {}
    extent = bounds.get("BoxExtent") if isinstance(bounds, dict) else None
    nanite = props.get("NaniteSettings")
    info: dict[str, Any] = {
        "lods": len(props.get("SourceModels") or []) or 1,
        "nanite": bool(isinstance(nanite, dict) and nanite.get("bEnabled")),
        "custom_collision": bool(props.get("bCustomizedCollision")),
        "lod_for_collision": props.get("LODForCollision"),
    }
    if isinstance(extent, list) and len(extent) == 3:
        info["size_cm"] = [round(2 * float(v), 1) for v in extent]
    if md is not None:
        info["vertices"] = md.counts.get("Vertices", 0)
        info["triangles"] = md.counts.get("Triangles", 0)
        info["uv_channels"] = len(md.attributes.get("VertexInstances", {}).get("TextureCoordinate").channels) \
            if md.attributes.get("VertexInstances", {}).get("TextureCoordinate") else 0
    return info


def _slots(pkg, props: dict[str, Any], resolve: Callable[[str], Optional[Path]]) -> list[dict[str, Any]]:
    out = []
    for slot in props.get("StaticMaterials") or []:
        if not isinstance(slot, dict):
            continue
        entry: dict[str, Any] = {"slot": str(slot.get("MaterialSlotName") or slot.get("ImportedMaterialSlotName") or ""),
                                 "imported": str(slot.get("ImportedMaterialSlotName") or "")}
        ref = slot.get("MaterialInterface")
        if isinstance(ref, Ref) and ref.index < 0:
            path = pkg.object_path(ref.index)
            entry["material"] = path.rsplit(".", 1)[-1]
            file = resolve(path)
            if file is None:
                entry["why"] = f"{path} is not a file of this project"
            else:
                try:
                    entry.update(surface_look(file, with_relative(resolve, file, load_package(file).package_name)))
                    entry["file"] = file
                except (ReadError, OSError, KeyError, IndexError, TypeError) as exc:
                    entry["why"] = str(exc) or "the material could not be read"
        else:
            entry["why"] = "no material in this slot"
        out.append(entry)
    return out


def _texture(path: str, resolve: Callable[[str], Optional[Path]]) -> tuple[Optional[bytes], str]:
    """(PNG at most MAX_TEXTURE_PX, why not) for the texture at object path ``path``."""
    file = resolve(path)
    if file is None:
        return None, "its texture is not a file of this project"
    try:
        png = texture_png(load_package(file))
    except (ReadError, OSError, ValueError, TypeError):
        png = None
    if png is None:
        return None, "its texture could not be decoded here"
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        if max(img.size) > MAX_TEXTURE_PX:
            img.thumbnail((MAX_TEXTURE_PX, MAX_TEXTURE_PX))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
    return png, ""


def build_glb(md: MeshDescription, slots: list[dict[str, Any]], resolve: Callable[[str], Optional[Path]],
              scale: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> bytes:
    """GLB of the mesh; ``scale`` is the mesh's BuildScale3D (UEFN applies it to the source)."""
    pos = md.floats("Vertices", "Position")
    vertex_of = md.ints("VertexInstances", "VertexIndex")
    corners = md.ints("Triangles", "VertexInstanceIndex")
    if pos is None or vertex_of is None or corners is None:
        raise ReadError("the mesh has no positions or triangles")
    normals = md.floats("VertexInstances", "Normal")
    uvs = md.floats("VertexInstances", "TextureCoordinate")
    groups = md.ints("Triangles", "PolygonGroupIndex")
    n = len(vertex_of)
    if max(vertex_of, default=0) * 3 + 2 >= len(pos) or max(corners, default=0) >= n:
        raise ReadError("the mesh's indices point outside it")

    # one glTF vertex per Unreal vertex instance (it carries its own normal and UV)
    sx, sy, sz = (float(c) / 100 for c in scale)
    p = array.array("f", bytes(12 * n))
    for i, v in enumerate(vertex_of):
        p[3 * i], p[3 * i + 1], p[3 * i + 2] = pos[3 * v] * sx, pos[3 * v + 2] * sz, pos[3 * v + 1] * sy
    nrm = None
    if normals is not None and len(normals) == 3 * n:
        nrm = array.array("f", bytes(12 * n))
        nrm[0::3], nrm[1::3], nrm[2::3] = normals[0::3], normals[2::3], normals[1::3]
        if not sx == sy == sz:  # a non-uniform scale bends normals the other way
            for i in range(0, 3 * n, 3):
                x, y, z = nrm[i] / sx, nrm[i + 1] / sz, nrm[i + 2] / sy
                length = (x * x + y * y + z * z) ** 0.5 or 1.0
                nrm[i], nrm[i + 1], nrm[i + 2] = x / length, y / length, z / length
    tex = uvs if uvs is not None and len(uvs) == 2 * n else None

    # Winding: the axis swap mirrors the mesh, so keep triangles facing their normals
    flip = False
    if nrm is not None:
        agree = 0
        for t in range(0, min(len(corners), 3 * 2000), 3):
            a, b, c = corners[t], corners[t + 1], corners[t + 2]
            ux, uy, uz = p[3*b] - p[3*a], p[3*b+1] - p[3*a+1], p[3*b+2] - p[3*a+2]
            vx, vy, vz = p[3*c] - p[3*a], p[3*c+1] - p[3*a+1], p[3*c+2] - p[3*a+2]
            fx, fy, fz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            agree += 1 if fx * nrm[3*a] + fy * nrm[3*a+1] + fz * nrm[3*a+2] >= 0 else -1
        flip = agree < 0

    by_group: dict[int, array.array] = {}
    for t in range(0, len(corners), 3):
        g = groups[t // 3] if groups is not None and t // 3 < len(groups) else 0
        idx = by_group.setdefault(g, array.array("I"))
        if flip:
            idx.extend((corners[t], corners[t + 2], corners[t + 1]))
        else:
            idx.extend((corners[t], corners[t + 1], corners[t + 2]))

    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []

    def add_view(data: bytes, target: Optional[int] = None) -> int:
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        blob.extend(data)
        views.append(view)
        return len(views) - 1

    def add_accessor(data: array.array, kind: str, comp: int, count: int, target: int, minmax: bool = False) -> int:
        acc: dict[str, Any] = {"bufferView": add_view(data.tobytes(), target), "componentType": comp, "count": count, "type": kind}
        if minmax:
            acc["min"] = [min(data[i::3]) for i in range(3)]
            acc["max"] = [max(data[i::3]) for i in range(3)]
        accessors.append(acc)
        return len(accessors) - 1

    attributes = {"POSITION": add_accessor(p, "VEC3", 5126, n, 34962, minmax=True)}
    if nrm is not None:
        attributes["NORMAL"] = add_accessor(nrm, "VEC3", 5126, n, 34962)
    if tex is not None:
        attributes["TEXCOORD_0"] = add_accessor(tex, "VEC2", 5126, n, 34962)

    materials: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    material_of_group: dict[int, int] = {}
    slot_by_name = {s.get("imported") or s.get("slot"): i for i, s in enumerate(slots)}
    for g in sorted(by_group):
        name = md.slot_names[g] if g < len(md.slot_names) else ""
        si = slot_by_name.get(name, g if g < len(slots) else -1)
        slot = slots[si] if 0 <= si < len(slots) else {}
        pbr: dict[str, Any] = {"metallicFactor": 0.0, "roughnessFactor": 0.8}
        color = slot.get("color")
        if isinstance(color, list) and len(color) >= 3:
            pbr["baseColorFactor"] = [max(0.0, min(1.0, float(c))) for c in color[:3]] + [1.0]
        if slot.get("texture") and tex is not None:
            png, why = _texture(slot["texture"], resolve)
            if png:
                images.append({"bufferView": add_view(png), "mimeType": "image/png"})
                textures.append({"source": len(images) - 1, "sampler": 0})
                pbr["baseColorTexture"] = {"index": len(textures) - 1}
                pbr.pop("baseColorFactor", None)
                slot["textured"] = True
            else:
                slot.setdefault("why", why)
        if "baseColorTexture" not in pbr and "baseColorFactor" not in pbr:
            pbr["baseColorFactor"] = [0.6, 0.6, 0.6, 1.0]
        materials.append({"name": slot.get("slot") or name or f"slot {g}", "pbrMetallicRoughness": pbr, "doubleSided": False})
        material_of_group[g] = len(materials) - 1

    primitives = []
    for g, idx in sorted(by_group.items()):
        primitives.append({"attributes": attributes, "indices": add_accessor(idx, "SCALAR", 5125, len(idx), 34963),
                           "material": material_of_group[g]})
    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "UAsset Preview"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}], "materials": materials,
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    if images:
        gltf.update(images=images, textures=textures,
                    samplers=[{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}])
    head = json.dumps(gltf, separators=(",", ":")).encode()
    head += b" " * (-len(head) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(head) + 8 + len(blob)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(head), 0x4E4F534A) + head
            + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))


def build_mesh_preview(file: Path) -> dict[str, Any]:
    content = content_dir_of(file)
    if content is None:
        return {"ok": False, "error": "The mesh is not inside a project Content folder."}
    try:
        pkg = load_package(file)
        resolve = with_relative(project_resolver(content), file, pkg.package_name)
        mesh = next((e for e in pkg.exports if e.outer_index == 0 and pkg.class_name(e) == "StaticMesh"), None)
        if mesh is None:
            return {"ok": False, "error": "not a StaticMesh"}
        props = export_properties(pkg, mesh)
        payload = first_payload(pkg)
        md = read_mesh_description(payload) if payload else None
        slots = _slots(pkg, props, resolve)
        info = _info(props, md)
        if md is None:
            return {"ok": False, "error": "the mesh's geometry could not be read here", "info": info,
                    "slots": [_public(s) for s in slots]}
        deps = [file] + [s["file"] for s in slots if s.get("file")]
        key = hashlib.sha256("\0".join(f"{d}:{d.stat().st_mtime_ns}:{d.stat().st_size}" for d in deps).encode()).hexdigest()[:40]
        folder = cache_root(for_write=True) / _DIR
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{key}.glb"
        meta = folder / f"{key}.json"
        if target.is_file() and meta.is_file():
            slots_out = json.loads(meta.read_text(encoding="utf-8"))
        else:
            target.write_bytes(build_glb(md, slots, resolve, _build_scale(props)))
            slots_out = [_public(s) for s in slots]
            meta.write_text(json.dumps(slots_out), encoding="utf-8")
    except (ReadError, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc) or "the mesh could not be read"}
    return {"ok": True, "media_url": _url(target.name), "media_filename": target.name, "info": info, "slots": slots_out}


def _build_scale(props: dict[str, Any]) -> tuple[float, float, float]:
    models = props.get("SourceModels") or []
    settings = models[0].get("BuildSettings") if models and isinstance(models[0], dict) else None
    value = settings.get("BuildScale3D") if isinstance(settings, dict) else None
    if isinstance(value, list) and len(value) == 3 and all(isinstance(c, (int, float)) and c for c in value):
        return float(value[0]), float(value[1]), float(value[2])
    return 1.0, 1.0, 1.0


def _public(slot: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in slot.items() if k in ("slot", "material", "how", "why", "textured")}
    if slot.get("texture"):
        out["texture"] = slot["texture"].rsplit(".", 1)[-1]
    if slot.get("color") and not slot.get("textured"):
        out["color"] = [round(float(c), 3) for c in slot["color"][:3]]
    return out


def load_mesh_preview(relative_path: str) -> dict[str, Any]:
    from frontend.ui_web import project_files as pf

    rel = (relative_path or "").strip().replace("\\", "/")
    try:
        file = Path(pf.resolve_project_file_path(rel))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not file.is_file():
        return {"ok": False, "error": f"Not a file: {relative_path}"}
    try:
        return build_mesh_preview(file)
    except Exception as exc:  # noqa: BLE001 - the panel falls back to UEFN's export and says why
        return {"ok": False, "error": f"Could not read the mesh: {exc}"}
