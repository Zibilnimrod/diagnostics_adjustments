"""Settings shared across the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The teacher's machine exposes the key as CLAUDE_API_KEY; the Anthropic SDK
# looks for ANTHROPIC_API_KEY, so we resolve explicitly and pass it in.
API_KEY_ENV_VARS = ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-sonnet-5"


def api_key_source() -> str | None:
    """Where the key would come from: 'env' | 'saved' | None. No secret returned."""
    for var in API_KEY_ENV_VARS:
        if os.environ.get(var):
            return "env"
    from . import keystore

    return "saved" if keystore.load_api_key() else None


def resolve_api_key() -> str:
    # Env vars win (a power user can override); otherwise the key the teacher
    # saved in the app, decrypted from the OS keystore.
    for var in API_KEY_ENV_VARS:
        key = os.environ.get(var)
        if key:
            return key

    from . import keystore

    saved = keystore.load_api_key()
    if saved:
        return saved

    raise RuntimeError(
        "לא הוגדר מפתח API. הזינו מפתח בהגדרות התוכנה, "
        "או הגדירו את משתנה הסביבה CLAUDE_API_KEY."
    )


@dataclass
class Settings:
    input_dir: Path
    output_dir: Path
    year: str = "תשפו"
    model: str = DEFAULT_MODEL
    effort: str = "high"
    max_tokens: int = 8000
    # Local Tesseract (C:\T_OCR) by default — no tokens, no network for scans.
    ocr_engine: str = "tesseract"   # tesseract | claude | none
    ocr_dpi: int = 300
    max_pages: int = 12             # relevant pages sent to the model per student
    min_native_chars: int = 40      # below this a page counts as image-only
    use_cache: bool = True
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))
    only_classes: list[str] = field(default_factory=list)
    teachers: dict[str, str] = field(default_factory=dict)
