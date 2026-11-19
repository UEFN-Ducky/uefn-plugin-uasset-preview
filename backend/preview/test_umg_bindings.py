"""View Bindings are read from a Widget Blueprint as UEFN lists them.

Needs ``DUCKY_TEST_WIDGET`` (a Widget Blueprint .uasset with View Bindings); no project
asset is shipped with the plugin.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .uasset import load_package
from .uasset.umg_bindings import view_bindings

_WIDGET = os.environ.get("DUCKY_TEST_WIDGET", "")
needs_widget = pytest.mark.skipif(not _WIDGET, reason="set DUCKY_TEST_WIDGET to a widget .uasset")


@needs_widget
def test_every_binding_names_where_it_reads_and_what_it_drives():
    pkg = load_package(Path(_WIDGET).read_bytes())
    found = view_bindings(pkg)
    assert set(found) == {"values", "events", "conditions"}
    assert found["values"] or found["events"], "the test widget should have View Bindings"
    for value in found["values"]:
        assert value["target"] and value["source"] and value["mode"]
        assert value["widget"] and value["target"].startswith(value["widget"] + ".")
    for event in found["events"]:
        assert event["event"] and event["function"]
        assert event["widget"] and event["event"].startswith(event["widget"] + ".")
        for arg in event["args"]:
            assert set(arg) == {"name", "value", "bound"}


def test_a_widget_without_a_view_has_no_bindings():
    class NoView:
        exports: list = []

    assert view_bindings(NoView()) == {"values": [], "events": [], "conditions": []}  # type: ignore[arg-type]
