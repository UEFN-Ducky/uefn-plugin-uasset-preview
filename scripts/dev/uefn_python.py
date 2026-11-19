"""Dev tool: run a Python file inside the open UEFN through the Ducky listener.

    python scripts/dev/uefn_python.py <code.py> [--wait SECONDS]

Run with the host app's Python (``ducky_app`` is put on the path here). The code
assigns ``result``; it is printed as JSON. Exit code 2 when UEFN is not reachable.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent.parent / "UEFN-Ducky" / "ducky_app"))


def main() -> int:
    from backend.bridge.client import _post_command_to_listener
    from frontend.settings import PANEL_LISTENER_PORT

    args = sys.argv[1:]
    wait = float(args[args.index("--wait") + 1]) if "--wait" in args else 0.0
    code = Path(args[0]).read_text(encoding="utf-8")
    deadline = time.monotonic() + wait
    while True:
        try:
            _post_command_to_listener(PANEL_LISTENER_PORT, "ping", {}, timeout=3.0)
            break
        except Exception as exc:  # noqa: BLE001 - offline until the editor finished loading
            if time.monotonic() >= deadline:
                print(json.dumps({"offline": str(exc)[:200]}))
                return 2
            time.sleep(5)
    out = _post_command_to_listener(PANEL_LISTENER_PORT, "execute_python", {"code": code}, timeout=120.0)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
