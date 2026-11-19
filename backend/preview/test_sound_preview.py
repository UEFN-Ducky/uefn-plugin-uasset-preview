"""Sound preview: WAV header reading, relative asset lookup."""

from __future__ import annotations

import struct
from pathlib import Path

from .sound_preview import wav_info
from .uasset.resolve import relative_to_package


def _wav(samples: list[int], rate: int = 48000) -> bytes:
    data = struct.pack(f"<{len(samples)}h", *samples)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data


def test_wav_info_reads_the_header():
    assert wav_info(_wav([0] * 4800)) == {"channels": 1, "sample_rate": 48000, "bits": 16, "duration": 0.1}
    assert wav_info(b"not a wav") == {}


def test_reference_found_next_to_a_copied_package(tmp_path: Path):
    cue = tmp_path / "Content" / "Copied" / "EvePort" / "Sounds" / "SC_Cue.uasset"
    wave = tmp_path / "Content" / "Copied" / "EvePort" / "Sounds" / "Wav" / "W_01.uasset"
    wave.parent.mkdir(parents=True)
    cue.touch(); wave.touch()
    found = relative_to_package(cue, "/Athena/EvePort/Sounds/SC_Cue", "/Athena/EvePort/Sounds/Wav/W_01.W_01")
    assert found == wave
    assert relative_to_package(cue, "/Athena/EvePort/Sounds/SC_Cue", "/Other/Wav/W_01.W_01") is None
