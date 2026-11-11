"""Decode the tagged properties of an export (UE5 "complete type name" tags).

Every top-level tag carries its byte size, so a value this module cannot decode is
kept as ``Raw`` and the cursor still lands on the next tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .package import (
    UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME,
    UE5_PROPERTY_TAG_EXTENSION,
    Export,
    Package,
)
from .reader import ReadError, Reader

_TAG_HAS_ARRAY_INDEX = 0x01
_TAG_HAS_PROPERTY_GUID = 0x02
_TAG_HAS_EXTENSIONS = 0x04
_TAG_BINARY_OR_NATIVE = 0x08
_TAG_BOOL_TRUE = 0x10
_EXT_OVERRIDABLE = 0x02


@dataclass(frozen=True)
class Ref:
    """Object reference: >0 export index, <0 import index, 0 null."""

    index: int


@dataclass(frozen=True)
class Raw:
    """A value kept undecoded (native struct we do not know, or a failed decode)."""

    type_name: str
    data: bytes


@dataclass(frozen=True)
class TypeName:
    name: str
    inner: tuple["TypeName", ...] = ()

    def __str__(self) -> str:
        return self.name + (f"({','.join(map(str, self.inner))})" if self.inner else "")


def _read_type_name(pkg: Package, r: Reader) -> TypeName:
    flat: list[tuple[str, int]] = []
    remaining = 1
    while remaining > 0:
        name = pkg.read_name(r)
        inner_count = r.i32()
        if not 0 <= inner_count < 64 or len(flat) > 256:
            raise ReadError(f"implausible property type at {r.pos}")
        flat.append((name, inner_count))
        remaining += inner_count - 1

    cursor = 0

    def build() -> TypeName:
        nonlocal cursor
        name, count = flat[cursor]
        cursor += 1
        return TypeName(name, tuple(build() for _ in range(count)))

    return build()


def _vec(r: Reader, count: int, fmt: str = "d") -> list[float]:
    return [r.f64() if fmt == "d" else r.f32() for _ in range(count)]


def _soft_path(pkg: Package, r: Reader) -> str:
    if pkg.soft_object_paths:
        index = r.i32()
        if 0 <= index < len(pkg.soft_object_paths):
            return pkg.soft_object_paths[index]
        return ""
    package_name = pkg.read_name(r)
    asset_name = pkg.read_name(r)
    sub_path = r.fstring()
    path = package_name if asset_name in ("", "None") else f"{package_name}.{asset_name}"
    return f"{path}:{sub_path}" if sub_path else path


def _frame_range(pkg: Package, r: Reader) -> dict[str, Any]:
    lower_type, lower = r.i8(), r.i32()
    upper_type, upper = r.i8(), r.i32()
    # Bound types: 0 exclusive, 1 inclusive, 2 open.
    return {"lower": None if lower_type == 2 else lower, "upper": None if upper_type == 2 else upper,
            "lower_inclusive": lower_type == 1, "upper_inclusive": upper_type == 1}


def _curve_channel(pkg: Package, r: Reader) -> dict[str, Any]:
    """FMovieSceneFloatChannel / DoubleChannel: key times (ticks) and cubic key values."""
    pre, post = r.u8(), r.u8()
    time_size, count = r.i32(), r.i32()
    if time_size != 4 or not 0 <= count <= 1_000_000:
        raise ReadError("unexpected channel time layout")
    times = [r.i32() for _ in range(count)]
    value_size, value_count = r.i32(), r.i32()
    if value_size not in (28, 32) or value_count != count:
        raise ReadError("unexpected channel value layout")
    keys = []
    for time in times:
        rec = Reader(r.raw(value_size))
        value = rec.f32() if value_size == 28 else rec.f64()
        arrive, leave, arrive_w, leave_w = rec.f32(), rec.f32(), rec.f32(), rec.f32()
        rec.pos = value_size - 4
        # interp: 0 linear, 1 constant, 2 cubic
        keys.append({"t": time, "v": value, "arrive": arrive, "leave": leave,
                     "arrive_w": arrive_w, "leave_w": leave_w, "interp": rec.u8()})
    default = r.f32() if value_size == 28 else r.f64()
    has_default = bool(r.i32())
    numerator, denominator = r.i32(), r.i32()
    return {"keys": keys, "default": default if has_default else None, "pre": pre, "post": post,
            "tick_rate": numerator / denominator if denominator else 0.0}


# Structs with their own binary serializer (no tags inside).
_NATIVE_STRUCTS = {
    "Vector2D": lambda p, r: _vec(r, 2),
    "Vector": lambda p, r: _vec(r, 3),
    "Vector4": lambda p, r: _vec(r, 4),
    "Rotator": lambda p, r: _vec(r, 3),
    "Quat": lambda p, r: _vec(r, 4),
    "Vector2f": lambda p, r: _vec(r, 2, "f"),
    "Vector3f": lambda p, r: _vec(r, 3, "f"),
    "DeprecateSlateVector2D": lambda p, r: _vec(r, 2, "f"),
    "LinearColor": lambda p, r: _vec(r, 4, "f"),
    "Color": lambda p, r: (lambda b: [b[2], b[1], b[0], b[3]])(r.raw(4)),  # stored BGRA
    "Guid": lambda p, r: r.guid(),
    "IntPoint": lambda p, r: [r.i32(), r.i32()],
    "IntVector": lambda p, r: [r.i32(), r.i32(), r.i32()],
    "DateTime": lambda p, r: r.i64(),
    "Timespan": lambda p, r: r.i64(),
    "FrameNumber": lambda p, r: r.i32(),
    "SoftObjectPath": _soft_path,
    "SoftClassPath": _soft_path,
    "MovieSceneFrameRange": _frame_range,
    "MovieSceneFloatChannel": _curve_channel,
    "MovieSceneDoubleChannel": _curve_channel,
    "Box2D": lambda p, r: {"min": _vec(r, 2), "max": _vec(r, 2), "valid": r.u8()},
}


def _read_text(r: Reader, pkg: Package) -> str:
    r.u32()  # flags
    history = r.i8()
    if history == -1:
        return r.fstring() if r.i32() else ""
    if history == 0:
        r.fstring()  # namespace
        r.fstring()  # key
        return r.fstring()
    if history == 11:  # string table entry
        pkg.read_name(r)
        return r.fstring()
    raise ReadError(f"unsupported text history {history}")


def _read_value(pkg: Package, r: Reader, tn: TypeName, *, in_container: bool, bool_value: bool = False,
                native: Optional[bool] = None, size: int = 0) -> Any:
    name = tn.name
    if name == "BoolProperty":
        return bool(r.u8()) if in_container else bool_value
    if name == "IntProperty":
        return r.i32()
    if name == "FloatProperty":
        return r.f32()
    if name == "DoubleProperty":
        return r.f64()
    if name == "ByteProperty":
        if tn.inner and (in_container or size == 8):
            return pkg.read_name(r)
        return r.u8()
    if name == "EnumProperty":
        return pkg.read_name(r)
    if name == "NameProperty":
        return pkg.read_name(r)
    if name in ("StrProperty", "Utf8StrProperty", "AnsiStrProperty"):
        return r.fstring()
    if name == "TextProperty":
        return _read_text(r, pkg)
    if name in ("ObjectProperty", "ClassProperty", "WeakObjectProperty", "InterfaceProperty",
                "ObjectPtrProperty"):
        return Ref(r.i32())
    if name in ("SoftObjectProperty", "SoftClassProperty"):
        return _soft_path(pkg, r)
    if name == "Int8Property":
        return r.i8()
    if name == "Int16Property":
        return r.i16()
    if name == "Int64Property":
        return r.i64()
    if name == "UInt16Property":
        return r.u16()
    if name == "UInt32Property":
        return r.u32()
    if name == "UInt64Property":
        return r.u64()
    if name == "StructProperty":
        struct_name = tn.inner[0].name if tn.inner else ""
        decoder = _NATIVE_STRUCTS.get(struct_name)
        if decoder is not None and native is not False:
            return decoder(pkg, r)
        # Structs flagged "native" without a decoder here are often tagged anyway
        # (their serializer defers to tags in editor packages); the size check tells.
        start = r.pos
        value = read_tagged(pkg, r)
        if native and size and r.pos != start + size:
            raise ReadError(f"native struct {struct_name} has no decoder")
        value["$struct"] = struct_name
        return value
    if name in ("ArrayProperty", "SetProperty"):
        if name == "SetProperty" and r.i32() != 0:
            raise ReadError("set with removed entries is not supported")
        count = r.i32()
        if not 0 <= count <= 1_000_000 or not tn.inner:
            raise ReadError(f"implausible array count {count}")
        inner = tn.inner[0]
        return [_read_value(pkg, r, inner, in_container=True) for _ in range(count)]
    if name == "MapProperty":
        if r.i32() != 0:
            raise ReadError("map with removed entries is not supported")
        count = r.i32()
        if not 0 <= count <= 1_000_000 or len(tn.inner) < 2:
            raise ReadError(f"implausible map count {count}")
        out = []
        for _ in range(count):
            key = _read_value(pkg, r, tn.inner[0], in_container=True)
            out.append((key, _read_value(pkg, r, tn.inner[1], in_container=True)))
        return out
    if name == "DelegateProperty":
        return {"object": Ref(r.i32()), "function": pkg.read_name(r)}
    if name in ("MulticastDelegateProperty", "MulticastInlineDelegateProperty",
                "MulticastSparseDelegateProperty"):
        return [{"object": Ref(r.i32()), "function": pkg.read_name(r)} for _ in range(r.i32())]
    raise ReadError(f"unsupported property type {name}")


def read_tagged(pkg: Package, r: Reader, end: Optional[int] = None) -> dict[str, Any]:
    """Read tags until ``None``. Repeated names (static arrays) become lists."""
    if pkg.ue5_version < UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME:
        raise ReadError(
            f"package saved by an older UEFN (object version {pkg.ue5_version}); re-save it in UEFN to preview"
        )
    out: dict[str, Any] = {}
    limit = len(r.data) if end is None else end
    while r.pos < limit:
        name = pkg.read_name(r)
        if name == "None":
            break
        tn = _read_type_name(pkg, r)
        size = r.i32()
        flags = r.u8()
        array_index = r.i32() if flags & _TAG_HAS_ARRAY_INDEX else 0
        if flags & _TAG_HAS_PROPERTY_GUID:
            r.guid()
        if flags & _TAG_HAS_EXTENSIONS:
            ext = r.u8()
            if ext & _EXT_OVERRIDABLE:
                r.u8()
                r.i32()
        start = r.pos
        if size < 0 or start + size > len(r.data):
            raise ReadError(f"property {name} has implausible size {size}")
        try:
            value = _read_value(
                pkg, r, tn, in_container=False, bool_value=bool(flags & _TAG_BOOL_TRUE),
                native=bool(flags & _TAG_BINARY_OR_NATIVE) if tn.name == "StructProperty" else None,
                size=size,
            )
        except (ReadError, UnicodeDecodeError, IndexError):
            value = Raw(str(tn), r.data[start : start + size])
        r.pos = start + size
        if array_index:
            existing = out.get(name)
            items = existing if isinstance(existing, list) and out.get(f"${name}$static") else [existing]
            while len(items) <= array_index:
                items.append(None)
            items[array_index] = value
            out[name] = items
            out[f"${name}$static"] = True
        else:
            out[name] = value
    return out


def export_properties(pkg: Package, export: Export) -> dict[str, Any]:
    """The tagged properties of one export."""
    start = export.serial_offset
    end = export.serial_offset + export.serial_size
    if export.script_end > export.script_start:
        start, end = export.serial_offset + export.script_start, export.serial_offset + export.script_end
    r = Reader(pkg.data, start)
    if pkg.ue5_version >= UE5_PROPERTY_TAG_EXTENSION:
        control = r.u8()
        if control & _EXT_OVERRIDABLE:
            r.u8()
    return read_tagged(pkg, r, end)
