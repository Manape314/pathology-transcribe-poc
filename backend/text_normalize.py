"""
Shared text-normalization primitives used by both terminology_normalize.py
(tests_required, matched against NHLS/LOINC) and clinical_terminology.py
(clinical_history/provisional_diagnosis/medication, matched against curated
clinical/medication dictionaries). Extracted here so neither module has to
import the other's private names.
"""

import re


def normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[.,;:]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def despace_letters(s: str) -> str:
    """Collapses spoken/punctuated letter-by-letter forms ("F B C",
    "F.B.C.", "C-R-P") to a plain run ("fbc", "crp") — but ONLY when every
    token is a single letter, so ordinary multi-letter words separated by
    spaces/hyphens (e.g. "c-reactive protein") are never touched."""
    tokens = [t for t in re.split(r"[\s.\-]+", s.strip()) if t]
    if len(tokens) >= 2 and all(len(t) == 1 and t.isalpha() for t in tokens):
        return "".join(tokens)
    return s


def abbrev_key(s: str) -> str:
    key = normalize_text(s)
    key = re.sub(r"\s*&\s*", "&", key)
    return despace_letters(key)
