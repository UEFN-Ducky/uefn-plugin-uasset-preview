"""MCP tools for UAsset preview (intent-gated)."""

from __future__ import annotations

from typing import Any

_INTENT = r"\b(uasset|umap|static\s*mesh|material\s*preview|texture\s*preview|mesh\s*preview)\b"


def _tool(api: Any, **kwargs: Any):
    """Prefer api.tool(listener=False); fall back on older hosts."""
    try:
        return api.tool(listener=False, **kwargs)
    except TypeError:
        return api.tool(**kwargs)


def register_tools(api: Any) -> None:
    @_tool(api, intent=_INTENT)
    def uasset_preview_info(relative_path: str) -> str:
        """Return cached/local preview metadata for a project .uasset (no auto-export)."""
        from backend.util.json_util import tool_json

        from .preview import preview_project_asset

        return tool_json(preview_project_asset(relative_path))
