"""
Normalizes the "tests required" field only — never patient/doctor names,
IDs, dates, or any other extracted field (see field_extraction.py).

Matching order (per spec, most to least certain):
  1. curated abbreviation/synonym dictionary (exact, deterministic)
  2. exact canonical match against NHLS/LOINC (case/punctuation-insensitive)
  3. fuzzy match, via matching.best_single_match, at a STRICTER cutoff than
     the general whole-transcript suggestion panel — this field assigns one
     canonical answer per item, so precision matters more here.
  4. no confident match -> status "unmatched", item kept with raw text and
     normalized=None. Never dropped, never guessed.

Reuses matching.py's already-loaded NHLS/LOINC data — does not reload or
duplicate it.
"""

import re

import matching

# Stricter than matching.MIN_MATCH_SCORE (80, used for the browsable
# "suggested tests" panel) — here we're assigning ONE canonical answer to a
# specific field, so a weak fuzzy hit is worse than leaving it unmatched.
FUZZY_MIN_SCORE = 88

# Curated abbreviations that must resolve deterministically, not
# probabilistically. This exists because pure fuzzy matching on short
# acronyms is unsafe — confirmed earlier: naive fuzzy scoring gave a
# 2-character junk fragment a 90% score against an unrelated LOINC entry.
ABBREVIATIONS: dict[str, str] = {
    "fbc": "Full Blood Count",
    "full blood count": "Full Blood Count",
    "crp": "C-reactive protein",
    "c reactive protein": "C-reactive protein",
    "c-reactive protein": "C-reactive protein",
    "u&e": "Urea and Electrolytes",
    "u and e": "Urea and Electrolytes",
    "ue": "Urea and Electrolytes",
    "urea and electrolytes": "Urea and Electrolytes",
    "fbe": "Full Blood Examination",
    "lft": "Liver Function Tests",
    "liver function tests": "Liver Function Tests",
    "tft": "Thyroid Function Tests",
    "thyroid function tests": "Thyroid Function Tests",
    "esr": "Erythrocyte Sedimentation Rate",
    "inr": "International Normalized Ratio",
}


def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[.,;:]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _abbrev_key(s: str) -> str:
    key = _normalize_text(s)
    return re.sub(r"\s*&\s*", "&", key)


# Built once at import time from matching's already-loaded data (which is
# itself loaded once at ITS import time) — no per-call scanning of the full
# ~45k LOINC / ~1.2k NHLS test lists.
_NHLS_EXACT: dict[str, str] = {
    _normalize_text(t["test_name"]): t["test_name"] for t in matching._NHLS_TESTS
}
_LOINC_EXACT: dict[str, str] = {
    _normalize_text(t["long_common_name"]): t["long_common_name"]
    for t in matching._LOINC_TESTS
}

# Multi-word phrases that must never be split on their internal "and" —
# checked longest-first so e.g. "urea and electrolytes" isn't partially
# consumed by a shorter overlapping phrase.
_PROTECTED_PHRASES = sorted(
    ["u and e", "u & e", "urea and electrolytes"], key=len, reverse=True
)
_PROTECTED_RE = re.compile(
    "(" + "|".join(re.escape(p) for p in _PROTECTED_PHRASES) + ")", re.IGNORECASE
)
_DELIM_RE = re.compile(r"\s*(?:,|;|\.|\band\b)\s*", re.IGNORECASE)


def _split_plain(segment: str) -> list[str]:
    return [p for p in _DELIM_RE.split(segment) if p.strip()]


def split_test_items(raw: str) -> list[str]:
    """
    Tokenizes a raw "tests required" string into individual items on
    commas/semicolons/periods/"and", while protecting known multi-word
    phrases (like "U and E") from being split on their own internal "and".
    """
    if not raw or not raw.strip():
        return []

    items: list[str] = []
    pos = 0
    for m in _PROTECTED_RE.finditer(raw):
        items.extend(_split_plain(raw[pos : m.start()]))
        items.append(m.group(0).strip())
        pos = m.end()
    items.extend(_split_plain(raw[pos:]))

    return [i.strip() for i in items if i.strip()]


def normalize_test_item(item: str) -> dict:
    raw = item.strip()

    key = _abbrev_key(raw)
    if key in ABBREVIATIONS:
        return {
            "raw": raw,
            "normalized": ABBREVIATIONS[key],
            "source": "abbreviation",
            "status": "confirmed",
        }

    normalized = _normalize_text(raw)
    if normalized in _NHLS_EXACT:
        return {
            "raw": raw,
            "normalized": _NHLS_EXACT[normalized],
            "source": "nhls",
            "status": "confirmed",
        }
    if normalized in _LOINC_EXACT:
        return {
            "raw": raw,
            "normalized": _LOINC_EXACT[normalized],
            "source": "loinc",
            "status": "confirmed",
        }

    hit = matching.best_single_match(raw, min_score=FUZZY_MIN_SCORE)
    if hit is not None:
        return {
            "raw": raw,
            "normalized": hit["name"],
            "source": hit["source"],
            "status": "confirmed",
            "score": hit["score"],
        }

    # Nothing cleared the bar — keep the raw item, flag it, never guess.
    return {"raw": raw, "normalized": None, "source": None, "status": "unmatched"}


def normalize_tests_required(raw: str) -> list[dict]:
    return [normalize_test_item(item) for item in split_test_items(raw)]
