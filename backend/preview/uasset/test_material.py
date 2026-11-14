"""Material wires decode from their native layout; instances keep the nearest override."""

from __future__ import annotations

import struct

from .package import Package
from .props import TypeName, _read_value
from .reader import Reader


def _pkg() -> Package:
    return Package(data=b"", ue5_version=1018, names=["None"])


def _wire(node: int, output: int, mask: tuple[int, int, int, int, int]) -> bytes:
    return struct.pack("<ii", node, output) + struct.pack("<ii", 0, 0) + struct.pack("<5i", *mask)


def _struct(name: str) -> TypeName:
    return TypeName("StructProperty", (TypeName(name),))


def test_expression_input_wire_and_mask():
    value = _read_value(_pkg(), Reader(_wire(41, 2, (1, 1, 1, 0, 0))), _struct("ExpressionInput"), in_container=True)
    assert value == {"node": 41, "output": 2, "mask": "rg"}


def test_unmasked_wire_has_no_mask():
    value = _read_value(_pkg(), Reader(_wire(7, 0, (0, 0, 0, 0, 0))), _struct("ExpressionInput"), in_container=True)
    assert value == {"node": 7, "output": 0, "mask": ""}


def test_scalar_and_colour_pins_carry_their_constant():
    scalar = _wire(0, 0, (0, 0, 0, 0, 0)) + struct.pack("<if", 1, 0.25)
    value = _read_value(_pkg(), Reader(scalar), _struct("ScalarMaterialInput"), in_container=True)
    assert value["use_constant"] is True and value["constant"] == 0.25 and value["node"] == 0
    colour = _wire(44, 0, (1, 1, 1, 1, 0)) + struct.pack("<i4f", 0, 1.0, 0.5, 0.25, 1.0)
    value = _read_value(_pkg(), Reader(colour), _struct("ColorMaterialInput"), in_container=True)
    assert value["mask"] == "rgb" and value["constant"] == [1.0, 0.5, 0.25, 1.0] and value["use_constant"] is False
