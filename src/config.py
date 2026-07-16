"""Settings shared across the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The teacher's machine exposes the key as CLAUDE_API_KEY; the Anthropic SDK
# looks for ANTHROPIC_API_KEY, so we resolve explicitly and pass it in.
API_KEY_ENV_VARS = ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")

DEFAULT_MODEL = "claude-sonnet-5"


def resolve_api_key() -> str:
    for var in API_KEY_ENV_VARS:
        key = os.environ.get(var)
        if key:
            return key
    raise RuntimeError(
        "No API key found. Set the CLAUDE_API_KEY environment variable "
        "(ANTHROPIC_API_KEY is also accepted)."
    )


@dataclass
class Settings:
    input_dir: Path
    output_dir: Path
    year: str = "תשפו"
    model: str = DEFAULT_MODEL
    effort: str = "high"
    max_tokens: int = 8000
    ocr_engine: str = "claude"      # claude | tesseract | none
    ocr_dpi: int = 300
    max_pages: int = 12             # relevant pages sent to the model per student
    min_native_chars: int = 40      # below this a page counts as image-only
    use_cache: bool = True
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))
    only_classes: list[str] = field(default_factory=list)
    teachers: dict[str, str] = field(default_factory=dict)
