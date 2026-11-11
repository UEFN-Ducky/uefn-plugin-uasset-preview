"""Offline .uasset decoding: readers, tagged values, spec helpers.

Set ``DUCKY_TEST_WIDGET`` to a Widget Blueprint .uasset to also run the end-to-end
check against a real file (none is shipped: project assets belong to their authors).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from .package import Package, load_package
from .props import TypeName, _curve_channel, _read_type_name, _read_value
from .reader import ReadError, Reader
from .umg_spec import _channel, _color, _enum, _margin, build_umg_spec, project_resolver


def _pkg(names: list[str]) -> Package:
    return Package(data=b"", ue5_version=1018, names=names)


def _name(index: int, number: int = 0) -> bytes:
    return struct.pack("<ii", index, number)


def test_fstring_ansi_and_utf16():
    data = struct.pack("<i", 4) + b"abc\x00" + struct.pack("<i", -3) + "hé\x00".encode("utf-16-le")
    r = Reader(data)
    assert r.fstring() == "abc"
    assert r.fstring() == "hé"


def test_reader_refuses_to_run_past_the_file():
    with pytest.raises(ReadError):
        Reader(b"\x01\x02").i32()


def test_load_package_rejects_non_packages():
    with pytest.raises(ReadError):
        load_package(b"not a package" * 10)


def test_name_with_instance_number():
    pkg = _pkg(["Image"])
    assert pkg.read_name(Reader(_name(0))) == "Image"
    assert pkg.read_name(Reader(_name(0, 4))) == "Image_3"


def test_type_name_tree():
    pkg = _pkg(["MapProperty", "StrProperty", "StructProperty", "Margin"])
    data = _name(0) + struct.pack("<i", 2) + _name(1) + struct.pack("<i", 0)
    data += _name(2) + struct.pack("<i", 1) + _name(3) + struct.pack("<i", 0)
    tn = _read_type_name(pkg, Reader(data))
    assert str(tn) == "MapProperty(StrProperty,StructProperty(Margin))"


def test_native_struct_values():
    pkg = _pkg([])
    vec = TypeName("StructProperty", (TypeName("Vector2D"),))
    assert _read_value(pkg, Reader(struct.pack("<dd", 0.5, 1.5)), vec, in_container=True) == [0.5, 1.5]
    color = TypeName("StructProperty", (TypeName("Color"),))
    assert _read_value(pkg, Reader(bytes([3, 2, 1, 255])), color, in_container=True) == [1, 2, 3, 255]


def test_curve_channel_keys_and_default():
    key = struct.pack("<fffff", 2.0, 0.25, 0.5, 0.0, 0.0) + bytes([0, 0, 0, 0]) + bytes([2, 0, 0, 0])
    data = bytes([4, 4]) + struct.pack("<ii", 4, 1) + struct.pack("<i", 30000)
    data += struct.pack("<ii", 28, 1) + key + struct.pack("<fiii", 7.0, 1, 60000, 1) + struct.pack("<i", 0)
    channel = _curve_channel(_pkg([]), Reader(data))
    assert channel["default"] == 7.0 and channel["tick_rate"] == 60000
    assert channel["keys"] == [
        {"t": 30000, "v": 2.0, "arrive": 0.25, "leave": 0.5, "arrive_w": 0.0, "leave_w": 0.0, "interp": 2}
    ]
    # seconds for the engine, tangents rescaled from per-tick to per-second
    assert _channel(channel)["keys"] == [[0.5, 2.0, 2, 0.25 * 60000, 0.5 * 60000]]


def test_spec_value_helpers():
    assert _enum("ESlateBrushDrawType::RoundedBox") == "RoundedBox"
    assert _enum("HAlign_Center") == "Center"
    assert _enum(None, "Fill") == "Fill"
    # absent fields keep the slot's default (tags only store what differs)
    assert _margin({"Right": 300.0, "Bottom": 100.0}, [0, 0, 100, 30]) == [0, 0, 300.0, 100.0]
    assert _color({"SpecifiedColor": [0.0, 0.5, 1.0, 1.0]}) == [0.0, 0.5, 1.0, 1.0]
    assert _color(None, [0, 0, 0, 0]) == [0, 0, 0, 0]


def test_project_resolver_ignores_the_mount_name(tmp_path: Path):
    asset = tmp_path / "UI" / "T_Icon.uasset"
    asset.parent.mkdir()
    asset.write_bytes(b"")
    resolve = project_resolver(tmp_path)
    assert resolve("/MyProject/UI/T_Icon.T_Icon") == asset
    assert resolve("/OldProjectName/UI/T_Icon.T_Icon") == asset  # migrated asset, stale mount
    assert resolve("/Game/Valkyrie/UMG/UEFN_TextBlock.UEFN_TextBlock_C") is None
    assert resolve("/MyProject/UI/Missing.Missing") is None


@pytest.mark.skipif(not os.environ.get("DUCKY_TEST_WIDGET"), reason="set DUCKY_TEST_WIDGET to a widget .uasset")
def test_real_widget_blueprint_builds_a_spec():
    file = Path(os.environ["DUCKY_TEST_WIDGET"])
    content = next(p for p in file.parents if p.name == "Content")
    spec = build_umg_spec(file, project_resolver(content))
    assert spec["root"] and spec["root"]["kind"]
    assert spec["name"] == file.stem
