"""Texture reconstruction preserves dimensions, channel order and alpha."""
import io
import struct

import pytest
from PIL import Image

from . import payload
from .package import Package


def _source(monkeypatch, data, width, height, compression="TSCF_UEDELTA", **extra):
    pkg = Package(data=b"", ue5_version=1018, names=[])
    monkeypatch.setattr(payload, "first_payload", lambda _: data)
    # Metadata is supplied independently of package serialization in these tests.
    monkeypatch.setattr(payload, "_texture_source", lambda _: dict(
        SizeX=width, SizeY=height, Format="TSF_BGRA8", CompressionFormat=compression,
        NumMips=1, NumSlices=1, **extra), raising=False)
    return pkg


@pytest.mark.parametrize("width,height,tiles", [
    (512, 512, [(0, 512, 64)]),
    (1024, 1024, [(0, 1024, 32)]),
    (1200, 662, [(0, 608, 56), (608, 1200, 61)]),
])
def test_delta_tile_boundaries_and_bgra(monkeypatch, width, height, tiles):
    # Tile boundaries observed in independently authored UEFN textures. Each
    # subsequent row is a difference; restarting at the wrong row corrupts it.
    raw = bytearray(width * height * 4)
    expected = bytearray(len(raw))
    for x0, x1, rows in tiles:
        for y in range(height):
            pixel = bytes(((y * 3) % 256, (y * 7) % 256, (y * 11) % 256, (y * 13) % 256))
            delta = pixel if y % rows == 0 else bytes([3, 7, 11, 13])
            start, end = (y * width + x0) * 4, (y * width + x1) * 4
            raw[start:end] = delta * (x1 - x0)
            # stored blue-green-red-alpha, shown red-green-blue-alpha
            expected[start:end] = bytes((pixel[2], pixel[1], pixel[0], pixel[3])) * (x1 - x0)
    png = payload.texture_png(_source(monkeypatch, bytes(raw), width, height))
    assert png is not None
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    assert img.size == (width, height)
    assert img.tobytes() == bytes(expected)


def test_uncompressed_bgra_is_not_delta_rgba(monkeypatch):
    png = payload.texture_png(_source(monkeypatch, bytes([1, 2, 3, 127]), 1, 1, "TSCF_None"))
    assert png is not None
    assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == (3, 2, 1, 127)


@pytest.mark.parametrize("data,w,h,extra", [
    (b"\0" * 3, 1, 1, {}), (b"\0" * 8, 1, 1, {}),
    (b"", 100000, 100000, {}), (b"\0" * 4, 1, 1, {"LayerFormat": ["TSF_BGRA8", "TSF_G8"]}),
])
def test_unsupported_or_incomplete_source_falls_back(monkeypatch, data, w, h, extra):
    assert payload.texture_png(_source(monkeypatch, data, w, h, **extra)) is None


def test_compressed_buffer_rejects_truncation_before_native_decoder(monkeypatch):
    calls = []
    monkeypatch.setattr(payload, "_oodle", lambda: lambda data, size: calls.append(size))
    header = struct.pack(">IIBBBBIQQ", 0xB7756362, 0, 3, 3, 3, 18, 1, 1024, 72).ljust(64, b"\0")
    assert payload._decompress(header + struct.pack(">I", 4) + b"x", 0) is None
    assert not calls
