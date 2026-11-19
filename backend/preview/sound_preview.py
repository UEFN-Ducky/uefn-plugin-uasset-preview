"""Sound preview: a SoundWave's WAV, or a SoundCue's node tree and the waves it plays.

The cue's tree is read from the .uasset. The samples come from UEFN's own WAV export
(see ``uasset.payload.sound_wav`` for why the payload is not decoded here), kept in the
cache so a sound UEFN exported once plays later with UEFN closed. The panel plays a cue
the way UEFN does: Random picks a child, Modulator draws a volume and a pitch, Mixer
plays its children together.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Callable, Optional

from .uasset import ReadError, load_package
from .uasset.package import Package
from .uasset.props import Ref, export_properties
from .uasset.resolve import with_relative
from .uasset.umg_spec import project_resolver
from .umg_preview import _listener, _media_dir, _media_key, _media_url, content_dir_of

MAX_CUE_NODES = 200
# Node properties the panel uses; the rest (editor positions, GUIDs) stays out.
_NODE_KEYS = (
    "PitchMin", "PitchMax", "VolumeMin", "VolumeMax", "Weights", "InputVolume", "DelayMin", "DelayMax",
    "bLooping", "bLoopIndefinitely", "LoopCount", "bRandomizeWithoutReplacement",
)


def wav_info(wav: bytes) -> dict[str, Any]:
    """Channels, sample rate, bits and duration from a RIFF/WAVE header ({} if unreadable)."""
    if len(wav) < 12 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        return {}
    pos, fmt, data_size = 12, None, None
    while pos + 8 <= len(wav):
        tag, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        if tag == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", wav, pos + 8)
        elif tag == b"data":
            data_size = min(size, len(wav) - pos - 8)
            break
        pos += 8 + size + (size & 1)
    if not fmt or data_size is None:
        return {}
    _codec, channels, rate, byte_rate, _align, bits = fmt
    return {"channels": channels, "sample_rate": rate, "bits": bits,
            "duration": round(data_size / byte_rate, 3) if byte_rate else 0.0}


def _top_export(pkg: Package):
    stem = pkg.package_name.rsplit("/", 1)[-1]
    return next((e for e in pkg.exports if e.outer_index == 0 and e.object_name == stem), None)


def uefn_island() -> Optional[tuple[str, Path]]:
    """(content root, island folder) of the island open in UEFN; None when UEFN is closed."""
    info = _listener("get_project_info", {}, 10.0) or {}
    root, island = str(info.get("content_root") or ""), str(info.get("island_dir") or "")
    return (root, Path(island)) if root and island else None


def _wave(path: str, resolve: Callable[[str], Optional[Path]], island: Optional[tuple[str, Path]]) -> dict[str, Any]:
    """Media entry for the SoundWave at object path ``path`` (no ``url``, and why, when unavailable)."""
    asset: dict[str, Any] = {"name": path.rsplit(".", 1)[-1].rsplit("/", 1)[-1], "class": "SoundWave"}
    file = resolve(path)
    if file is None:
        asset["missing"] = "not a file of this project"
        return asset
    target = _media_dir() / f"{_media_key(file)}.uefn.wav"  # same file as the UMG preview's
    if not target.is_file():
        content = content_dir_of(file)
        if island is None:
            asset["missing"] = "open the island in UEFN once to hear it (the sound is exported from UEFN)"
            return asset
        if content is None or content.parent.resolve() != island[1].resolve():
            asset["missing"] = "UEFN has another island open: open this one to hear it"
            return asset
        # UEFN names a package after its file, whatever name a copied package carries inside
        rel = file.relative_to(content).with_suffix("").as_posix()
        res = _listener("export_sound_wav", {"asset_path": f"{island[0].rstrip('/')}/{rel}.{file.stem}"}, 60.0) or {}
        exported = Path(str(res.get("exported_file") or ""))
        if not exported.is_file():
            asset["missing"] = "UEFN could not export it"
            return asset
        target.write_bytes(exported.read_bytes())
    asset.update(url=_media_url(target.name), quality="full", **wav_info(target.read_bytes()))
    return asset


def _cue(pkg: Package, top) -> tuple[Optional[str], dict[str, Any], list[str]]:
    """(first node id, nodes, wave paths) of a SoundCue; ids are export indexes."""
    props = export_properties(pkg, top)
    first = props.get("FirstNode")
    nodes: dict[str, Any] = {}
    waves: list[str] = []
    pending = [first.index] if isinstance(first, Ref) and first.index > 0 else []
    while pending:
        index = pending.pop()
        if str(index) in nodes:
            continue
        if len(nodes) >= MAX_CUE_NODES:
            raise ReadError("the sound cue has too many nodes to preview")
        exp = pkg.exports[index - 1]
        node_props = export_properties(pkg, exp)
        children = [c.index for c in node_props.get("ChildNodes") or [] if isinstance(c, Ref) and c.index > 0]
        node: dict[str, Any] = {"op": pkg.class_name(exp).removeprefix("SoundNode"),
                                "children": [str(c) for c in children],
                                "p": {k: node_props[k] for k in _NODE_KEYS if k in node_props}}
        wave = node_props.get("SoundWaveAssetPtr")
        if node["op"] == "WavePlayer":
            if isinstance(wave, str) and wave:
                node["wave"] = wave
                if wave not in waves:
                    waves.append(wave)
            elif isinstance(node_props.get("SoundWave"), Ref) and node_props["SoundWave"].index < 0:
                node["wave"] = pkg.object_path(node_props["SoundWave"].index)
                waves.append(node["wave"])
        nodes[str(index)] = node
        pending.extend(children)
    return (str(first.index) if isinstance(first, Ref) and first.index > 0 else None), nodes, waves


def build_sound_preview(file: Path, *, online: bool = False) -> dict[str, Any]:
    content = content_dir_of(file)
    if content is None:
        return {"ok": False, "error": "The sound is not inside a project Content folder."}
    try:
        pkg = load_package(file)
        resolve = with_relative(project_resolver(content), file, pkg.package_name)
        top = _top_export(pkg)
        cls = pkg.class_name(top) if top is not None else ""
        island = uefn_island() if online else None
        own_path = f"{pkg.package_name}.{pkg.package_name.rsplit('/', 1)[-1]}"
        if cls == "SoundWave":
            wave = _wave(own_path, lambda _p: file, island)
            return {"ok": True, "kind": "wave", "waves": {own_path: wave}, "duration": wave.get("duration", 0.0)}
        if cls == "SoundCue":
            first, nodes, paths = _cue(pkg, top)
            duration = export_properties(pkg, top).get("Duration")
            return {"ok": True, "kind": "cue", "first": first, "nodes": nodes,
                    "waves": {p: _wave(p, resolve, island) for p in paths},
                    "duration": float(duration) if isinstance(duration, (int, float)) else 0.0}
    except (ReadError, OSError, KeyError, IndexError, TypeError) as exc:
        return {"ok": False, "error": str(exc) or "the sound could not be read"}
    return {"ok": False, "error": f"{cls or 'this asset'} sounds are not previewed yet (SoundWave and SoundCue are)"}


def load_sound_preview(relative_path: str) -> dict[str, Any]:
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
        return build_sound_preview(file, online=_listener_online())
    except Exception as exc:  # noqa: BLE001 - the panel shows the reason instead of a blank pane
        return {"ok": False, "error": f"Could not read the sound: {exc}"}
