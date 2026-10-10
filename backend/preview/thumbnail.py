"""Read the Content Browser thumbnail UEFN embeds in a saved .uasset.

Each thumbnail-table entry is ``int32 width, int32 height, int32 size`` followed by
``size`` bytes of image data (JPEG when height < 0, else PNG). Rather than parse the
version-dependent package summary, find the image magic and validate that header.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
_HEADER = struct.Struct("<iii")
_MAX_DIM = 4096


def extract_embedded_thumbnail(path: Path | str) -> Optional[bytes]:
    """Return the embedded JPEG/PNG thumbnail bytes, or None if the asset has none."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    for sig in _SIGNATURES:
        start = data.find(sig)
        while start != -1:
            if start >= _HEADER.size:
                width, height, size = _HEADER.unpack_from(data, start - _HEADER.size)
                if (
                    0 < width <= _MAX_DIM
                    and 0 < abs(height) <= _MAX_DIM
                    and 0 < size <= len(data) - start
                ):
                    return data[start : start + size]
            start = data.find(sig, start + 1)
    return None
