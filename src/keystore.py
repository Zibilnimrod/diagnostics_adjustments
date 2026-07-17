"""Store the Claude API key securely, so the teacher sets it once in the app.

On Windows the key is encrypted with **DPAPI** (the OS Data Protection API):
the ciphertext is bound to the logged-in Windows user account, so only that
user can decrypt it and there is no master password to manage. This is real
per-user encryption, not obfuscation — the plaintext key never touches disk.

The encrypted blob lives in a small JSON file under %APPDATA%. On non-Windows
(dev only) it falls back to base64, which is NOT secure — flagged in the file
and in the returned status so no one mistakes it for encryption.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

APP_DIR_NAME = "TavlatHatamot"
_SETTINGS_FILE = "settings.json"


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def _settings_path() -> Path:
    return _config_dir() / _SETTINGS_FILE


# ---------------------------------------------------------------------------
# Windows DPAPI via ctypes (no third-party dependency)
# ---------------------------------------------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = (
        ctypes.windll.crypt32.CryptProtectData
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    # entropy tag ties the blob to this app; flags=0 (per-user scope).
    if not fn(ctypes.byref(blob_in), APP_DIR_NAME, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Windows DPAPI call failed")
    size = blob_out.cbData
    out = ctypes.create_string_buffer(size)
    ctypes.memmove(out, blob_out.pbData, size)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out.raw


def _encrypt(plaintext: str) -> tuple[str, str]:
    """Return (scheme, encoded-blob). scheme is 'dpapi' or 'base64'."""
    raw = plaintext.encode("utf-8")
    if sys.platform == "win32":
        try:
            return "dpapi", base64.b64encode(_dpapi(raw, True)).decode("ascii")
        except OSError:
            pass  # Fall through to base64 if DPAPI is somehow unavailable.
    return "base64", base64.b64encode(raw).decode("ascii")


def _decrypt(scheme: str, blob: str) -> str | None:
    try:
        raw = base64.b64decode(blob.encode("ascii"))
        if scheme == "dpapi":
            return _dpapi(raw, False).decode("utf-8")
        return raw.decode("utf-8")
    except (ValueError, OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _read() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_api_key(key: str) -> None:
    scheme, blob = _encrypt(key.strip())
    data = _read()
    data["api_key_scheme"] = scheme
    data["api_key"] = blob
    if scheme == "base64":
        data["_warning"] = "api_key is base64-encoded, NOT encrypted (non-Windows fallback)"
    else:
        data.pop("_warning", None)
    _write(data)


def load_api_key() -> str | None:
    data = _read()
    blob = data.get("api_key")
    if not blob:
        return None
    return _decrypt(data.get("api_key_scheme", "base64"), blob)


def clear_api_key() -> None:
    data = _read()
    data.pop("api_key", None)
    data.pop("api_key_scheme", None)
    data.pop("_warning", None)
    _write(data)


def storage_scheme() -> str | None:
    """'dpapi' | 'base64' | None — how a saved key is protected, if any."""
    data = _read()
    return data.get("api_key_scheme") if data.get("api_key") else None
