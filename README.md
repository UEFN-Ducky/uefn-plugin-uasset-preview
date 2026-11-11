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
