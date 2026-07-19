"""Walk the input tree: one folder per class, one PDF per student, one .docx out."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import quality, review_report
from .config import Settings, resolve_api_key
from .console import log as console_log
from .docx_writer import build_class_document
from .extractor import CallStats, RecordExtractor, StudentRecord
from .relevance import build_excerpt, select_relevant_pages
from .review_report import ClassReview, StudentReview
from .teachers import resolve_teacher
from .text_extract import PageTextExtractor


@dataclass
class _StudentOutcome:
    record: StudentRecord
    stats: CallStats
    excerpt: str
    source_path: Path
    ocr_pages: int
    total_pages: int


@dataclass
class ClassResult:
    folder_name: str
    records: list[StudentRecord]
    failures: list[tuple[str, str]]  # (filename, error)
    # {name: count} for children with more than one diagnostic file — kept as
    # separate rows for the teacher to merge by hand.
    duplicate_names: dict[str, int] = field(default_factory=dict)
    class_review: ClassReview | None = None
    docx_path: Path | None = None
    json_path: Path | None = None


def group_by_student(
    records: list[StudentRecord],
) -> tuple[list[StudentRecord], dict[str, int]]:
    """Put rows for the same child next to each other.

    Two diagnostic files for one child (e.g. a didactic eval and a neurological
    eval) each become their own row — we don't merge them, since merging across
    documents is error-prone and the teacher wants to do it by hand. But we keep
    those rows adjacent so they're easy to find and merge, and report which names
    are duplicated. First-appearance order is otherwise preserved.
    """
    order: list[str] = []
    groups: dict[str, list[StudentRecord]] = {}
    for record in records:
        key = record.student_name.strip()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    grouped = [record for key in order for record in groups[key]]
    duplicates = {key: len(groups[key]) for key in order if len(groups[key]) > 1}
    return grouped, duplicates


@dataclass
class RunTotals:
    """What the run actually cost, and how much caching saved."""

    students: int = 0
    from_cache: int = 0
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, stats: CallStats) -> None:
        self.students += 1
        if stats.cached:
            self.from_cache += 1
            return
        self.api_calls += 1
        self.input_tokens += stats.input_tokens
        self.output_tokens += stats.output_tokens
        self.cache_read_tokens += stats.cache_read_tokens
        self.cache_write_tokens += stats.cache_write_tokens

    def summary_lines(self) -> list[str]:
        lines = [
            f"Students:      {self.students} "
            f"({self.api_calls} via API, {self.from_cache} from local cache)"
        ]
        if self.api_calls:
            lines.append(
                f"Tokens:        {self.input_tokens:,} in / {self.output_tokens:,} out"
            )
            billed = self.cache_read_tokens + self.cache_write_tokens + self.input_tokens
            if self.cache_read_tokens:
                pct = 100 * self.cache_read_tokens / billed if billed else 0
                lines.append(
                    f"Prompt cache:  {self.cache_read_tokens:,} tokens read at ~10% price "
                    f"({pct:.0f}% of input), {self.cache_write_tokens:,} written"
                )
            elif self.cache_write_tokens:
                lines.append(
                    f"Prompt cache:  {self.cache_write_tokens:,} tokens written "
                    "(no reads yet — first run warms it)"
                )
        return lines


class Pipeline:
    def __init__(self, settings: Settings, log=console_log, progress=None):
        self.settings = settings
        self.log = log
        # Optional structured-event callback (the GUI's live checklist). Events
        # are dicts: file_start / file_done, with class + file names. The text
        # log above stays the human-readable channel; this one is machine-read.
        self.progress = progress or (lambda ev: None)
        self.totals = RunTotals()
        self.report_path: Path | None = None
        api_key = resolve_api_key()
        self.text_extractor = PageTextExtractor(
            ocr_engine=settings.ocr_engine,
            api_key=api_key,
            dpi=settings.ocr_dpi,
            min_native_chars=settings.min_native_chars,
            cache_dir=settings.cache_dir,
            use_cache=settings.use_cache,
        )
        self.record_extractor = RecordExtractor(settings, api_key)

    # ------------------------------------------------------------------

    def _class_folders(self) -> list[Path]:
        folders = sorted(p for p in self.settings.input_dir.iterdir() if p.is_dir())
        if self.settings.only_classes:
            wanted = set(self.settings.only_classes)
            folders = [f for f in folders if f.name in wanted]
        return folders

    def _student_record(self, pdf_path: Path, folder_name: str) -> _StudentOutcome:
        extracted = self.text_extractor.extract(pdf_path, log=self.log)
        pages = extracted.pages
        indices = select_relevant_pages(pages, max_pages=self.settings.max_pages)
        excerpt = build_excerpt(pages, indices)
        record, stats = self.record_extractor.extract(
            excerpt=excerpt,
            filename=pdf_path.name,
            class_hint=folder_name,
        )
        if stats.cached:
            self.log(f"      {len(pages)} page(s); cached, no API call")
        else:
            self.log(
                f"      {len(pages)} page(s); sent "
                f"{len(indices)} ({', '.join(str(i + 1) for i in indices)}); "
                f"{stats.input_tokens:,} in / {stats.output_tokens:,} out"
                + (f", {stats.cache_read_tokens:,} cached" if stats.cache_read_tokens else "")
            )
        return _StudentOutcome(
            record=record,
            stats=stats,
            excerpt=excerpt,
            source_path=pdf_path,
            ocr_pages=extracted.ocr_pages,
            total_pages=extracted.total_pages,
        )

    def _build_review(
        self,
        class_name: str,
        teacher: str | None,
        outcomes: list[_StudentOutcome],
        failures: list[tuple[str, str]],
    ) -> ClassReview:
        reviews: list[StudentReview] = []
        for o in outcomes:
            rec = o.record
            q = quality.assess(rec, o.excerpt, o.ocr_pages, o.total_pages)
            reviews.append(
                StudentReview(
                    student_name=rec.student_name,
                    filename=o.source_path.name,
                    source_path=str(o.source_path),
                    confidence=rec.confidence,
                    reasons=q.reasons,
                    difficulties=rec.difficulties,
                    accommodations=rec.accommodations,
                    diagnosis_type=rec.diagnosis_type,
                    # Only carry a source snippet for flagged rows (keeps the
                    # report lean and avoids copying clean students' text around).
                    snippet=self._snippet(o.excerpt) if q.needs_review else "",
                    needs_review=q.needs_review,
                    score=q.score,
                    band=q.band,
                )
            )
        return ClassReview(
            class_name=class_name, teacher=teacher, reviews=reviews, failures=failures
        )

    @staticmethod
    def _snippet(excerpt: str, limit: int = 350) -> str:
        text = re.sub(r"=====.*?=====", " ", excerpt)  # drop page markers
        text = " ".join(text.split())
        return text[:limit] + ("…" if len(text) > limit else "")

    def process_class(self, folder: Path, write_docx: bool = True) -> ClassResult:
        pdfs = sorted(p for p in folder.glob("*.pdf") if not p.name.startswith("~$"))
        self.log(f"\n[{folder.name}] {len(pdfs)} diagnostic file(s)")

        outcomes: list[_StudentOutcome] = []
        failures: list[tuple[str, str]] = []

        for pdf_path in pdfs:
            self.log(f"   - {pdf_path.name}")
            self.progress({"type": "file_start", "class": folder.name, "file": pdf_path.name})
            try:
                outcome = self._student_record(pdf_path, folder.name)
            except Exception as exc:
                self.log(f"      FAILED: {exc}")
                failures.append((pdf_path.name, str(exc)))
                self.progress(
                    {
                        "type": "file_done",
                        "class": folder.name,
                        "file": pdf_path.name,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                continue
            self.totals.add(outcome.stats)
            outcomes.append(outcome)
            record = outcome.record
            note = ""
            if record.missing_info:
                note = f"  (missing: {', '.join(record.missing_info)})"
            self.log(f"      -> {record.student_name} [{record.confidence}]{note}")
            self.progress(
                {
                    "type": "file_done",
                    "class": folder.name,
                    "file": pdf_path.name,
                    "ok": True,
                    "student": record.student_name,
                }
            )

        # Keep same-child rows adjacent so multiple diagnostics are easy to merge.
        records, duplicates = group_by_student([o.record for o in outcomes])
        for name, count in duplicates.items():
            self.log(f"   ! {count} diagnostic files for {name} — adjacent rows, merge by hand")

        # Reorder outcomes to match the grouped records (by identity) so the
        # review report and the table stay in lockstep.
        by_id = {id(o.record): o for o in outcomes}
        ordered = [by_id[id(r)] for r in records]

        teacher = resolve_teacher(folder, self.settings.teachers)
        class_review = self._build_review(folder.name, teacher, ordered, failures)
        for r in class_review.flagged:
            self.log(f"   ⚠ {r.student_name} — לבדיקה: {'; '.join(r.reasons)}")

        result = ClassResult(
            folder_name=folder.name,
            records=records,
            failures=failures,
            duplicate_names=duplicates,
            class_review=class_review,
        )

        # ".records.json" rather than plain ".json" so the extracted student data
        # is ignorable by pattern wherever -o points. See .gitignore.
        json_path = self.settings.output_dir / f"{folder.name}.records.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                {
                    "class": folder.name,
                    "students": [r.model_dump() for r in records],
                    "failures": [{"file": f, "error": e} for f, e in failures],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result.json_path = json_path

        if write_docx and records:
            if not teacher:
                self.log(
                    "      no teacher name — add teacher_name.txt to "
                    f"{folder.name}\\ to fill the 'מחנכת:' line"
                )
            filename = f"טבלת התאמות כיתה {folder.name} {self.settings.year}.docx"
            result.docx_path = build_class_document(
                records=records,
                folder_name=folder.name,
                teacher=teacher,
                output_path=self.settings.output_dir / filename,
            )
            self.log(f"   wrote {result.docx_path.name}")

        return result

    def run(self, write_docx: bool = True) -> list[ClassResult]:
        folders = self._class_folders()
        if not folders:
            raise RuntimeError(
                f"No class folders found under {self.settings.input_dir}. "
                "Expected one sub-folder per class (e.g. א2)."
            )
        results = [self.process_class(folder, write_docx=write_docx) for folder in folders]

        # One combined review report per run, covering every class.
        class_reviews = [r.class_review for r in results if r.class_review]
        if class_reviews:
            self.report_path = review_report.write_report(
                class_reviews, self.settings.year, self.settings.output_dir
            )
            flagged = sum(len(cr.flagged) for cr in class_reviews)
            self.log(
                f"\nדו\"ח בקרה: {self.report_path.name} "
                f"({flagged} תלמידים לבדיקה)" if flagged else
                f"\nדו\"ח בקרה: {self.report_path.name} (הכול נחזה בביטחון גבוה)"
            )
        return results
