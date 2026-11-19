"""Write widget edits back into a Widget Blueprint .uasset.

Only the property blocks of the edited objects are rewritten; every other byte is
kept, and the sizes/offsets that follow are shifted. A property name the file has
never used is appended to its name table first (which moves the rest of the header:
every absolute offset is shifted with it). An image the widget does not reference
yet gets two new entries at the end of the import table (its package and the asset),
the same way. ``apply_edits`` returns new bytes; the caller backs up, writes and
re-reads them.
"""

from __future__ import annotations

import struct
from typing import Any, Optional

from .package import Export, Package, load_package
from .props import Ref, export_properties
from .reader import ReadError
from .tags import TAG_BINARY_OR_NATIVE, export_block, find_tag, scan_tags


class EditError(ValueError):
    """The edit cannot be written safely; the message is shown to the person."""


# Type trees exactly as UEFN writes them (surveyed from saved Widget Blueprints).
def _struct(name: str, package: str) -> tuple:
    return ("StructProperty", (name, (package,)))


_T = {
    "float": ("FloatProperty",),
    "object": ("ObjectProperty",),
    "text": ("TextProperty",),
    "vec2d": _struct("Vector2D", "/Script/CoreUObject"),
    "vec2f": _struct("DeprecateSlateVector2D", "/Script/SlateCore"),
    "color": _struct("LinearColor", "/Script/CoreUObject"),
    "SlateColor": _struct("SlateColor", "/Script/SlateCore"),
    "SlateBrush": _struct("SlateBrush", "/Script/SlateCore"),
    "SlateFontInfo": _struct("SlateFontInfo", "/Script/SlateCore"),
    "Margin": _struct("Margin", "/Script/SlateCore"),
    "AnchorData": _struct("AnchorData", "/Script/UMG"),
    "WidgetTransform": _struct("WidgetTransform", "/Script/UMG"),
}
_NATIVE = {"vec2d", "vec2f", "color"}

# field id -> (where it lives, path of (property, type key)); the last step is the value.
_SIDES = ("Left", "Top", "Right", "Bottom")
FIELDS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "opacity": ("widget", (("RenderOpacity", "float"),)),
    "translation": ("widget", (("RenderTransform", "WidgetTransform"), ("Translation", "vec2d"))),
    "scale": ("widget", (("RenderTransform", "WidgetTransform"), ("Scale", "vec2d"))),
    "angle": ("widget", (("RenderTransform", "WidgetTransform"), ("Angle", "float"))),
    "text": ("widget", (("Text", "text"),)),
    "text_color": ("widget", (("ColorAndOpacity", "SlateColor"), ("SpecifiedColor", "color"))),
    "shadow_color": ("widget", (("ShadowColorAndOpacity", "color"),)),
    "font_size": ("widget", (("Font", "SlateFontInfo"), ("Size", "float"))),
    "image_color": ("widget", (("ColorAndOpacity", "color"),)),
    "tint": ("widget", (("Brush", "SlateBrush"), ("TintColor", "SlateColor"), ("SpecifiedColor", "color"))),
    "image_size": ("widget", (("Brush", "SlateBrush"), ("ImageSize", "vec2f"))),
    "resource": ("widget", (("Brush", "SlateBrush"), ("ResourceObject", "object"))),
    "alignment": ("slot", (("LayoutData", "AnchorData"), ("Alignment", "vec2d"))),
}
for _i, _side in enumerate(_SIDES):
    FIELDS[f"offsets.{_i}"] = ("slot", (("LayoutData", "AnchorData"), ("Offsets", "Margin"), (_side, "float")))
    FIELDS[f"padding.{_i}"] = ("slot", (("Padding", "Margin"), (_side, "float")))
# which fields a widget kind accepts (the panel sends the engine's kind)
KIND_FIELDS = {
    "TextBlock": {"text", "text_color", "shadow_color", "font_size"},
    "Image": {"image_color", "tint", "image_size", "resource"},
}
COMMON_FIELDS = {"opacity", "translation", "scale", "angle"}
_NONE_SIZE = 8


def _fname(pkg: Package, name: str) -> bytes:
    try:
        return struct.pack("<ii", pkg.names.index(name), 0)
    except ValueError:  # apply_edits adds every needed name beforehand
        raise EditError(f"name {name} is missing from the file; not edited") from None


def _tree_names(tree: tuple) -> list[str]:
    return [tree[0]] + [n for child in _inner(tree) for n in _tree_names(child)]


def _needed_names(path: tuple[tuple[str, str], ...]) -> list[str]:
    out = ["None"]
    for name, kind in path:
        out += [name] + _tree_names(_T[kind])
    return out


_COUNTS = ("name_count", "names_referenced_from_export_data_count", "import_count")
_IMPORT_ENTRY = 40  # class package, class name (names), outer, object name, package name, optional flag
_IMPORTABLE = {"Texture2D", "Material", "MaterialInstanceConstant"}


def add_names(data: bytes, names: list[str]) -> bytes:
    """``data`` with ``names`` appended to the name table and every later offset shifted."""
    pkg = load_package(data)
    start = pkg.names_end
    entries = b"".join(_fstring(name) + bytes(4) for name in names)  # hashes are ignored on load
    out = _insert_in_header(pkg, data, start, entries)
    count_pos, count, _ = pkg.fields["name_count"]
    struct.pack_into("<i", out, count_pos, count + len(names))
    referenced_pos, referenced, _ = pkg.fields["names_referenced_from_export_data_count"]
    if referenced == count:
        struct.pack_into("<i", out, referenced_pos, count + len(names))
    return bytes(out)


def add_imports(data: bytes, assets: list[tuple[str, str]]) -> bytes:
    """``data`` importing each ``(object path, class)``: a package entry and the asset inside it."""
    wanted = ["None", "Package", "/Script/CoreUObject", "/Script/Engine"]
    for object_path, asset_class in assets:
        if asset_class not in _IMPORTABLE:
            raise EditError(f"{asset_class or 'This asset'} cannot be used as a widget image.")
        package, _, asset = object_path.partition(".")
        if not package.startswith("/") or not asset or ":" in asset:
            raise EditError(f"unexpected asset path {object_path}")
        wanted += [package, asset, asset_class]
    pkg = load_package(data)
    missing = [n for n in dict.fromkeys(wanted) if n not in pkg.names]
    if missing:
        data = add_names(data, missing)
        pkg = load_package(data)
    count_pos, count, _ = pkg.fields["import_count"]
    start = pkg.fields["import_offset"][1] + count * _IMPORT_ENTRY
    if start > pkg.fields["export_offset"][1]:
        raise EditError("unexpected import table layout; file not modified")
    entries = b""
    for i, (object_path, asset_class) in enumerate(assets):
        package, _, asset = object_path.partition(".")
        package_index = -(count + 2 * i + 1)
        entries += (_fname(pkg, "/Script/CoreUObject") + _fname(pkg, "Package") + struct.pack("<i", 0)
                    + _fname(pkg, package) + _fname(pkg, "None") + struct.pack("<i", 0))
        entries += (_fname(pkg, "/Script/Engine") + _fname(pkg, asset_class) + struct.pack("<i", package_index)
                    + _fname(pkg, asset) + _fname(pkg, "None") + struct.pack("<i", 0))
    out = _insert_in_header(pkg, data, start, entries)
    struct.pack_into("<i", out, count_pos, count + 2 * len(assets))
    return _grow_import_bits(bytes(out), count, 2 * len(assets))


def _grow_import_bits(data: bytes, old_count: int, added: int) -> bytes:
    """Asset-registry dependency data keeps one "used in game" bit per import: add the new ones.

    Layout at its offset: int32 bit count, 32-bit words, then the same for soft package
    references. A count that disagrees with the import table makes UEFN's asset registry
    reject the whole package ("Package is unloadable ... SerializeAssetRegistryDependencyData").
    """
    pkg = load_package(data)
    registry = pkg.fields["asset_registry_data_offset"][1]
    if not registry:
        return data
    (start,) = struct.unpack_from("<q", data, registry)
    if not registry < start <= pkg.total_header_size - 4:
        raise EditError("unexpected asset registry data; file not modified")
    (bits,) = struct.unpack_from("<i", data, start)
    if bits != old_count:
        raise EditError("asset registry data does not match the import table; file not modified")
    old_words, new_words = (bits + 31) // 32, (bits + added + 31) // 32
    words_at = start + 4
    if new_words > old_words:
        grown = _insert_in_header(pkg, data, words_at + 4 * old_words, bytes(4 * (new_words - old_words)))
    else:
        grown = bytearray(data)
    struct.pack_into("<i", grown, start, bits + added)
    for bit in range(bits, bits + added):  # a widget's image is used in game
        position = words_at + 4 * (bit // 32)
        (word,) = struct.unpack_from("<I", grown, position)
        struct.pack_into("<I", grown, position, word | (1 << (bit % 32)))
    return bytes(grown)


def _insert_in_header(pkg: Package, data: bytes, start: int, entries: bytes) -> bytearray:
    """``entries`` inserted at ``start`` (inside the header), every later absolute offset shifted."""
    if pkg.data_resource_offset:
        raise EditError("This file stores data resources; changing its header is not supported. File not modified.")
    if not 0 < start <= pkg.total_header_size:
        raise EditError("unexpected header layout; file not modified")
    delta = len(entries)
    out = bytearray(data[:start] + entries + data[start:])

    def moved(position: int) -> int:
        return position + delta if position >= start else position

    for name, (position, value, wide) in pkg.fields.items():
        if name in _COUNTS:
            continue
        if value >= start:
            struct.pack_into("<q" if wide else "<i", out, moved(position), value + delta)
    for exp in pkg.exports:
        struct.pack_into("<q", out, moved(exp.size_pos + 8), exp.serial_offset + delta)
    # thumbnail table: class name, object path, absolute offset of the image
    table = pkg.fields["thumbnail_table_offset"][1]
    if table:
        (entries_count,) = struct.unpack_from("<i", data, table)
        if not 0 <= entries_count < 64:
            raise EditError("unexpected thumbnail table; file not modified")
        cursor = table + 4
        for _ in range(entries_count):
            cursor = _skip_fstring(data, _skip_fstring(data, cursor))
            (offset,) = struct.unpack_from("<i", data, cursor)
            if offset >= start:
                struct.pack_into("<i", out, moved(cursor), offset + delta)
            cursor += 4
    registry = pkg.fields["asset_registry_data_offset"][1]
    if registry:
        (dependency_data,) = struct.unpack_from("<q", data, registry)
        if start <= dependency_data <= len(data):
            struct.pack_into("<q", out, moved(registry), dependency_data + delta)
    return out


def _inner(tree: tuple) -> list:
    return [part for part in tree[1:] if isinstance(part, tuple)]


def _type_bytes(pkg: Package, tree: tuple) -> bytes:
    children = _inner(tree)
    out = _fname(pkg, tree[0]) + struct.pack("<i", len(children))
    for child in children:
        out += _type_bytes(pkg, child)
    return out


def _tree_str(tree: tuple) -> str:
    children = _inner(tree)
    return tree[0] + (f"({','.join(_tree_str(c) for c in children)})" if children else "")


def _fstring(text: str) -> bytes:
    if not text:
        return struct.pack("<i", 0)
    try:
        raw = text.encode("ascii") + b"\x00"
        return struct.pack("<i", len(raw)) + raw
    except UnicodeEncodeError:
        raw = text.encode("utf-16-le") + b"\x00\x00"
        return struct.pack("<i", -(len(raw) // 2)) + raw


def _skip_fstring(data: bytes, pos: int) -> int:
    (length,) = struct.unpack_from("<i", data, pos)
    end = pos + 4 + (length if length >= 0 else -length * 2)
    if end > len(data):
        raise EditError("text value is damaged; not edited")
    return end


def _text_value(old: Optional[bytes], text: str, seed: str = "") -> bytes:
    """Same FText, new source string (its localization key is kept)."""
    if old is None or len(old) < 5:
        # No text yet: a new localizable text, laid out as UEFN saves one (empty namespace
        # stored as a lone terminator, 32-hex key). The key comes from ``seed`` so the
        # designer copy and the compiled copy of a widget share it.
        import hashlib

        key = hashlib.md5(("ducky-umg-text\0" + seed).encode("utf-8")).hexdigest().upper()
        # ...and a closing int32 that UEFN writes as 0 after the source string. Without it
        # UEFN refuses the property ("Failed loading tagged TextProperty", seen in its log).
        return (struct.pack("<Ib", 0, 0) + struct.pack("<i", 1) + bytes(1) + _fstring(key) + _fstring(text)
                + struct.pack("<i", 0))
    history = struct.unpack_from("<b", old, 4)[0]
    if history == 0:  # namespace, key, source
        pos = _skip_fstring(old, _skip_fstring(old, 5))
        end = _skip_fstring(old, pos)
        return old[:pos] + _fstring(text) + old[end:]
    if history == -1:  # culture-invariant: has-string flag, string
        return old[:5] + struct.pack("<i", 1) + _fstring(text)
    raise EditError("This text is generated (formatted or from a string table) and cannot be edited here.")


def _floats(value: Any, count: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise EditError(f"expected {count} numbers")
    try:
        out = [float(v) for v in value]
    except (TypeError, ValueError):
        raise EditError("expected numbers") from None
    if any(v != v or abs(v) > 1e9 for v in out):
        raise EditError("number out of range")
    return out


def _import_index(pkg: Package, object_path: str) -> int:
    for i in range(len(pkg.imports)):
        if pkg.object_path(-(i + 1)) == object_path:
            return -(i + 1)
    raise EditError(f"asset {object_path} is not referenced by the file; not edited")  # apply_edits adds it first


def _leaf(pkg: Package, kind: str, value: Any, old: Optional[bytes], seed: str = "") -> bytes:
    if kind == "float":
        return struct.pack("<f", _floats([value], 1)[0])
    if kind == "vec2d":
        return struct.pack("<2d", *_floats(value, 2))
    if kind == "vec2f":
        return struct.pack("<2f", *_floats(value, 2))
    if kind == "color":
        return struct.pack("<4f", *_floats(value, 4))
    if kind == "text":
        return _text_value(old, str(value), seed)
    if kind == "object":
        return struct.pack("<i", _import_index(pkg, str(value)))
    raise EditError(f"unsupported value type {kind}")


def _set(pkg: Package, block: bytes, path: tuple[tuple[str, str], ...], value: Any, seed: str = "") -> bytes:
    """``block`` (tags + None terminator) with ``path`` set to ``value``."""
    tags, terminator = scan_tags(pkg, block, 0, len(block))
    (name, kind), rest = path[0], path[1:]
    tag = find_tag(tags, name)
    if tag is not None and str(tag.type) != _tree_str(_T[kind]):
        raise EditError(f"{name} is stored in an unexpected form ({tag.type}); not edited.")
    old = block[tag.value_start : tag.end] if tag is not None else None
    if rest:
        new_value = _set(pkg, old if old is not None else _fname(pkg, "None"), rest, value, seed)
    else:
        new_value = _leaf(pkg, kind, value, old, seed)
    if tag is not None:
        return (block[: tag.size_pos] + struct.pack("<i", len(new_value))
                + block[tag.size_pos + 4 : tag.value_start] + new_value + block[tag.end :])
    flags = TAG_BINARY_OR_NATIVE if kind in _NATIVE else 0
    header = _fname(pkg, name) + _type_bytes(pkg, _T[kind]) + struct.pack("<iB", len(new_value), flags)
    return block[:terminator] + header + new_value + block[terminator:]


def _targets(pkg: Package, widget: str, where: str) -> list[Export]:
    """Every copy of the widget (designer tree and compiled class tree), or of its slot."""
    trees = {e.index for e in pkg.exports if pkg.class_name(e) == "WidgetTree"}
    found = [e for e in pkg.exports if e.object_name == widget and e.outer_index in trees]
    if not found:
        raise EditError(
            f"Widget {widget} is not part of this Widget Blueprint "
            "(widgets nested from another file are edited in their own file)."
        )
    if where == "widget":
        return found
    slots = []
    for exp in found:
        ref = export_properties(pkg, exp).get("Slot")
        if not isinstance(ref, Ref) or ref.index <= 0:
            raise EditError(f"Widget {widget} has no slot to edit.")
        slots.append(pkg.exports[ref.index - 1])
    return slots


def apply_edits(data: bytes, edits: list[dict[str, Any]]) -> bytes:
    """New file bytes with ``edits`` applied: ``[{"widget", "kind", "field", "value"}, ...]``."""
    pkg = load_package(data)
    if not any(pkg.class_name(e) == "WidgetBlueprint" and e.outer_index == 0 for e in pkg.exports):
        raise EditError("not a Widget Blueprint")
    wanted: list[str] = []
    for edit in edits:
        if str(edit.get("field")) in FIELDS:
            wanted += _needed_names(FIELDS[str(edit.get("field"))][1])
    missing = [n for n in dict.fromkeys(wanted) if n not in pkg.names]
    if missing:
        data = add_names(data, missing)
        pkg = load_package(data)
    # images the widget does not reference yet (the caller says what class each one is)
    known = {pkg.object_path(-(i + 1)) for i in range(len(pkg.imports))}
    new_assets: dict[str, str] = {}
    for edit in edits:
        if str(edit.get("field")) == "resource" and str(edit.get("value")) not in known:
            new_assets[str(edit.get("value"))] = str(edit.get("asset_class") or "")
    if new_assets:
        data = add_imports(data, list(new_assets.items()))
        pkg = load_package(data)
    blocks: dict[int, bytes] = {}  # export index -> new property block
    spans: dict[int, tuple[int, int]] = {}
    for edit in edits:
        field, widget, kind = str(edit.get("field")), str(edit.get("widget")), str(edit.get("kind"))
        if field not in FIELDS:
            raise EditError(f"unknown field {field}")
        where, path = FIELDS[field]
        if where == "widget" and field not in COMMON_FIELDS and field not in KIND_FIELDS.get(kind, ()):
            raise EditError(f"{field} cannot be set on a {kind}")
        for exp in _targets(pkg, widget, where):
            try:
                if exp.index not in blocks:
                    start, limit = export_block(pkg, exp)
                    _tags, terminator = scan_tags(pkg, data, start, limit)
                    spans[exp.index] = (start, terminator + _NONE_SIZE)
                    blocks[exp.index] = data[start : terminator + _NONE_SIZE]
                blocks[exp.index] = _set(pkg, blocks[exp.index], path, edit.get("value"),
                                         f"{pkg.package_name}\0{widget}\0{field}")
            except ReadError as exc:
                raise EditError(f"{widget}: {exc}") from exc
    return _rebuild(pkg, data, blocks, spans) if blocks else data


def _rebuild(pkg: Package, data: bytes, blocks: dict[int, bytes], spans: dict[int, tuple[int, int]]) -> bytes:
    order = sorted(blocks, key=lambda i: spans[i][0])
    deltas = {i: len(blocks[i]) - (spans[i][1] - spans[i][0]) for i in order}
    if spans[order[0]][0] < min(e.serial_offset for e in pkg.exports):
        raise EditError("edit would move the file header; file not modified")
    out = bytearray()
    cursor = 0
    for i in order:
        start, end = spans[i]
        if start < cursor:
            raise EditError("overlapping exports; file not modified")
        out += data[cursor:start] + blocks[i]
        cursor = end
    out += data[cursor:]

    def shift(position: int) -> int:  # how far a byte that was at ``position`` moved
        return sum(deltas[i] for i in order if spans[i][1] <= position)

    for exp in pkg.exports:
        delta = deltas.get(exp.index, 0)
        struct.pack_into("<qq", out, exp.size_pos, exp.serial_size + delta, exp.serial_offset + shift(exp.serial_offset))
        if exp.script_pos and exp.script_end > exp.script_start and delta:
            struct.pack_into("<qq", out, exp.script_pos, exp.script_start, exp.script_end + delta)
    total = sum(deltas.values())
    exports_end = max(e.serial_offset + e.serial_size for e in pkg.exports)
    for position, value, fmt in ((pkg.bulk_data_start_pos, pkg.bulk_data_start, "<q"),
                                 (pkg.payload_toc_pos, pkg.payload_toc_offset, "<q"),
                                 (pkg.data_resource_pos, pkg.data_resource_offset, "<i")):
        if position and value >= exports_end:
            struct.pack_into(fmt, out, position, value + total)
    return bytes(out)
