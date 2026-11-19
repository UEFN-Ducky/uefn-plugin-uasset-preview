"""Locate property tags inside an export, byte-exact, for in-place editing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .package import UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME, UE5_PROPERTY_TAG_EXTENSION, Export, Package
from .props import TypeName, _read_type_name
from .reader import ReadError, Reader

TAG_HAS_ARRAY_INDEX = 0x01
TAG_HAS_PROPERTY_GUID = 0x02
TAG_HAS_EXTENSIONS = 0x04
TAG_BINARY_OR_NATIVE = 0x08
TAG_BOOL_TRUE = 0x10
EXT_OVERRIDABLE = 0x02


@dataclass(frozen=True)
class Tag:
    name: str
    type: TypeName
    flags: int
    array_index: int
    start: int  # first byte of the tag (its name)
    size_pos: int  # where the int32 value size is stored
    flags_pos: int
    value_start: int
    size: int

    @property
    def end(self) -> int:
        return self.value_start + self.size


def scan_tags(pkg: Package, data: bytes, pos: int, limit: int) -> tuple[list[Tag], int]:
    """Tags of the block starting at ``pos``; also returns where its ``None`` terminator starts."""
    if pkg.ue5_version < UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
        raise ReadError("package saved by an older UEFN; re-save it in UEFN first")
    r = Reader(data, pos)
    tags: list[Tag] = []
    while r.pos < limit:
        start = r.pos
        name = pkg.read_name(r)
        if name == "None":
            return tags, start
        tn = _read_type_name(pkg, r)
        size_pos = r.pos
        size = r.i32()
        flags_pos = r.pos
        flags = r.u8()
        array_index = r.i32() if flags & TAG_HAS_ARRAY_INDEX else 0
        if flags & TAG_HAS_PROPERTY_GUID:
            r.raw(16)
        if flags & TAG_HAS_EXTENSIONS:
            if r.u8() & EXT_OVERRIDABLE:
                r.raw(5)
        if size < 0 or r.pos + size > limit:
            raise ReadError(f"property {name} has implausible size {size}")
        tags.append(Tag(name, tn, flags, array_index, start, size_pos, flags_pos, r.pos, size))
        r.pos += size
    raise ReadError("property block has no terminator")


def export_block(pkg: Package, export: Export) -> tuple[int, int]:
    """(start, limit) of an export's tagged properties, as absolute file positions."""
    start = export.serial_offset
    limit = export.serial_offset + export.serial_size
    if export.script_end > export.script_start:
        start, limit = export.serial_offset + export.script_start, export.serial_offset + export.script_end
    if pkg.ue5_version >= UE5_PROPERTY_TAG_EXTENSION:
        control = pkg.data[start]
        start += 2 if control & EXT_OVERRIDABLE else 1
    return start, limit


def find_tag(tags: list[Tag], name: str) -> Optional[Tag]:
    return next((t for t in tags if t.name == name and t.array_index == 0), None)
