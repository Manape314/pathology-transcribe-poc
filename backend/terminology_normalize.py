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
    # Coagulation
    "pt": "Prothrombin Time",
    "aptt": "Activated Partial Thromboplastin Time",
    "ptt": "Activated Partial Thromboplastin Time",
    # Haematology
    "hb": "Haemoglobin",
    "hct": "Haematocrit",
    "haematocrit": "Haematocrit",
    "wcc": "White Cell Count",
    "wbc": "White Cell Count",
    "plt": "Platelet Count",
    "mcv": "Mean Corpuscular Volume",
    "mch": "Mean Corpuscular Haemoglobin",
    "mchc": "Mean Corpuscular Haemoglobin Concentration",
    "retic": "Reticulocyte Count",
    "retics": "Reticulocyte Count",
    # Chemistry / renal / liver
    "creat": "Creatinine",
    "egfr": "Estimated Glomerular Filtration Rate",
    "po4": "Phosphate",
    "phos": "Phosphate",
    "ldh": "Lactate Dehydrogenase",
    "alp": "Alkaline Phosphatase",
    "alt": "Alanine Transaminase",
    "ast": "Aspartate Transaminase",
    "ggt": "Gamma-Glutamyl Transferase",
    "hba1c": "Glycated Haemoglobin",
    # Endocrine
    "tsh": "Thyroid Stimulating Hormone",
    "ft4": "Free T4",
    "ft3": "Free T3",
    # Cardiac
    "ck": "Creatine Kinase",
    "ckmb": "Creatine Kinase MB",
    "ck-mb": "Creatine Kinase MB",
    # Microbiology
    "mcs": "Microscopy, Culture and Sensitivity",
    "mc&s": "Microscopy, Culture and Sensitivity",
    "msu": "Midstream Urine",
    # Haematology (additional)
    "rbc": "Red Cell Count",
    "rdw": "Red Cell Distribution Width",
    "neut": "Neutrophils",
    "neuts": "Neutrophils",
    "lymph": "Lymphocytes",
    "lymphs": "Lymphocytes",
    "eos": "Eosinophils",
    "baso": "Basophils",
    "mono": "Monocytes",
    "monos": "Monocytes",
    "g6pd": "Glucose-6-Phosphate Dehydrogenase",
    "fe": "Iron",
    "tibc": "Total Iron Binding Capacity",
    "tsat": "Transferrin Saturation",
    "b12": "Vitamin B12",
    "tt": "Thrombin Time",
    # Chemistry / lipids
    "glu": "Glucose",
    "chol": "Cholesterol",
    "hdl": "HDL Cholesterol",
    "ldl": "LDL Cholesterol",
    "tg": "Triglycerides",
    "trig": "Triglycerides",
    "hco3": "Bicarbonate",
    "bicarb": "Bicarbonate",
    "ag": "Anion Gap",
    "osm": "Osmolality",
    # Cardiac (additional)
    "trop": "Troponin",
    "bnp": "B-type Natriuretic Peptide",
    "nt-probnp": "N-terminal pro B-type Natriuretic Peptide",
    # Endocrine (additional)
    "fbg": "Fasting Blood Glucose",
    "fbs": "Fasting Blood Glucose",
    "ogtt": "Oral Glucose Tolerance Test",
    "acth": "Adrenocorticotropic Hormone",
    "fsh": "Follicle Stimulating Hormone",
    "lh": "Luteinizing Hormone",
    "pth": "Parathyroid Hormone",
    "vit d": "Vitamin D",
    # Tumour markers / reproductive
    "psa": "Prostate-Specific Antigen",
    "cea": "Carcinoembryonic Antigen",
    "afp": "Alpha-Fetoprotein",
    "ca125": "Cancer Antigen 125",
    "ca19-9": "Cancer Antigen 19-9",
    "hcg": "Human Chorionic Gonadotropin",
    "beta-hcg": "Human Chorionic Gonadotropin",
    # Serology / immunology
    "hbsag": "Hepatitis B Surface Antigen",
    "rpr": "Rapid Plasma Reagin",
    "vdrl": "Venereal Disease Research Laboratory Test",
    "ana": "Antinuclear Antibody",
    "rf": "Rheumatoid Factor",
    # Blood bank
    "xm": "Crossmatch",
    # NOTE: deliberately excluded as genuinely ambiguous or too
    # collision-prone for safe deterministic matching (silently guessing
    # would violate this pipeline's core "never guess" rule):
    #   - "TB" (Total Bilirubin vs. Tuberculosis)
    #   - "UA" (Uric Acid vs. Urinalysis)
    #   - single-letter electrolytes (K, Na, Ca) and "mg" (collides with
    #     the milligram dosage unit)
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
