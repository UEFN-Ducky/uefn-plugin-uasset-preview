"""UAsset Preview plugin — panel RPCs + optional MCP tools."""

from __future__ import annotations

from typing import Any


def register(api: Any) -> None:
    from .panel_rpc import register_panel_rpcs
    from .tools import register_tools

    register_tools(api)
    if hasattr(api, "register_panel_rpc"):
        register_panel_rpcs(api)
    api.log("uasset-preview registered")


def unload() -> None:
    pass
