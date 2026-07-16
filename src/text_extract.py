"""Turn a diagnostic PDF into per-page text.

Most diagnostics arrive with a usable text layer, so we read that directly and
fall back to OCR only for the pages that come back empty (scans, image-only
pages). Results are cached on disk because OCR is the slow, paid part.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF

# The shared OCR library lives outside this project.
OCR_LIB_ROOT = Path(r"C:\work\common_infrastructures")
if str(OCR_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(OCR_LIB_ROOT))


class PageTextExtractor:
    def __init__(
        self,
        ocr_engine: str = "claude",
        api_key: str | None = None,
        dpi: int = 300,
        min_native_chars: int = 40,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ):
        self.ocr_engine = ocr_engine
        self.api_key = api_key
        self.dpi = dpi
        self.min_native_chars = min_native_chars
        self.cache_dir = cache_dir
        self.use_cache = use_cache and cache_dir is not None
        self._processor = None

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_path(self, pdf_path: Path) -> Path:
        stat = pdf_path.stat()
        fingerprint = f"{pdf_path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{self.ocr_engine}"
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.json"

    def _load_cached(self, pdf_path: Path) -> list[str] | None:
        if not self.use_cache:
            return None
        path = self._cache_path(pdf_path)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["pages"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _store_cached(self, pdf_path: Path, pages: list[str]) -> None:
        if not self.use_cache:
            return
        path = self._cache_path(pdf_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"source": str(pdf_path), "pages": pages}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # OCR fallback
    # ------------------------------------------------------------------

    def _get_processor(self):
        if self._processor is None:
            from ocr import OcrConfig, PdfOcr, TesseractEngine, ClaudeVisionEngine

            config = OcrConfig(language="heb", dpi=self.dpi)
            if self.ocr_engine == "claude":
                engine = ClaudeVisionEngine(config, api_key=self.api_key)
            elif self.ocr_engine == "tesseract":
                engine = TesseractEngine(config)
            else:
                raise ValueError(f"Unknown OCR engine: {self.ocr_engine}")
            self._processor = PdfOcr(engine)
        return self._processor

    def _ocr_pages(
        self, pdf_path: Path, indices: list[int], log=print
    ) -> dict[int, str]:
        processor = self._get_processor()
        results: dict[int, str] = {}
        total = len(indices)
        # Tesseract runs a couple of seconds per page, so announce each one —
        # a silent multi-page OCR looks exactly like a hang.
        for done, (page_num, image) in enumerate(
            processor.iter_pages(pdf_path, indices), 1
        ):
            log(f"      OCR page {page_num} ({done}/{total})")
            results[page_num - 1] = processor.engine.ocr_page(image)
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, pdf_path: Path, log=print) -> list[str]:
        cached = self._load_cached(pdf_path)
        if cached is not None:
            return cached

        doc = fitz.open(str(pdf_path))
        try:
            pages = [page.get_text() for page in doc]
        finally:
            doc.close()

        weak = [i for i, text in enumerate(pages) if len(text.strip()) < self.min_native_chars]
        if weak and self.ocr_engine != "none":
            where = " (local C:\\T_OCR)" if self.ocr_engine == "tesseract" else ""
            log(f"      {self.ocr_engine} OCR{where} on {len(weak)} image-only page(s)")
            try:
                for index, text in self._ocr_pages(pdf_path, weak, log=log).items():
                    pages[index] = text
            except Exception as exc:  # OCR is best-effort; native text still stands
                log(f"      OCR failed ({exc}); continuing with the text layer only")

        self._store_cached(pdf_path, pages)
        return pages
