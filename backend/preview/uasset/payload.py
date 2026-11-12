"""Editor bulk payloads (texture source pixels, sound WAV) stored in the package trailer.

Payloads are FCompressedBuffers, normally Oodle-compressed. Oodle is proprietary: it
is decoded only when an ``ooz`` module is importable; otherwise callers get ``None``
and fall back (embedded thumbnail, UEFN export).
"""

from __future__ import annotations

import io
import struct
from typing import Callable, Optional

from .package import Package

_TRAILER_ID = 0xD1C43B2E80A5F697
_BUFFER_MAGIC = 0xB7756362
_METHOD_NONE, _METHOD_OODLE = 0, 3
_MAX_PAYLOAD = 128 * 1024 * 1024


def _oodle() -> Optional[Callable[[bytes, int], bytes]]:
    try:
        import ooz  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - optional dependency, any import failure means "absent"
        return None
    return ooz.decompress


def oodle_available() -> bool:
    return _oodle() is not None


def _decompress(data: bytes, pos: int) -> Optional[bytes]:
    if pos < 0 or pos + 64 > len(data):
        return None
    magic, _crc, method, _compressor, _level, block_exp, block_count, raw_size, comp_size = struct.unpack_from(
        ">IIBBBBIQQ", data, pos
    )
    if magic != _BUFFER_MAGIC or not 0 < raw_size <= _MAX_PAYLOAD:
        return None
    if comp_size < 64 or pos + comp_size > len(data):
        return None
    if method == _METHOD_NONE:
        if comp_size != 64 + raw_size:
            return None
        return data[pos + 64 : pos + 64 + raw_size]
    if method != _METHOD_OODLE or not 1 <= block_exp <= 26:
        return None
    block_size = 1 << block_exp
    if block_count != (raw_size + block_size - 1) // block_size or 64 + 4 * block_count > comp_size:
        return None
    decode = _oodle()
    if decode is None:
        return None
    sizes = struct.unpack_from(f">{block_count}I", data, pos + 64)
    if sum(sizes) != comp_size - 64 - 4 * block_count or not all(sizes):
        return None
    cursor = pos + 64 + 4 * block_count
    out = bytearray()
    for size in sizes:
        raw = min(block_size, raw_size - len(out))
        if size > raw:
            return None
        block = data[cursor : cursor + size]
        cursor += size
        decoded = block if size == raw else decode(block, raw)
        if not isinstance(decoded, bytes) or len(decoded) != raw:
            return None
        out += decoded
    return bytes(out) if len(out) == raw_size else None


def first_payload(pkg: Package) -> Optional[bytes]:
    """The package's first trailer payload, decompressed; None when unavailable."""
    data, toc = pkg.data, pkg.payload_toc_offset
    if not 0 < toc < len(data) - 28:
        return None
    trailer_id, _version, header_len, _payload_len = struct.unpack_from("<QiIQ", data, toc)
    if trailer_id != _TRAILER_ID:
        return None
    try:
        return _decompress(data, toc + header_len)
    except Exception:  # noqa: BLE001 - a bad or unsupported stream is "no payload", never a crash
        return None


def sound_wav(pkg: Package) -> Optional[bytes]:
    payload = first_payload(pkg)
    return payload if payload and payload[:4] == b"RIFF" else None


def _texture_source(pkg: Package) -> dict:
    from .props import export_properties

    for exp in pkg.exports:
        if pkg.class_name(exp) == "Texture2D":
            return export_properties(pkg, exp).get("Source") or {}
    return {}


def _decode_delta_rgba(data: bytes, width: int, height: int) -> bytes:
    """Invert UE's row differences within independently encoded image tiles.

    Layout verified on 512-square, 1024-square and 1200x662 UEFN source images:
    columns split to <=1024 pixels, balanced and aligned to 16 pixels; each
    column uses floor(area / 32768) balanced row groups. The first row of each
    group is absolute. Pixels stay in BGRA order (the caller converts).
    Pillow adds unsigned bytes modulo 256 in C, keeping memory bounded per row.
    """
    from PIL import Image, ImageChops

    columns = (width + 1023) // 1024
    column_width = (((width + columns - 1) // columns + 15) // 16) * 16
    result = bytearray(data)
    stride = width * 4
    for x in range(0, width, column_width):
        tile_width = min(column_width, width - x)
        groups = max(1, tile_width * height // 32768)
        rows = (height + groups - 1) // groups
        previous = None
        for y in range(height):
            start = y * stride + x * 4
            end = start + tile_width * 4
            row = Image.frombytes("RGBA", (tile_width, 1), data[start:end])
            if y % rows:
                row = ImageChops.add_modulo(row, previous)
                result[start:end] = row.tobytes()
            previous = row
    return bytes(result)


def texture_png(pkg: Package) -> Optional[bytes]:
    """Native-resolution source for supported single-layer, single-mip textures.

    Unsupported layouts deliberately fall back; never label a guessed decode as
    full quality. This reads source pixels, not Unreal material/shader output.
    """
    from PIL import Image

    source = _texture_source(pkg)
    width, height = int(source.get("SizeX", 0)), int(source.get("SizeY", 0))
    if width <= 0 or height <= 0 or width * height > 16 * 1024 * 1024:
        return None
    if (source.get("NumSlices", 1) != 1 or source.get("NumMips", 1) != 1
            or len(source.get("LayerFormat", [])) > 1 or source.get("Blocks")):
        return None
    compression = source.get("CompressionFormat", "TSCF_None").rsplit("::", 1)[-1]
    fmt = source.get("Format", "").rsplit("::", 1)[-1]
    data = first_payload(pkg)
    if not data:
        return None
    if compression in ("TSCF_PNG", "TSCF_JPEG"):
        with Image.open(io.BytesIO(data)) as img:
            if img.size != (width, height) or img.format not in ("PNG", "JPEG"):
                return None
            image = img.convert("RGBA")
    elif fmt == "TSF_BGRA8" and compression in ("TSCF_None", "TSCF_UEDELTA"):
        if len(data) != width * height * 4:
            return None
        if compression == "TSCF_UEDELTA":
            data = _decode_delta_rgba(data, width, height)
        # BGRA in both cases: checked against UEFN's own rendering of these textures
        image = Image.frombytes("RGBA", (width, height), data, "raw", "BGRA")
    else:
        return None
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
