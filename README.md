# UAsset Preview (UEFN-Ducky Store plugin)

Preview `.uasset` / `.umap` assets (thumbnails, materials, textures, StaticMesh 3D)
and standalone 3D model files (`.fbx`, `.glb`, …) inside the UEFN-Ducky panel.

Without this plugin installed, those files open as binary with an **Open Store**
link (plus View raw hex).

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
