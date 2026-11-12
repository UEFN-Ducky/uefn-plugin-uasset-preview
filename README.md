# UAsset Preview (UEFN-Ducky Store plugin)

Preview `.uasset` / `.umap` assets (thumbnails, materials, textures, StaticMesh 3D)
and standalone 3D model files (`.fbx`, `.glb`, …) inside the UEFN-Ducky panel.

Without this plugin installed, those files open as binary with an **Open Store**
link (plus View raw hex).

## Interactive UMG preview (development)

Widget Blueprints open in a canvas-based preview with hover/click events,
animation playback and manual switcher controls. This is a partial renderer:
Verse/Blueprint game logic and Unreal cinematics/shaders are not executed.

With the optional `pyooz==0.0.8` decoder available in the host's Python,
supported Texture2D sources are extracted at native resolution, with alpha,
without UEFN. This includes single-mip, single-layer BGRA8 UEDELTA sources
(validated on 512×512, 1024×1024 and 1200×662 assets), raw BGRA8, PNG and JPEG.
Unknown layouts fall back to explicitly labelled thumbnails. Material effects
still use thumbnails; extracting an input texture does not render a material.
Full images are cached separately from thumbnails and survive offline reloads.

`pyooz` is a separately installed optional GPLv3+ dependency (per its package
metadata), not bundled in this MIT plugin. Store distribution needs a dependency
and licensing decision before bundling it. Source assets and generated caches
must never be included in a release.

### Materials

UI materials (`MD_UI`) are drawn by their own shader: the node graph is read from
the `.uasset` (`backend/preview/uasset/material.py`) and compiled to GLSL in the
panel (`ui/umg/material.js`), with the instance's scalar, vector and texture
overrides. Supported nodes are the arithmetic, vector, trigonometric, parameter,
texture-sample, time and panner nodes listed in `material.js`; a graph with any
other node (material functions included) is refused and keeps its thumbnail, with
the reason shown in the banner. Material-parameter animation tracks are not played.

### Nested widgets and their events

A widget placed inside another keeps its own animations. The event bindings stored
in each Widget Blueprint (`button event -> play / reverse an animation`, or a call
into Verse) are read from the file and honoured: hovering a card plays the hover
animation its own blueprint binds, clicking it plays its select animation, and a
bound Verse function is listed in the event log (Verse itself is not run). A
blueprint listening to a button inside a nested one (`Card_1 -> Root_Button ->
clicked`) is handled the same way. Logic written in Verse or in a Blueprint graph
is not executed.

### Editing

`Edit` selects a widget (click), moves it (drag) and exposes its text, colours,
opacity, font size, position, padding, image size, render transform and image.
`Save` writes the pending changes into the Widget Blueprint `.uasset`
(`backend/preview/uasset/writer.py`): only the edited objects' property blocks are
rewritten, in both the designer tree and the compiled class tree. The result is
checked before the file is touched (`umg_edit.check_rewrite`), the original is
copied under `cache/umg_backups/`, and `Undo save` restores it.

An image the widget does not reference yet can be picked from the project (search
box in the inspector): its package and asset are appended to the import table and
the asset registry's per-import bits are extended. Limits: material parameters are
not editable; widgets nested from another Widget Blueprint are edited in their own
file. Do not save while UEFN has the widget open, since UEFN would overwrite the
file with its own copy.

While editing, every widget is shown whatever its Visibility (unless hidden with the
designer's eye icon), as UEFN's designer does; outside edit mode the preview follows
Visibility as the game does.

### Checking against UEFN (development)

The renderer and the writer are checked against UEFN itself, not only against this
plugin's reader. With the island open in UEFN and the Ducky listener running:

```powershell
# does UEFN accept a rewritten widget? (works on a copy in Content/DuckyEditTest)
python scripts/dev/uefn_roundtrip_check.py /Island/UI/WBP_Menu /Island/UI/Textures/T_Icon
# UEFN's designer window as a PNG, to compare with the preview
powershell -File scripts/dev/capture_window.ps1 -Hwnd <hwnd> -Out uefn.png
node scripts/dev/browser.cjs .devtmp/menu.html mine.png scripts/dev/render_page.js
python scripts/dev/compare_with_uefn.py uefn.png mine.png compare.png
```

`uefn_roundtrip_check.py` edits text, colours, transforms and the image of a copy,
has UEFN reload and compile it, then reads the values back through Epic's widget
analysis tool. UEFN's own log (`Saved/Logs/UnrealEditorFortnite.log`) is the place
to look when a property does not come back: it names the property and the byte
count it expected. Findings so far that only UEFN could show: source pixels and JPEG
thumbnails are stored blue-green-red; a new text needs a closing int32; the asset
registry rejects a package whose per-import bit count differs from its import table.

Generate a private, self-contained example with the host's Python:

```powershell
python scripts/export_umg_demo.py "C:/Project/Content/UI/WBP_Menu.uasset" ".devtmp/menu.html"
```

The HTML embeds the same renderer and decoded media as the plugin. Open it in a
browser; no local server is needed. It contains project artwork and should stay
local. `Reload` reloads this snapshot; rerun the exporter after changing assets.

## Develop

```bash
npm install
npm run build:ui
py scripts/build_zip.py
py scripts/release.py --publish --changelog "v1.0.0: initial extract from host"
```

Install/update only via **Settings → Store**.

Requires host app ≥ `min_app_version` in `plugin.json` (plugin-owned editor kinds +
plugin listener handler overlay).

## License

MIT. Copyright (c) 2026 Mindful Path Company, LLC. See [LICENSE](LICENSE).
