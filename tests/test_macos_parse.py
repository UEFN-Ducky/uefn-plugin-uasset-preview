"""A Mac reads .uasset thumbnails, source images and asset paths locally: no PC, no Win32.

The parsers are loaded by file, so this runs without the app host (plain ``py -3 -m pytest tests``).
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

PREVIEW = Path(__file__).resolve().parents[1] / "backend" / "preview"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"uasset_preview_{name}", PREVIEW / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mac(tmp_path, monkeypatch):  # tmp_path first: pytest's own temp folder reads sys.platform
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)  # any Win32 call would now fail
    monkeypatch.delattr(ctypes, "windll", raising=False)


def test_mac_parses_a_fixture_uasset(mac, tmp_path):
    thumbnail, paths = _load("thumbnail"), _load("paths")
    png = b"\x89PNG\r\n\x1a\n" + b"rock-pixels"
    source = tmp_path / "src" / "rock.png"
    source.parent.mkdir()
    source.write_bytes(b"source-image")
    import_data = json.dumps([{"RelativeFilename": "../src/rock.png",
                               "FileMD5": hashlib.md5(b"source-image").hexdigest()}], separators=(", ", " : "))
    asset = tmp_path / "Content" / "SM_Rock.uasset"
    asset.parent.mkdir()
    asset.write_bytes(b"\xc1\x83\x2a\x9e" + b"\0" * 64 + import_data.replace("[{", "[{ ").encode()
                      + b"\0" * 8 + struct.pack("<iii", 64, 64, len(png)) + png + b"\0" * 4)

    assert thumbnail.extract_embedded_thumbnail(asset) == png
    assert thumbnail.extract_source_image(asset) == b"source-image"
    assert paths.is_previewable_binary("Content/SM_Rock.uasset")
    assert paths.disk_path_to_asset_path("Content/Meshes/SM_Rock.uasset", "/MyIsland/") == "/MyIsland/Meshes/SM_Rock"


def test_mac_ignores_a_pc_drive_path_it_cannot_reach(mac, tmp_path):
    thumbnail = _load("thumbnail")
    import_data = '[{ "RelativeFilename" : "C:/Users/pc/rock.png", "FileMD5" : "00"}]'
    asset = tmp_path / "T_Rock.uasset"
    asset.write_bytes(b"\0" * 16 + import_data.encode() + b"\0" * 16)
    assert thumbnail.extract_source_image(asset) is None
