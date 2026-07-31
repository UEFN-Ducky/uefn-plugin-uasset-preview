"""Plugin-local AppData paths and cache URLs served via /plugin-ui/uasset-preview/."""

from __future__ import annotations

from pathlib import Path

from frontend.settings import PANEL_LISTENER_PORT

PLUGIN_ID = "uasset-preview"
PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1


def plugin_root() -> Path:
    """Installed plugin root: …/uefn_plugins/uasset-preview/."""
    return Path(__file__).resolve().parents[2]


def cache_root(*, for_write: bool = False) -> Path:
    path = plugin_root() / "cache"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def png_cache_url(preview_id: str) -> str:
    return (
        f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/"
        f"cache/png/{preview_id}.png"
    )


def mesh_cache_url(cache_id: str, filename: str = "model.fbx") -> str:
    safe = Path(filename).name
    return (
        f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/"
        f"cache/mesh/{cache_id}/{safe}"
    )


def mesh_cache_base_url(cache_id: str) -> str:
    return (
        f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/plugin-ui/{PLUGIN_ID}/"
        f"cache/mesh/{cache_id}/"
    )
