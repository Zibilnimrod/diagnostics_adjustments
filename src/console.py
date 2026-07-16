"""Make Hebrew print correctly on a Windows console.

Two separate problems, both of which have to be fixed or Hebrew is unusable:

1. **Encoding.** A default Windows console runs codepage 862 (DOS Hebrew) or
   437. cp862 encodes most Hebrew letters but has no en dash, curly quotes, or
   similar — and diagnostic text is full of them, so a run dies partway through
   with UnicodeEncodeError. We switch the console to UTF-8 and, as a belt-and-
   braces measure, reconfigure the Python streams with errors="replace" so a
   stray character can degrade a log line but never crash the run.

2. **Glyphs.** The console's default raster font ("Terminal") has no Hebrew
   glyphs, so Hebrew renders as boxes even once the encoding is right. We switch
   the font to one that covers Hebrew. This affects the current console window
   only — it is not a persistent system change.

3. **Direction.** The Windows console paints characters in memory order and does
   not implement the Unicode bidi algorithm, so Hebrew comes out reversed. If
   python-bidi is installed we reorder log lines to visual order on the way out.

   This is a **display-only** transform, and deliberately narrow: it is applied
   by `log()` alone. The .docx, the JSON, the filenames, and the text sent to
   the API all keep logical order — reordering any of those would corrupt them.
   It is also skipped when stdout is redirected to a file, where logical order
   is what a text editor expects.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

UTF8_CODEPAGE = 65001
_STD_OUTPUT_HANDLE = -11

# Monospace fonts shipped with Windows that actually contain Hebrew glyphs.
# Consolas — the usual console default — does NOT, which is the common cause of
# the boxes.
HEBREW_CAPABLE_FONTS = ("Courier New", "Lucida Console")


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("nFont", wintypes.DWORD),
        ("dwFontSize", _COORD),
        ("FontFamily", ctypes.c_uint),
        ("FontWeight", ctypes.c_uint),
        ("FaceName", ctypes.c_wchar * 32),
    ]


def _fix_stream_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # Redirected to something that isn't a reconfigurable stream.


def _fix_codepage() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        return bool(kernel32.SetConsoleOutputCP(UTF8_CODEPAGE)) and bool(
            kernel32.SetConsoleCP(UTF8_CODEPAGE)
        )
    except (AttributeError, OSError):
        return False


def _fix_font() -> bool:
    """Switch to a Hebrew-capable console font, preserving the current size."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)

        font = _CONSOLE_FONT_INFOEX()
        font.cbSize = ctypes.sizeof(_CONSOLE_FONT_INFOEX)
        if not kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(font)):
            return False  # Not attached to a real console (piped, redirected, IDE).

        if font.FaceName in HEBREW_CAPABLE_FONTS:
            return True

        # A raster font reports height 0; give it a sane default so the console
        # does not end up with an unreadable font size.
        if font.dwFontSize.Y <= 0:
            font.dwFontSize.X, font.dwFontSize.Y = 0, 16

        font.FaceName = HEBREW_CAPABLE_FONTS[0]
        font.FontFamily = 54  # FF_MODERN | TMPF_TRUETYPE | TMPF_VECTOR
        font.FontWeight = 400
        return bool(kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(font)))
    except (AttributeError, OSError):
        return False


# Set by setup_console(); log() is a no-op transform until then.
_reorder_for_display = False


def _bidi_available() -> bool:
    try:
        import bidi.algorithm  # noqa: F401
    except ImportError:
        return False
    return True


def _should_reorder() -> bool:
    """Only reorder for a real Windows console that won't do bidi itself."""
    if sys.platform != "win32" or not _bidi_available():
        return False
    try:
        return sys.stdout.isatty()  # False when piped/redirected to a file.
    except (AttributeError, ValueError):
        return False


def display(text: str) -> str:
    """Logical order -> visual order, for a console that can't do bidi itself."""
    if not _reorder_for_display or not text:
        return text
    from bidi.algorithm import get_display

    # base_dir="L" keeps the line's LTR skeleton — indentation, "->", "[high]",
    # page numbers — where it belongs, and reorders only the Hebrew runs.
    try:
        return get_display(text, base_dir="L")
    except Exception:
        return text  # Never let a cosmetic transform break a run.


def log(message: str = "") -> None:
    """print() for anything that may contain Hebrew."""
    print(display(str(message)))


def setup_console(fix_font: bool = True, reorder_hebrew: bool = True) -> None:
    """Prepare the console for Hebrew output. Safe to call on any platform."""
    global _reorder_for_display
    if sys.platform == "win32":
        _fix_codepage()
        if fix_font:
            _fix_font()
    _fix_stream_encoding()
    _reorder_for_display = reorder_hebrew and _should_reorder()
