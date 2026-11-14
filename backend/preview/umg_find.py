"""Find a project's Widget Blueprint from the name people (and agents) use for it in chat."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

_PREFIX = re.compile(r"^(uw|wbp|wb|w|ui|hud|umg)_", re.IGNORECASE)
_SUFFIX = re.compile(r"(_?(ui|hud|widget|umg|wbp))$", re.IGNORECASE)
_MAX_CANDIDATES = 40


def _keys(name: str) -> set[str]:
    """Spellings treated as the same widget: ``ShopUi``, ``UW_Shop``, ``WBP_ShopUI`` -> ``shop``."""
    base = re.sub(r"[^a-z0-9_]", "", name.strip().lower())
    out = {base}
    stripped = _PREFIX.sub("", base)
    out.add(stripped)
    out.add(_SUFFIX.sub("", stripped))
    out.add(_SUFFIX.sub("", base))
    return {k for k in out if len(k) >= 2}


def find_widget(content: Path, name: str) -> Optional[Path]:
    """The Widget Blueprint .uasset under ``content`` that ``name`` most plausibly means."""
    from .uasset import main_asset_class

    wanted = name.strip().rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not wanted or len(wanted) > 80 or not content.is_dir():
        return None
    keys = _keys(wanted)
    exact, loose = [], []
    for path in content.rglob("*.uasset"):
        if path.relative_to(content).parts[0].startswith("__External"):
            continue
        stem = path.stem.lower()
        if stem == wanted.lower():
            exact.append(path)
        elif _keys(stem) & keys:
            loose.append(path)
    # an exact file name wins; otherwise the shortest name sharing the same root word
    for path in sorted(exact, key=lambda p: len(p.parts)) + sorted(loose, key=lambda p: (len(p.stem), len(p.parts)))[:_MAX_CANDIDATES]:
        if main_asset_class(path) == "WidgetBlueprint":
            return path
    return None


def find_widget_blueprint(name: str) -> dict[str, Any]:
    from frontend.ui_web import project_files as pf

    try:
        root = pf._project_root().resolve()  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - no project selected
        return {"ok": False, "error": str(exc)}
    found = find_widget(root / "Content", str(name or ""))
    if found is None:
        return {"ok": True, "found": False}
    return {"ok": True, "found": True, "name": found.stem,
            "relative_path": found.relative_to(root).as_posix()}
