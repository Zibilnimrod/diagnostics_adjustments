"""Pick the pages worth sending to the model.

A diagnostic runs 15-30 pages, but almost everything the table needs sits in two
places: the identity header on page 1-2, and the 'סיכום והמלצות' block that
starts roughly two thirds in. The rest is mostly normed score tables, which are
expensive to send and add nothing. This module scores pages and keeps the good
ones.
"""

from __future__ import annotations

import re

# Headings that mark the summary / recommendations block.
STRONG_MARKERS: dict[str, float] = {
    "סיכום והמלצות": 10.0,
    "המלצות להתאמה בדרכי היבחנות": 10.0,
    "המלצות להתאמות בדרכי היבחנות": 10.0,
    "המלצות להתאמות": 9.0,
    "המלצות להתאמה": 9.0,
    "המלצות לבית הספר": 8.0,
    "המלצות לצוות": 8.0,
    "דרכי היבחנות": 7.0,
    "אבחנה": 6.0,
    "לסיכום": 6.0,
    "המלצות להורים": 4.0,
    "סיבת הפנייה": 4.0,
    "מטרות האבחון": 3.0,
}

WEAK_MARKERS: dict[str, float] = {
    "המלצות": 2.0,
    "סיכום": 1.5,
    "התאמות": 1.5,
    "קשיים": 1.0,
    "לקות": 1.0,
    "הפרעת קשב": 1.0,
}

# Identity block: name, ID, date of birth, class, examiner, exam date.
HEADER_PAGES = 2

_DIGIT_RE = re.compile(r"\d")
_HEBREW_WORD_RE = re.compile("[֐-׿]{2,}")


def _numeric_ratio(text: str) -> float:
    """Fraction of non-space characters that are digits.

    Score-table appendices run high here; prose runs low.
    """
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return 0.0
    return sum(1 for c in stripped if _DIGIT_RE.match(c)) / len(stripped)


def score_page(text: str) -> float:
    if not text.strip():
        return 0.0

    score = 0.0
    for marker, weight in STRONG_MARKERS.items():
        if marker in text:
            score += weight
    for marker, weight in WEAK_MARKERS.items():
        score += weight * min(text.count(marker), 3)

    # Penalise pages that are mostly normed-score tables.
    ratio = _numeric_ratio(text)
    if ratio > 0.35:
        score *= 0.25
    elif ratio > 0.2:
        score *= 0.6

    # A page with almost no Hebrew prose carries no usable narrative.
    if len(_HEBREW_WORD_RE.findall(text)) < 20:
        score *= 0.3

    return score


def select_relevant_pages(pages: list[str], max_pages: int = 12) -> list[int]:
    """Return 0-based page indices to send to the model, in document order."""
    total = len(pages)
    if total <= max_pages:
        return list(range(total))

    selected: set[int] = set(range(min(HEADER_PAGES, total)))

    scored = sorted(
        ((score_page(text), i) for i, text in enumerate(pages)),
        key=lambda pair: (-pair[0], pair[1]),
    )

    budget = max_pages - len(selected)
    for score, index in scored:
        if budget <= 0:
            break
        if score <= 0 or index in selected:
            continue
        selected.add(index)
        budget -= 1
        # Recommendations usually spill onto the next page.
        if budget > 0 and score >= 6.0 and index + 1 < total and index + 1 not in selected:
            selected.add(index + 1)
            budget -= 1

    # Nothing scored: fall back to the middle-to-late band, where summaries live.
    if len(selected) <= HEADER_PAGES:
        start = int(total * 0.5)
        for index in range(start, min(start + max_pages - len(selected), total)):
            selected.add(index)

    return sorted(selected)[:max_pages]


def build_excerpt(pages: list[str], indices: list[int]) -> str:
    """Join selected pages with page markers so the model can cite locations."""
    parts = []
    for index in indices:
        parts.append(f"===== עמוד {index + 1} =====\n{pages[index].strip()}")
    return "\n\n".join(parts)
