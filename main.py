"""Build 'טבלת התאמות' tables from student diagnostic PDFs.

    python main.py inputs/diagnostics -o output

Each sub-folder of the input directory is a class; each PDF in it is a student
and becomes one row in that class's table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.config import DEFAULT_MODEL, Settings
from src.console import setup_console
from src.pipeline import Pipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Produce one טבלת התאמות .docx per class from diagnostic PDFs.",
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="inputs/diagnostics",
        type=Path,
        help="Directory holding one sub-folder per class (default: inputs/diagnostics)",
    )
    parser.add_argument(
        "-o", "--output-dir", default="output", type=Path,
        help="Where to write the .docx and .json files (default: output)",
    )
    parser.add_argument(
        "--year", default="תשפו", help="School year in the filename (default: תשפו)"
    )
    parser.add_argument(
        "--classes", nargs="*", default=[],
        help="Only process these class folders (default: all)",
    )
    parser.add_argument(
        "--teachers", type=Path,
        help='JSON file mapping class folder to teacher, e.g. {"א2": "שרה כהן"}',
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"(default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
        help="Model effort level (default: high)",
    )
    parser.add_argument(
        "--ocr-engine", default="claude", choices=["claude", "tesseract", "none"],
        help="Engine for pages with no text layer (default: claude)",
    )
    parser.add_argument(
        "--max-pages", type=int, default=12,
        help="Max pages per diagnostic sent to the model (default: 12)",
    )
    parser.add_argument(
        "--json-only", action="store_true", help="Extract data but skip writing .docx"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Ignore the local cache: re-OCR and re-call the model for every student",
    )
    parser.add_argument(
        "--keep-console-font", action="store_true",
        help="Don't switch the console font (Hebrew may show as boxes)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Must happen before anything prints Hebrew: a default Windows console is on
    # codepage 862 with a font that has no Hebrew glyphs.
    setup_console(fix_font=not args.keep_console_font)

    if not args.input_dir.is_dir():
        print(f"Input directory not found: {args.input_dir}", file=sys.stderr)
        return 1

    teachers: dict[str, str] = {}
    if args.teachers:
        teachers = json.loads(args.teachers.read_text(encoding="utf-8"))

    settings = Settings(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        year=args.year,
        model=args.model,
        effort=args.effort,
        ocr_engine=args.ocr_engine,
        max_pages=args.max_pages,
        use_cache=not args.no_cache,
        only_classes=args.classes,
        teachers=teachers,
    )

    pipeline = Pipeline(settings)
    try:
        results = pipeline.run(write_docx=not args.json_only)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    total = sum(len(r.records) for r in results)
    failures = [(r.folder_name, f) for r in results for f in r.failures]

    print(f"\nDone: {total} student(s) across {len(results)} class(es) -> {args.output_dir}")
    for line in pipeline.totals.summary_lines():
        print(f"  {line}")
    for result in results:
        needs_review = [
            r.student_name for r in result.records if r.confidence != "high" or r.missing_info
        ]
        if needs_review:
            print(f"  [{result.folder_name}] review by hand: {', '.join(needs_review)}")
    if failures:
        print(f"\n{len(failures)} file(s) failed:", file=sys.stderr)
        for folder_name, (filename, error) in failures:
            print(f"  [{folder_name}] {filename}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
