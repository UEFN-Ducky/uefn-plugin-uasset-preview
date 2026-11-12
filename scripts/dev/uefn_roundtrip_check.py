"""Dev check: does UEFN itself accept a Widget Blueprint rewritten by this plugin?

    python scripts/dev/uefn_roundtrip_check.py <ObjectPathOfAWidget> [<ObjectPathOfAnotherImage>]

e.g.  /MyIsland/UI/WBP_Menu  /MyIsland/UI/Textures/T_Icon

With the island open in UEFN it: duplicates the widget into ``DuckyEditTest/`` (the
original is never touched), edits the copy on disk with ``umg_edit.save_edits``, asks
UEFN to reload it, compiles it and reads the values back through Epic's widget
analysis tool. Prints one line per edit: OK / MISMATCH. The copy stays in
``Content/DuckyEditTest`` (delete it from the Content Browser).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent.parent / "UEFN-Ducky" / "ducky_app"))


def _plugin_preview():
    """The plugin's ``backend/preview`` package under its own name: the host app also has a ``backend``."""
    import importlib.util

    init = ROOT / "backend" / "preview" / "__init__.py"
    spec = importlib.util.spec_from_file_location("ducky_uasset_preview", init, submodule_search_locations=[str(init.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["ducky_uasset_preview"] = module
    spec.loader.exec_module(module)
    return module


def uefn(code: str) -> dict:
    from backend.bridge.client import _post_command_to_listener
    from frontend.settings import PANEL_LISTENER_PORT

    out = _post_command_to_listener(PANEL_LISTENER_PORT, "execute_python", {"code": code}, timeout=180.0)
    if out.get("stderr"):
        raise RuntimeError(out["stderr"][-800:])
    return out.get("result") or {}


_DESCRIBE = '''
import unreal, json
reg = unreal.ToolsetRegistry.get_default_object()
_cls = getattr(unreal, "WidgetStaticAnalysisToolset", None)
if _cls is not None:
    try: reg.register_toolset_class(_cls.static_class())
    except Exception: pass
def describe(path):
    res = reg.execute_tool("WidgetStaticAnalysisToolset.WidgetStaticAnalysisToolset", "DescribeWidget",
                           json.dumps({"widgetBlueprint": {"refPath": path}, "maxDepth": -1}))
    raw = res.get_value_as_json_string()
    if not raw:
        raise RuntimeError("DescribeWidget returned nothing: " + str(getattr(res, "error", "")) + " | classes: "
                           + str([n for n in dir(unreal) if "StaticAnalysis" in n]))
    val = json.loads(raw)
    if isinstance(val, str): val = json.loads(val)
    out = {}
    for w in val.get("returnValue", val).get("widgets", []):
        out[w["widgetName"]] = {"class": w["widgetClassPath"]["refPath"].rsplit(".", 1)[-1],
                                "p": {x["propertyName"]: x["exportedValue"] for x in w["properties"]},
                                "s": {x["propertyName"]: x["exportedValue"] for x in w["slotProperties"]}}
    return out
'''


def main() -> int:
    import importlib

    _plugin_preview()
    umg_edit = importlib.import_module("ducky_uasset_preview.umg_edit")
    uasset = importlib.import_module("ducky_uasset_preview.uasset")
    load_package, main_asset_class = uasset.load_package, uasset.main_asset_class

    source = sys.argv[1].split(".")[0]
    other_image = sys.argv[2].split(".")[0] if len(sys.argv) > 2 else ""
    mount = source.strip("/").split("/")[0]
    copy = f"/{mount}/DuckyEditTest/{source.rsplit('/', 1)[-1]}_DuckyTest"
    made = uefn(f'''
import unreal
src, dst = {source!r}, {copy!r}
if unreal.EditorAssetLibrary.does_asset_exist(dst):
    unreal.EditorAssetLibrary.save_asset(dst, only_if_is_dirty=False)
else:
    unreal.EditorAssetLibrary.duplicate_asset(src, dst)
    unreal.EditorAssetLibrary.save_asset(dst, only_if_is_dirty=False)
result = {{"file": str(unreal.SystemLibrary.get_system_path(unreal.EditorAssetLibrary.load_asset(dst)))}}
''')
    file = Path(made["file"])
    name = copy.rsplit("/", 1)[-1]
    before = uefn(_DESCRIBE + f"result = describe({copy + '.' + name!r})")
    texts = [n for n, w in before.items() if "TextBlock" in w["class"]]
    with_text = [n for n in texts if before[n]["p"].get("Text")]
    without_text = [n for n in texts if not before[n]["p"].get("Text")]
    images = [n for n, w in before.items() if w["class"] == "Image"]
    edits, expect = [], []

    def add(widget, kind, field, value, where, prop, pattern):
        edits.append({"widget": widget, "kind": kind, "field": field, "value": value})
        expect.append((widget, field, where, prop, pattern))

    if with_text:
        add(with_text[0], "TextBlock", "text", "Ducky édité", "p", "Text", r"Ducky édité")
        add(with_text[0], "TextBlock", "text_color", [1.0, 0.25, 0.0, 1.0], "p", "ColorAndOpacity", r"R=1\.0+,G=0\.250+,B=0\.0+")
        add(with_text[0], "TextBlock", "font_size", 44, "p", "Font", r",Size=44\.0")
    if without_text:
        add(without_text[0], "TextBlock", "text", "Nouveau texte", "p", "Text", r"Nouveau texte")
    if images:
        img = images[0]
        add(img, "Image", "opacity", 0.5, "p", "RenderOpacity", r"0\.50+")
        add(img, "Image", "tint", [0.25, 0.5, 0.75, 1.0], "p", "Brush", r"TintColor=\(SpecifiedColor=\(R=0\.250+,G=0\.50+,B=0\.750+")
        add(img, "Image", "translation", [12.0, -8.0], "p", "RenderTransform", r"Translation=\(X=12\.0+,Y=-8\.0+")
        add(img, "Image", "angle", 15.0, "p", "RenderTransform", r"Angle=15\.0+")
        if other_image:
            content = next(p for p in file.parents if p.name == "Content")
            target = content.joinpath(*other_image.strip("/").split("/")[1:]).with_suffix(".uasset")
            edits.append({"widget": img, "kind": "Image", "field": "resource", "value": f"{other_image}.{other_image.rsplit('/', 1)[-1]}",
                          "asset_class": main_asset_class(target)})
            expect.append((img, "resource", "p", "Brush", re.escape(other_image)))
    umg_edit.cache_root = lambda for_write=False: Path(tempfile.gettempdir()) / "umg-roundtrip-cache"
    size_before, names_before = file.stat().st_size, len(load_package(file).names)
    saved = umg_edit.save_edits(file, edits)
    print("save:", {k: v for k, v in saved.items() if k != "backup"},
          "| names", names_before, "->", len(load_package(file).names), "| imports", len(load_package(file).imports))
    if not saved.get("ok"):
        return 1
    after = uefn(_DESCRIBE + f'''
a = unreal.EditorAssetLibrary.load_asset({copy!r})
reload = str(unreal.EditorLoadingAndSavingUtils.reload_packages([a.get_outermost()]))
a = unreal.EditorAssetLibrary.load_asset({copy!r})
unreal.BlueprintEditorLibrary.compile_blueprint(a)
result = {{"reload": reload, "status": str(a.get_editor_property("status")), "widgets": describe({copy + '.' + name!r})}}
''')
    print("UEFN reload:", after["reload"], "| blueprint:", after["status"], "| widgets:", len(after["widgets"]), "(before:", len(before), ")")
    failed = 0
    for widget, field, where, prop, pattern in expect:
        value = after["widgets"].get(widget, {}).get(where, {}).get(prop, "")
        ok = re.search(pattern, value) is not None
        failed += not ok
        print(("OK       " if ok else "MISMATCH ") + f"{widget}.{field}" + ("" if ok else f"  UEFN has: {value[:160]!r}"))
    print(json.dumps({"edits": len(expect), "failed": failed, "copy": copy}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
