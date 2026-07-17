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


def _wire_native_drag_drop(window) -> None:
    """Let files dragged from Explorer resolve to real filesystem paths.

    WebView2 supports native file drop, but pywebview only resolves the real
    path when a DOM 'drop' listener is registered, and delivers it to this
    Python handler (never to browser JS). So we register the handler here,
    pull `pywebviewFullPath` off the dropped files, and hand the paths to the
    page, which drops them onto whichever class card the cursor was over.

    Wired on the `loaded` event so the document exists. Fails quietly if the
    binding isn't available — click-to-pick still covers adding files.
    """
    try:
        from webview.dom import DOMEventHandler

        def on_drop(event):
            files = (event or {}).get("dataTransfer", {}).get("files", []) or []
            paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
            if paths:
                import json

                window.evaluate_js(f"window.onFilesDropped({json.dumps(paths)})")

        # dragover must preventDefault or the browser refuses the drop; the drop
        # listener is what makes pywebview resolve the real paths.
        window.dom.document.on("dragover", DOMEventHandler(lambda e: None, prevent_default=True))
        window.dom.document.on("drop", DOMEventHandler(on_drop, prevent_default=True))
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

    # Wire native drag-and-drop once the page has loaded (the document must
    # exist before we attach the DOM handler). GUI_NODND=1 disables it.
    if os.environ.get("GUI_NODND") != "1":
        try:
            window.events.loaded += lambda: _wire_native_drag_drop(window)
        except Exception:
            pass

    webview.start(debug=debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
