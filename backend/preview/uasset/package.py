"""Read the tables of an uncooked UE5 package: names, imports, exports.

Only what the previews need. Covers the editor packages UEFN writes (UE5 object
versions 1000+); cooked or older packages raise ``ReadError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .reader import ReadError, Reader

PACKAGE_TAG = 0x9E2A83C1
PKG_FILTER_EDITOR_ONLY = 0x80000000

# EUnrealEngineObjectUE5Version
UE5_OPTIONAL_RESOURCES = 1003
UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID = 1005
UE5_TRACK_OBJECT_EXPORT_IS_INHERITED = 1006
UE5_SOFTOBJECTPATH_REMOVE_ASSET_PATH_FNAMES = 1007
UE5_ADD_SOFTOBJECTPATH_LIST = 1008
UE5_DATA_RESOURCES = 1009
UE5_SCRIPT_SERIALIZATION_OFFSET = 1010
UE5_PROPERTY_TAG_EXTENSION = 1011
UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME = 1012
UE5_METADATA_SERIALIZATION_OFFSET = 1014
UE5_VERSE_CELLS = 1015
UE5_PACKAGE_SAVED_HASH = 1016
UE5_IMPORT_TYPE_HIERARCHIES = 1018


@dataclass
class Import:
    class_package: str
    class_name: str
    outer_index: int
    object_name: str


@dataclass
class Export:
    index: int
    class_index: int
    super_index: int
    template_index: int
    outer_index: int
    object_name: str
    object_flags: int
    serial_size: int
    serial_offset: int
    script_start: int = 0
    script_end: int = 0


@dataclass
class Package:
    data: bytes
    ue4_version: int = 0
    ue5_version: int = 0
    package_name: str = ""
    package_flags: int = 0
    names: list[str] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    exports: list[Export] = field(default_factory=list)
    soft_object_paths: list[str] = field(default_factory=list)
    bulk_data_start: int = 0
    payload_toc_offset: int = 0
    total_header_size: int = 0

    # -- names -------------------------------------------------------------

    def read_name(self, r: Reader) -> str:
        """FName as stored in export data: name-table index + instance number."""
        index = r.i32()
        number = r.i32()
        if not 0 <= index < len(self.names):
            raise ReadError(f"name index {index} out of range at {r.pos - 8}")
        base = self.names[index]
        return f"{base}_{number - 1}" if number > 0 else base

    # -- object references ---------------------------------------------------

    def object_name(self, index: int) -> str:
        if index > 0 and index <= len(self.exports):
            return self.exports[index - 1].object_name
        if index < 0 and -index <= len(self.imports):
            return self.imports[-index - 1].object_name
        return ""

    def object_path(self, index: int) -> str:
        """``/Package/Path.Object`` (imports) or ``Outer.Object`` chain (exports)."""
        parts: list[str] = []
        seen = 0
        while index != 0 and seen < 64:
            seen += 1
            if index > 0:
                if index > len(self.exports):
                    break
                exp = self.exports[index - 1]
                parts.append(exp.object_name)
                index = exp.outer_index
            else:
                if -index > len(self.imports):
                    break
                imp = self.imports[-index - 1]
                parts.append(imp.object_name)
                index = imp.outer_index
        parts.reverse()
        if not parts:
            return ""
        if parts[0].startswith("/"):
            head = parts[0]
            if len(parts) == 1:
                return head
            return head + "." + ":".join([parts[1]] + [".".join(parts[2:])] if len(parts) > 2 else [parts[1]])
        return ".".join(parts)

    def class_name(self, export: Export) -> str:
        return self.object_name(export.class_index)

    def class_path(self, export: Export) -> str:
        return self.object_path(export.class_index)

    def export_children(self, export: Export) -> list[Export]:
        return [e for e in self.exports if e.outer_index == export.index]

    def find_export(self, name: str, class_name: str = "") -> Optional[Export]:
        for exp in self.exports:
            if exp.object_name == name and (not class_name or self.class_name(exp) == class_name):
                return exp
        return None


def main_asset_class(source: Path | str) -> str:
    """Class of the asset a .uasset holds (``WidgetBlueprint``, ``Texture2D``…); '' if unreadable."""
    try:
        pkg = load_package(source)
    except (ReadError, OSError):
        return ""
    stem = pkg.package_name.rsplit("/", 1)[-1]
    for exp in pkg.exports:
        if exp.outer_index == 0 and exp.object_name == stem:
            return pkg.class_name(exp)
    return ""


def _custom_versions(r: Reader) -> None:
    count = r.i32()
    if not 0 <= count < 4096:
        raise ReadError(f"implausible custom version count {count}")
    r.raw(count * 20)


def _engine_version(r: Reader) -> None:
    r.raw(10)  # major, minor, patch (uint16 each) + changelist (uint32)
    r.fstring()


def load_package(source: Path | str | bytes) -> Package:
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    data = bytes(data)
    r = Reader(data)
    if len(data) < 64 or r.u32() != PACKAGE_TAG:
        raise ReadError("not an Unreal package (bad tag)")
    legacy = r.i32()
    if legacy > -8:
        raise ReadError(f"unsupported package (legacy file version {legacy}, need UE5)")
    r.i32()  # LegacyUE3Version
    pkg = Package(data=data)
    pkg.ue4_version = r.i32()
    pkg.ue5_version = r.i32()
    r.i32()  # licensee version
    if pkg.ue5_version == 0:
        raise ReadError("unversioned (cooked) package — only editor packages are supported")
    ue5 = pkg.ue5_version
    if ue5 >= UE5_PACKAGE_SAVED_HASH:
        r.raw(20)  # SavedHash
        pkg.total_header_size = r.i32()
    _custom_versions(r)
    if ue5 < UE5_PACKAGE_SAVED_HASH:
        pkg.total_header_size = r.i32()
    pkg.package_name = r.fstring()
    pkg.package_flags = r.u32()
    editor = not (pkg.package_flags & PKG_FILTER_EDITOR_ONLY)
    if not editor:
        raise ReadError("cooked package (editor-only data stripped) is not supported")
    name_count, name_offset = r.i32(), r.i32()
    soft_count = soft_offset = 0
    if ue5 >= UE5_ADD_SOFTOBJECTPATH_LIST:
        soft_count, soft_offset = r.i32(), r.i32()
    r.fstring()  # LocalizationId
    r.i32(), r.i32()  # gatherable text count / offset
    export_count, export_offset = r.i32(), r.i32()
    import_count, import_offset = r.i32(), r.i32()
    if ue5 >= UE5_VERSE_CELLS:
        r.raw(16)
    if ue5 >= UE5_METADATA_SERIALIZATION_OFFSET:
        r.i32()
    r.i32()  # DependsOffset
    r.i32(), r.i32()  # soft package references count / offset
    r.i32()  # SearchableNamesOffset
    r.i32()  # ThumbnailTableOffset
    if ue5 >= UE5_IMPORT_TYPE_HIERARCHIES:
        r.i32(), r.i32()  # import type hierarchies count / offset
    if ue5 < UE5_PACKAGE_SAVED_HASH:
        r.guid()
    r.guid()  # PersistentGuid
    generations = r.i32()
    if not 0 <= generations < 1024:
        raise ReadError(f"implausible generation count {generations}")
    r.raw(generations * 8)
    _engine_version(r)
    _engine_version(r)
    r.u32()  # CompressionFlags
    if r.i32() != 0:
        raise ReadError("package-level compression is not supported")
    r.u32()  # PackageSource
    for _ in range(r.i32()):
        r.fstring()
    r.i32()  # AssetRegistryDataOffset
    pkg.bulk_data_start = r.i64()
    r.i32()  # WorldTileInfoDataOffset
    chunk_ids = r.i32()
    if not 0 <= chunk_ids < 4096:
        raise ReadError(f"implausible chunk id count {chunk_ids}")
    r.raw(chunk_ids * 4)
    r.i32(), r.i32()  # preload dependency count / offset
    r.i32()  # NamesReferencedFromExportDataCount
    pkg.payload_toc_offset = r.i64()

    for count, offset, what in (
        (name_count, name_offset, "name"),
        (export_count, export_offset, "export"),
        (import_count, import_offset, "import"),
    ):
        if not (0 <= count < 5_000_000 and 0 <= offset <= len(data)):
            raise ReadError(f"implausible {what} table ({count} entries at {offset})")

    r.pos = name_offset
    for _ in range(name_count):
        pkg.names.append(r.fstring())
        r.raw(4)  # two 16-bit hashes

    if soft_count:
        r.pos = soft_offset
        for _ in range(soft_count):
            package_name = pkg.read_name(r)
            asset_name = pkg.read_name(r)
            sub_path = r.fstring()
            path = package_name if asset_name in ("", "None") else f"{package_name}.{asset_name}"
            pkg.soft_object_paths.append(f"{path}:{sub_path}" if sub_path else path)

    r.pos = import_offset
    for _ in range(import_count):
        class_package = pkg.read_name(r)
        class_name = pkg.read_name(r)
        outer_index = r.i32()
        object_name = pkg.read_name(r)
        r.raw(8)  # PackageName (editor)
        if ue5 >= UE5_OPTIONAL_RESOURCES:
            r.i32()  # bImportOptional
        pkg.imports.append(Import(class_package, class_name, outer_index, object_name))

    r.pos = export_offset
    for i in range(export_count):
        class_index, super_index, template_index, outer_index = r.i32(), r.i32(), r.i32(), r.i32()
        object_name = pkg.read_name(r)
        object_flags = r.u32()
        serial_size, serial_offset = r.i64(), r.i64()
        r.raw(12)  # bForcedExport, bNotForClient, bNotForServer
        if ue5 < UE5_REMOVE_OBJECT_EXPORT_PACKAGE_GUID:
            r.guid()
        if ue5 >= UE5_TRACK_OBJECT_EXPORT_IS_INHERITED:
            r.i32()
        r.u32()  # PackageFlags
        r.raw(8)  # bNotAlwaysLoadedForEditorGame, bIsAsset
        if ue5 >= UE5_OPTIONAL_RESOURCES:
            r.i32()  # bGeneratePublicHash
        r.raw(20)  # dependency header
        script_start = script_end = 0
        if ue5 >= UE5_SCRIPT_SERIALIZATION_OFFSET:
            script_start, script_end = r.i64(), r.i64()
        if not (0 <= serial_offset <= len(data) and 0 <= serial_size <= len(data)):
            raise ReadError(f"export {i} ({object_name}) points outside the file")
        pkg.exports.append(
            Export(
                index=i + 1,
                class_index=class_index,
                super_index=super_index,
                template_index=template_index,
                outer_index=outer_index,
                object_name=object_name,
                object_flags=object_flags,
                serial_size=serial_size,
                serial_offset=serial_offset,
                script_start=script_start,
                script_end=script_end,
            )
        )
    return pkg
