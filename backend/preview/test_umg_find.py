"""A chat name resolves to the project's Widget Blueprint, whatever prefix or suffix it carries."""

from __future__ import annotations

from pathlib import Path

from . import umg_find


def _project(tmp_path: Path, monkeypatch, widgets: list[str], others: list[str] = ()) -> Path:
    content = tmp_path / "Content"
    for rel in list(widgets) + list(others):
        file = content / (rel + ".uasset")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"")
    classes = {(content / (rel + ".uasset")): "WidgetBlueprint" for rel in widgets}
    monkeypatch.setattr("backend.preview.uasset.main_asset_class", lambda p: classes.get(Path(p), "Texture2D"), raising=False)
    import importlib

    uasset = importlib.import_module(umg_find.__package__ + ".uasset")
    monkeypatch.setattr(uasset, "main_asset_class", lambda p: classes.get(Path(p), "Texture2D"))
    return content


def test_spellings_of_one_widget_share_a_key():
    assert umg_find._keys("ShopUi") & umg_find._keys("UW_Shop")
    assert umg_find._keys("WBP_ShopUI") & umg_find._keys("shop")
    assert not umg_find._keys("ShopUi") & umg_find._keys("UW_Inventory")


def test_exact_name_wins_over_a_look_alike(tmp_path, monkeypatch):
    content = _project(tmp_path, monkeypatch, ["UI/ShopUI", "UI/Old/UW_Shop"])
    assert umg_find.find_widget(content, "ShopUI") == content / "UI/ShopUI.uasset"


def test_chat_name_finds_the_prefixed_blueprint_and_skips_textures(tmp_path, monkeypatch):
    content = _project(tmp_path, monkeypatch, ["ShopUI/UW_Shop"], others=["Textures/T_Shop", "ShopUI/shop"])
    assert umg_find.find_widget(content, "ShopUi") == content / "ShopUI/UW_Shop.uasset"


def test_unknown_or_silly_names_find_nothing(tmp_path, monkeypatch):
    content = _project(tmp_path, monkeypatch, ["UI/UW_Shop"])
    assert umg_find.find_widget(content, "Inventory") is None
    assert umg_find.find_widget(content, "") is None
    assert umg_find.find_widget(content, "x" * 200) is None
