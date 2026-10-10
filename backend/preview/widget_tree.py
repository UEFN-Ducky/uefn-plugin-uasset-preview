"""Flatten the listener's ``get_widget_blueprint_info`` tree for the panel."""

from __future__ import annotations

from typing import Any


def _ref_name(ref: Any) -> str:
    path = str((ref or {}).get("refPath") or "") if isinstance(ref, dict) else ""
    return path.rsplit(".", 1)[-1]


def _short_class(ref: Any) -> str:
    name = _ref_name(ref)
    return name[:-2] if name.endswith("_C") else name


def summarize_widget_tree(info: dict[str, Any]) -> dict[str, Any]:
    """Return ``widgets`` in tree order (name, class, depth, is_variable) + ``variables``."""
    rows = [w for w in ((info.get("tree") or {}).get("widgets") or []) if isinstance(w, dict)]
    children: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        children.setdefault(_ref_name(row.get("parent")), []).append(row)

    widgets: list[dict[str, Any]] = []

    def walk(parent: str, depth: int) -> None:
        for row in children.get(parent, []):
            name = str(row.get("widgetName") or _ref_name(row.get("widget")))
            widgets.append(
                {
                    "name": name,
                    "class": _short_class(row.get("widgetClassPath")),
                    "depth": depth,
                    "is_variable": bool(row.get("bIsVariable")),
                }
            )
            walk(_ref_name(row.get("widget")), depth + 1)

    walk("", 0)
    # VerseFieldInternalVariable_* are UEFN's hidden backing fields for Verse fields.
    variables = [
        str(v) for v in info.get("member_variables") or [] if not str(v).startswith("VerseFieldInternalVariable_")
    ]
    return {"widgets": widgets, "variables": variables}
