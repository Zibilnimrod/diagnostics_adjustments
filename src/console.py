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


def setup_console(fix_font: bool = True) -> None:
    """Prepare the console for Hebrew output. Safe to call on any platform."""
    if sys.platform == "win32":
        _fix_codepage()
        if fix_font:
            _fix_font()
    _fix_stream_encoding()
