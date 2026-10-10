"""Summarize the listener's get_widget_blueprint_info payload for the panel."""

from __future__ import annotations

from .widget_tree import summarize_widget_tree

_ROOT = "/x/AdminPanel/WBP_TimerUI.WBP_TimerUI:WidgetTree."


def _w(name, cls, parent=None, var=False):
    return {
        "widget": {"refPath": _ROOT + name},
        "parent": {"refPath": _ROOT + parent} if parent else None,
        "widgetClassPath": {"refPath": cls},
        "widgetName": name,
        "bIsVariable": var,
    }


def test_summarize_widget_tree():
    info = {
        "member_variables": ["AbilityName", "VerseFieldInternalVariable_Btn", "Timer"],
        "tree": {
            "widgets": [
                _w("CanvasPanel_9", "/Script/UMG.CanvasPanel"),
                _w("Overlay_0", "/Script/UMG.Overlay", "CanvasPanel_9"),
                _w("Image_0", "/Script/UMG.Image", "Overlay_0", var=True),
                _w("Label", "/Game/Valkyrie/UMG/UEFN_TextBlock.UEFN_TextBlock_C", "Overlay_0"),
            ]
        },
    }
    out = summarize_widget_tree(info)
    assert out["variables"] == ["AbilityName", "Timer"]
    assert out["widgets"] == [
        {"name": "CanvasPanel_9", "class": "CanvasPanel", "depth": 0, "is_variable": False},
        {"name": "Overlay_0", "class": "Overlay", "depth": 1, "is_variable": False},
        {"name": "Image_0", "class": "Image", "depth": 2, "is_variable": True},
        {"name": "Label", "class": "UEFN_TextBlock", "depth": 2, "is_variable": False},
    ]


def test_summarize_empty_tree():
    assert summarize_widget_tree({}) == {"widgets": [], "variables": []}
