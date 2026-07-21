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
SPLASH_IMAGE = BASE / "inputs" / "SplashScreen" / "LoolaAnim.PNG"
SPLASH_SECONDS = 8
WINDOW_ICON = BASE / "inputs" / "SplashScreen" / "LoolaAnim.ico"

# Taskbar identity. Without one, Windows groups the window under pythonw.exe.
APP_ID = "LoolaDiagnostics.AdjustmentsTable"

WINDOW_TITLE = "  טבלת התאמות — מחולל דוחות כיתתיים לפונומורפוגית הנחמדת"


def _show_splash(seconds: int = SPLASH_SECONDS) -> None:
    """Show the splash image in a frameless, centred window, then close it.

    Runs before the webview window is created, so it blocks for `seconds` and
    the app then continues as usual. Fails quietly (no splash) if the image or
    tkinter isn't available — never a reason to stop the app from starting.
    """
    if not SPLASH_IMAGE.exists():
        return
    try:
        import tkinter as tk

        from PIL import Image, ImageTk

        image = Image.open(SPLASH_IMAGE)
        # The PNG has an alpha channel; flatten it onto white so the corners
        # don't render as black on the Tk canvas.
        if image.mode in ("RGBA", "LA", "P"):
            flat = Image.new("RGB", image.size, "white")
            image = image.convert("RGBA")
            flat.paste(image, mask=image.split()[-1])
            image = flat

        root = tk.Tk()
        root.overrideredirect(True)  # no title bar / borders
        root.attributes("-topmost", True)
        photo = ImageTk.PhotoImage(image, master=root)
        w, h = image.size
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")
        tk.Label(root, image=photo, borderwidth=0, highlightthickness=0).pack()

        root.after(int(seconds * 1000), root.destroy)
        root.mainloop()
    except Exception:
        pass


def _set_taskbar_icon(hwnd: int) -> None:
    """Point the taskbar button at our icon.

    WM_SETICON alone only fixes the title bar: because we run under
    pythonw.exe, the taskbar keeps drawing that interpreter's icon. The
    documented override is the window's shell property store — the taskbar
    reads System.AppUserModel.RelaunchIconResource from it. The relaunch
    command and display name go with it so a pinned button still works.

    Plain ctypes COM: SHGetPropertyStoreForWindow hands back an
    IPropertyStore, whose vtable is [QueryInterface, AddRef, Release,
    GetCount, GetAt, GetValue, SetValue, Commit].
    """
    import ctypes
    from ctypes import POINTER, byref, c_void_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("d1", wintypes.DWORD),
            ("d2", wintypes.WORD),
            ("d3", wintypes.WORD),
            ("d4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.WORD),
            ("r1", wintypes.WORD),
            ("r2", wintypes.WORD),
            ("r3", wintypes.WORD),
            ("value", c_void_p),
            ("pad", c_void_p),
        ]

    def _guid(d1, d2, d3, rest):
        return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))

    # The System.AppUserModel.* property set, and IID_IPropertyStore.
    APP_USER_MODEL = _guid(0x9F4C2855, 0x9F79, 0x4B39, [0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3])
    IID_PROPERTY_STORE = _guid(0x886D8EEB, 0x8CF2, 0x4446, [0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99])

    ctypes.windll.ole32.CoInitialize(None)
    store = c_void_p()
    hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(
        wintypes.HWND(hwnd), byref(IID_PROPERTY_STORE), byref(store)
    )
    if hr or not store:
        return

    vtbl = ctypes.cast(store, POINTER(POINTER(c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[2])
    set_value = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)
    )(vtbl[6])
    commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p)(vtbl[7])

    properties = {
        5: APP_ID,  # AppUserModel.ID
        2: f'"{sys.executable}" "{Path(__file__).resolve()}"',  # RelaunchCommand
        3: f"{WINDOW_ICON},0",  # RelaunchIconResource
        4: "טבלת התאמות",  # RelaunchDisplayNameResource
    }
    try:
        for pid, text in properties.items():
            key = PROPERTYKEY(APP_USER_MODEL, pid)
            buffer = ctypes.create_unicode_buffer(text)
            value = PROPVARIANT(31, 0, 0, 0, ctypes.cast(buffer, c_void_p), None)  # VT_LPWSTR
            set_value(store, byref(key), byref(value))
        commit(store)
    finally:
        release(store)


def _set_window_icon(window) -> None:
    """Put our own icon on the window and its taskbar button.

    On Windows pywebview copies the icon out of `sys.executable`, so the
    window inherits the generic pythonw icon. We override it by loading the
    .ico ourselves and sending WM_SETICON to the window handle (small = the
    title-bar icon, big = Alt-Tab), then fix the taskbar button separately.
    Fails quietly — a wrong icon is never worth failing to open the app.
    """
    if not WINDOW_ICON.exists() or sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = None
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is not None:
            hwnd = int(str(handle.ToInt64())) if hasattr(handle, "ToInt64") else int(handle)
        if not hwnd:
            hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return

        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x0010, 1, 0x0080
        for size, which in ((16, 0), (32, 1)):  # 0 = ICON_SMALL, 1 = ICON_BIG
            hicon = user32.LoadImageW(
                None, str(WINDOW_ICON), IMAGE_ICON, size, size, LR_LOADFROMFILE
            )
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, which, hicon)

        _set_taskbar_icon(hwnd)
    except Exception:
        pass


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

    # Give the process its own taskbar identity; otherwise Windows groups the
    # window under pythonw.exe and shows that program's icon instead of ours.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    _show_splash()

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

    # The window must exist before its icon can be replaced.
    try:
        window.events.shown += lambda: _set_window_icon(window)
    except Exception:
        pass

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
