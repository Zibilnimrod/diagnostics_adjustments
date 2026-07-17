"""The Python side of the GUI — the object the web page calls into.

The GUI is a thin front door over the existing pipeline, exactly like the
research_extract control panel and the ISF Audit Tool: it manages the class
folders and files, then runs `src.pipeline` to produce the tables. It
reimplements none of the extraction, so the GUI and the CLI always do identical
work.

Every method here is exposed to JavaScript via pywebview's js_api bridge and is
called as `window.pywebview.api.<method>(...)`. Methods return plain dicts/lists
(JSON-friendly) and never raise across the bridge — failures come back as
`{"ok": False, "error": "..."}` so the page can show a message instead of dying.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .image_to_pdf import convert as image_to_pdf
from .image_to_pdf import is_image

# Characters Windows forbids in a folder name, plus path separators.
_ILLEGAL = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


@dataclass
class GuiPaths:
    diagnostics_root: Path  # inputs/diagnostics — one sub-folder per class
    output_root: Path       # where the .docx tables are written


def _safe_class_name(name: str) -> str | None:
    name = (name or "").strip()
    if not name or name in (".", ".."):
        return None
    if any(ch in _ILLEGAL for ch in name):
        return None
    if len(name) > 40:
        return None
    return name


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_pick_dir() -> str:
    """A predictable folder for the file picker to open at."""
    home = Path.home()
    for candidate in (home / "Downloads", home / "Desktop", home / "Documents", home):
        if candidate.is_dir():
            return str(candidate)
    return ""


class Api:
    def __init__(self, paths: GuiPaths, settings: Settings):
        self._paths = paths
        self._settings = settings
        self._window = None  # set by the launcher; underscore so pywebview ignores it
        self._busy = threading.Lock()
        # Where the "add files" picker opens. Starts at a sensible place (so it
        # never falls back to some unrelated last-browsed folder) and then
        # follows wherever the teacher last picked from.
        self._last_pick_dir = _default_pick_dir()
        self._paths.diagnostics_root.mkdir(parents=True, exist_ok=True)
        self._paths.output_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Push progress to the page (thread-safe enough: evaluate_js queues).
    # ------------------------------------------------------------------

    def _emit(self, channel: str, payload) -> None:
        if self._window is None:
            return
        import json

        try:
            self._window.evaluate_js(f"window.{channel}({json.dumps(payload, ensure_ascii=False)})")
        except Exception:
            pass  # Page closed mid-run; nothing to do.

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _teacher_of(self, folder: Path) -> str:
        from .teachers import read_teacher_file

        return read_teacher_file(folder) or ""

    def _class_payload(self, folder: Path) -> dict:
        files = []
        for p in sorted(folder.glob("*")):
            if p.is_file() and not p.name.startswith("~$") and p.name != "teacher_name.txt":
                files.append(
                    {
                        "name": p.name,
                        "size_kb": max(1, p.stat().st_size // 1024),
                        "is_pdf": p.suffix.lower() == ".pdf",
                    }
                )
        return {
            "name": folder.name,
            "teacher": self._teacher_of(folder),
            "files": files,
            "count": len(files),
        }

    def list_classes(self) -> list[dict]:
        root = self._paths.diagnostics_root
        return [
            self._class_payload(f)
            for f in sorted(root.iterdir())
            if f.is_dir()
        ]

    def create_class(self, name: str) -> dict:
        safe = _safe_class_name(name)
        if safe is None:
            return {"ok": False, "error": "שם כיתה לא תקין"}
        folder = self._paths.diagnostics_root / safe
        if folder.exists():
            return {"ok": False, "error": f"הכיתה '{safe}' כבר קיימת"}
        folder.mkdir(parents=True)
        return {"ok": True, "class": self._class_payload(folder)}

    def delete_class(self, name: str) -> dict:
        safe = _safe_class_name(name)
        folder = self._paths.diagnostics_root / (safe or "")
        if not safe or not folder.is_dir():
            return {"ok": False, "error": "כיתה לא נמצאה"}
        shutil.rmtree(folder)
        return {"ok": True}

    def set_teacher(self, name: str, teacher: str) -> dict:
        safe = _safe_class_name(name)
        folder = self._paths.diagnostics_root / (safe or "")
        if not safe or not folder.is_dir():
            return {"ok": False, "error": "כיתה לא נמצאה"}
        target = folder / "teacher_name.txt"
        teacher = (teacher or "").strip()
        if teacher:
            target.write_text(teacher + "\n", encoding="utf-8")
        elif target.exists():
            target.unlink()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def pick_files(self) -> list[str]:
        """Open the native file picker; return chosen paths (the button path)."""
        import webview

        if self._window is None:
            return []
        open_dialog = getattr(webview, "FileDialog", None)
        dialog_type = open_dialog.OPEN if open_dialog else webview.OPEN_DIALOG
        result = self._window.create_file_dialog(
            dialog_type,
            directory=self._last_pick_dir,
            allow_multiple=True,
            file_types=("אבחונים (*.pdf;*.jpg;*.jpeg;*.png)", "כל הקבצים (*.*)"),
        )
        result = list(result or [])
        if result:  # remember this folder so the next pick starts there
            self._last_pick_dir = str(Path(result[0]).parent)
        return result

    def add_files(self, class_name: str, paths: list[str]) -> dict:
        """Copy files into a class, converting images to PDF and skipping dups.

        Duplicate detection is by content hash against what's already in the
        folder, so a re-download ("(1)" copy) or the same photo dropped twice
        can't create a second row.
        """
        safe = _safe_class_name(class_name)
        folder = self._paths.diagnostics_root / (safe or "")
        if not safe or not folder.is_dir():
            return {"ok": False, "error": "כיתה לא נמצאה"}

        existing = {
            _file_hash(p): p.name
            for p in folder.glob("*")
            if p.is_file() and p.name != "teacher_name.txt"
        }

        added, converted, skipped = [], [], []
        for raw in paths:
            src = Path(raw)
            if not src.is_file():
                skipped.append({"name": src.name, "why": "לא נמצא"})
                continue
            try:
                if is_image(src):
                    dest = self._unique_path(folder, src.stem + ".pdf")
                    image_to_pdf(src, dest)
                    converted.append(dest.name)
                else:
                    dest = self._unique_path(folder, src.name)
                    shutil.copy2(src, dest)
                # Dedup after writing (image conversion changes bytes, so we hash
                # the produced PDF, catching the same screenshot twice too).
                digest = _file_hash(dest)
                if digest in existing:
                    dest.unlink()
                    skipped.append({"name": src.name, "why": f"כפילות של {existing[digest]}"})
                    continue
                existing[digest] = dest.name
                added.append(dest.name)
            except Exception as exc:
                skipped.append({"name": src.name, "why": str(exc)})

        return {
            "ok": True,
            "added": added,
            "converted": converted,
            "skipped": skipped,
            "class": self._class_payload(folder),
        }

    def _unique_path(self, folder: Path, filename: str) -> Path:
        dest = folder / filename
        if not dest.exists():
            return dest
        stem, suffix = Path(filename).stem, Path(filename).suffix
        i = 2
        while (folder / f"{stem} ({i}){suffix}").exists():
            i += 1
        return folder / f"{stem} ({i}){suffix}"

    def remove_file(self, class_name: str, filename: str) -> dict:
        safe = _safe_class_name(class_name)
        folder = self._paths.diagnostics_root / (safe or "")
        target = folder / Path(filename).name  # basename only — no traversal
        if not safe or not target.is_file() or target.parent != folder:
            return {"ok": False, "error": "קובץ לא נמצא"}
        target.unlink()
        return {"ok": True, "class": self._class_payload(folder)}

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def open_output_folder(self) -> dict:
        return self._open(self._paths.output_root)

    def open_class_folder(self, name: str) -> dict:
        safe = _safe_class_name(name)
        folder = self._paths.diagnostics_root / (safe or "")
        return self._open(folder) if safe and folder.is_dir() else {"ok": False}

    def _open(self, path: Path) -> dict:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Report generation — runs the real pipeline in a thread.
    # ------------------------------------------------------------------

    def generate(self) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "כבר רץ תהליך"}
        threading.Thread(target=self._generate_worker, daemon=True).start()
        return {"ok": True}

    def _generate_worker(self) -> None:
        from .pipeline import Pipeline

        try:
            classes = self.list_classes()
            if not classes:
                self._emit("onDone", {"ok": False, "error": "אין כיתות"})
                return
            if not any(c["count"] for c in classes):
                self._emit("onDone", {"ok": False, "error": "לא נוספו אבחונים לאף כיתה"})
                return

            self._emit("onProgress", {"type": "start"})
            pipeline = Pipeline(self._settings, log=lambda m: self._emit("onProgress", {"type": "log", "line": m}))
            results = pipeline.run(write_docx=True)

            summary = []
            for r in results:
                review = [
                    rec.student_name for rec in r.records
                    if rec.confidence != "high" or rec.missing_info
                ]
                summary.append(
                    {
                        "class": r.folder_name,
                        "students": len(r.records),
                        "docx": r.docx_path.name if r.docx_path else None,
                        "review": review,
                        "merge": r.duplicate_names,
                        "failures": [f for f, _ in r.failures],
                    }
                )
            self._emit(
                "onDone",
                {
                    "ok": True,
                    "output": str(self._paths.output_root),
                    "totals": pipeline.totals.summary_lines(),
                    "classes": summary,
                },
            )
        except Exception as exc:
            self._emit("onDone", {"ok": False, "error": str(exc)})
        finally:
            self._busy.release()
