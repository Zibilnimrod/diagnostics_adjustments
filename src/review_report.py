"""Render a 'דו"ח בקרה' — a review report that points the teacher at the rows
worth a second look, and shows enough that she can often verify without opening
the original file.

Self-contained HTML (inline CSS, RTL): opens in any browser, prints cleanly,
and keeps the sensitive extraction out of the accommodations table itself.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class StudentReview:
    student_name: str
    filename: str
    source_path: str
    confidence: str
    reasons: list[str]
    difficulties: str
    accommodations: str
    diagnosis_type: str
    snippet: str
    needs_review: bool


@dataclass
class ClassReview:
    class_name: str
    teacher: str | None
    reviews: list[StudentReview] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def flagged(self) -> list[StudentReview]:
        return [r for r in self.reviews if r.needs_review]

    @property
    def clean(self) -> list[StudentReview]:
        return [r for r in self.reviews if not r.needs_review]


_CONF_LABEL = {"high": "ביטחון גבוה", "medium": "ביטחון בינוני", "low": "ביטחון נמוך"}

_CSS = """
:root { --ink:#2c2925; --soft:#6b6459; --faint:#9a9285; --line:#e4ddcf;
        --green:#2f8f7f; --green-tint:#e7f2ee; --amber:#c8871b; --amber-tint:#fbf1dc;
        --rose:#c0574b; --rose-tint:#f7e6e2; --paper:#f6f3ec; --card:#fffefb; }
* { box-sizing:border-box; }
body { font-family:"Segoe UI","Arial Hebrew",system-ui,sans-serif; background:var(--paper);
       color:var(--ink); margin:0; padding:28px; line-height:1.6; }
.wrap { max-width:900px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 2px; }
.meta { color:var(--faint); font-size:13px; margin-bottom:22px; }
.cls { background:var(--card); border:1px solid var(--line); border-radius:14px;
       padding:18px 20px; margin-bottom:20px; box-shadow:0 6px 20px -12px rgba(60,50,35,.25); }
.cls-head { display:flex; align-items:baseline; gap:10px; border-bottom:1px solid var(--line);
            padding-bottom:10px; margin-bottom:14px; }
.cls-head h2 { font-size:19px; margin:0; }
.cls-head .tch { color:var(--soft); font-size:14px; }
.cls-head .tag { margin-inline-start:auto; font-size:13px; color:var(--soft); }
.stu { border-radius:10px; padding:13px 15px; margin-bottom:12px; border:1px solid var(--line);
       background:var(--amber-tint); }
.stu.low { background:var(--rose-tint); }
.stu-name { font-weight:700; font-size:16px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.badge { font-size:12px; font-weight:600; border-radius:999px; padding:2px 10px; }
.badge.low { background:var(--rose); color:#fff; }
.badge.medium { background:var(--amber); color:#fff; }
.reasons { margin:8px 0 10px; padding:0; list-style:none; }
.reasons li { font-size:14px; color:var(--ink); padding-inline-start:20px; position:relative; }
.reasons li::before { content:"⚠"; position:absolute; inset-inline-start:0; color:var(--amber); }
.fields { font-size:13.5px; color:var(--soft); background:var(--paper); border-radius:8px;
          padding:8px 12px; margin-bottom:8px; }
.fields b { color:var(--ink); font-weight:600; }
.snip { font-size:12.5px; color:var(--faint); border-inline-start:3px solid var(--line);
        padding-inline-start:10px; margin:8px 0; white-space:pre-wrap; max-height:120px; overflow:hidden; }
.open { font-size:13px; color:var(--green); text-decoration:none; font-weight:600; }
.open:hover { text-decoration:underline; }
.ok { color:var(--green); font-size:14px; }
.fail { background:var(--rose-tint); color:var(--rose); border-radius:8px; padding:8px 12px;
        font-size:14px; margin-bottom:8px; }
.legend { font-size:12.5px; color:var(--faint); margin-top:26px; border-top:1px solid var(--line); padding-top:12px; }
@media print { body { background:#fff; } .cls { box-shadow:none; } }
"""


def _e(text: str) -> str:
    return html.escape(text or "")


def _student_html(r: StudentReview) -> str:
    cls = "low" if r.confidence == "low" else ""
    badge = ""
    if r.confidence in ("low", "medium"):
        badge = f'<span class="badge {r.confidence}">{_CONF_LABEL[r.confidence]}</span>'
    reasons = "".join(f"<li>{_e(x)}</li>" for x in r.reasons)
    # file:// link so the browser can open the original PDF from the report.
    href = "file:///" + _e(str(Path(r.source_path).resolve()).replace("\\", "/"))
    fields = (
        f'<div class="fields">'
        f"<div><b>קשיים:</b> {_e(r.difficulties) or '—'}</div>"
        f"<div><b>המלצות:</b> {_e(r.accommodations) or '—'}</div>"
        f"<div><b>אבחון:</b> {_e(r.diagnosis_type) or '—'}</div>"
        f"</div>"
    )
    snip = f'<div class="snip">{_e(r.snippet)}</div>' if r.snippet else ""
    return (
        f'<div class="stu {cls}">'
        f'<div class="stu-name">{_e(r.student_name)} {badge}</div>'
        f'<ul class="reasons">{reasons}</ul>'
        f"{fields}{snip}"
        f'<a class="open" href="{href}">📄 פתח את הקובץ המקורי ({_e(r.filename)})</a>'
        f"</div>"
    )


def _class_html(c: ClassReview) -> str:
    parts = [f'<div class="cls"><div class="cls-head"><h2>כיתה {_e(c.class_name)}</h2>']
    if c.teacher:
        parts.append(f'<span class="tch">· מחנכת: {_e(c.teacher)}</span>')
    n = len(c.flagged)
    parts.append(
        f'<span class="tag">{n} לבדיקה מתוך {len(c.reviews)}</span></div>'
        if c.reviews
        else "</div>"
    )
    for f, err in c.failures:
        parts.append(f'<div class="fail">✕ הקובץ «{_e(f)}» נכשל: {_e(err)}</div>')
    for r in c.flagged:
        parts.append(_student_html(r))
    if c.clean:
        names = "، ".join(_e(r.student_name) for r in c.clean)
        parts.append(f'<div class="ok">✓ נחזו בביטחון גבוה, אין צורך בבדיקה מיוחדת: {names}</div>')
    parts.append("</div>")
    return "".join(parts)


def render(classes: list[ClassReview], year: str) -> str:
    total_flagged = sum(len(c.flagged) for c in classes)
    body = "".join(_class_html(c) for c in classes)
    subtitle = (
        f"{total_flagged} תלמידים שכדאי לבדוק"
        if total_flagged
        else "כל השורות נחזו בביטחון גבוה"
    )
    return (
        "<!DOCTYPE html><html lang='he' dir='rtl'><head><meta charset='utf-8'>"
        f"<title>דוח בקרה {_e(year)}</title><style>{_CSS}</style></head><body><div class='wrap'>"
        f"<h1>דו\"ח בקרה — טבלת התאמות</h1>"
        f"<div class='meta'>{subtitle} · נוצר {date.today().strftime('%d/%m/%Y')}</div>"
        f"{body}"
        "<div class='legend'>הדו\"ח מסמן שורות שכדאי לעבור עליהן שוב מול הקובץ המקורי — "
        "בגלל ביטחון נמוך בחילוץ, פרטים חסרים, מקור סרוק (OCR) או טקסט משובש. "
        "טבלת ההתאמות עצמה אינה כוללת את הסימונים האלה.</div>"
        "</div></body></html>"
    )


def write_report(classes: list[ClassReview], year: str, output_dir: Path) -> Path:
    path = output_dir / f"דוח בקרה {year}.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(render(classes, year), encoding="utf-8")
    return path
