"""Turn a Widget Blueprint .uasset into the JSON spec the in-panel UMG engine runs.

Everything comes from the file on disk: tree, slots, styles, animations, the sounds
and images it references, and nested user widgets (read from their own .uasset).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .package import Export, Package, load_package
from .props import Raw, Ref, export_properties
from .reader import ReadError

MAX_NESTING = 6
DEFAULT_DESIGN_SIZE = [1920, 1080]

_NATIVE_KINDS = {
    "CanvasPanel", "Overlay", "StackBox", "HorizontalBox", "VerticalBox", "SizeBox", "ScaleBox",
    "WidgetSwitcher", "Image", "TextBlock", "Button", "Border", "Spacer", "GridPanel",
    "UniformGridPanel", "WrapBox", "ScrollBox", "ProgressBar", "Slider", "CheckBox",
    "RichTextBlock", "NamedSlot", "RetainerBox", "InvalidationBox", "SafeZone", "BackgroundBlur",
}
# Epic's UEFN widgets live in Fortnite content we cannot read: (engine kind, style variant).
_UEFN_KINDS = {
    "UEFN_TextBlock_C": ("TextBlock", "uefn"),
    "UEFN_Button_Loud_C": ("Button", "loud"),
    "UEFN_Button_Regular_C": ("Button", "regular"),
    "UEFN_Button_Quiet_C": ("Button", "quiet"),
    "UIFrameworkCustomButtonWidget": ("Button", "custom"),
    "UEFN_Slider_C": ("Slider", "uefn"),
    "UEFN_ProgressBar_C": ("ProgressBar", "uefn"),
    "UEFN_CheckBox_C": ("CheckBox", "uefn"),
}
_SLOT_DEFAULTS = {
    "OverlaySlot": ("Left", "Top", [0, 0, 0, 0]),
    "StackBoxSlot": ("Fill", "Fill", [0, 0, 0, 0]),
    "HorizontalBoxSlot": ("Fill", "Fill", [0, 0, 0, 0]),
    "VerticalBoxSlot": ("Fill", "Fill", [0, 0, 0, 0]),
    "SizeBoxSlot": ("Fill", "Fill", [0, 0, 0, 0]),
    "ScaleBoxSlot": ("Center", "Center", [0, 0, 0, 0]),
    "ButtonSlot": ("Center", "Center", [4, 2, 4, 2]),
    "BorderSlot": ("Fill", "Fill", [4, 2, 4, 2]),
    "WidgetSwitcherSlot": ("Fill", "Fill", [0, 0, 0, 0]),
}


def _enum(value: Any, default: str = "") -> str:
    if not isinstance(value, str) or not value:
        return default
    value = value.rsplit("::", 1)[-1]
    for prefix in ("HAlign_", "VAlign_", "Orient_", "ETextJustify_", "ESlateVisibility_"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _margin(value: Any, default: Optional[list[float]] = None) -> list[float]:
    base = list(default or [0, 0, 0, 0])
    if isinstance(value, dict):
        for i, key in enumerate(("Left", "Top", "Right", "Bottom")):
            if isinstance(value.get(key), (int, float)):
                base[i] = float(value[key])
    return base


def _color(value: Any, default: Optional[list[float]] = None) -> list[float]:
    """SlateColor or LinearColor → linear [r, g, b, a]."""
    if isinstance(value, dict):
        value = value.get("SpecifiedColor")
    if isinstance(value, list) and len(value) == 4:
        return [float(c) for c in value]
    return list(default or [1, 1, 1, 1])


def _vec2(value: Any, default: tuple[float, float]) -> list[float]:
    if isinstance(value, list) and len(value) >= 2:
        return [float(value[0]), float(value[1])]
    return [default[0], default[1]]


class _Builder:
    def __init__(self, resolve: Callable[[str], Optional[Path]]) -> None:
        self.resolve = resolve
        self.assets: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []
        self._packages: dict[str, Optional[Package]] = {}
        self._next_id = 0

    def warn(self, message: str) -> None:
        if message not in self.warnings and len(self.warnings) < 40:
            self.warnings.append(message)

    # -- assets ----------------------------------------------------------

    def asset(self, pkg: Package, ref: Any) -> str:
        """Register the asset behind an object reference; returns its key ('' if none)."""
        if not isinstance(ref, Ref) or ref.index >= 0:
            return ""
        imp = pkg.imports[-ref.index - 1]
        path = pkg.object_path(ref.index)
        if not path or path.startswith("/Script/"):
            return ""
        if path not in self.assets:
            cls = imp.class_name
            kind = ("texture" if cls.startswith("Texture") else
                    "material" if cls.startswith("Material") else
                    "sound" if cls.startswith("Sound") else
                    "font" if cls.startswith("Font") else "other")
            file = self.resolve(path)
            self.assets[path] = {"kind": kind, "class": cls, "name": imp.object_name,
                                 "file": str(file) if file else ""}
        return path

    def package(self, object_path: str) -> Optional[Package]:
        if object_path not in self._packages:
            pkg = None
            file = self.resolve(object_path)
            if file is not None:
                try:
                    pkg = load_package(file)
                except (ReadError, OSError):
                    pkg = None
            self._packages[object_path] = pkg
        return self._packages[object_path]

    # -- styles ------------------------------------------------------------

    def brush(self, pkg: Package, value: Any) -> dict[str, Any]:
        value = value if isinstance(value, dict) else {}
        outline = value.get("OutlineSettings") if isinstance(value.get("OutlineSettings"), dict) else {}
        out: dict[str, Any] = {
            "draw": _enum(value.get("DrawAs"), "Image"),
            "tint": _color(value.get("TintColor")),
            "size": _vec2(value.get("ImageSize"), (32.0, 32.0)),
            "margin": _margin(value.get("Margin")),
            "tiling": _enum(value.get("Tiling"), "NoTile"),
            "resource": self.asset(pkg, value.get("ResourceObject")),
        }
        if outline:
            radii = outline.get("CornerRadii")
            out["outline"] = {
                "color": _color(outline.get("Color"), [0, 0, 0, 0]),
                "width": float(outline.get("Width") or 0),
                "radii": [float(x) for x in radii] if isinstance(radii, list) and len(radii) == 4 else [0, 0, 0, 0],
                "rounding": _enum(outline.get("RoundingType"), "HalfHeightRadius"),
            }
        return out

    def font(self, pkg: Package, value: Any, variant: str) -> dict[str, Any]:
        value = value if isinstance(value, dict) else {}
        outline = value.get("OutlineSettings") if isinstance(value.get("OutlineSettings"), dict) else {}
        family = self.asset(pkg, value.get("FontObject"))
        return {
            "family": self.assets[family]["name"] if family else "",
            "typeface": str(value.get("TypefaceFontName") or ("Bold" if variant != "uefn" else "")),
            "size": float(value.get("Size") or 24),
            "letter_spacing": float(value.get("LetterSpacing") or 0),
            "outline_size": float(outline.get("OutlineSize") or 0),
            "outline_color": _color(outline.get("OutlineColor"), [0, 0, 0, 1]),
        }

    def slot(self, pkg: Package, ref: Any) -> dict[str, Any]:
        if not isinstance(ref, Ref) or ref.index <= 0:
            return {"type": "none"}
        exp = pkg.exports[ref.index - 1]
        cls = pkg.class_name(exp)
        props = export_properties(pkg, exp)
        if cls == "CanvasPanelSlot":
            layout = props.get("LayoutData") if isinstance(props.get("LayoutData"), dict) else {}
            anchors = layout.get("Anchors") if isinstance(layout.get("Anchors"), dict) else {}
            return {
                "type": "canvas",
                "offsets": _margin(layout.get("Offsets"), [0, 0, 100, 30]),
                "anchor_min": _vec2(anchors.get("Minimum"), (0, 0)),
                "anchor_max": _vec2(anchors.get("Maximum"), (0, 0)),
                "align": _vec2(layout.get("Alignment"), (0, 0)),
                "auto_size": bool(props.get("bAutoSize")),
                "z": int(props.get("ZOrder") or 0),
            }
        h, v, pad = _SLOT_DEFAULTS.get(cls, ("Fill", "Fill", [0, 0, 0, 0]))
        size = props.get("Size") if isinstance(props.get("Size"), dict) else {}
        return {
            "type": cls[:-4].lower() if cls.endswith("Slot") else cls.lower(),
            "h": _enum(props.get("HorizontalAlignment"), h),
            "v": _enum(props.get("VerticalAlignment"), v),
            "pad": _margin(props.get("Padding"), pad),
            "fill": _enum(size.get("SizeRule"), "Automatic") == "Fill",
            "fill_value": float(size.get("Value", 1.0)),
        }

    # -- tree --------------------------------------------------------------

    def widget(self, pkg: Package, exp: Export, depth: int,
               overrides: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
        props = export_properties(pkg, exp)
        if overrides and exp.object_name in overrides:
            props.update(overrides[exp.object_name])
        cls_name = pkg.class_name(exp)
        cls_path = pkg.class_path(exp)
        kind, variant = _UEFN_KINDS.get(cls_name, (cls_name if cls_name in _NATIVE_KINDS else "", ""))
        self._next_id += 1
        transform = props.get("RenderTransform") if isinstance(props.get("RenderTransform"), dict) else {}
        node: dict[str, Any] = {
            "id": self._next_id,
            "name": exp.object_name,
            "label": str(props.get("DisplayLabel") or exp.object_name),
            "kind": kind or "UserWidget",
            "variant": variant,
            "class": cls_name,
            "visibility": _enum(props.get("Visibility"), "Visible"),
            "opacity": float(props.get("RenderOpacity", 1.0)),
            "enabled": props.get("bIsEnabled", True) is not False,
            "clip": _enum(props.get("Clipping"), "Inherit") not in ("Inherit", ""),
            "xform": {
                "t": _vec2(transform.get("Translation"), (0, 0)),
                "s": _vec2(transform.get("Scale"), (1, 1)),
                "shear": _vec2(transform.get("Shear"), (0, 0)),
                "angle": float(transform.get("Angle") or 0),
            },
            "pivot": _vec2(props.get("RenderTransformPivot"), (0.5, 0.5)),
            "slot": self.slot(pkg, props.get("Slot")),
            "props": {},
            "children": [],
        }
        p = node["props"]
        if kind == "TextBlock":
            p.update(
                text=str(props.get("Text") or "") if not isinstance(props.get("Text"), Raw) else "",
                color=_color(props.get("ColorAndOpacity")),
                font=self.font(pkg, props.get("Font"), variant),
                shadow_color=_color(props.get("ShadowColorAndOpacity"), [0, 0, 0, 0]),
                shadow_offset=_vec2(props.get("ShadowOffset"), (1, 1)),
                justify=_enum(props.get("Justification"), "Left"),
                wrap=bool(props.get("AutoWrapText")),
                wrap_at=float(props.get("WrapTextAt") or 0),
                line_height=float(props.get("LineHeightPercentage") or 1.0),
                transform=_enum(props.get("TextTransformPolicy"), "None"),
            )
        elif kind == "Image":
            p.update(brush=self.brush(pkg, props.get("Brush")), color=_color(props.get("ColorAndOpacity")))
        elif kind == "Button":
            if variant in ("loud", "regular", "quiet"):
                self.warn("Epic's UEFN button look (Loud / Regular / Quiet) lives in Fortnite content: "
                          "its colours and font are approximated.")
            p.update(text=str(props.get("Text") or "") if not isinstance(props.get("Text"), Raw) else "",
                     min_width=float(props.get("MinWidth") or 0), min_height=float(props.get("MinHeight") or 0))
            style = props.get("WidgetStyle") if isinstance(props.get("WidgetStyle"), dict) else {}
            if style:
                p["style"] = {state.lower(): self.brush(pkg, style.get(state))
                              for state in ("Normal", "Hovered", "Pressed", "Disabled") if style.get(state)}
                for key, state in (("PressedSlateSound", "pressed_sound"), ("HoveredSlateSound", "hovered_sound")):
                    sound = style.get(key) if isinstance(style.get(key), dict) else {}
                    p[state] = self.asset(pkg, sound.get("ResourceObject"))
            p["color"] = _color(props.get("BackgroundColor"))
        elif kind == "Border":
            p.update(brush=self.brush(pkg, props.get("Background")), color=_color(props.get("BrushColor")),
                     pad=_margin(props.get("Padding"), [4, 2, 4, 2]),
                     h=_enum(props.get("HorizontalAlignment"), "Fill"), v=_enum(props.get("VerticalAlignment"), "Fill"))
        elif kind in ("StackBox", "HorizontalBox", "VerticalBox"):
            orient = "Vertical" if kind == "VerticalBox" else _enum(props.get("Orientation"), "Horizontal")
            p["orientation"] = orient
            node["kind"] = "StackBox"
        elif kind == "SizeBox":
            for key, flag, name in (("WidthOverride", "bOverride_WidthOverride", "width"),
                                    ("HeightOverride", "bOverride_HeightOverride", "height"),
                                    ("MinDesiredWidth", "bOverride_MinDesiredWidth", "min_width"),
                                    ("MinDesiredHeight", "bOverride_MinDesiredHeight", "min_height"),
                                    ("MaxDesiredWidth", "bOverride_MaxDesiredWidth", "max_width"),
                                    ("MaxDesiredHeight", "bOverride_MaxDesiredHeight", "max_height")):
                if props.get(flag):
                    p[name] = float(props.get(key) or 0)
        elif kind == "ScaleBox":
            p.update(stretch=_enum(props.get("Stretch"), "ScaleToFit"),
                     direction=_enum(props.get("StretchDirection"), "Both"),
                     user_scale=float(props.get("UserSpecifiedScale", 1.0)))
        elif kind == "WidgetSwitcher":
            p["active"] = int(props.get("ActiveWidgetIndex") or 0)
        elif kind == "ProgressBar":
            p.update(percent=float(props.get("Percent") or 0), fill=_color(props.get("FillColorAndOpacity")))
        elif kind == "Spacer":
            p["size"] = _vec2(props.get("Size"), (1, 1))
        elif not kind:
            self.user_widget(pkg, node, cls_path, props, depth)

        for slot_ref in props.get("Slots") or []:
            if isinstance(slot_ref, Ref) and slot_ref.index > 0:
                content = export_properties(pkg, pkg.exports[slot_ref.index - 1]).get("Content")
                if isinstance(content, Ref) and content.index > 0:
                    node["children"].append(self.widget(pkg, pkg.exports[content.index - 1], depth, overrides))
        return node

    def user_widget(self, pkg: Package, node: dict[str, Any], cls_path: str, props: dict[str, Any], depth: int) -> None:
        """Inline another project Widget Blueprint (its own .uasset) under ``node``."""
        if depth >= MAX_NESTING:
            self.warn("Nested widgets deeper than 6 levels are not expanded.")
            return
        sub = self.package(cls_path)
        if sub is None:
            self.warn(f"{node['class']}: its Widget Blueprint was not found on disk, drawn as a placeholder.")
            node["props"]["missing"] = True
            return
        tree = _blueprint_tree(sub)
        design = _class_defaults(sub)
        blueprint = _blueprint_export(sub)
        parent = export_properties(sub, blueprint).get("ParentClass") if blueprint else None
        parent_kind = _UEFN_KINDS.get(sub.object_name(parent.index) if isinstance(parent, Ref) else "")
        if parent_kind and parent_kind[0] == "Button":
            # A project button built on Epic's UEFN button: own background and sounds.
            node["kind"], node["variant"] = parent_kind

            def pick(name: str) -> str:
                if isinstance(props.get(name), Ref):
                    return self.asset(pkg, props[name])
                return self.asset(sub, design.get(name))

            text = props.get("Text", design.get("Text"))
            node["props"].update(
                text=str(text or "") if not isinstance(text, Raw) else "",
                min_width=float(props.get("MinWidth") or design.get("MinWidth") or 0),
                min_height=float(props.get("MinHeight") or design.get("MinHeight") or 0),
                background=pick("BackgroundMaterial"),
                pressed_sound=pick("PressedSound"),
                hovered_sound=pick("HoveredSound"),
            )
        elif tree is None:
            node["props"]["missing"] = True
            return
        if tree is None:
            return
        root = self.widget(sub, tree, depth + 1, _binding_overrides(sub, props))
        root["slot"] = {"type": "root"}
        node["children"].append(root)
        if _enum(design.get("DesignSizeMode")) in ("Custom", "CustomOnScreen") and design.get("DesignTimeSize"):
            node["props"]["design_size"] = _vec2(design.get("DesignTimeSize"), (0, 0))


def _blueprint_export(pkg: Package) -> Optional[Export]:
    for exp in pkg.exports:
        if exp.outer_index == 0 and pkg.class_name(exp) == "WidgetBlueprint":
            return exp
    return None


def _blueprint_tree(pkg: Package) -> Optional[Export]:
    """Root widget of the designer tree (the one owned by the WidgetBlueprint asset)."""
    blueprint = _blueprint_export(pkg)
    if blueprint is None:
        return None
    for exp in pkg.exports:
        if exp.outer_index == blueprint.index and pkg.class_name(exp) == "WidgetTree":
            root = export_properties(pkg, exp).get("RootWidget")
            if isinstance(root, Ref) and root.index > 0:
                return pkg.exports[root.index - 1]
    return None


def _class_defaults(pkg: Package) -> dict[str, Any]:
    for exp in pkg.exports:
        if exp.outer_index == 0 and exp.object_name.startswith("Default__"):
            return export_properties(pkg, exp)
    return {}


def _binding_overrides(pkg: Package, instance_props: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """View bindings ``variable → widget.property`` resolved with this instance's values."""
    values = dict(_class_defaults(pkg))
    values.update(instance_props)
    out: dict[str, dict[str, Any]] = {}
    for exp in pkg.exports:
        if pkg.class_name(exp) != "MVVMBlueprintView":
            continue
        for binding in export_properties(pkg, exp).get("Bindings") or []:
            if not isinstance(binding, dict):
                continue
            try:
                source = binding["SourcePath"]["Paths"][0]["BindingReference"]["MemberName"]
                dest = binding["DestinationPath"]
                prop = dest["Paths"][0]["BindingReference"]["MemberName"]
                widget = dest["WidgetName"]
            except (KeyError, IndexError, TypeError):
                continue
            if source in values and not isinstance(values[source], (Raw, Ref)):
                out.setdefault(widget, {})[prop] = values[source]
    return out


def _channel(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict) or "keys" not in value:
        return None
    rate = value.get("tick_rate") or 60000.0
    return {
        "default": value.get("default"),
        # tangents are stored per tick; the engine works in seconds
        "keys": [[k["t"] / rate, k["v"], k["interp"], k["arrive"] * rate, k["leave"] * rate] for k in value["keys"]],
    }


def _animations(builder: _Builder, pkg: Package, blueprint: Export) -> list[dict[str, Any]]:
    out = []
    for ref in export_properties(pkg, blueprint).get("Animations") or []:
        if not isinstance(ref, Ref) or ref.index <= 0:
            continue
        anim_exp = pkg.exports[ref.index - 1]
        anim = export_properties(pkg, anim_exp)
        scene_ref = anim.get("MovieScene")
        if not isinstance(scene_ref, Ref) or scene_ref.index <= 0:
            continue
        scene = export_properties(pkg, pkg.exports[scene_ref.index - 1])
        targets = {}
        for b in anim.get("AnimationBindings") or []:
            if isinstance(b, dict):
                slot_of = str(b.get("SlotWidgetName") or "None")
                targets[b.get("AnimationGuid")] = {
                    "widget": "" if b.get("bIsRootWidget") else str(b.get("WidgetName") or ""),
                    "slot_of": "" if slot_of == "None" else slot_of,
                    "root": bool(b.get("bIsRootWidget")),
                }
        rate = 60000.0
        tick = scene.get("TickResolution")
        if isinstance(tick, dict) and tick.get("Numerator"):
            rate = tick["Numerator"] / (tick.get("Denominator") or 1)
        playback = scene.get("PlaybackRange") if isinstance(scene.get("PlaybackRange"), dict) else {}
        tracks: list[dict[str, Any]] = []
        audio: list[dict[str, Any]] = []
        unsupported = set()

        def sections(track_props: dict[str, Any], key: str = "Sections"):
            for sref in track_props.get(key) or []:
                if isinstance(sref, Ref) and sref.index > 0:
                    yield export_properties(pkg, pkg.exports[sref.index - 1])

        def add_tracks(track_refs: Any, target: Optional[dict[str, Any]]) -> None:
            for tref in track_refs or []:
                if not isinstance(tref, Ref) or tref.index <= 0:
                    continue
                texp = pkg.exports[tref.index - 1]
                tcls = pkg.class_name(texp)
                tprops = export_properties(pkg, texp)
                binding = tprops.get("PropertyBinding") if isinstance(tprops.get("PropertyBinding"), dict) else {}
                path = str(binding.get("PropertyPath") or "")
                if tcls == "MovieSceneAudioTrack":
                    for sec in sections(tprops, "AudioSections"):
                        rng = sec.get("SectionRange") or {}
                        volume = sec.get("SoundVolume") if isinstance(sec.get("SoundVolume"), dict) else {}
                        pitch = sec.get("PitchMultiplier") if isinstance(sec.get("PitchMultiplier"), dict) else {}
                        audio.append({
                            "sound": builder.asset(pkg, sec.get("Sound")),
                            "start": (rng.get("lower") or 0) / rate,
                            "end": (rng.get("upper") or 0) / rate,
                            "volume": volume.get("default") if volume.get("default") is not None else 1.0,
                            "pitch": pitch.get("default") if pitch.get("default") is not None else 1.0,
                            "loop": bool(sec.get("bLooping")),
                        })
                    continue
                if target is None:
                    continue
                for sec in sections(tprops):
                    if tcls == "MovieScene2DTransformTrack":
                        def pair(name: str) -> list[Any]:
                            items = sec.get(name)
                            return items if isinstance(items, list) else [None, None]
                        channels = {"tx": pair("Translation")[0], "ty": pair("Translation")[1],
                                    "sx": pair("Scale")[0], "sy": pair("Scale")[1],
                                    "shx": pair("Shear")[0], "shy": pair("Shear")[1], "angle": sec.get("Rotation")}
                        kind = "transform"
                    elif tcls == "MovieSceneFloatTrack":
                        channels, kind = {"v": sec.get("FloatCurve")}, "float"
                    elif tcls == "MovieSceneColorTrack":
                        channels = {"r": sec.get("RedCurve"), "g": sec.get("GreenCurve"),
                                    "b": sec.get("BlueCurve"), "a": sec.get("AlphaCurve")}
                        kind = "color"
                    elif tcls == "MovieSceneMarginTrack":
                        channels = {"l": sec.get("LeftCurve"), "t": sec.get("TopCurve"),
                                    "r": sec.get("RightCurve"), "b": sec.get("BottomCurve")}
                        kind = "margin"
                    else:
                        unsupported.add(tcls)
                        continue
                    decoded = {name: ch for name, raw in channels.items() if (ch := _channel(raw)) is not None}
                    rng = sec.get("SectionRange") or {}
                    tracks.append({**target, "kind": kind, "prop": path, "channels": decoded,
                                   "start": (rng.get("lower") or 0) / rate,
                                   "end": (rng["upper"] / rate) if rng.get("upper") is not None else None})

        for binding in scene.get("ObjectBindings") or []:
            if isinstance(binding, dict):
                add_tracks(binding.get("Tracks"), targets.get(binding.get("ObjectGuid")))
        add_tracks(scene.get("Tracks"), None)
        for cls in sorted(unsupported):
            builder.warn(f"Animation track {cls} is not played (material parameters need Unreal's renderer).")
        start = (playback.get("lower") or 0) / rate
        end = (playback.get("upper") or 0) / rate
        out.append({"name": str(anim.get("DisplayLabel") or anim_exp.object_name), "start": start,
                    "end": max(end, start), "tracks": tracks, "audio": audio})
    return out


def build_umg_spec(path: Path | str, resolve: Callable[[str], Optional[Path]]) -> dict[str, Any]:
    """Spec for the Widget Blueprint at ``path``.

    ``resolve`` maps an object path (``/Project/Dir/Asset.Asset``) to its .uasset on disk.
    """
    pkg = load_package(path)
    blueprint = _blueprint_export(pkg)
    root_exp = _blueprint_tree(pkg)
    if blueprint is None:
        raise ReadError("not a Widget Blueprint")
    builder = _Builder(resolve)
    root = None
    if root_exp is not None:
        root = builder.widget(pkg, root_exp, 0, _binding_overrides(pkg, {}))
        root["slot"] = {"type": "root"}
    defaults = _class_defaults(pkg)
    design = list(DEFAULT_DESIGN_SIZE)
    custom = _enum(defaults.get("DesignSizeMode")) in ("Custom", "CustomOnScreen") and defaults.get("DesignTimeSize")
    if custom:
        design = _vec2(defaults.get("DesignTimeSize"), (1920, 1080))
    return {
        "version": 1,
        "name": blueprint.object_name,
        "package": pkg.package_name,
        "design_size": design,
        "custom_size": bool(custom),
        "root": root,
        "animations": _animations(builder, pkg, blueprint),
        "assets": builder.assets,
        "warnings": builder.warnings,
    }


def project_resolver(content_dir: Path | str) -> Callable[[str], Optional[Path]]:
    """Resolve ``/Mount/Dir/Asset.Asset`` under one project's Content folder.

    The mount name is ignored: assets migrated from another project keep their old
    mount in references, but live at the same relative path here.
    """
    content = Path(content_dir)

    def resolve(object_path: str) -> Optional[Path]:
        package = object_path.split(".", 1)[0].strip("/")
        parts = package.split("/")
        if len(parts) < 2 or parts[0] in ("Script", "Game", "Engine"):
            return None
        candidate = content.joinpath(*parts[1:]).with_suffix(".uasset")
        return candidate if candidate.is_file() else None

    return resolve
