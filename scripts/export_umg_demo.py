"""Export a private, self-contained HTML demo using the actual preview pipeline.

Run with the host's Python (Pillow and optionally pyooz installed). No server,
UEFN session, external URLs, or edits to the source project are needed.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent.parent / "UEFN-Ducky" / "ducky_app"))
sys.path.insert(0, str(ROOT))


def export_demo(widget: Path, output: Path) -> None:
    from backend.preview.umg_preview import build_preview, _media_dir

    result = build_preview(widget.resolve(), online=False)
    if not result.get("ok"):
        raise ValueError(result.get("error"))
    media = _media_dir()
    for asset in result["spec"]["assets"].values():
        url = asset.get("url")
        if not url:
            continue
        file = media / Path(urlparse(url).path).name
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".wav": "audio/wav"}[file.suffix]
        asset["url"] = f"data:{mime};base64," + base64.b64encode(file.read_bytes()).decode("ascii")
    html = (ROOT / "ui" / "umg.html").read_text(encoding="utf-8")
    engine = (ROOT / "ui" / "umg" / "engine.js").read_text(encoding="utf-8")
    html = html.replace('<script src="umg/engine.js"></script>', '<script>' + engine + '</script>')
    material = (ROOT / "ui" / "umg" / "material.js").read_text(encoding="utf-8")
    html = html.replace('<script src="umg/material.js"></script>', '<script>' + material + '</script>')
    bindings = (ROOT / "ui" / "umg" / "bindings.js").read_text(encoding="utf-8")
    html = html.replace('<script src="umg/bindings.js"></script>', '<script>' + bindings + '</script>')
    data = json.dumps(result, ensure_ascii=True).replace("<", "\\u003c")
    html = html.replace('</head>', '<script>window.__offlineDemo=' + data + ';</script></head>')
    html = html.replace('id="btn-details"', 'id="btn-details" hidden')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "media": result["media"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("widget", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_demo(args.widget, args.output)
