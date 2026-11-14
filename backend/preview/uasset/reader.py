"""Little-endian cursor over the bytes of a .uasset."""

from __future__ import annotations

import struct


class ReadError(ValueError):
    """The bytes do not look like what the caller expected."""


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def _take(self, fmt: str, size: int):
        if self.pos < 0 or self.pos + size > len(self.data):
            raise ReadError(f"read of {size} bytes at {self.pos} runs past the file")
        (value,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def u8(self) -> int:
        return self._take("<B", 1)

    def i8(self) -> int:
        return self._take("<b", 1)

    def u16(self) -> int:
        return self._take("<H", 2)

    def i16(self) -> int:
        return self._take("<h", 2)

    def i32(self) -> int:
        return self._take("<i", 4)

    def u32(self) -> int:
        return self._take("<I", 4)

    def i64(self) -> int:
        return self._take("<q", 8)

    def u64(self) -> int:
        return self._take("<Q", 8)

    def f32(self) -> float:
        return self._take("<f", 4)

    def f64(self) -> float:
        return self._take("<d", 8)

    def raw(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise ReadError(f"read of {size} bytes at {self.pos} runs past the file")
        out = self.data[self.pos : self.pos + size]
        self.pos += size
        return out

    def guid(self) -> str:
        return self.raw(16).hex()

    def fstring(self) -> str:
        """FString: int32 length (negative = UTF-16), then the text with its terminator."""
        length = self.i32()
        if length == 0:
            return ""
        if length < 0:
            count = -length
            if count > 1_000_000:
                raise ReadError(f"implausible string length {length} at {self.pos - 4}")
            return self.raw(count * 2).decode("utf-16-le", "replace").rstrip("\x00")
        if length > 1_000_000:
            raise ReadError(f"implausible string length {length} at {self.pos - 4}")
        return self.raw(length).decode("utf-8", "replace").rstrip("\x00")
