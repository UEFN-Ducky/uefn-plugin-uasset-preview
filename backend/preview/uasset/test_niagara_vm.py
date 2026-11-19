"""Niagara compiled data: data set layouts mixing float and int components."""

from __future__ import annotations

from .niagara_vm import _components, _layout


def test_types_split_into_float_and_int_components():
    assert _components("NiagaraPosition") == (3, 0)
    assert _components("LinearColor") == (4, 0)
    assert _components("NiagaraInt32") == (0, 1)
    assert _components("ENiagaraExecutionState") == (0, 1)  # enums are int32
    assert _components("NiagaraSpawnInfo") == (2, 2)  # int Count, float start, float interval, int group


def test_layout_counts_floats_and_ints_separately():
    layout = _layout([("Age", "NiagaraFloat", b""), ("UniqueID", "NiagaraInt32", b""),
                      ("Info", "NiagaraSpawnInfo", b""), ("Position", "NiagaraPosition", b"")])
    by = {e["name"]: e for e in layout}
    assert (by["Age"]["f"], by["UniqueID"]["i"]) == (0, 0)
    assert (by["Info"]["f"], by["Info"]["nf"], by["Info"]["i"], by["Info"]["ni"]) == (1, 2, 1, 2)
    assert by["Position"]["offset"] == 3 and by["Position"]["size"] == 3 and by["Position"]["int"] is False
    assert "offset" not in by["Info"]  # mixed: only the split offsets
