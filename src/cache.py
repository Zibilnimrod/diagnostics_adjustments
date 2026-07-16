"""A small JSON-on-disk cache.

Two things get cached, for different reasons:

- **Page text** (`text_extract.py`) — avoids re-running OCR, which is slow and
  paid.
- **Extraction results** (`extractor.py`) — avoids re-calling the model for a
  student whose diagnostic and prompt have not changed. This is the big saver:
  re-running a class to regenerate a .docx costs nothing.

Keys are content-derived, so anything that would change the answer — the PDF,
the prompt text, the model — misses the cache automatically. There is no
version number to remember to bump.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def make_key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")  # Separator, so ("ab","c") != ("a","bc").
    return digest.hexdigest()[:24]


class JsonDiskCache:
    def __init__(self, root: Path, namespace: str, enabled: bool = True):
        self.dir = Path(root) / namespace
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.misses += 1  # Corrupt entry: treat as a miss and overwrite later.
            return None
        self.hits += 1
        return value

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(key))  # Atomic: no half-written cache entries.
