"""Rewrite preview tests for plugin-local cache helpers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend" / "preview"

for name in (
    "test_mesh_cache.py",
    "test_mesh_preview_service.py",
    "test_preview_cache_stale.py",
):
    p = ROOT / name
    if not p.is_file():
        continue
    t = p.read_text(encoding="utf-8")
    t = t.replace("from frontend.asset_preview import mesh_cache", "from . import mesh_cache")
    t = t.replace("from frontend.asset_preview import cache as preview_cache", "from . import cache as preview_cache")
    t = t.replace("from frontend.asset_preview import service as preview_service", "from . import service as preview_service")
    t = t.replace("from frontend.asset_preview.", "from .")
    # Cache root is now plugin_cache_root, not resolve_app_data_dir
    t = t.replace(
        'monkeypatch.setattr(mesh_cache, "resolve_app_data_dir", lambda *, for_write=False: root)',
        'monkeypatch.setattr(mesh_cache, "plugin_cache_root", lambda *, for_write=False: root / "cache")',
    )
    t = t.replace(
        'monkeypatch.setattr(preview_cache, "resolve_app_data_dir", lambda *, for_write=False: root)',
        'monkeypatch.setattr(preview_cache, "plugin_cache_root", lambda *, for_write=False: root / "cache")',
    )
    p.write_text(t, encoding="utf-8")
    print("fixed", name)
