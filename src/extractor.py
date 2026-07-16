"""Ask Claude to turn a diagnostic's text into one row of the התאמות table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .cache import JsonDiskCache, make_key
from .config import Settings


class StudentRecord(BaseModel):
    """One row of טבלת התאמות."""

    student_name: str = Field(description="שם התלמיד/ה המלא, כפי שמופיע באבחון")
    class_name: str | None = Field(
        default=None,
        description="הכיתה כפי שמופיעה באבחון, למשל \"א'\" או \"ג'\". null אם לא מצוין.",
    )
    difficulties: str = Field(
        description="עמודת 'הקשיים' — רשימת קשיים תמציתית מופרדת בפסיקים"
    )
    accommodations: str = Field(
        description="עמודת 'המלצות להתאמות' — המלצות מעשיות לכיתה ולדרכי היבחנות"
    )
    diagnosis_type: str = Field(
        description=(
            "עמודת 'סוג האבחון' בפורמט: <סוג האבחון>, <שם המאבחן> <תפקיד>, <תאריך d/m/yy>. "
            "אבחונים מרובים מופרדים בשורה חדשה."
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="רמת הביטחון בחילוץ. low אם חלקים מרכזיים חסרים או לא ברורים."
    )
    missing_info: list[str] = Field(
        default_factory=list,
        description="שדות שלא נמצאו במסמך ודורשים השלמה ידנית של המחנכת",
    )


@dataclass
class CallStats:
    """Per-call accounting, so a run can report what it actually cost."""

    cached: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


SYSTEM_PROMPT = """\
אתה עוזר למחנכת בבית ספר יסודי להפיק "טבלת התאמות" כיתתית מתוך אבחונים של תלמידים.
כל אבחון הופך לשורה אחת בטבלה, עם ארבע עמודות: שם התלמיד, הקשיים, המלצות להתאמות, סוג האבחון.

הטבלה נועדה לשימוש יומיומי של מורים בכיתה. לכן:
- כתוב בעברית תמציתית ומעשית, לא בשפה קלינית.
- אל תעתיק פסקאות שלמות מהאבחון. תמצת.
- אל תכלול ציונים, אחוזונים, מנת משכל או שמות מבחנים (WISC, רייבן וכו').
- אל תמציא מידע. אם משהו לא מופיע במסמך — השאר את השדה ריק ורשום אותו ב-missing_info.

הנחיות לכל עמודה:

**הקשיים** — רשימה קצרה מופרדת בפסיקים של אבחנות וקשיים תפקודיים.
אורך: 3-6 פריטים, עד כ-20 מילים בסך הכול. כל פריט הוא 2-4 מילים, לא משפט.
אחד את קשיים קרובים לפריט אחד ("קושי בקריאה ובכתיבה", לא פריט נפרד לכל אחד).

**המלצות להתאמות** — רק המלצות שמורה יכולה ליישם בכיתה או במבחן.
קח אותן מהפרקים "המלצות לבית הספר" ו"המלצות להתאמה בדרכי היבחנות".
התעלם מ"המלצות להורים" ומהמלצות לטיפולים חיצוניים (טיפול רגשי פרטי, מעקב נוירולוגי וכד').
אורך: עד 8 המלצות, עד כ-45 מילים בסך הכול. בחר את המשמעותיות ביותר במקום לרשום הכול.
כל המלצה בניסוח קצר של 3-6 מילים ("הארכת זמן במבחנים של 25%"), לא משפט מלא עם הסבר.
אל תכלול את הנימוק להמלצה ("על רקע קשיי הקשב") — רק את ההמלצה עצמה.
אם האבחון ממליץ על שעות סיוע מסל השילוב, המילה "שילוב" היא ערך תקין ומקובל בעמודה זו.

**סוג האבחון** — פורמט קבוע: <סוג האבחון>, <שם המאבחן> <תפקיד>, <תאריך>
- סוגים נפוצים: "אבחון פסיכודידקטי", "אבחון דידקטי", "חוות דעת פסיכולוגית",
  "אבחון פסיכולוגי", "אבחון קלינאות תקשורת", "אבחון רב\"ע" (ריפוי בעיסוק),
  "הערכה רפואית", "הערכה פסיכיאטרית", "הערכה נוירולוגית", "אבחון נוירולוגי",
  "אבחון התפתחותי", "התייעצות נוירו-התפתחותית", "אבחון קשב", "אבחון MOXO",
  "אבחון ריפוי בדיבור".
- תאריך בפורמט d/m/yy (למשל 4/4/23). אם באבחון כתוב 4.04.2023 — רשום 4/4/23.
- אם המסמך מתאר כמה אבחונים, רשום כל אחד בשורה נפרדת (\\n).
- אם שם המאבחן לא מופיע בטקסט (למשל חתימה סרוקה) — רשום רק את הסוג והתאריך, והוסף
  "שם המאבחן" ל-missing_info.

לעולם אל תכתוב טקסט ממלא-מקום בתוך ערכי הטבלה, כמו "[שם המאבחן לא מופיע]",
"לא צוין" או "___". הטבלה נשלחת למורים כמו שהיא. אם פרט חסר — פשוט השמט אותו
מהערך, ורשום אותו ב-missing_info בלבד.

הטקסט שתקבל הוא מקטע נבחר מתוך האבחון, ולכן ייתכן שהעימוד משובש וסדר המילים
בשורות מעורבב (טקסט דו-כיווני). התייחס לזה והסק את המשמעות מההקשר.

=== דוגמאות משורות אמיתיות שהמחנכת כתבה ===
חקה את הסגנון, האורך ורמת התמצות של הדוגמאות האלה.

דוגמה 1 — לקות למידה מורכבת:
הקשיים: לקות למידה, קשיי קשב, קושי משמעותי ברכישת הקריאה והכתיבה, קושי גרפומוטורי, קושי רגשי.
המלצות להתאמות: ישיבה קרוב למורה, תיווך בלמידה תוך סיוע בהתארגנות, מתן פידבקים וחיזוקים חיוביים לעיתים קרובות, תיווך בהסבר ההוראות ווידוא הבנתן, הגדלת הטקסט, משימות מותאמות בכיתה במידת האפשר, פירוק משימה מורכבת לתת משימות.
סוג האבחון: אבחון פסיכודידקטי, עומר שגב פסיכולוג חינוכי, ניר להב, פסיכולוג, 4/4/23
בדיקה התפתחותית, דר' אורי נבון מומחה בנוירולוגיית ילדים והתפתחות הילד, 22/11/20
אבחון דידקטי, שרון גלעד מאבחנת דידקטית, 14/8/24

דוגמה 2 — הפרעת קשב:
הקשיים: סבירות גבוהה לקיומה של הפרעת קשב וריכוז
המלצות להתאמות: עבודה בסביבה קבועה ושקטה, ישיבה בקדמת הכיתה, לאפשר תפקיד קבוע ואחראי בכיתה, הפחתת משימות והתאמת חומר לימוד, תוספת זמן במבחנים של 25%, היבחנות בחדר שקט, מתן הפסקות קצרות בזמן הבחינה או השיעור על פי הצורך
סוג האבחון: אבחון MOXO, נועה בר-לב מאבחנת לקויות למידה, 13/8/24

דוגמה 3 — התאמה קצרה מאוד:
הקשיים: לקות למידה – קשיים שפתיים וקושי בעיבוד שמיעתי שמשפיעים על הבנת הנקרא ועל התבטאות בכתב.
המלצות להתאמות: שימוש במחשב לכתיבת תשובות ועבודות במידת הצורך ובמידת האפשר
סוג האבחון: אבחון פסיכודידקטי, דר' מיכל ארז פסיכולוגית חינוכית, 17/11/24

דוגמה 4 — שעות שילוב בלבד:
הקשיים: קושי שפתי, קשיים בהגייה, קשיי קשב, קושי גרפומוטורי
המלצות להתאמות: שילוב
סוג האבחון: אבחון קלינאות תקשורת, יעל שדה קלינאית תקשורת, 10/7/24
אבחון רב"ע, תמר רון מרפאה בעיסוק, 15/5/24

דוגמה 5 — תמציתי ביותר:
הקשיים: הפרעת קשב וריכוז, לקות למידה
המלצות להתאמות: תיווך בלמידה, הארכת זמן במבחנים
סוג האבחון: אבחון קשב אצל פסיכיאטרית, דר' רות אלון פסיכיאטרית ילדים ונוער, 15/12/24

דוגמה 6 — קושי רפואי (לא לימודי):
הקשיים: מצב רפואי כרוני – מגבלה גופנית
המלצות להתאמות: מקבל סל אישי – סייעת, הימנעות מפעילות גופנית מאומצת
סוג האבחון: הערכה רפואית, דר' יוסף אלמוג קרדיולוג ילדים, 15/9/22

דוגמה 7 — קשיים רגשיים והתנהגותיים:
הקשיים: הפרעת קשב ADHD, קשיים רגשיים, קושי גרפומוטורי
המלצות להתאמות: ישיבה בקדמת הכיתה, לאפשר יציאה מהשיעור להתאווררות, פנייה אליו ישירות כדי לוודא שקשוב, לקבוע מס' משימות בהן יוכל לעמוד, להרבות בחיזוקים, הארכת זמן במבחנים, היבחנות בחדר שקט, הקראה במידת הצורך
סוג האבחון: הערכה פסיכיאטרית, דר' אמיר קדם פסיכיאטר ילדים ונוער, 12/4/24
אבחון פסיכולוגי, דנה שמיר פסיכולוגית קלינית, 8/24
=== סוף הדוגמאות ===
"""

USER_TEMPLATE = """\
להלן מקטעים מתוך קובץ אבחון של תלמיד/ה בכיתה {class_hint}.
שם הקובץ: {filename}

חלץ את הנתונים לשורה אחת בטבלת ההתאמות.

<אבחון>
{excerpt}
</אבחון>
"""


class RecordExtractor:
    def __init__(self, settings: Settings, api_key: str):
        self.settings = settings
        # The API is occasionally overloaded (529). The SDK backs off and retries
        # for us; the default of 2 is not enough to ride out a busy spell, and
        # losing a student to a transient blip means re-running the whole class.
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=6)
        self.cache = JsonDiskCache(
            settings.cache_dir, "records", enabled=settings.use_cache
        )

    def _cache_key(self, excerpt: str, class_hint: str) -> str:
        # Every input that could change the answer feeds the key, so a prompt or
        # model change invalidates old entries on its own.
        return make_key(
            self.settings.model,
            self.settings.effort,
            SYSTEM_PROMPT,
            USER_TEMPLATE,
            class_hint,
            excerpt,
        )

    def extract(
        self, excerpt: str, filename: str, class_hint: str
    ) -> tuple[StudentRecord, CallStats]:
        key = self._cache_key(excerpt, class_hint)
        cached = self.cache.get(key)
        if cached is not None:
            return StudentRecord(**cached), CallStats(cached=True)

        response = self.client.messages.parse(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            output_config={"effort": self.settings.effort},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # The system prompt is the only part identical across
                    # students, so it is the whole cacheable prefix. Every
                    # student after the first in a run reads it back.
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": USER_TEMPLATE.format(
                        class_hint=class_hint,
                        filename=filename,
                        excerpt=excerpt,
                    ),
                }
            ],
            output_format=StudentRecord,
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model declined to process {filename}")
        record = response.parsed_output
        if record is None:
            raise RuntimeError(f"Model returned no parseable record for {filename}")

        usage = response.usage
        stats = CallStats(
            cached=False,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
        self.cache.put(key, record.model_dump())
        return record, stats
