"""Decide which extracted rows a teacher should look at with her own eyes.

Combines two kinds of signal:

- **What the model said** — its ``confidence`` and its ``missing_info`` list.
- **Objective source signals the model can't hide** — mostly-scanned sources
  (so bad OCR is a real risk), visibly garbled text, and empty fields. The model
  can be confidently wrong on a bad scan; these catch that.

Produces a short, human list of reasons ("why to look") for the review report.
Deliberately conservative: a clean, high-confidence row raises nothing, so the
flags stay meaningful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# U+FFFD replacement char and lone control chars are OCR/text-decode wreckage.
_GARBLED_RE = re.compile("[�\x00-\x08\x0b\x0c\x0e-\x1f]")
_HEBREW_RE = re.compile("[֐-׿]")


@dataclass
class Quality:
    ocr_ratio: float = 0.0
    ocr_heavy: bool = False
    garbled: bool = False
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False


def _garbled_ratio(text: str) -> float:
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return 0.0
    return sum(1 for c in stripped if _GARBLED_RE.match(c)) / len(stripped)


def assess(record, excerpt: str, ocr_pages: int, total_pages: int) -> Quality:
    """`record` is a StudentRecord; `excerpt` is the source text it was built from."""
    q = Quality()

    q.ocr_ratio = (ocr_pages / total_pages) if total_pages else 0.0
    q.ocr_heavy = q.ocr_ratio >= 0.5 and ocr_pages > 0
    q.garbled = _garbled_ratio(excerpt) > 0.02

    # --- model's own signals ---
    if record.confidence == "low":
        q.reasons.append("המערכת אינה בטוחה בחילוץ (ביטחון נמוך)")
    elif record.confidence == "medium":
        q.reasons.append("ביטחון בינוני בחילוץ — כדאי לאמת")
    for item in record.missing_info:
        q.reasons.append(item)

    # --- objective source signals ---
    if q.ocr_heavy:
        q.reasons.append(
            f"רוב עמודי המקור נסרקו וזוהו אוטומטית (OCR, {ocr_pages}/{total_pages}) — "
            "כדאי להשוות מול הקובץ"
        )
    if q.garbled:
        q.reasons.append("טקסט המקור משובש חלקית — ייתכנו אי-דיוקים בחילוץ")
    if not record.difficulties.strip():
        q.reasons.append("עמודת הקשיים ריקה")
    if not record.accommodations.strip():
        q.reasons.append("עמודת ההמלצות ריקה")

    q.needs_review = bool(q.reasons)
    return q
