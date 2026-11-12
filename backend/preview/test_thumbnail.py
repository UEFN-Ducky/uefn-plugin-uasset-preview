"""Embedded Content Browser thumbnail extraction from .uasset bytes."""

from __future__ import annotations

import struct

from .thumbnail import extract_embedded_thumbnail

_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body" + b"\xff\xd9"
_PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"


def _entry(width: int, height: int, image: bytes) -> bytes:
    return struct.pack("<iii", width, height, len(image)) + image


def test_extracts_jpeg_thumbnail(tmp_path):
    # UE5 marks a JPEG thumbnail with a negative height.
    path = tmp_path / "M_Gold.uasset"
    path.write_bytes(b"header" * 50 + _entry(256, -256, _JPEG) + b"trailer")
    assert extract_embedded_thumbnail(path) == _JPEG


def test_extracts_png_thumbnail(tmp_path):
    path = tmp_path / "M_Gold.uasset"
    path.write_bytes(b"header" * 50 + _entry(256, 256, _PNG) + b"trailer")
    assert extract_embedded_thumbnail(path) == _PNG


def test_ignores_signature_without_thumbnail_header(tmp_path):
    # Image magic bytes inside other data, not preceded by a sane width/height/size.
    path = tmp_path / "T_Raw.uasset"
    path.write_bytes(b"\x00" * 4 + b"\xff\xff\xff\xff" * 3 + _JPEG + b"tail")
    assert extract_embedded_thumbnail(path) is None


def test_no_thumbnail(tmp_path):
    path = tmp_path / "Empty.uasset"
    path.write_bytes(b"no thumbnail here")
    assert extract_embedded_thumbnail(path) is None


def test_missing_file(tmp_path):
    assert extract_embedded_thumbnail(tmp_path / "gone.uasset") is None


def test_jpeg_thumbnail_gets_its_red_and_blue_back(tmp_path):
    import io

    from PIL import Image

    from .thumbnail import embedded_thumbnail_png

    jpeg = io.BytesIO()
    Image.new("RGB", (64, 64), (250, 200, 20)).save(jpeg, "JPEG", quality=95)  # as Unreal stores a blue pixel
    data = jpeg.getvalue()
    asset = tmp_path / "T.uasset"
    asset.write_bytes(bytes(40) + struct.pack("<iii", 64, -64, len(data)) + data + b"tail")
    with Image.open(io.BytesIO(embedded_thumbnail_png(asset))) as img:
        r, g, b, _a = img.convert("RGBA").getpixel((32, 32))
    assert abs(r - 20) < 12 and abs(g - 200) < 12 and abs(b - 250) < 12
