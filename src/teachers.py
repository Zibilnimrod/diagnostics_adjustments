"""Find the מחנכת (homeroom teacher) name for a class.

The teacher's name is not in any diagnostic, so it has to come from the user.
Two ways, in precedence order:

1. ``--teachers teachers.json`` — a bulk override, handy for setting every class
   at once or for a one-off run.
2. ``teacher_name.txt`` inside the class folder — one line, just the name. This
   is the everyday path: it lives next to the class's data, so adding a class
   means creating a folder, dropping in the PDFs, and writing one short file.
   No punctuation to get wrong.
"""

from __future__ import annotations

from pathlib import Path

# Both spellings work; a Hebrew-speaking user may reach for either.
TEACHER_FILENAMES = ("teacher_name.txt", "מחנכת.txt")

# Notepad writes UTF-8, UTF-8-with-BOM, or "ANSI" (cp1255 on Hebrew Windows)
# depending on the Encoding dropdown. Guessing wrong yields mojibake in the
# table rather than an error, so try the plausible ones in order.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1255")


def _decode(raw: bytes) -> str | None:
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def read_teacher_file(folder: Path) -> str | None:
    """Return the teacher name written in the class folder, if there is one."""
    for filename in TEACHER_FILENAMES:
        path = folder / filename
        if not path.is_file():
            continue
        try:
            text = _decode(path.read_bytes())
        except OSError:
            continue
        if text is None:
            continue
        # First non-empty line, so a trailing newline or a stray comment line
        # below the name doesn't matter.
        for line in text.splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                return name
    return None


def resolve_teacher(folder: Path, overrides: dict[str, str]) -> str | None:
    """Teacher for a class folder: --teachers wins, else teacher_name.txt."""
    override = overrides.get(folder.name)
    if override and override.strip():
        return override.strip()
    return read_teacher_file(folder)
