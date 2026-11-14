"""Niagara system preview without UEFN: what the effect is made of, read from the .uasset.

The particles are not simulated (that needs Niagara itself). What is shown: the saved
thumbnail when UEFN stored one, every emitter (enabled, CPU or GPU, local space), its
renderers (sprite, mesh, ribbon...) with their material's look (the texture or colour
feeding Emissive or Base Color) and mesh, and the module inputs set in the emitter's
scripts (spawn rate, burst count, lifetime, loop duration...), decoded from the scripts'
rapid-iteration parameter stores.
"""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path
from typing import Any, Callable, Optional

from .paths_plugin import PANEL_UI_HTTP_PORT, PLUGIN_ID, cache_root
from .thumbnail import embedded_thumbnail_png
from .uasset import ReadError, load_package
from .uasset.material import surface_look
from .uasset.package import Package
from .uasset.payload import texture_png
from .uasset.props import Raw, Ref, export_properties
from .uasset.resolve import with_relative
from .uasset.umg_spec import project_resolver
from .umg_preview import content_dir_of

_DIR = "niagara"
ICON_PX = 256
# module inputs worth a line in an emitter's summary: (suffix of "Module.Input", label, unit)
_SUMMARY = (
    ("SpawnRate.SpawnRate", "Spawn rate", "/s"),
    ("SpawnBurst_Instantaneous.Spawn Count", "Burst", " particles"),
    ("SpawnPerUnit.SpawnPerUnit", "Spawn per unit", ""),
    ("InitializeParticle.Lifetime", "Lifetime", " s"),
    ("InitializeParticle.Lifetime Min", "Lifetime min", " s"),
    ("InitializeParticle.Lifetime Max", "Lifetime max", " s"),
    ("EmitterState.Loop Duration", "Loop duration", " s"),
    ("EmitterState.Loop Delay", "Loop delay", " s"),
    ("EmitterState.Loop Count", "Loop count", ""),
    ("InitializeParticle.Uniform Sprite Size", "Sprite size", " cm"),
    ("InitializeParticle.Mass", "Mass", " kg"),
)


def _url(name: str) -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/cache/{_DIR}/{name}"


def _folder() -> Path:
    folder = cache_root(for_write=True) / _DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _value(data: bytes) -> Any:
    """A parameter's bytes as a number, a vector, or None when the type can't be told."""
    if len(data) == 4:
        f = struct.unpack("<f", data)[0]
        i = struct.unpack("<i", data)[0]
        # small ints (enums, counts, bools) read as tiny denormal floats
        if f == 0 or 1e-6 <= abs(f) <= 1e9:
            return round(f, 4)
        return i
    if len(data) in (8, 12, 16):
        return [round(v, 4) for v in struct.unpack(f"<{len(data) // 4}f", data)]
    return None


def rapid_parameters(pkg: Package) -> dict[str, dict[str, Any]]:
    """{emitter: {"Module.Input": value}} from every script's rapid-iteration parameters.

    A store lists (FName "Constants.<Emitter>.<Module>.<Input>", type, int32 offset) items
    and a byte block; each item ends with its offset, right before the next item's name.
    """
    out: dict[str, dict[str, Any]] = {}
    for exp in pkg.exports:
        if pkg.class_name(exp) != "NiagaraScript":
            continue
        try:
            store = export_properties(pkg, exp).get("RapidIterationParameters")
        except (ReadError, KeyError, IndexError, TypeError):
            continue
        if not isinstance(store, dict) or not isinstance(store.get("SortedParameterOffsets"), Raw):
            continue
        table = store["SortedParameterOffsets"].data
        block = bytes(store.get("ParameterData") or [])
        starts = []
        for pos in range(4, len(table) - 7, 1):
            index, number = struct.unpack_from("<ii", table, pos)
            if number == 0 and 0 <= index < len(pkg.names) and pkg.names[index].startswith("Constants."):
                starts.append((pos, pkg.names[index]))
        items = []
        for k, (pos, name) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(table)
            if end - pos < 12:
                continue
            offset = struct.unpack_from("<i", table, end - 4)[0]
            if 0 <= offset < len(block):
                items.append((offset, name))
        items.sort()
        for k, (offset, name) in enumerate(items):
            end = items[k + 1][0] if k + 1 < len(items) else len(block)
            parts = name.split(".")
            if len(parts) < 4:
                continue
            value = _value(block[offset:end])
            if value is not None:
                out.setdefault(parts[1], {})[".".join(parts[2:])] = value
    return out


def _icon(texture: str, resolve: Callable[[str], Optional[Path]]) -> Optional[str]:
    """URL of a small PNG of the texture at object path ``texture``."""
    file = resolve(texture)
    if file is None:
        return None
    stat = file.stat()
    name = hashlib.sha256(f"{file}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:32] + ".png"
    target = _folder() / name
    if not target.is_file():
        try:
            png = texture_png(load_package(file))
        except (ReadError, OSError, ValueError, TypeError):
            png = None
        png = png or embedded_thumbnail_png(file)  # a format not decoded here: UEFN's thumbnail of it
        if png is None:
            return None
        from PIL import Image

        with Image.open(io.BytesIO(png)) as img:
            img.thumbnail((ICON_PX, ICON_PX))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
        target.write_bytes(buf.getvalue())
    return _url(name)


def _look(path: str, resolve: Callable[[str], Optional[Path]]) -> dict[str, Any]:
    """Material entry: name, and the texture icon or colour that gives it its look."""
    out: dict[str, Any] = {"material": path.rsplit(".", 1)[-1]}
    file = resolve(path)
    if file is None:
        out["why"] = "not a file of this project" if not path.startswith(("/Niagara/", "/Engine/")) else "an engine material"
        return out
    try:
        look = surface_look(file, with_relative(resolve, file, load_package(file).package_name), ("EmissiveColor", "BaseColor"))
    except (ReadError, OSError, KeyError, IndexError, TypeError) as exc:
        out["why"] = str(exc) or "could not be read"
        return out
    if look.get("texture"):
        out["texture"] = look["texture"].rsplit(".", 1)[-1]
        icon = _icon(look["texture"], resolve)
        if icon:
            out["icon"] = icon
    if look.get("color"):
        out["color"] = [round(float(c), 3) for c in look["color"][:3]]
    if look.get("how"):
        out["how"] = look["how"]
    if look.get("why"):
        out["why"] = look["why"]
    return out


def _renderer(pkg: Package, exp, resolve: Callable[[str], Optional[Path]]) -> dict[str, Any]:
    cls = pkg.class_name(exp)
    out: dict[str, Any] = {"type": cls.removeprefix("Niagara").removesuffix("RendererProperties") or cls}
    props = export_properties(pkg, exp)
    if props.get("bIsEnabled") is False:
        out["disabled"] = True
    material = props.get("Material")
    if isinstance(material, Ref) and material.index < 0:
        out["look"] = _look(pkg.object_path(material.index), resolve)
    sub = props.get("SubImageSize")
    if isinstance(sub, list) and len(sub) == 2 and (sub[0] > 1 or sub[1] > 1):
        out["flipbook"] = [int(sub[0]), int(sub[1])]
    meshes, mesh_paths = [], []
    for entry in props.get("Meshes") or []:
        ref = entry.get("Mesh") if isinstance(entry, dict) else None
        if isinstance(ref, Ref) and ref.index < 0:
            mesh_paths.append(pkg.object_path(ref.index))
            meshes.append(mesh_paths[-1].rsplit(".", 1)[-1])
    if meshes:
        out["meshes"] = meshes
    overrides = [m.get("ExplicitMat") for m in props.get("OverrideMaterials") or [] if isinstance(m, dict)]
    looks = [_look(pkg.object_path(r.index), resolve) for r in overrides if isinstance(r, Ref) and r.index < 0]
    if looks:
        out["override_looks"] = looks
    elif mesh_paths:  # no override: the particles wear their mesh's own materials
        mesh_looks = [_look(path, resolve) for path in dict.fromkeys(p for m in mesh_paths for p in _mesh_materials(m, resolve))]
        if mesh_looks:
            out["mesh_looks"] = mesh_looks
    return out


def _mesh_materials(mesh: str, resolve: Callable[[str], Optional[Path]]) -> list[str]:
    """Object paths of the materials a StaticMesh at ``mesh`` uses."""
    file = resolve(mesh)
    if file is None:
        return []
    try:
        pkg = load_package(file)
        exp = next((e for e in pkg.exports if e.outer_index == 0 and pkg.class_name(e) == "StaticMesh"), None)
        slots = export_properties(pkg, exp).get("StaticMaterials") or [] if exp is not None else []
    except (ReadError, OSError, KeyError, IndexError, TypeError):
        return []
    refs = [s.get("MaterialInterface") for s in slots if isinstance(s, dict)]
    return [pkg.object_path(r.index) for r in refs if isinstance(r, Ref) and r.index < 0]


def _summary(params: dict[str, Any]) -> list[list[str]]:
    lines = []
    for suffix, label, unit in _SUMMARY:
        value = params.get(suffix)
        if isinstance(value, (int, float)):
            text = f"{value:g}{unit}"
            if suffix.endswith("Loop Duration") and value >= 100000:
                text = f"endless ({value:g} s)"
            lines.append([label, text])
    return lines


def _particle_color(params: dict[str, Any]) -> Optional[list[float]]:
    """InitializeParticle.Color (linear, often above 1 for a glow), when the emitter sets one."""
    value = params.get("InitializeParticle.Color")
    return value if isinstance(value, list) and len(value) == 4 else None


def build_niagara_preview(file: Path) -> dict[str, Any]:
    content = content_dir_of(file)
    if content is None:
        return {"ok": False, "error": "The effect is not inside a project Content folder."}
    try:
        pkg = load_package(file)
        resolve = with_relative(project_resolver(content), file, pkg.package_name)
        system = next((e for e in pkg.exports if e.outer_index == 0 and pkg.class_name(e) == "NiagaraSystem"), None)
        if system is None:
            return {"ok": False, "error": "not a Niagara System"}
        props = export_properties(pkg, system)
        params = rapid_parameters(pkg)
        emitters = []
        for handle in props.get("EmitterHandles") or []:
            if not isinstance(handle, dict):
                continue
            name = str(handle.get("Name") or "")
            entry: dict[str, Any] = {"name": name, "enabled": handle.get("bIsEnabled") is not False}
            instance = handle.get("VersionedInstance") or {}
            ref = instance.get("Emitter") if isinstance(instance, dict) else None
            if isinstance(ref, Ref) and ref.index > 0:
                emitter = export_properties(pkg, pkg.exports[ref.index - 1])
                unique = str(emitter.get("UniqueEmitterName") or name)
                versions = emitter.get("VersionData") or []
                data = next((v for v in versions if isinstance(v, dict)
                             and (v.get("Version") or {}).get("VersionGuid") == instance.get("Version")), versions[0] if versions else {})
                entry["sim"] = "GPU" if "GPU" in str(data.get("SimTarget") or "") else "CPU"
                entry["local_space"] = bool(data.get("bLocalSpace"))
                entry["renderers"] = [_renderer(pkg, pkg.exports[r.index - 1], resolve)
                                      for r in data.get("RendererProperties") or [] if isinstance(r, Ref) and r.index > 0]
                emitter_params = params.get(unique) or params.get(name) or {}
                entry["summary"] = _summary(emitter_params)
                color = _particle_color(emitter_params)
                if color:
                    entry["particle_color"] = color
                entry["parameters"] = sorted([k, v] for k, v in emitter_params.items())
            emitters.append(entry)
        out: dict[str, Any] = {"ok": True, "emitters": emitters,
                               "system_parameters": sorted([k, v] for k, v in (params.get(pkg.package_name.rsplit("/", 1)[-1]) or {}).items())}
        thumb = embedded_thumbnail_png(file)
        if thumb:
            stat = file.stat()
            name = hashlib.sha256(f"{file}:{stat.st_mtime_ns}".encode()).hexdigest()[:32] + "_thumb.png"
            target = _folder() / name
            if not target.is_file():
                target.write_bytes(thumb)
            out["thumbnail"] = _url(name)
        return out
    except (ReadError, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc) or "the effect could not be read"}


def load_niagara_preview(relative_path: str) -> dict[str, Any]:
    from frontend.ui_web import project_files as pf

    rel = (relative_path or "").strip().replace("\\", "/")
    try:
        file = Path(pf.resolve_project_file_path(rel))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if not file.is_file():
        return {"ok": False, "error": f"Not a file: {relative_path}"}
    try:
        return build_niagara_preview(file)
    except Exception as exc:  # noqa: BLE001 - the panel keeps the thumbnail and says why
        return {"ok": False, "error": f"Could not read the effect: {exc}"}
