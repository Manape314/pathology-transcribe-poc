"""
Deterministic date/time normalization for dictated clinical fields.

No LLM, no guessing: every function here is regex/lookup-based and either
returns a confident, validated ISO 8601 value or explicitly reports
"ambiguous" with the raw text preserved. See backend/field_extraction.py
for how these are wired into specific transcript fields (date of birth,
date/time requested, date/time collected).
"""

import re
from datetime import date

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #

_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_HOUR = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_AMPM = r"(a\.?m\.?|p\.?m\.?)"

# Tried in order; first match against the WHOLE cleaned string wins.
_TIME_PATTERNS = [
    (re.compile(rf"^quarter\s+to\s+{_HOUR}\s*{_AMPM}$"), 15, "to"),
    (re.compile(rf"^quarter\s+past\s+{_HOUR}\s*{_AMPM}$"), 15, "past"),
    (re.compile(rf"^half\s+past\s+{_HOUR}\s*{_AMPM}$"), 30, "past"),
    (re.compile(rf"^(\d{{1,2}})\s*minutes?\s+to\s+{_HOUR}\s*{_AMPM}$"), None, "to"),
    (re.compile(rf"^(\d{{1,2}})\s*minutes?\s+(?:past|after)\s+{_HOUR}\s*{_AMPM}$"), None, "past"),
]
_BARE_TIME = re.compile(rf"^{_HOUR}:(\d{{2}})\s*{_AMPM}$")
_BARE_HOUR = re.compile(rf"^{_HOUR}\s*{_AMPM}$")


def _hour_to_int(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _HOUR_WORDS[token]


def _to_24h(hour12: int, ampm: str) -> int:
    ampm = ampm.lower().replace(".", "")
    if ampm == "pm":
        return 12 if hour12 == 12 else hour12 + 12
    return 0 if hour12 == 12 else hour12


def normalize_time(raw: str) -> tuple[str | None, str]:
    """Returns (HH:MM or None, status) — status is "confirmed" or "ambiguous"."""
    if not raw or not raw.strip():
        return None, "ambiguous"

    cleaned = re.sub(r"\s+", " ", raw.strip().lower())
    cleaned = cleaned.rstrip(".")

    # Fixed-offset patterns (quarter to/past, half past, "N minutes to/past").
    for pattern, fixed_offset, direction in _TIME_PATTERNS:
        m = pattern.match(cleaned)
        if not m:
            continue
        groups = m.groups()
        if fixed_offset is None:
            offset = int(groups[0])
            hour_token, ampm = groups[1], groups[2]
        else:
            offset = fixed_offset
            hour_token, ampm = groups[0], groups[1]

        base_minutes = _to_24h(_hour_to_int(hour_token), ampm) * 60
        total = base_minutes - offset if direction == "to" else base_minutes + offset
        total %= 24 * 60
        return f"{total // 60:02d}:{total % 60:02d}", "confirmed"

    # "H:MM am/pm"
    m = _BARE_TIME.match(cleaned)
    if m:
        hour_token, minute, ampm = m.groups()
        minute = int(minute)
        if minute > 59:
            return None, "ambiguous"
        hour24 = _to_24h(_hour_to_int(hour_token), ampm)
        return f"{hour24:02d}:{minute:02d}", "confirmed"

    # Bare "H am/pm"
    m = _BARE_HOUR.match(cleaned)
    if m:
        hour_token, ampm = m.groups()
        hour24 = _to_24h(_hour_to_int(hour_token), ampm)
        return f"{hour24:02d}:00", "confirmed"

    # Anything else (bare "H o'clock" with no am/pm, "around three", "later",
    # "this morning", ...) is intentionally NOT guessed — point 14.
    return None, "ambiguous"


# --------------------------------------------------------------------------- #
# Date
# --------------------------------------------------------------------------- #

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_NAME_RE = re.compile(r"\b(" + "|".join(_MONTHS.keys()) + r")\b")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_DAY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\b")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _valid_ymd(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def _remove_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return text[:start] + (" " * (end - start)) + text[end:]


def normalize_date(raw: str) -> tuple[str | None, str]:
    """Returns (YYYY-MM-DD or None, status) — status is "confirmed" or "ambiguous"."""
    if not raw or not raw.strip():
        return None, "ambiguous"

    cleaned = raw.strip().rstrip(".")

    m = _ISO_RE.match(cleaned)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return (f"{y:04d}-{mo:02d}-{d:02d}", "confirmed") if _valid_ymd(y, mo, d) else (None, "ambiguous")

    lower = cleaned.lower()

    year_matches = list(_YEAR_RE.finditer(lower))
    month_matches = list(_MONTH_NAME_RE.finditer(lower))
    if len(year_matches) != 1 or len(month_matches) != 1:
        # No year, no month, or MULTIPLE candidates for either — genuinely
        # ambiguous (this is exactly what catches "1998-8-August 14th, 6 May",
        # which contains two month names).
        return None, "ambiguous"

    year = int(year_matches[0].group())
    month = _MONTHS[month_matches[0].group()]

    remainder = _remove_span(lower, year_matches[0].span())
    remainder = _remove_span(remainder, month_matches[0].span())

    day_matches = _DAY_RE.findall(remainder)
    if len(day_matches) != 1:
        return None, "ambiguous"
    day = int(day_matches[0])

    # Nothing else of substance should be left over once the year, month and
    # day are accounted for — filler words/punctuation are fine, stray extra
    # numbers or words are not.
    leftover = re.sub(r"\b(of|the)\b", " ", remainder)
    leftover = _DAY_RE.sub(" ", leftover)
    leftover = re.sub(r"[,.\-]", " ", leftover)
    leftover = re.sub(r"\s+", " ", leftover).strip()
    if leftover:
        return None, "ambiguous"

    if not _valid_ymd(year, month, day):
        return None, "ambiguous"
    return f"{year:04d}-{month:02d}-{day:02d}", "confirmed"
