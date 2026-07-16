# טבלת התאמות — class accommodation tables from diagnostics

Reads a folder of student diagnostic PDFs and produces one **טבלת התאמות** Word
table per class, using the Claude API to summarise each diagnostic into a row.

- One input sub-folder = one class = one output `.docx`
- One PDF in that folder = one student = one row

## Setup

```bash
pip install -r requirements.txt
```

The API key is read from the `CLAUDE_API_KEY` environment variable
(`ANTHROPIC_API_KEY` also works).

## Usage

```bash
python main.py inputs/diagnostics -o output
```

Input layout — the folder name is the class:

```text
inputs/diagnostics/
├── א2/
│   └── דני לוי.pdf
├── ג1/
│   ├── מאיה כהן.pdf
│   └── איתי שגב.pdf
└── ד2/
    └── נועה ברק.pdf
```

Output, per class:

```text
output/
├── טבלת התאמות כיתה א2 תשפו.docx   ← the table
└── א2.records.json                  ← the same data, for review or re-runs
```

### Common options

| Option | Purpose |
| --- | --- |
| `--classes א2 ג1` | Only process these class folders |
| `--teachers teachers.json` | Override the "מחנכת:" line in bulk — `{"א2": "שרה כהן"}` |
| `--year תשפז` | School year in the output filename (default `תשפו`) |
| `--json-only` | Extract the data but skip writing `.docx` |
| `--max-pages 12` | Pages per diagnostic sent to the model |
| `--ocr-engine` | `claude` (default), `tesseract`, or `none` |
| `--no-cache` | Ignore the local cache and re-do everything |
| `--keep-console-font` | Don't touch the console font (see below) |

## Teacher names

The teacher's name is not in any diagnostic, so it has to be supplied. Put a
**`teacher_name.txt`** in the class folder containing just the name:

```text
inputs/diagnostics/
├── א2/
│   ├── teacher_name.txt     ← contains: שרה כהן
│   └── דני לוי.pdf
└── ג1/
    ├── teacher_name.txt     ← contains: רונית לוי
    └── ...
```

Adding a class is then: make the folder, drop in the PDFs, write one line.
Details that are handled for you:

- Save it from Notepad however you like — UTF-8, UTF-8 with BOM, or "ANSI"
  (cp1255) all read correctly.
- `מחנכת.txt` works as a filename too.
- Blank lines are skipped, and a `#` line is treated as a comment.
- If the file is missing, the run says so and leaves a blank to fill in Word:

  ```text
  no teacher name — add teacher_name.txt to ד2\ to fill the 'מחנכת:' line
  ```

`--teachers teachers.json` still works and takes precedence, for setting many
classes at once or overriding for a single run.

## Hebrew in the console

A default Windows console runs **codepage 862** with a raster font, which breaks
Hebrew two different ways: the font has no Hebrew glyphs (so text shows as
boxes `▯▯▯`), and cp862 cannot encode characters like the en dash `–` that
diagnostic text is full of — which crashed runs part-way through with
`UnicodeEncodeError`.

`main.py` calls `setup_console()` before printing anything, which:

1. switches the console to UTF-8,
2. reconfigures Python's streams with `errors="replace"`, so an odd character
   can never take a run down again,
3. switches the console font to **Courier New**, which has Hebrew glyphs.

The font change applies to the current console window only — it is not a
persistent system setting. Use `--keep-console-font` to skip step 3.

Note that the Windows console does not reorder bidirectional text, so Hebrew
prints in logical order and reads right-to-left within each line. That is a
console limitation and affects the log only — the generated `.docx` is correct.
Windows Terminal renders this better than `cmd.exe` if you have the choice.

## Caching

Two layers, both on by default. Re-running a class after tweaking the output is
effectively free.

| Layer | What it saves | Where |
| --- | --- | --- |
| **Page text** | Re-reading and re-OCR'ing PDFs | `.cache/` |
| **Extraction result** | The model call entirely — a cached student costs **no tokens** | `.cache/records/` |
| **Claude prompt cache** | ~5,500 tokens of system prompt per student, billed at ~10% | server-side, 1h TTL |

Cache keys are derived from content — the PDF, the prompt text, the model, and
the effort level all feed the key. Change any of them and the affected entries
miss automatically; there is no version to bump and no stale-cache trap. The
run reports what it cost:

```text
Students:      4 (1 via API, 3 from local cache)
Tokens:        10,276 in / 1,237 out
Prompt cache:  5,551 tokens read at ~10% price (35% of input), 0 written
```

The system prompt is deliberately kept above ~2,048 tokens, which is the
minimum Claude will cache for this model — below that the prompt cache silently
does nothing.

Delete `.cache/` to reset. `--no-cache` bypasses both local layers.

## How it works

1. **Text extraction** (`src/text_extract.py`) — reads the PDF's text layer, and
   OCRs only the pages that come back empty, via the shared OCR library at
   `C:\work\common_infrastructures\ocr`. Page text is cached under `.cache/`.
2. **Page selection** (`src/relevance.py`) — a diagnostic runs 15–30 pages, but
   the table only needs the identity header and the `סיכום והמלצות` block. Pages
   are scored on section headings and penalised for being mostly score tables;
   the top ~12 are sent. This is what keeps a run cheap.
3. **Extraction** (`src/extractor.py`) — one Claude call per student returns a
   validated `StudentRecord` (Pydantic + structured outputs), plus a
   `confidence` rating and a `missing_info` list.
4. **Rendering** (`src/docx_writer.py`) — landscape RTL table matching the
   existing tables in `inputs/adjustments`.

## Reviewing the output

**The tables need a human pass before use.** The run prints which students to
check:

```text
[א2] review by hand: דני לוי
```

A student is flagged when `confidence` is not `high` or when `missing_info` is
non-empty. The most common gap is **the examiner's name**: in several
diagnostics the signature block is a scanned image with no text layer, so the
name cannot be read even though the diagnosis type and date come through. Those
rows come out as `אבחון פסיכודידקטי, 4/4/23` and need the name added by hand.

Fields are never filled with placeholder text — a missing detail is omitted from
the cell and reported in `missing_info` instead.

## Note on student data

Diagnostics and the generated tables contain personal and medical information
about children. `inputs/` and `output/` are gitignored. Page text is cached in
plain text under `.cache/`; delete it when you're done, or run with `--no-cache`.
Diagnostic excerpts are sent to the Claude API for processing.
