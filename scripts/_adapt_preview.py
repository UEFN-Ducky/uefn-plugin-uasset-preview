"""One-shot: rewrite copied frontend.asset_preview modules for plugin layout."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend" / "preview"


def adapt_cache() -> None:
    p = ROOT / "cache.py"
    t = p.read_text(encoding="utf-8")
    for old, new in [
        (
            "from frontend.app_paths import resolve_app_data_dir\n"
            "from frontend.asset_preview.project_scope import current_project_cache_slug\n"
            "from frontend.settings import PANEL_LISTENER_PORT\n"
            "\n"
            "PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1",
            "from .paths_plugin import cache_root as plugin_cache_root, png_cache_url\n"
            "from .project_scope import current_project_cache_slug",
        ),
        (
            "from .paths_plugin import cache_root as _plugin_cache_root\n"
            "from frontend.app_paths import resolve_app_data_dir\n"
            "from .project_scope import current_project_cache_slug\n"
            "from frontend.settings import PANEL_LISTENER_PORT\n"
            "\n"
            "PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1",
            "from .paths_plugin import cache_root as plugin_cache_root, png_cache_url\n"
            "from .project_scope import current_project_cache_slug",
        ),
        (
            'path = resolve_app_data_dir(for_write=for_write) / "asset_previews" / "projects" / slug',
            'path = plugin_cache_root(for_write=for_write) / "png" / "projects" / slug',
        ),
        (
            '    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/asset-previews/{preview_id}.png"',
            "    return png_cache_url(preview_id)",
        ),
    ]:
        t = t.replace(old, new)
    t = t.replace(
        "from frontend.asset_preview.project_scope import current_project_cache_slug",
        "from .project_scope import current_project_cache_slug",
    )
    # Drop legacy AppData fallbacks
    t = re.sub(
        r"\n    if path\.is_file\(\):\n        return path\n"
        r"    # Legacy flat cache \(pre per-project\) — read-only fallback\.\n"
        r"    legacy = resolve_app_data_dir\(\) / \"asset_previews\" / f\"\{preview_id\}\.png\"\n"
        r"    if legacy\.is_file\(\):\n        return legacy\n    return path",
        "\n    return path",
        t,
    )
    t = re.sub(
        r"def meta_path_for_id\(preview_id: str, \*, project_slug: str \| None = None\) -> Path:\n"
        r"    root = cache_dir\(project_slug=project_slug\)\n"
        r"    path = root / f\"\{preview_id\}\.json\"\n"
        r"    if path\.is_file\(\):\n        return path\n"
        r"    legacy = resolve_app_data_dir\(\) / \"asset_previews\" / f\"\{preview_id\}\.json\"\n"
        r"    if legacy\.is_file\(\):\n        return legacy\n    return path",
        'def meta_path_for_id(preview_id: str, *, project_slug: str | None = None) -> Path:\n'
        '    return cache_dir(project_slug=project_slug) / f"{preview_id}.json"',
        t,
    )
    p.write_text(t, encoding="utf-8")
    print("cache.py", "resolve_app_data_dir" in t, "png_cache_url" in t)


def adapt_mesh() -> None:
    p = ROOT / "mesh_cache.py"
    t = p.read_text(encoding="utf-8")
    for old, new in [
        (
            "from frontend.app_paths import resolve_app_data_dir\n"
            "from frontend.asset_preview.project_scope import current_project_cache_slug\n"
            "from frontend.settings import PANEL_LISTENER_PORT\n"
            "\n"
            "PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1",
            "from .paths_plugin import cache_root as plugin_cache_root, mesh_cache_base_url, mesh_cache_url\n"
            "from .project_scope import current_project_cache_slug",
        ),
        (
            "from .paths_plugin import cache_root as _plugin_cache_root\n"
            "from frontend.app_paths import resolve_app_data_dir\n"
            "from .project_scope import current_project_cache_slug\n"
            "from frontend.settings import PANEL_LISTENER_PORT\n"
            "\n"
            "PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1",
            "from .paths_plugin import cache_root as plugin_cache_root, mesh_cache_base_url, mesh_cache_url\n"
            "from .project_scope import current_project_cache_slug",
        ),
        (
            'path = resolve_app_data_dir(for_write=for_write) / "mesh_previews" / "projects" / slug',
            'path = plugin_cache_root(for_write=for_write) / "mesh" / "projects" / slug',
        ),
    ]:
        t = t.replace(old, new)
    t = t.replace(
        "from frontend.asset_preview.project_scope import current_project_cache_slug",
        "from .project_scope import current_project_cache_slug",
    )
    t = re.sub(
        r"def mesh_preview_url\(cache_id: str, filename: str = _MODEL_FILENAME\) -> str:\n"
        r"    safe_name = Path\(filename\)\.name\n"
        r'    return f"http://127\.0\.0\.1:\{PANEL_UI_HTTP_PORT\}/mesh-previews/\{cache_id\}/\{safe_name\}"\n'
        r"\n"
        r"def mesh_preview_base_url\(cache_id: str\) -> str:\n"
        r'    return f"http://127\.0\.0\.1:\{PANEL_UI_HTTP_PORT\}/mesh-previews/\{cache_id\}/"',
        "def mesh_preview_url(cache_id: str, filename: str = _MODEL_FILENAME) -> str:\n"
        "    return mesh_cache_url(cache_id, Path(filename).name)\n"
        "\n"
        "def mesh_preview_base_url(cache_id: str) -> str:\n"
        "    return mesh_cache_base_url(cache_id)",
        t,
    )
    # Drop legacy mesh cache fallback
    t = re.sub(
        r"    if target\.is_file\(\):\n        return target\n"
        r"    # Legacy flat mesh cache \(pre per-project\)\.\n"
        r"    legacy_root = \(resolve_app_data_dir\(\) / \"mesh_previews\" / cache_id\)\.resolve\(\)\n"
        r"    legacy = \(legacy_root / name\)\.resolve\(\)\n"
        r"    try:\n"
        r"        legacy\.relative_to\(legacy_root\)\n"
        r"    except ValueError as exc:\n"
        r'        raise ValueError\("Path escapes mesh cache"\) from exc\n'
        r"    if legacy\.is_file\(\):\n"
        r"        return legacy\n"
        r'    raise ValueError\("Not a file"\)',
        '    if target.is_file():\n        return target\n    raise ValueError("Not a file")',
        t,
    )
    p.write_text(t, encoding="utf-8")
    print("mesh_cache.py", "resolve_app_data_dir" in t, "mesh_cache_url" in t)


def adapt_service() -> None:
    p = ROOT / "service.py"
    t = p.read_text(encoding="utf-8")
    repls = [
        ("from frontend.asset_preview.badge import make_badge_png", "from .badge import make_badge_png"),
        ("from frontend.asset_preview.cache import", "from .cache import"),
        ("from frontend.asset_preview import mesh_cache", "from . import mesh_cache"),
        ("from frontend.asset_preview.kinds import", "from .kinds import"),
        ("from frontend.asset_preview.paths import", "from .paths import"),
        ("from frontend.asset_preview.verse_link import", "from .verse_link import"),
    ]
    for a, b in repls:
        t = t.replace(a, b)
    p.write_text(t, encoding="utf-8")
    print("service.py ok")


def adapt_tests() -> None:
    for name in (
        "test_kinds.py",
        "test_mesh_cache.py",
        "test_mesh_preview_service.py",
        "test_preview_cache_stale.py",
    ):
        p = ROOT / name
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8")
        t = t.replace("from frontend.asset_preview.", "from .")
        t = t.replace("import frontend.asset_preview.", "from . import ")
        t = t.replace("frontend.asset_preview.", ".")
        p.write_text(t, encoding="utf-8")
        print("test", name)


if __name__ == "__main__":
    adapt_cache()
    adapt_mesh()
    adapt_service()
    adapt_tests()
