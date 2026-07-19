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

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
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
        self._purge_trash()

    # ------------------------------------------------------------------
    # Trash — deletes are moves into .trash so the toast can offer undo.
    # ------------------------------------------------------------------

    def _trash_root(self) -> Path:
        # Beside (not inside) the diagnostics root, so it never shows up as a class.
        return self._paths.diagnostics_root.parent / ".trash"

    def _purge_trash(self, max_age_days: int = 7) -> None:
        root = self._trash_root()
        if not root.is_dir():
            return
        cutoff = time.time() - max_age_days * 86400
        for entry in root.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass

    def _to_trash(self, kind: str, class_name: str | None, src: Path) -> str:
        """Move a file/class-folder into the trash; return the undo token."""
        token = str(time.time_ns())
        entry = self._trash_root() / token
        payload = entry / "payload"
        payload.mkdir(parents=True)
        shutil.move(str(src), str(payload / src.name))
        (entry / "meta.json").write_text(
            json.dumps({"kind": kind, "class": class_name, "name": src.name}, ensure_ascii=False),
            encoding="utf-8",
        )
        return token

    def undo_delete(self, token: str) -> dict:
        entry = self._trash_root() / str(token)
        if not str(token).isdigit() or not entry.is_dir():
            return {"ok": False, "error": "אין מה לשחזר"}
        try:
            meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
            item = entry / "payload" / meta["name"]
            if meta["kind"] == "file":
                folder = self._paths.diagnostics_root / meta["class"]
                folder.mkdir(parents=True, exist_ok=True)  # class may have gone meanwhile
                shutil.move(str(item), str(self._unique_path(folder, item.name)))
                shutil.rmtree(entry, ignore_errors=True)
                return {"ok": True, "class": self._class_payload(folder)}
            # kind == "class"
            dest = self._paths.diagnostics_root / meta["name"]
            if dest.exists():
                return {"ok": False, "error": f"כיתה בשם {meta['name']} כבר קיימת"}
            shutil.move(str(item), str(dest))
            shutil.rmtree(entry, ignore_errors=True)
            return {"ok": True, "class": self._class_payload(dest)}
        except Exception as exc:
            return {"ok": False, "error": f"השחזור נכשל: {exc}"}

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
        token = self._to_trash("class", None, folder)
        return {"ok": True, "undo": token}

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
        # The same scan sitting in ANOTHER class is almost always a mistake
        # (a child belongs to one class) — warn with the class name, don't add.
        elsewhere: dict[str, tuple[str, str]] = {}
        for other in self._paths.diagnostics_root.iterdir():
            if other.is_dir() and other != folder:
                for p in other.glob("*"):
                    if p.is_file() and p.name != "teacher_name.txt":
                        elsewhere.setdefault(_file_hash(p), (other.name, p.name))

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
                if digest in elsewhere:
                    cls, fname = elsewhere[digest]
                    dest.unlink()
                    skipped.append({"name": src.name, "why": f"כבר קיים בכיתה {cls} ({fname})"})
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
        token = self._to_trash("file", safe, target)
        return {"ok": True, "class": self._class_payload(folder), "undo": token}

    def move_file(self, src_class: str, filename: str, dst_class: str) -> dict:
        """Reassign a diagnostic to another class (chip dragged between cards)."""
        safe_src, safe_dst = _safe_class_name(src_class), _safe_class_name(dst_class)
        src_folder = self._paths.diagnostics_root / (safe_src or "")
        dst_folder = self._paths.diagnostics_root / (safe_dst or "")
        target = src_folder / Path(filename).name
        if not safe_src or not safe_dst or not dst_folder.is_dir() or not target.is_file():
            return {"ok": False, "error": "קובץ או כיתה לא נמצאו"}
        digest = _file_hash(target)
        for p in dst_folder.glob("*"):
            if p.is_file() and p.name != "teacher_name.txt" and _file_hash(p) == digest:
                return {"ok": False, "error": f"כבר קיים בכיתה {safe_dst} ({p.name})"}
        shutil.move(str(target), str(self._unique_path(dst_folder, target.name)))
        return {
            "ok": True,
            "src": self._class_payload(src_folder),
            "dst": self._class_payload(dst_folder),
        }

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def open_output_folder(self) -> dict:
        return self._open(self._paths.output_root)

    def open_class_folder(self, name: str) -> dict:
        safe = _safe_class_name(name)
        folder = self._paths.diagnostics_root / (safe or "")
        return self._open(folder) if safe and folder.is_dir() else {"ok": False}

    def open_review_report(self) -> dict:
        path = getattr(self, "_report_path", None)
        if path and Path(path).is_file():
            return self._open_file(Path(path))
        return {"ok": False, "error": "עדיין לא נוצר דו\"ח בקרה"}

    def open_source_file(self, class_name: str, filename: str) -> dict:
        safe = _safe_class_name(class_name)
        folder = self._paths.diagnostics_root / (safe or "")
        target = folder / Path(filename).name  # basename only — no traversal
        if not safe or not target.is_file() or target.parent != folder:
            return {"ok": False, "error": "קובץ לא נמצא"}
        return self._open_file(target)

    # ------------------------------------------------------------------
    # Thumbnails — page 1 of each PDF, cached under <class>/.thumbs
    # ------------------------------------------------------------------

    def thumbnails(self, class_name: str) -> dict:
        """Return {filename: data-URI} of page-1 thumbnails for one class.

        Rendered lazily with PyMuPDF and cached on disk keyed by size+mtime, so
        after the first call this is just reading small PNGs back.
        """
        safe = _safe_class_name(class_name)
        folder = self._paths.diagnostics_root / (safe or "")
        if not safe or not folder.is_dir():
            return {"ok": False, "error": "כיתה לא נמצאה"}

        tdir = folder / ".thumbs"
        thumbs: dict[str, str] = {}
        keep: set[str] = set()
        for p in sorted(folder.glob("*.pdf")):
            if p.name.startswith("~$"):
                continue
            try:
                st = p.stat()
                cached = tdir / f"{p.stem}.{st.st_size}-{st.st_mtime_ns}.png"
                if not cached.is_file():
                    import fitz  # PyMuPDF

                    tdir.mkdir(exist_ok=True)
                    with fitz.open(p) as doc:
                        page = doc[0]
                        zoom = 96 / max(1.0, page.rect.width)
                        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                        cached.write_bytes(pix.tobytes("png"))
                keep.add(cached.name)
                thumbs[p.name] = "data:image/png;base64," + base64.b64encode(
                    cached.read_bytes()
                ).decode("ascii")
            except Exception:
                continue  # no thumbnail for this file; the chip keeps its icon
        # Drop thumbnails of removed/replaced files so the cache can't grow.
        if tdir.is_dir():
            for f in tdir.iterdir():
                if f.name not in keep:
                    try:
                        f.unlink()
                    except OSError:
                        pass
        return {"ok": True, "thumbs": thumbs}

    def _open_file(self, path: Path) -> dict:
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
    # API key
    # ------------------------------------------------------------------

    def api_key_status(self) -> dict:
        """Whether a key is configured and how — never returns the key itself."""
        from .config import api_key_source
        from . import keystore

        source = api_key_source()
        return {
            "configured": source is not None,
            "source": source,                    # 'env' | 'saved' | None
            "scheme": keystore.storage_scheme(),  # 'dpapi' | 'base64' | None
        }

    def save_api_key(self, key: str) -> dict:
        from . import keystore

        key = (key or "").strip()
        if not key:
            return {"ok": False, "error": "לא הוזן מפתח"}
        if not key.startswith("sk-ant-"):
            # Anthropic keys start with sk-ant-. Warn but don't block — formats
            # can change and a proxy setup may differ.
            return {"ok": False, "error": "המפתח אמור להתחיל ב-sk-ant-. בדקו שהעתקתם אותו במלואו."}
        try:
            keystore.save_api_key(key)
        except Exception as exc:
            return {"ok": False, "error": f"שמירה נכשלה: {exc}"}
        return {"ok": True, **self.api_key_status()}

    def clear_api_key(self) -> dict:
        from . import keystore

        keystore.clear_api_key()
        return {"ok": True, **self.api_key_status()}

    def test_api_key(self) -> dict:
        """Verify the configured key actually works, with one cheap call."""
        import anthropic

        from .config import resolve_api_key

        try:
            key = resolve_api_key()
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            client = anthropic.Anthropic(api_key=key)
            client.messages.count_tokens(
                model=self._settings.model,
                messages=[{"role": "user", "content": "x"}],
            )
            return {"ok": True}
        except anthropic.AuthenticationError:
            return {"ok": False, "error": "המפתח אינו תקין (שגיאת הזדהות)"}
        except anthropic.APIConnectionError:
            return {"ok": False, "error": "אין חיבור לאינטרנט"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Report generation — runs the real pipeline in a thread.
    # ------------------------------------------------------------------

    _DEFAULT_SECONDS_PER_FILE = 35.0  # first-run guess; runs recalibrate it

    def run_estimate(self) -> dict:
        """How many diagnostics a run would cover and a rough duration guess.

        The per-file average is learned from past runs (stored in the app's
        settings.json), so the guess gets better the more the app is used.
        """
        from . import keystore

        files = sum(
            1
            for f in self._paths.diagnostics_root.iterdir()
            if f.is_dir()
            for p in f.glob("*.pdf")
            if not p.name.startswith("~$")
        )
        avg = float(keystore.get_pref("avg_seconds_per_file", self._DEFAULT_SECONDS_PER_FILE))
        return {"files": files, "seconds": int(files * avg)}

    def _learn_run_speed(self, elapsed: float, totals) -> None:
        """Fold this run's pace into the stored per-file average (EMA)."""
        from . import keystore

        if totals.api_calls <= 0:
            return  # fully-cached run says nothing about real extraction speed
        # Cached files fly through (~2s); subtract them so the average tracks
        # genuinely-processed files and a cached rerun can't skew it low.
        per_file = (elapsed - 2.0 * totals.from_cache) / totals.api_calls
        per_file = max(3.0, min(300.0, per_file))
        old = float(keystore.get_pref("avg_seconds_per_file", self._DEFAULT_SECONDS_PER_FILE))
        keystore.set_pref("avg_seconds_per_file", round(0.6 * old + 0.4 * per_file, 1))

    def generate(self) -> dict:
        from .config import api_key_source

        if api_key_source() is None:
            return {"ok": False, "error": "no_api_key"}  # page opens the key dialog
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
            # The full work plan up front, so the page can draw the checklist
            # (every file, pending) before the first result arrives.
            plan = [
                {"class": c["name"], "files": [f["name"] for f in c["files"] if f["is_pdf"]]}
                for c in classes
            ]
            self._emit("onProgress", {"type": "plan", "classes": [p for p in plan if p["files"]]})

            started = time.monotonic()
            pipeline = Pipeline(
                self._settings,
                log=lambda m: self._emit("onProgress", {"type": "log", "line": m}),
                progress=lambda ev: self._emit("onProgress", ev),
            )
            results = pipeline.run(write_docx=True)
            self._learn_run_speed(time.monotonic() - started, pipeline.totals)

            # Remember the review report so open_review_report() can find it.
            self._report_path = str(pipeline.report_path) if pipeline.report_path else None

            summary = []
            for r in results:
                cr = r.class_review
                students = []
                if cr:
                    for rv in cr.reviews:  # every student, for the colored bar
                        students.append(
                            {
                                "name": rv.student_name,
                                "file": rv.filename,
                                "score": rv.score,
                                "band": rv.band,
                                "reasons": rv.reasons,
                            }
                        )
                summary.append(
                    {
                        "class": r.folder_name,
                        "students": students,        # [{name, file, score, band, reasons}]
                        "docx": r.docx_path.name if r.docx_path else None,
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
                    "has_report": bool(self._report_path),
                    "classes": summary,
                },
            )
        except Exception as exc:
            self._emit("onDone", {"ok": False, "error": str(exc)})
        finally:
            self._busy.release()
