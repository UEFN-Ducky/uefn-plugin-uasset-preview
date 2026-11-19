"""Niagara preview: parameter values and the emitter summary."""

from __future__ import annotations

import struct

from .niagara_preview import _particle_color, _summary, _value


def test_values_read_as_floats_ints_and_vectors():
    assert _value(struct.pack("<f", 22.0)) == 22.0
    assert _value(struct.pack("<f", 0.0)) == 0.0
    assert _value(struct.pack("<i", 2)) == 2  # an enum or a count: not a tiny denormal float
    assert _value(struct.pack("<3f", 0.0, 0.0, 1.0)) == [0.0, 0.0, 1.0]
    assert _value(struct.pack("<4f", 5.0, 2.2, 0.125, 1.0)) == [5.0, 2.2, 0.125, 1.0]
    assert _value(b"\0" * 20) is None


def test_summary_lists_known_inputs_and_calls_a_huge_loop_endless():
    lines = dict(_summary({"SpawnRate.SpawnRate": 22.0, "InitializeParticle.Lifetime": 4.0,
                           "EmitterState.Loop Duration": 1000000.0, "Unknown.Input": 3.0}))
    assert lines == {"Spawn rate": "22/s", "Lifetime": "4 s", "Loop duration": "endless (1e+06 s)"}


def test_particle_color_only_when_set():
    assert _particle_color({"InitializeParticle.Color": [5.0, 2.2, 0.125, 1.0]}) == [5.0, 2.2, 0.125, 1.0]
    assert _particle_color({}) is None
