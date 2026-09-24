"""
Normalizes the "tests required" field only — never patient/doctor names,
IDs, dates, or any other extracted field (see field_extraction.py).

Matching order:
  A/B/C. curated unambiguous abbreviation dictionary (exact, deterministic)
  D.     curated ambiguous abbreviation dictionary — ranked by context for
         DISPLAY/EXPLANATION ONLY. An abbreviation with more than one
         legitimate medical meaning is NEVER auto-resolved, no matter how
         strong the contextual evidence looks: it always comes back as
         `status: "ambiguous"` with a ranked candidate list (each carrying
         a plain-language `reason`) for the clinician to pick from.
         Automatic contextual disambiguation is intentionally deferred
         until it can be validated against a clinically reviewed dataset
         — see AMBIGUOUS_ABBREVIATIONS below.
  E.     exact canonical match against NHLS/LOINC (case/punctuation-
         insensitive)
  F.     fuzzy match, via matching.best_single_match, at a STRICTER cutoff
         than the general whole-transcript suggestion panel — this field
         assigns one canonical answer per item, so precision matters more
         here.
  H.     no confident match -> status "unrecognized", item kept with raw
         text and normalized=None. Never dropped, never guessed.

Reuses matching.py's already-loaded NHLS/LOINC data — does not reload or
duplicate it, and never fabricates a terminology code: `code`/
`terminology_system` are only ever populated by looking up an already-
loaded NHLS/LOINC entry, for both the curated-abbreviation and fuzzy
paths.
"""

import re

import matching
from text_normalize import abbrev_key as _abbrev_key
from text_normalize import normalize_text as _normalize_text

# Stricter than matching.MIN_MATCH_SCORE (80, used for the browsable
# "suggested tests" panel) — here we're assigning ONE canonical answer to a
# specific field, so a weak fuzzy hit is worse than leaving it unrecognized.
FUZZY_MIN_SCORE = 88

# Curated abbreviations with exactly one legitimate meaning — resolved
# deterministically, not probabilistically. This exists because pure fuzzy
# matching on short acronyms is unsafe — confirmed earlier: naive fuzzy
# scoring gave a 2-character junk fragment a 90% score against an
# unrelated LOINC entry. `domain` is a short fixed-vocabulary tag used
# only for display/context-ranking, not a terminology system.
ABBREVIATIONS: dict[str, dict] = {
    "fbc": {"canonical_name": "Full Blood Count", "domain": "haematology"},
    "full blood count": {"canonical_name": "Full Blood Count", "domain": "haematology"},
    "crp": {"canonical_name": "C-reactive protein", "domain": "chemical_pathology"},
    "c reactive protein": {"canonical_name": "C-reactive protein", "domain": "chemical_pathology"},
    "c-reactive protein": {"canonical_name": "C-reactive protein", "domain": "chemical_pathology"},
    "u&e": {"canonical_name": "Urea and Electrolytes", "domain": "chemical_pathology"},
    "u and e": {"canonical_name": "Urea and Electrolytes", "domain": "chemical_pathology"},
    "ue": {"canonical_name": "Urea and Electrolytes", "domain": "chemical_pathology"},
    "une": {"canonical_name": "Urea and Electrolytes", "domain": "chemical_pathology"},  # common mishearing of "U and E"
    "urea and electrolytes": {"canonical_name": "Urea and Electrolytes", "domain": "chemical_pathology"},
    "fbe": {"canonical_name": "Full Blood Examination", "domain": "haematology"},
    "lft": {"canonical_name": "Liver Function Tests", "domain": "chemical_pathology"},
    "liver function tests": {"canonical_name": "Liver Function Tests", "domain": "chemical_pathology"},
    "tft": {"canonical_name": "Thyroid Function Tests", "domain": "endocrine"},
    "thyroid function tests": {"canonical_name": "Thyroid Function Tests", "domain": "endocrine"},
    "esr": {"canonical_name": "Erythrocyte Sedimentation Rate", "domain": "haematology"},
    "inr": {"canonical_name": "International Normalized Ratio", "domain": "coagulation"},
    # Coagulation
    "pt": {"canonical_name": "Prothrombin Time", "domain": "coagulation"},
    "aptt": {"canonical_name": "Activated Partial Thromboplastin Time", "domain": "coagulation"},
    "ptt": {"canonical_name": "Activated Partial Thromboplastin Time", "domain": "coagulation"},
    "tt": {"canonical_name": "Thrombin Time", "domain": "coagulation"},
    # Haematology
    "hb": {"canonical_name": "Haemoglobin", "domain": "haematology"},
    "hct": {"canonical_name": "Haematocrit", "domain": "haematology"},
    "haematocrit": {"canonical_name": "Haematocrit", "domain": "haematology"},
    "wcc": {"canonical_name": "White Cell Count", "domain": "haematology"},
    "wbc": {"canonical_name": "White Cell Count", "domain": "haematology"},
    "plt": {"canonical_name": "Platelet Count", "domain": "haematology"},
    "mcv": {"canonical_name": "Mean Corpuscular Volume", "domain": "haematology"},
    "mch": {"canonical_name": "Mean Corpuscular Haemoglobin", "domain": "haematology"},
    "mchc": {"canonical_name": "Mean Corpuscular Haemoglobin Concentration", "domain": "haematology"},
    "retic": {"canonical_name": "Reticulocyte Count", "domain": "haematology"},
    "retics": {"canonical_name": "Reticulocyte Count", "domain": "haematology"},
    "rbc": {"canonical_name": "Red Cell Count", "domain": "haematology"},
    "rdw": {"canonical_name": "Red Cell Distribution Width", "domain": "haematology"},
    "neut": {"canonical_name": "Neutrophils", "domain": "haematology"},
    "neuts": {"canonical_name": "Neutrophils", "domain": "haematology"},
    "lymph": {"canonical_name": "Lymphocytes", "domain": "haematology"},
    "lymphs": {"canonical_name": "Lymphocytes", "domain": "haematology"},
    "eos": {"canonical_name": "Eosinophils", "domain": "haematology"},
    "baso": {"canonical_name": "Basophils", "domain": "haematology"},
    "mono": {"canonical_name": "Monocytes", "domain": "haematology"},
    "monos": {"canonical_name": "Monocytes", "domain": "haematology"},
    "g6pd": {"canonical_name": "Glucose-6-Phosphate Dehydrogenase", "domain": "haematology"},
    "fe": {"canonical_name": "Iron", "domain": "haematology"},
    "tibc": {"canonical_name": "Total Iron Binding Capacity", "domain": "haematology"},
    "tsat": {"canonical_name": "Transferrin Saturation", "domain": "haematology"},
    "b12": {"canonical_name": "Vitamin B12", "domain": "haematology"},
    # Chemistry / renal / liver
    "creat": {"canonical_name": "Creatinine", "domain": "chemical_pathology"},
    "egfr": {"canonical_name": "Estimated Glomerular Filtration Rate", "domain": "chemical_pathology"},
    "po4": {"canonical_name": "Phosphate", "domain": "chemical_pathology"},
    "phos": {"canonical_name": "Phosphate", "domain": "chemical_pathology"},
    "ldh": {"canonical_name": "Lactate Dehydrogenase", "domain": "chemical_pathology"},
    "alp": {"canonical_name": "Alkaline Phosphatase", "domain": "chemical_pathology"},
    "alt": {"canonical_name": "Alanine Transaminase", "domain": "chemical_pathology"},
    "ast": {"canonical_name": "Aspartate Transaminase", "domain": "chemical_pathology"},
    "ggt": {"canonical_name": "Gamma-Glutamyl Transferase", "domain": "chemical_pathology"},
    "glu": {"canonical_name": "Glucose", "domain": "chemical_pathology"},
    "chol": {"canonical_name": "Cholesterol", "domain": "chemical_pathology"},
    "hdl": {"canonical_name": "HDL Cholesterol", "domain": "chemical_pathology"},
    "ldl": {"canonical_name": "LDL Cholesterol", "domain": "chemical_pathology"},
    "tg": {"canonical_name": "Triglycerides", "domain": "chemical_pathology"},
    "trig": {"canonical_name": "Triglycerides", "domain": "chemical_pathology"},
    "hco3": {"canonical_name": "Bicarbonate", "domain": "chemical_pathology"},
    "bicarb": {"canonical_name": "Bicarbonate", "domain": "chemical_pathology"},
    "ag": {"canonical_name": "Anion Gap", "domain": "chemical_pathology"},
    "osm": {"canonical_name": "Osmolality", "domain": "chemical_pathology"},
    "k": {"canonical_name": "Potassium", "domain": "chemical_pathology"},
    "na": {"canonical_name": "Sodium", "domain": "chemical_pathology"},
    "mg": {"canonical_name": "Magnesium", "domain": "chemical_pathology"},
    # Endocrine
    "tsh": {"canonical_name": "Thyroid Stimulating Hormone", "domain": "endocrine"},
    "ft4": {"canonical_name": "Free T4", "domain": "endocrine"},
    "ft3": {"canonical_name": "Free T3", "domain": "endocrine"},
    "t4": {"canonical_name": "Free T4", "domain": "endocrine"},
    "t3": {"canonical_name": "Free T3", "domain": "endocrine"},
    "hba1c": {"canonical_name": "Glycated Haemoglobin", "domain": "endocrine"},
    "hb1c": {"canonical_name": "Glycated Haemoglobin", "domain": "endocrine"},  # common mishearing of HbA1c
    "fbg": {"canonical_name": "Fasting Blood Glucose", "domain": "endocrine"},
    "fbs": {"canonical_name": "Fasting Blood Glucose", "domain": "endocrine"},
    "ogtt": {"canonical_name": "Oral Glucose Tolerance Test", "domain": "endocrine"},
    "acth": {"canonical_name": "Adrenocorticotropic Hormone", "domain": "endocrine"},
    "fsh": {"canonical_name": "Follicle Stimulating Hormone", "domain": "endocrine"},
    "lh": {"canonical_name": "Luteinizing Hormone", "domain": "endocrine"},
    "pth": {"canonical_name": "Parathyroid Hormone", "domain": "endocrine"},
    "vit d": {"canonical_name": "Vitamin D", "domain": "endocrine"},
    # Cardiac
    "ck": {"canonical_name": "Creatine Kinase", "domain": "cardiac"},
    "ckmb": {"canonical_name": "Creatine Kinase MB", "domain": "cardiac"},
    "ck-mb": {"canonical_name": "Creatine Kinase MB", "domain": "cardiac"},
    "trop": {"canonical_name": "Troponin", "domain": "cardiac"},
    "bnp": {"canonical_name": "B-type Natriuretic Peptide", "domain": "cardiac"},
    "nt-probnp": {"canonical_name": "N-terminal pro B-type Natriuretic Peptide", "domain": "cardiac"},
    # Microbiology
    "mcs": {"canonical_name": "Microscopy, Culture and Sensitivity", "domain": "microbiology"},
    "mc&s": {"canonical_name": "Microscopy, Culture and Sensitivity", "domain": "microbiology"},
    "msu": {"canonical_name": "Midstream Urine", "domain": "microbiology"},
    "hbsag": {"canonical_name": "Hepatitis B Surface Antigen", "domain": "microbiology"},
    "rpr": {"canonical_name": "Rapid Plasma Reagin", "domain": "microbiology"},
    "vdrl": {"canonical_name": "Venereal Disease Research Laboratory Test", "domain": "microbiology"},
    # Tumour markers / reproductive
    "psa": {"canonical_name": "Prostate-Specific Antigen", "domain": "tumour_marker"},
    "cea": {"canonical_name": "Carcinoembryonic Antigen", "domain": "tumour_marker"},
    "afp": {"canonical_name": "Alpha-Fetoprotein", "domain": "tumour_marker"},
    "ca125": {"canonical_name": "Cancer Antigen 125", "domain": "tumour_marker"},
    "ca19-9": {"canonical_name": "Cancer Antigen 19-9", "domain": "tumour_marker"},
    "hcg": {"canonical_name": "Human Chorionic Gonadotropin", "domain": "tumour_marker"},
    "beta-hcg": {"canonical_name": "Human Chorionic Gonadotropin", "domain": "tumour_marker"},
    # Immunology
    "ana": {"canonical_name": "Antinuclear Antibody", "domain": "immunology"},
    "rf": {"canonical_name": "Rheumatoid Factor", "domain": "immunology"},
    # Blood bank
    "xm": {"canonical_name": "Crossmatch", "domain": "blood_bank"},
    # NOTE: the genuinely ambiguous abbreviations (TB, UA, PCR, MS, CA, BS,
    # BM, CP, RA, MI, CVA, PID, DM, CF, HD, ...) are NOT here — they live in
    # AMBIGUOUS_ABBREVIATIONS below instead, so they're never silently
    # resolved to one meaning. "Ca" (calcium symbol) is one of them — see
    # "ca" below, ranked against "Cancer". K/Na/Mg (above, chemical_
    # pathology) are NOT treated as ambiguous, even though "mg" also reads
    # as the milligram dosage unit: matching only ever compares a whole
    # already-split "tests required" list item (see split_test_items) — it
    # never substring-searches inside a dosage string like "500mg" — so a
    # bare "mg" appearing as its own item in that list is unambiguously
    # the Magnesium test, not a stray unit.
}

# Curated abbreviations with MORE THAN ONE legitimate medical meaning.
#
# This is intentionally a PLAIN DATA structure — {abbreviation: [{
# canonical_name, domain, keywords}, ...]} — and nothing below treats any
# particular key (e.g. "tb") specially. That's deliberate: this set (TB,
# UA, PCR, MS, CA, BS, BM, CP, RA, MI, CVA, PID, DM, CF, HD) covers the
# well-documented genuinely-ambiguous medical abbreviations relevant to a
# pathology request, not an exhaustive enumeration of every abbreviation
# that could theoretically have a second meaning. Growing it further — or
# generating/importing it from a validated terminology resource —
# importing it from a validated terminology resource later — only means
# adding more entries in this exact shape (the same way
# backend/data/nhls_tests.json / loinc_terms.json are loaded as plain data
# by matching.py); the resolver logic in normalize_test_item() below reads
# whatever is in this dict and needs no changes to support a larger set.
#
# `keywords` are used ONLY to rank/explain candidates for the clinician —
# see normalize_test_item's "D" branch. They are NEVER used to
# automatically pick a meaning; every ambiguous abbreviation always
# requires clinician confirmation, regardless of how one-sided the
# contextual evidence looks. That's a deliberate product decision, not a
# missing feature — automatic contextual disambiguation is deferred until
# it can be validated against a clinically reviewed dataset.
AMBIGUOUS_ABBREVIATIONS: dict[str, list[dict]] = {
    "tb": [
        {
            "canonical_name": "Total Bilirubin",
            "domain": "chemical_pathology",
            "keywords": ["liver", "jaundice", "bilirubin", "hepatic", "lft", "liver function"],
        },
        {
            "canonical_name": "Tuberculosis",
            "domain": "clinical_diagnosis",
            "keywords": ["tuberculosis", "cough", "chest", "sputum", "weight loss", "night sweats"],
        },
    ],
    "ua": [
        {
            "canonical_name": "Uric Acid",
            "domain": "chemical_pathology",
            "keywords": ["gout", "uric", "joint", "arthritis"],
        },
        {
            "canonical_name": "Urinalysis",
            "domain": "urinalysis",
            "keywords": ["urine", "bladder", "urinary", "dipstick", "msu"],
        },
    ],
    "pcr": [
        {
            "canonical_name": "Polymerase Chain Reaction",
            "domain": "microbiology",
            "keywords": ["viral", "swab", "molecular", "covid", "infection"],
        },
        {
            "canonical_name": "Protein Creatinine Ratio",
            "domain": "chemical_pathology",
            "keywords": ["urine", "protein", "creatinine", "proteinuria", "renal", "kidney"],
        },
    ],
    "ms": [
        {
            "canonical_name": "Multiple Sclerosis",
            "domain": "clinical_diagnosis",
            "keywords": ["neurological", "sclerosis", "demyelinating", "numbness", "weakness"],
        },
        {
            "canonical_name": "Mitral Stenosis",
            "domain": "clinical_diagnosis",
            "keywords": ["cardiac", "valve", "mitral", "murmur", "heart"],
        },
        {
            "canonical_name": "Morphine Sulphate",
            "domain": "medication",
            "keywords": ["pain", "analgesia", "opioid", "morphine"],
        },
    ],
    "ca": [
        {
            "canonical_name": "Calcium",
            "domain": "chemical_pathology",
            "keywords": ["bone", "parathyroid", "calcium", "hypocalcaemia", "hypercalcaemia"],
        },
        {
            "canonical_name": "Cancer",
            "domain": "clinical_diagnosis",
            "keywords": ["tumour", "tumor", "malignancy", "oncology", "mass"],
        },
    ],
    "bs": [
        {
            "canonical_name": "Blood Sugar",
            "domain": "chemical_pathology",
            "keywords": ["diabetes", "glucose", "hypoglycaemia", "hyperglycaemia"],
        },
        {
            "canonical_name": "Bowel Sounds",
            "domain": "clinical_diagnosis",
            "keywords": ["abdomen", "bowel", "obstruction", "ileus"],
        },
    ],
    "bm": [
        {
            "canonical_name": "Bone Marrow",
            "domain": "haematology",
            "keywords": ["leukaemia", "lymphoma", "marrow", "cytopenia", "biopsy"],
        },
        {
            "canonical_name": "Blood Glucose Monitoring",
            "domain": "endocrine",
            "keywords": ["diabetes", "glucose", "insulin", "monitoring"],
        },
    ],
    "cp": [
        {
            "canonical_name": "C-Peptide",
            "domain": "endocrine",
            "keywords": ["diabetes", "insulin", "pancreas", "c-peptide"],
        },
        {
            "canonical_name": "Chest Pain",
            "domain": "clinical_diagnosis",
            "keywords": ["cardiac", "chest", "angina", "dyspnoea"],
        },
    ],
    "ra": [
        {
            "canonical_name": "Rheumatoid Arthritis",
            "domain": "clinical_diagnosis",
            "keywords": ["joint", "arthritis", "rheumatoid", "synovitis"],
        },
        {
            "canonical_name": "Renal Artery Stenosis",
            "domain": "clinical_diagnosis",
            "keywords": ["renal", "kidney", "hypertension", "stenosis"],
        },
    ],
    "mi": [
        {
            "canonical_name": "Myocardial Infarction",
            "domain": "clinical_diagnosis",
            "keywords": ["chest pain", "cardiac", "troponin", "infarction", "ecg"],
        },
        {
            "canonical_name": "Mitral Insufficiency",
            "domain": "clinical_diagnosis",
            "keywords": ["murmur", "valve", "mitral", "regurgitation"],
        },
    ],
    "cva": [
        {
            "canonical_name": "Cerebrovascular Accident",
            "domain": "clinical_diagnosis",
            "keywords": ["stroke", "neurological", "hemiplegia", "weakness"],
        },
        {
            "canonical_name": "Costovertebral Angle Tenderness",
            "domain": "clinical_diagnosis",
            "keywords": ["flank", "renal", "pyelonephritis", "back"],
        },
    ],
    "pid": [
        {
            "canonical_name": "Pelvic Inflammatory Disease",
            "domain": "clinical_diagnosis",
            "keywords": ["pelvic", "gynaecological", "discharge", "cervical"],
        },
        {
            "canonical_name": "Prolapsed Intervertebral Disc",
            "domain": "clinical_diagnosis",
            "keywords": ["back", "spine", "radiculopathy", "sciatica"],
        },
    ],
    "dm": [
        {
            "canonical_name": "Diabetes Mellitus",
            "domain": "endocrine",
            "keywords": ["diabetes", "glucose", "insulin", "polyuria"],
        },
        {
            "canonical_name": "Dermatomyositis",
            "domain": "immunology",
            "keywords": ["muscle", "rash", "myositis", "skin"],
        },
    ],
    "cf": [
        {
            "canonical_name": "Cystic Fibrosis",
            "domain": "clinical_diagnosis",
            "keywords": ["respiratory", "sputum", "pancreatic", "sweat test"],
        },
        {
            "canonical_name": "Cardiac Failure",
            "domain": "clinical_diagnosis",
            "keywords": ["heart failure", "oedema", "dyspnoea", "cardiac"],
        },
        {
            "canonical_name": "Complement Fixation",
            "domain": "microbiology",
            "keywords": ["complement", "serology", "antibody"],
        },
    ],
    "hd": [
        {
            "canonical_name": "Hemodialysis",
            "domain": "chemical_pathology",
            "keywords": ["dialysis", "renal failure", "esrd"],
        },
        {
            "canonical_name": "Huntington's Disease",
            "domain": "clinical_diagnosis",
            "keywords": ["chorea", "neurological", "huntington"],
        },
        {
            "canonical_name": "Heart Disease",
            "domain": "clinical_diagnosis",
            "keywords": ["cardiac", "heart", "coronary"],
        },
    ],
    "wb": [
        {
            "canonical_name": "White Cell Count",
            "domain": "haematology",
            "keywords": ["infection", "sepsis", "leukocytosis", "fbc"],
        },
        {
            "canonical_name": "Western Blot",
            "domain": "microbiology",
            "keywords": ["hiv", "confirmatory", "antibody", "serology"],
        },
    ],
}


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
# Real LOINC codes, looked up by normalized canonical name — this is the
# ONLY source of `code` values returned to callers. Nothing is ever
# hardcoded per-abbreviation, so nothing can be an invented code.
_LOINC_CODE_BY_NAME: dict[str, str] = {
    _normalize_text(t["long_common_name"]): t["loinc_num"] for t in matching._LOINC_TESTS
}


def _resolve_terminology_code(canonical_name: str) -> tuple[str | None, str | None]:
    """(terminology_system, code) for a canonical name, looked up against
    already-loaded NHLS/LOINC data — never fabricated. NHLS has no codes
    in our source data (confirmed: the handbook PDF doesn't have any), so
    an NHLS match always returns code=None with terminology_system="NHLS"
    — that's honest, not a bug."""
    normalized = _normalize_text(canonical_name)
    if normalized in _NHLS_EXACT:
        return "NHLS", None
    if normalized in _LOINC_EXACT:
        return "LOINC", _LOINC_CODE_BY_NAME.get(normalized)

    hit = matching.best_single_match(canonical_name, min_score=FUZZY_MIN_SCORE)
    if hit is not None:
        if hit["source"] == "nhls":
            return "NHLS", None
        return "LOINC", _LOINC_CODE_BY_NAME.get(_normalize_text(hit["name"]))
    return None, None


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


def _context_text(context: dict | None, sibling_raw: str) -> str:
    """Assembles the keyword-search text used to RANK (never auto-select)
    ambiguous candidates: the full raw "tests required" list (so sibling
    test names count as context too) plus clinical_history/
    provisional_diagnosis/specimen_type/department's raw values, if
    extracted. `context` is field_extraction's in-progress `result` dict
    (passthrough fields are `{"raw","value",...}` dicts) — see
    field_extraction.py's two-pass extraction."""
    parts = [sibling_raw or ""]
    if context:
        for key in ("clinical_history", "provisional_diagnosis", "specimen_type", "department"):
            field = context.get(key)
            if isinstance(field, dict):
                parts.append(field.get("value") or "")
    return " ".join(parts).lower()


def _rank_ambiguous_candidates(candidates: list[dict], context_text: str) -> list[dict]:
    """Orders candidates by keyword overlap with context, purely for
    display order and the human-readable `reason` shown to the clinician —
    this ranking is NEVER used to auto-pick a meaning (see module
    docstring and AMBIGUOUS_ABBREVIATIONS's comment)."""
    scored = []
    for cand in candidates:
        matched = [kw for kw in cand["keywords"] if kw in context_text]
        scored.append({**cand, "matched_keywords": matched})
    scored.sort(key=lambda c: len(c["matched_keywords"]), reverse=True)
    return scored


def _base_result(raw: str) -> dict:
    return {
        "raw": raw,
        "normalized": None,
        "source": None,
        "terminology_system": None,
        "code": None,
        "match_type": None,
        "confidence": None,
        "status": "unrecognized",
        "confirmation_status": "pending",
        "candidates": None,
        "reason": None,
    }


def normalize_test_item(raw_item: str, context_text: str = "") -> dict:
    raw = raw_item.strip()
    key = _abbrev_key(raw)
    result = _base_result(raw)

    # A/B/C: curated unambiguous abbreviation — deterministic, no scoring.
    if key in ABBREVIATIONS:
        entry = ABBREVIATIONS[key]
        terminology_system, code = _resolve_terminology_code(entry["canonical_name"])
        result.update(
            normalized=entry["canonical_name"],
            source="abbreviation",
            terminology_system=terminology_system,
            code=code,
            match_type="known_abbreviation",
            confidence=1.0,
            status="confirmed",
            confirmation_status="automatic",
        )
        return result

    # D: ambiguous abbreviation. ALWAYS returns status "ambiguous" with a
    # ranked, explained candidate list — context never auto-selects one,
    # no matter how one-sided it looks (see module docstring).
    if key in AMBIGUOUS_ABBREVIATIONS:
        ranked = _rank_ambiguous_candidates(AMBIGUOUS_ABBREVIATIONS[key], context_text)
        candidates_out = []
        for cand in ranked:
            terminology_system, code = _resolve_terminology_code(cand["canonical_name"])
            reason = (
                "context matched: " + ", ".join(cand["matched_keywords"])
                if cand["matched_keywords"]
                else "no supporting context found"
            )
            candidates_out.append(
                {
                    "canonical_name": cand["canonical_name"],
                    "domain": cand["domain"],
                    "terminology_system": terminology_system,
                    "code": code,
                    "reason": reason,
                }
            )
        result.update(
            match_type="ambiguous_abbreviation",
            status="ambiguous",
            confirmation_status="pending",
            candidates=candidates_out,
        )
        return result

    # E: exact canonical NHLS/LOINC name match.
    normalized_text = _normalize_text(raw)
    if normalized_text in _NHLS_EXACT:
        result.update(
            normalized=_NHLS_EXACT[normalized_text],
            source="nhls",
            terminology_system="NHLS",
            match_type="canonical_match",
            confidence=1.0,
            status="confirmed",
            confirmation_status="automatic",
        )
        return result
    if normalized_text in _LOINC_EXACT:
        name = _LOINC_EXACT[normalized_text]
        result.update(
            normalized=name,
            source="loinc",
            terminology_system="LOINC",
            code=_LOINC_CODE_BY_NAME.get(normalized_text),
            match_type="canonical_match",
            confidence=1.0,
            status="confirmed",
            confirmation_status="automatic",
        )
        return result

    # F: fuzzy fallback, stricter cutoff than the general suggestion panel.
    hit = matching.best_single_match(raw, min_score=FUZZY_MIN_SCORE)
    if hit is not None:
        terminology_system = "NHLS" if hit["source"] == "nhls" else "LOINC"
        code = (
            None
            if hit["source"] == "nhls"
            else _LOINC_CODE_BY_NAME.get(_normalize_text(hit["name"]))
        )
        result.update(
            normalized=hit["name"],
            source=hit["source"],
            terminology_system=terminology_system,
            code=code,
            match_type="fuzzy_match",
            confidence=round(hit["score"] / 100, 2),
            status="confirmed",
            confirmation_status="automatic",
        )
        return result

    # H: nothing resolves — keep the raw item, flag it, never guess.
    return result


def normalize_tests_required(raw: str, context: dict | None = None) -> list[dict]:
    context_text = _context_text(context, raw)
    return [normalize_test_item(item, context_text) for item in split_test_items(raw)]
