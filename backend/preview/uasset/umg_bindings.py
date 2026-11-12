"""The View Bindings of a Widget Blueprint, as UEFN's "View Bindings" panel lists them.

Three kinds, all stored as data in the asset (MVVM view): value bindings (a Verse field or a
widget property feeding a widget property, maybe through a conversion function), event
bindings (a widget's event calling a Verse function or playing an animation) and condition
bindings (a field reaching a value runs a function). Read-only: nothing here writes.
"""

from __future__ import annotations

from typing import Any, Optional

from .package import Package
from .props import Raw, Ref, export_properties

_MODES = {
    "OneTimeToDestination": "one time",
    "OneWayToDestination": "one way",
    "TwoWay": "two way",
    "OneTimeToSource": "one time to source",
    "OneWayToSource": "one way to source",
}
_OPERATIONS = {"Equal": "==", "NotEqual": "!=", "LessThan": "<", "LessThanOrEqual": "<=",
               "GreaterThan": ">", "GreaterThanOrEqual": ">=", "BetweenInclusive": "between"}


def _enum(value: Any) -> str:
    return str(value or "").rsplit("::", 1)[-1]


def _path(pkg: Package, value: Any) -> dict[str, Any]:
    """``{"text": "Card_1.Root_Button.OnButtonClicked", "kind": "widget"|"field"|"viewmodel"|""}``."""
    if not isinstance(value, dict):
        return {"text": "", "kind": ""}
    names = []
    for step in value.get("Paths") or []:
        try:
            names.append(str(step["BindingReference"]["MemberName"]))
        except (KeyError, TypeError):
            continue
    source = _enum(value.get("Source"))
    widget = str(value.get("WidgetName") or "")
    if source == "Widget" and widget and widget != "None":
        return {"text": ".".join([widget] + names), "kind": "widget", "widget": widget}
    if source == "SelfContext":
        return {"text": ".".join(names), "kind": "field"}
    if source == "ViewModel":
        return {"text": ".".join(names), "kind": "viewmodel"}
    return {"text": ".".join(names), "kind": "field" if names else ""}


def _pins(pkg: Package, pins: Any) -> list[dict[str, str]]:
    """A function's inputs: each bound to a path, or set to a fixed value."""
    out = []
    for pin in pins or []:
        if not isinstance(pin, dict):
            continue
        names = (pin.get("Id") or {}).get("PinNames") or []
        path = _path(pkg, pin.get("Path"))
        value = path["text"] or str(pin.get("DefaultString") or pin.get("DefaultText") or "")
        if isinstance(pin.get("DefaultObject"), Ref) and pin["DefaultObject"].index and not value:
            value = pkg.object_path(pin["DefaultObject"].index).rsplit(".", 1)[-1]
        out.append({"name": ".".join(str(n) for n in names), "value": value, "bound": bool(path["text"])})
    return out


def _function_name(pkg: Package, ref: Any) -> str:
    if not isinstance(ref, dict):
        return ""
    node = ref.get("Node")
    if isinstance(node, Ref) and node.index:
        return pkg.object_path(node.index).rsplit(".", 1)[-1].replace("MVVMK2Node_", "")
    function = ref.get("FunctionReference")
    if isinstance(function, dict):
        return str(function.get("MemberName") or "")
    return ""


def _short(value: Any) -> Optional[str]:
    """A field's default value as one short line, or None when it is not a plain value."""
    if isinstance(value, (Raw, Ref)) or value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (int, str)):
        text = str(value)
        return text if len(text) <= 60 else text[:57] + "..."
    return None


def view_bindings(pkg: Package, defaults: Optional[dict[str, Any]] = None) -> dict[str, list[dict[str, Any]]]:
    """Every binding of the blueprint's own view (nested widgets keep theirs in their own asset)."""
    defaults = defaults or {}
    out: dict[str, list[dict[str, Any]]] = {"values": [], "events": [], "conditions": []}
    view = next((e for e in pkg.exports if pkg.class_name(e) == "MVVMBlueprintView"), None)
    if view is None:
        return out
    props = export_properties(pkg, view)

    def export_props(ref: Any) -> Optional[dict[str, Any]]:
        if isinstance(ref, Ref) and ref.index > 0:
            return export_properties(pkg, pkg.exports[ref.index - 1])
        return None

    for binding in props.get("Bindings") or []:
        if not isinstance(binding, dict):
            continue
        source, target = _path(pkg, binding.get("SourcePath")), _path(pkg, binding.get("DestinationPath"))
        entry: dict[str, Any] = {
            "source": source["text"], "source_kind": source["kind"],
            "target": target["text"], "widget": target.get("widget", ""),
            "mode": _MODES.get(_enum(binding.get("BindingType")), _enum(binding.get("BindingType"))),
            "enabled": binding.get("bEnabled", True) is not False,
        }
        conversion = export_props((binding.get("Conversion") or {}).get("SourceToDestinationConversion"))
        if conversion is None:
            conversion = export_props((binding.get("Conversion") or {}).get("DestinationToSourceConversion"))
        if conversion is not None:
            entry["function"] = _function_name(pkg, conversion.get("ConversionFunction"))
            entry["args"] = _pins(pkg, conversion.get("SavedPins"))
            if not entry["source"]:  # the conversion's bound inputs are the real sources
                entry["source"] = ", ".join(a["value"] for a in entry["args"] if a["bound"])
                entry["source_kind"] = "field"
        if entry["source_kind"] == "field" and entry["source"] in defaults:
            value = _short(defaults[entry["source"]])
            if value is not None:
                entry["value"] = value
        out["values"].append(entry)

    for ref in props.get("Events") or []:
        event = export_props(ref)
        if event is None:
            continue
        path = _path(pkg, event.get("EventPath"))
        function = _path(pkg, event.get("DestinationPath"))
        entry = {"event": path["text"], "widget": path.get("widget", ""), "function": function["text"],
                 "args": _pins(pkg, event.get("SavedPins")), "enabled": event.get("bEnabled", True) is not False}
        out["events"].append(entry)

    for ref in props.get("Conditions") or []:
        condition = export_props(ref)
        if condition is None:
            continue
        operation = _enum(condition.get("ConditionOperation")) or "Equal"
        value = condition.get("Value", 0.0)
        entry = {"source": _path(pkg, condition.get("ConditionPath"))["text"],
                 "operation": _OPERATIONS.get(operation, operation),
                 "value": _short(value) or "0",
                 "function": _path(pkg, condition.get("DestinationPath"))["text"],
                 "args": _pins(pkg, condition.get("SavedPins")),
                 "enabled": condition.get("bEnabled", True) is not False}
        if operation == "BetweenInclusive":
            entry["value_max"] = _short(condition.get("ValueMax", 0.0)) or "0"
        out["conditions"].append(entry)
    return out
