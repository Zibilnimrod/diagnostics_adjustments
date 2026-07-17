"""Desktop launcher — the double-click entry point for the teacher's GUI.

An embedded webview window (pywebview, using the WebView2 runtime already on
Windows) shows the HTML front-end and bridges it to the Python `Api`. No
browser, no local server, no port — one window that behaves like a native app.

Run:
    python gui_app.py

Package to a single .exe later with PyInstaller (see docs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import webview

from src.config import DEFAULT_MODEL, Settings
from src.gui_api import Api, GuiPaths

# When frozen by PyInstaller the web assets sit next to the executable in the
# bundle; in dev they're beside this file.
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
WEB_INDEX = BASE / "gui" / "web" / "index.html"

WINDOW_TITLE = "טבלת התאמות — מחולל דוחות כיתתיים"


def build_api() -> Api:
    project = Path(__file__).parent
    paths = GuiPaths(
        diagnostics_root=project / "inputs" / "diagnostics",
        output_root=project / "output",
    )
    settings = Settings(
        input_dir=paths.diagnostics_root,
        output_dir=paths.output_root,
        model=DEFAULT_MODEL,
    )
    return Api(paths, settings)


def _wire_native_drag_drop(window, api: Api) -> None:
    """Best-effort: let files dragged from Explorer resolve to real paths.

    pywebview only exposes dropped-file paths through a Python-side DOM handler,
    so we attach one to the document. If the binding isn't available in this
    pywebview build it fails quietly — the click-to-pick path still works.
    """
    try:
        from webview.dom import DOMEventHandler

        def on_drop(event):
            files = (event or {}).get("dataTransfer", {}).get("files", []) or []
            paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
            if paths:
                import json

                window.evaluate_js(f"window.onFilesDropped({json.dumps(paths)})")

        # prevent_default lets the drop reach our handler instead of navigating.
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
    except Exception:
        pass


def main() -> int:
    if not WEB_INDEX.exists():
        print(f"UI assets not found at {WEB_INDEX}", file=sys.stderr)
        return 1

    api = build_api()
    window = webview.create_window(
        WINDOW_TITLE,
        str(WEB_INDEX),
        js_api=api,
        width=1080,
        height=760,
        min_size=(720, 560),
        text_select=False,
    )
    api._window = window

    # GUI_DEBUG=1 opens devtools (F12) so JS errors are visible while iterating.
    debug = os.environ.get("GUI_DEBUG") == "1"

    # Native drag-from-Explorer is opt-in (GUI_DRAGDROP=1): its pywebview DOM
    # wiring is fragile and untested on this machine, and click-to-pick already
    # covers adding files. Enable it only once the base app is confirmed stable.
    startup = None
    if os.environ.get("GUI_DRAGDROP") == "1":
        startup = lambda: _wire_native_drag_drop(window, api)  # noqa: E731

    webview.start(startup, debug=debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
