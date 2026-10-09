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

## Next release: ship compiled

This plugin still ships its Python source on the Store. Its next release has to ship compiled and signed, the way Ducky Account and Roguelike do:

1. Give `scripts/release.py` and `scripts/build_zip.py` the compiled build from `uefn-plugin-account` (`build_compiled_zip`, upload by ticket, `--plain` only as an escape hatch).
2. Bump `version` and set `min_app_version` to `1.2.356` or newer.
3. Publish, then check the download with the start-up license check (signature, id and version, compiled, team access), not only the signature.
4. The Store must hold the version back from apps older than `min_app_version`. Until it does, older apps install a build they can't run.

For this plugin:

- `listener/` runs in UEFN's own Python and stays readable source; only the Ducky-side backend compiles.

Remove this section once a compiled version is live.

## License

MIT. Copyright (c) 2026 Mindful Path Company, LLC. See [LICENSE](LICENSE).
