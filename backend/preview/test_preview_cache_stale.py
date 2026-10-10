"""Stale offline fallback for texture/material image cache (no UEFN required)."""

from __future__ import annotations

import pytest

from . import cache as preview_cache
from . import service as preview_service


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    root = tmp_path / "UEFN-Ducky"
    monkeypatch.setattr(preview_cache, "plugin_cache_root", lambda *, for_write=False: root / "cache")
    monkeypatch.setattr(preview_cache, "current_project_cache_slug", lambda: "TestProject_deadbeef")
    yield


def test_latest_pointer_survives_mtime_change():
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        10,
        100,
        b"\x89PNG\r\n\x1a\n",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
        metadata={"size_x": 64, "size_y": 64},
    )
    assert preview_cache.get_cached(rel, 10, 100) is not None
    assert preview_cache.get_cached(rel, 11, 100) is None

    latest = preview_cache.get_latest_cached(rel)
    assert latest is not None
    assert latest.mode == "texture"
    assert latest.asset_class == "Texture2D"
    assert latest.metadata["size_x"] == 64


def test_badge_mode_does_not_clobber_latest_pointer():
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        1,
        1,
        b"real-png",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )
    preview_cache.put_cached(
        rel,
        2,
        1,
        b"badge",
        mode="asset",
        asset_class="",
        asset_path="/Game/Textures/T_Rock",
    )
    latest = preview_cache.get_latest_cached(rel)
    assert latest is not None
    assert latest.mode == "texture"


def test_load_texture_preview_cache_hit_skips_listener(monkeypatch):
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        5,
        20,
        b"\x89PNG",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("listener must not be called on cache hit")

    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 5, "size": 20, "path": path},
    )
    monkeypatch.setattr("backend.bridge.post_command_to_listener", boom)

    out = preview_service.load_texture_preview(rel)
    assert out["ok"] is True
    assert out["from_cache"] is True
    assert out["mode"] == "texture"
    assert called["n"] == 0


def test_load_texture_preview_offline_stale_latest(monkeypatch):
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        5,
        20,
        b"\x89PNG",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 99, "size": 20, "path": path},
    )

    out = preview_service.load_texture_preview(rel)
    assert out["ok"] is True
    assert out["stale"] is True
    assert out["from_cache"] is True
    assert out["listener_online"] is False
    assert out["mode"] == "texture"
    assert out["preview_url"]


def test_preview_project_asset_offline_prefers_stale_over_badge(monkeypatch):
    rel = "Content/Materials/M_Glow.uasset"
    preview_cache.put_cached(
        rel,
        1,
        8,
        b"\x89PNG",
        mode="material",
        asset_class="Material",
        asset_path="/Game/Materials/M_Glow",
    )

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 50, "size": 8, "path": path},
    )

    out = preview_service.preview_project_asset(rel)
    assert out["mode"] == "material"
    assert out["stale"] is True
    assert out["listener_online"] is False
    assert out["preview_url"]


def _fake_bridge(monkeypatch, fake_post):
    import sys
    import types

    bridge = types.ModuleType("backend.bridge")
    bridge.post_command_to_listener = fake_post
    monkeypatch.setitem(sys.modules, "backend.bridge", bridge)


def test_load_texture_preview_trusts_class_over_unhinted_path(monkeypatch, tmp_path):
    # No T_ prefix, no Textures/ folder: only UEFN's class says it is a texture.
    rel = "Content/Lobby/chut.uasset"
    png = tmp_path / "chut.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    def fake_post(port, command, params, timeout=8.0):
        calls.append(command)
        if command == "get_asset_info":
            return {"asset": {"asset_class": "Texture2D"}}
        assert command == "preview_asset"
        return {"preview_file": str(png), "asset_class": "Texture2D"}

    _fake_bridge(monkeypatch, fake_post)
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 3, "size": 8, "path": path},
    )

    out = preview_service.load_texture_preview(rel)
    assert out["ok"] is True, out
    assert out["mode"] == "texture"
    assert out["preview_kind"] == "texture"
    assert "preview_asset" in calls


def test_load_texture_preview_rejects_non_texture_class(monkeypatch):
    def fake_post(port, command, params, timeout=8.0):
        assert command == "get_asset_info", "must not export a non-texture"
        return {"asset": {"asset_class": "SoundWave"}}

    _fake_bridge(monkeypatch, fake_post)
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 3, "size": 8, "path": path},
    )

    out = preview_service.load_texture_preview("Content/Lobby/chut.uasset")
    assert out["ok"] is False
    assert "SoundWave" in out["error"]


def test_load_material_preview_uses_embedded_thumbnail_offline(monkeypatch, tmp_path):
    import struct

    jpeg = b"\xff\xd8\xff\xe0" + b"thumb" + b"\xff\xd9"
    asset = tmp_path / "Gold_Rank_1_Mat.uasset"
    asset.write_bytes(b"pkg" * 40 + struct.pack("<iii", 256, -256, len(jpeg)) + jpeg)

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(preview_service.pf, "resolve_project_file_path", lambda rel: str(asset))
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 6, "size": 9, "path": path},
    )

    out = preview_service.load_material_preview("Content/RankIcons/Gold_Rank_1_Mat.uasset")
    assert out["ok"] is True, out
    assert out["from_embedded_thumbnail"] is True
    assert out["supports_material_preview"] is True
    assert out["preview_url"]
    assert "asset_path" not in out


def test_preview_url_changes_when_image_is_replaced():
    # Badge then real thumbnail land on the same cache key; the panel must not
    # keep showing the badge from the webview's image cache.
    import os

    rel = "Content/RankIcons/Gold_Rank_1_Mat.uasset"
    kw = dict(mode="asset", asset_class="Material", asset_path="/Game/RankIcons/Gold_Rank_1_Mat")
    preview_id = preview_cache.put_cached(rel, 1, 1, b"badge", **kw)
    png = preview_cache.preview_path_for_id(preview_id)
    os.utime(png, ns=(1_000_000_000, 1_000_000_000))
    first = preview_cache.preview_url(preview_id)

    assert preview_cache.put_cached(rel, 1, 1, b"real thumbnail", **{**kw, "mode": "material"}) == preview_id
    assert preview_cache.preview_url(preview_id) != first


def test_preview_project_asset_widget_shows_embedded_thumbnail(monkeypatch, tmp_path):
    import struct

    jpeg = b"\xff\xd8\xff\xe0" + b"panel" + b"\xff\xd9"
    asset = tmp_path / "WBP_AdminPanel.uasset"
    asset.write_bytes(b"pkg" * 40 + struct.pack("<iii", 256, -256, len(jpeg)) + jpeg)
    rel = "Content/AdminPanel/WBP_AdminPanel.uasset"

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(preview_service.pf, "resolve_project_file_path", lambda path: str(asset))
    monkeypatch.setattr(preview_service.pf, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(preview_service, "find_verse_source", lambda *a, **k: None)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 7, "size": 12, "path": path},
    )

    out = preview_service.preview_project_asset(rel)
    assert out["preview_kind"] == "widget"
    assert out["mode"] == "image"
    assert out["from_embedded_thumbnail"] is True
    png = preview_cache.preview_path_for_id(out["preview_id"])
    assert png.read_bytes() != b""
    # Second open is a plain cache hit on the thumbnail, not a badge.
    again = preview_service.preview_project_asset(rel)
    assert again["mode"] == "image"


def test_load_widget_tree(monkeypatch):
    root = "/x/AdminPanel/WBP_TimerUI.WBP_TimerUI:WidgetTree."

    def fake_post(port, command, params, timeout=8.0):
        assert command == "get_widget_blueprint_info"
        assert params == {"widget_path": "/Game/AdminPanel/WBP_TimerUI"}
        return {
            "member_variables": ["Timer"],
            "tree": {"widgets": [{
                "widget": {"refPath": root + "CanvasPanel_9"},
                "parent": None,
                "widgetClassPath": {"refPath": "/Script/UMG.CanvasPanel"},
                "widgetName": "CanvasPanel_9",
                "bIsVariable": False,
            }]},
        }

    _fake_bridge(monkeypatch, fake_post)
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")

    out = preview_service.load_widget_tree("Content/AdminPanel/WBP_TimerUI.uasset")
    assert out["ok"] is True, out
    assert out["variables"] == ["Timer"]
    assert out["widgets"][0]["class"] == "CanvasPanel"


def test_load_widget_tree_offline(monkeypatch):
    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    out = preview_service.load_widget_tree("Content/AdminPanel/WBP_TimerUI.uasset")
    assert out["ok"] is False
    assert "offline" in out["error"]


def test_load_material_preview_prefers_live_capture_when_online(monkeypatch, tmp_path):
    import struct

    live_png = tmp_path / "capture.png"
    live_png.write_bytes(b"\x89PNG\r\n\x1a\nlive")
    jpeg = b"\xff\xd8\xff\xe0" + b"stale" + b"\xff\xd9"
    asset = tmp_path / "Gold_Rank_1_Mat.uasset"
    asset.write_bytes(b"pkg" * 40 + struct.pack("<iii", 256, -256, len(jpeg)) + jpeg)
    polls = {"n": 0}

    def fake_post(port, command, params, timeout=8.0):
        if command == "capture_asset_image_start":
            assert params == {"asset_path": "/Game/RankIcons/Gold_Rank_1_Mat"}
            return {"token": "t1"}
        assert command == "capture_asset_image_poll"
        polls["n"] += 1
        if polls["n"] == 1:
            return {"done": False}
        return {"done": True, "preview_file": str(live_png)}

    _fake_bridge(monkeypatch, fake_post)
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")
    monkeypatch.setattr(preview_service, "_CAPTURE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(preview_service.pf, "resolve_project_file_path", lambda rel: str(asset))
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 6, "size": 9, "path": path},
    )

    out = preview_service.load_material_preview("Content/RankIcons/Gold_Rank_1_Mat.uasset")
    assert out["ok"] is True, out
    assert out["from_live_capture"] is True
    assert preview_cache.preview_path_for_id(out["preview_id"]).read_bytes().endswith(b"live")


def test_load_material_preview_falls_back_to_embedded_when_capture_fails(monkeypatch, tmp_path):
    import struct

    jpeg = b"\xff\xd8\xff\xe0" + b"saved" + b"\xff\xd9"
    asset = tmp_path / "Gold_Rank_1_Mat.uasset"
    asset.write_bytes(b"pkg" * 40 + struct.pack("<iii", 256, -256, len(jpeg)) + jpeg)

    def fake_post(port, command, params, timeout=8.0):
        raise RuntimeError("Asset capture unavailable")

    _fake_bridge(monkeypatch, fake_post)
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")
    monkeypatch.setattr(preview_service.pf, "resolve_project_file_path", lambda rel: str(asset))
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 6, "size": 9, "path": path},
    )

    out = preview_service.load_material_preview("Content/RankIcons/Gold_Rank_1_Mat.uasset")
    assert out["ok"] is True, out
    assert out["from_embedded_thumbnail"] is True
