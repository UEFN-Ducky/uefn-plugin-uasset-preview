"""Read a StaticMesh's source geometry (its serialized FMeshDescription) from the .uasset.

The payload is a list of element types (Vertices, VertexInstances, UVs, Edges, Triangles,
Polygons, PolygonGroups). Each is written once per channel (UVs: once per UV channel)
with an allocation bit array, its element count and a set of attributes; an attribute
is written as::

    FString name, int32 type, int32 extent, int32 count, int32 channels,
    per channel: int32 extent, int32 value size, int32 value count, raw values
    default value (one value, sized by type: a bool takes 4 bytes there), int32 flags

(``extent`` values per element: 3 for a triangle's corners). Only plain-value attributes
are decoded; a name-typed one (material slot names) ends the walk, and the slot names
are picked up by pattern from the PolygonGroups block.
"""

from __future__ import annotations

import array
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from .reader import ReadError

_NAME_TYPE = 6  # FName attribute: not a plain array
# attribute type -> bytes per value in the arrays / in the lone default value
# (FVector4f, FVector3f, FVector2f, float, int32, bool: 1 byte in arrays, 4 alone)
_VALUE_SIZE = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 1}
_DEFAULT_SIZE = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 4}


@dataclass
class Attribute:
    type: int
    extent: int
    size: int  # bytes per value
    channels: list[bytes] = field(default_factory=list)


@dataclass
class MeshDescription:
    counts: dict[str, int]
    attributes: dict[str, dict[str, Attribute]]
    slot_names: list[str]

    def floats(self, element: str, name: str, channel: int = 0) -> Optional[array.array]:
        attr = self.attributes.get(element, {}).get(name)
        if attr is None or channel >= len(attr.channels) or attr.size % 4:
            return None
        out = array.array("f")
        out.frombytes(attr.channels[channel])
        return out

    def ints(self, element: str, name: str) -> Optional[array.array]:
        attr = self.attributes.get(element, {}).get(name)
        if attr is None or not attr.channels or attr.size != 4:
            return None
        out = array.array("i")
        out.frombytes(attr.channels[0])
        return out


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def i32(self) -> int:
        if self.pos + 4 > len(self.data):
            raise ReadError("mesh description ends early")
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def fstring(self) -> str:
        n = self.i32()
        if not 0 < n <= 256 or self.pos + n > len(self.data):
            raise ReadError(f"bad name in mesh description at {self.pos - 4}")
        text = self.data[self.pos:self.pos + n - 1].decode("latin-1").strip()
        self.pos += n
        return text

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ReadError("mesh description ends early")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


def read_mesh_description(data: bytes) -> MeshDescription:
    cur = _Cursor(data)
    types = cur.i32()
    if not 1 <= types <= 16:
        raise ReadError("not a mesh description")
    counts: dict[str, int] = {}
    attributes: dict[str, dict[str, Attribute]] = {}
    for _ in range(types):
        element = cur.fstring()
        sets = cur.i32()  # 1, or one per channel (UVs: one per UV channel)
        if not 0 <= sets <= 8:
            raise ReadError(f"bad {element} channel count")
        for channel in range(sets):
            count, attrs = _element(cur, element)
            if channel == 0:
                counts[element], attributes[element] = count, attrs or {}
            if attrs is None:  # a name-typed attribute: the slot names follow, the walk ends
                # from just before the attribute header: its name may be the slot-name attribute itself
                return MeshDescription(counts, attributes, _slot_names(data[max(0, cur.pos - 64):]))
    return MeshDescription(counts, attributes, [])


def _element(cur: _Cursor, element: str) -> tuple[int, Optional[dict[str, Attribute]]]:
    bits = cur.i32()
    if not 0 <= bits <= 50_000_000:
        raise ReadError(f"bad {element} count")
    cur.take(((bits + 31) // 32) * 4)
    cur.i32()
    count = cur.i32()
    attrs: dict[str, Attribute] = {}
    for _ in range(cur.i32()):
        name = cur.fstring()
        kind, extent, n, channels = cur.i32(), cur.i32(), cur.i32(), cur.i32()
        if kind == _NAME_TYPE:
            return count, None
        if kind not in _VALUE_SIZE or n != count or not 0 <= channels <= 8:
            raise ReadError(f"bad attribute {element}.{name}")
        attr = Attribute(kind, extent, _VALUE_SIZE[kind])
        for _ in range(channels):
            cur.i32()  # extent again
            size, values = cur.i32(), cur.i32()
            if not 0 < size <= 64 or values != count * extent:
                raise ReadError(f"bad values for {element}.{name}")
            attr.size = size
            attr.channels.append(cur.take(size * values))
        cur.take(_DEFAULT_SIZE[kind])  # default value: one value, even with no channel
        cur.i32()  # flags
        attrs[name] = attr
    return count, attrs


def _slot_names(rest: bytes) -> list[str]:
    """Material slot names: the strings after the ImportedMaterialSlotName attribute."""
    at = rest.find(b"ImportedMaterialSlotName\x00")
    if at < 0:
        return []
    out = []
    for m in re.finditer(rb"([\x02-\x40])\x00\x00\x00([ -~]{1,63})\x00", rest[at + 25:at + 4096]):
        if m.group(1)[0] == len(m.group(2)) + 1 and m.group(2) != b"None":
            out.append(m.group(2).decode("latin-1"))
    return out
