"""Mesh description reader and GLB builder on a small description written in UEFN's layout."""

from __future__ import annotations

import json
import struct

import pytest

from .mesh_description import read_mesh_description
from .reader import ReadError


def _s(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("<i", len(raw)) + raw


def _attr(name: str, kind: int, extent: int, count: int, fmt: str, values: list, default: bytes) -> bytes:
    size = struct.calcsize("<" + fmt)
    out = _s(name) + struct.pack("<iiii", kind, extent, count, 1)
    out += struct.pack("<iii", extent, size, len(values) // len(fmt)) + struct.pack("<" + fmt * (len(values) // len(fmt)), *values)
    return out + default + struct.pack("<i", 0x30)


def _element(name: str, count: int, attrs: list[bytes], sets: int = 1) -> bytes:
    one = struct.pack("<i", count) + struct.pack("<I", (1 << count) - 1 if count else 0) * ((count + 31) // 32)
    one += struct.pack("<iii", 0, count, len(attrs)) + b"".join(attrs)
    return _s(name) + struct.pack("<i", sets) + one * sets


def triangle_description() -> bytes:
    """One triangle in Unreal space (cm), two UV channels in the UVs element, one slot."""
    elements = [
        _element("Vertices", 3, [_attr("Position ", 1, 1, 3, "fff", [0, 0, 0, 100, 0, 0, 0, 0, 100], b"\0" * 12)]),
        _element("VertexInstances", 3, [
            _attr("VertexIndex", 4, 1, 3, "i", [0, 1, 2], struct.pack("<i", -1)),
            _attr("TextureCoordinate", 2, 1, 3, "ff", [0, 0, 1, 0, 0, 1], b"\0" * 8),
            _attr("Normal", 1, 1, 3, "fff", [0, -1, 0] * 3, b"\0" * 12),
        ]),
        _element("UVs", 0, [], sets=2),
        _element("Edges", 1, [
            _attr("VertexIndex", 4, 2, 1, "i", [0, 1], struct.pack("<i", -1)),
            _attr("IsHard", 5, 1, 1, "?", [False], struct.pack("<i", 0)),  # bool: 1 byte in the array, 4 alone
        ]),
        _element("Triangles", 1, [
            _attr("VertexInstanceIndex", 4, 3, 1, "i", [0, 1, 2], struct.pack("<i", -1)),
            _attr("PolygonGroupIndex", 4, 1, 1, "i", [0], struct.pack("<i", -1)),
        ]),
    ]
    groups = _s("PolygonGroups") + struct.pack("<i", 1) + struct.pack("<iIii", 1, 1, 0, 1) + struct.pack("<i", 1)
    groups += _s("ImportedMaterialSlotName") + struct.pack("<iiiiiii", 6, 1, 1, 1, 1, 1, 1) + _s("Body") + _s("None")
    return struct.pack("<i", len(elements) + 1) + b"".join(elements) + groups


def test_reads_positions_corners_and_slot_names():
    md = read_mesh_description(triangle_description())
    assert md.counts["Vertices"] == 3 and md.counts["Triangles"] == 1
    assert list(md.floats("Vertices", "Position")) == [0, 0, 0, 100, 0, 0, 0, 0, 100]
    assert list(md.ints("Triangles", "VertexInstanceIndex")) == [0, 1, 2]
    assert md.slot_names == ["Body"]


def test_garbage_is_refused_not_misread():
    with pytest.raises(ReadError):
        read_mesh_description(struct.pack("<i", 3) + b"\xff" * 64)


def test_glb_swaps_axes_converts_units_and_faces_its_normals():
    from ..mesh_preview import build_glb

    md = read_mesh_description(triangle_description())
    glb = build_glb(md, [{"slot": "Body", "imported": "Body", "color": [1, 0, 0]}], lambda _p: None, (2.0, 2.0, 2.0))
    magic, version, total = struct.unpack_from("<III", glb, 0)
    assert (magic, version, total) == (0x46546C67, 2, len(glb))
    length, kind = struct.unpack_from("<II", glb, 12)
    gltf = json.loads(glb[20:20 + length])
    position = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    # Unreal Z (up, 100 cm, build scale 2) becomes glTF Y in metres
    assert position["max"] == [2.0, 2.0, 0.0]
    assert gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 0.0, 0.0, 1.0]
    blob = glb[20 + length + 8:]
    view = gltf["bufferViews"][gltf["accessors"][gltf["meshes"][0]["primitives"][0]["indices"]]["bufferView"]]
    indices = struct.unpack_from("<3I", blob, view["byteOffset"])
    # the triangle's own normal agrees with the vertex normal (0, 0, -1 in glTF after the swap)
    assert indices in ((0, 1, 2), (0, 2, 1))
    p = [struct.unpack_from("<3f", blob, gltf["bufferViews"][position["bufferView"]]["byteOffset"] + 12 * i) for i in indices]
    u = [p[1][k] - p[0][k] for k in range(3)]
    v = [p[2][k] - p[0][k] for k in range(3)]
    assert u[0] * v[1] - u[1] * v[0] < 0
