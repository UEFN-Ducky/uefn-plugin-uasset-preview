"""Edits are written into a Widget Blueprint, checked, backed up and restorable.

The real-file tests need ``DUCKY_TEST_WIDGET`` (a Widget Blueprint .uasset with at
least one Image); no project asset is shipped with the plugin. They only ever write
to a copy in a temporary folder.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

import pytest

from . import umg_edit
from .uasset import load_package
from .uasset.props import export_properties
from .uasset.writer import EditError, _fstring, _text_value, apply_edits

_WIDGET = os.environ.get("DUCKY_TEST_WIDGET", "")
needs_widget = pytest.mark.skipif(not _WIDGET, reason="set DUCKY_TEST_WIDGET to a widget .uasset")


def test_fstring_ascii_utf16_and_empty():
    assert _fstring("") == struct.pack("<i", 0)
    assert _fstring("ab") == struct.pack("<i", 3) + b"ab\x00"
    assert _fstring("é") == struct.pack("<i", -2) + "é\x00".encode("utf-16-le")


def test_text_keeps_its_localization_key():
    old = struct.pack("<Ib", 0, 0) + _fstring("") + _fstring("KEY123") + _fstring("Old")
    new = _text_value(old, "New text")
    assert new == struct.pack("<Ib", 0, 0) + _fstring("") + _fstring("KEY123") + _fstring("New text")


def test_generated_text_is_refused():
    with pytest.raises(EditError):
        _text_value(struct.pack("<Ib", 0, 2) + b"\x00" * 12, "x")


def test_new_text_is_laid_out_like_uefn_and_keyed_by_its_widget():
    new = _text_value(None, "Hello", "pkg/widget")
    # flags, history 0, empty namespace stored as a lone terminator, 32-hex key, source
    assert new[:10] == struct.pack("<Ib", 0, 0) + struct.pack("<i", 1) + bytes(1)
    assert new[10:14] == struct.pack("<i", 33) and new.endswith(_fstring("Hello") + struct.pack("<i", 0))
    assert _text_value(None, "Hello", "pkg/widget") == new  # both copies of a widget get the same key
    assert _text_value(None, "Hello", "pkg/other") != new


def _first(pkg, cls):
    trees = {e.index for e in pkg.exports if pkg.class_name(e) == "WidgetTree"}
    return next(e for e in pkg.exports if pkg.class_name(e) == cls and e.outer_index in trees)


@pytest.fixture()
def widget(tmp_path: Path, monkeypatch) -> Path:
    copy = tmp_path / Path(_WIDGET).name
    shutil.copy2(_WIDGET, copy)
    monkeypatch.setattr(umg_edit, "cache_root", lambda for_write=False: tmp_path / "cache")
    return copy


@needs_widget
def test_edit_is_written_to_every_copy_and_nothing_else_changes(widget: Path):
    original = widget.read_bytes()
    name = _first(load_package(original), "Image").object_name
    edits = [
        {"widget": name, "kind": "Image", "field": "opacity", "value": 0.25},
        {"widget": name, "kind": "Image", "field": "tint", "value": [0.1, 0.2, 0.3, 1.0]},
        {"widget": name, "kind": "Image", "field": "translation", "value": [7.0, -3.0]},
    ]
    result = umg_edit.save_edits(widget, edits)
    assert result["ok"], result
    after = load_package(widget.read_bytes())
    copies = [e for e in after.exports if e.object_name == name and after.class_name(e) == "Image"]
    assert len(copies) >= 1
    for exp in copies:
        props = export_properties(after, exp)
        assert props["RenderOpacity"] == 0.25
        assert props["RenderTransform"]["Translation"] == [7.0, -3.0]
        assert [round(c, 4) for c in props["Brush"]["TintColor"]["SpecifiedColor"]] == [0.1, 0.2, 0.3, 1.0]
    # saving the same values again changes nothing
    assert umg_edit.save_edits(widget, edits).get("unchanged") is True
    # and the backup puts the original bytes back
    assert umg_edit.restore_last(widget)["ok"]
    assert widget.read_bytes() == original


@needs_widget
def test_refused_edit_leaves_the_file_alone(widget: Path):
    original = widget.read_bytes()
    result = umg_edit.save_edits(widget, [{"widget": "NoSuchWidget", "kind": "Image", "field": "opacity", "value": 1}])
    assert result["ok"] is False and "NoSuchWidget" in result["error"]
    assert widget.read_bytes() == original
    assert not list((widget.parent / "cache").rglob("*.uasset"))


@needs_widget
def test_check_rewrite_catches_a_damaged_result(widget: Path):
    original = widget.read_bytes()
    name = _first(load_package(original), "Image").object_name
    good = apply_edits(original, [{"widget": name, "kind": "Image", "field": "opacity", "value": 0.5}])
    umg_edit.check_rewrite(original, good, {name})
    with pytest.raises(EditError):  # an object that was not edited must not differ
        umg_edit.check_rewrite(original, good, set())
    with pytest.raises(EditError):  # the data after the objects must survive
        umg_edit.check_rewrite(original, good[:-8] + b"\x00" * 8, {name})


@needs_widget
def test_an_image_the_widget_never_used_is_added_and_survives_the_check(widget: Path, tmp_path: Path):
    source = Path(_WIDGET)
    content = next(p for p in source.parents if p.name == "Content")
    original = widget.read_bytes()
    pkg = load_package(original)
    used = {pkg.object_path(-(i + 1)) for i in range(len(pkg.imports))}
    mount = pkg.package_name.strip("/").split("/")[0]
    candidates = [(f"/{mount}/{t.relative_to(content).with_suffix('').as_posix()}.{t.stem}", t) for t in content.rglob("*.uasset")]
    from .uasset import main_asset_class

    path, target = next((p, t) for p, t in candidates if p not in used and main_asset_class(t) == "Texture2D")
    name = _first(pkg, "Image").object_name
    edit = {"widget": name, "kind": "Image", "field": "resource", "value": path, "asset_class": "Texture2D"}
    new = apply_edits(original, [edit])
    umg_edit.check_rewrite(original, new, {name})
    after = load_package(new)
    assert len(after.imports) == len(pkg.imports) + 2
    # the asset registry's per-import bits follow the import table (UEFN rejects the file otherwise)
    registry = after.fields["asset_registry_data_offset"][1]
    (dependency,) = struct.unpack_from("<q", new, registry)
    (bits,) = struct.unpack_from("<i", new, dependency)
    assert bits == len(after.imports)
    (last_word,) = struct.unpack_from("<I", new, dependency + 4 + 4 * ((bits - 1) // 32))
    assert last_word >> ((bits - 1) % 32) & 1
    for exp in after.exports:
        if exp.object_name == name and after.class_name(exp) == "Image":
            ref = export_properties(after, exp)["Brush"]["ResourceObject"]
            assert after.object_path(ref.index) == path
    assert apply_edits(new, [edit]) == new  # already referenced: nothing more to add
    with pytest.raises(EditError):  # only images can be put on a widget
        apply_edits(original, [{**edit, "asset_class": "SoundWave"}])


@needs_widget
def test_a_file_that_cannot_be_replaced_is_left_alone_without_a_stray_backup(widget: Path, monkeypatch):
    original = widget.read_bytes()
    name = _first(load_package(original), "Image").object_name

    def locked(*_args):
        raise PermissionError(5, "Access denied")  # what Windows says while UEFN holds the file

    monkeypatch.setattr(umg_edit.os, "replace", locked)
    result = umg_edit.save_edits(widget, [{"widget": name, "kind": "Image", "field": "opacity", "value": 0.3}])
    assert result["ok"] is False and "Could not write" in result["error"]
    assert widget.read_bytes() == original
    assert not list(widget.parent.glob("*.ducky-tmp"))
    assert not list((widget.parent / "cache").rglob("*.uasset"))
