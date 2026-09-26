"""
Field-aware abbreviation resolution for free-text clinical fields —
clinical_history, provisional_diagnosis, and medication. This is a sibling
to terminology_normalize.py (which stays focused on tests_required against
NHLS/LOINC) rather than a replacement: lab-test abbreviations, clinical
diagnosis/symptom abbreviations, and medication dosing shorthand are three
different vocabulary domains, so they get their own curated dictionaries
here (CLINICAL_ABBREVIATIONS, MEDICATION_ABBREVIATIONS) — but genuinely
ambiguous abbreviations are NOT duplicated per field: this module reuses
terminology_normalize.AMBIGUOUS_ABBREVIATIONS directly, so the same set of
legitimate meanings is offered no matter which field an abbreviation
appeared in, and the clinician always makes the final call. Context may
rank candidates for display; it never auto-selects one.

Unlike tests_required (a discrete, comma-delimited list of items),
clinical_history/provisional_diagnosis/medication are free-flowing prose.
resolve_field_text() scans that prose for EXACT, word-boundary matches
against the curated dictionaries below (never fuzzy matching against a
corpus — that's what makes it safe to run over free text; naive
whole-transcript fuzzy matching was deliberately removed from this project
earlier for producing unrelated suggestions from ordinary prose). A word
that isn't in one of these dictionaries is never flagged, scored, or
altered — it passes through untouched.

terminology_system/code are always None here: SNOMED CT is not integrated
into this project (see README's "out of scope"), and nothing here ever
fabricates a code. The shape is deliberately SNOMED-pluggable: a future
coded-terminology lookup could populate terminology_system/code for a
confirmed match without changing this module's public shape.

Never applied to patient_name, patient_id, hpcsa_number,
requesting_doctor, ward, hospital, specimen_type, specimen_site, priority,
or department — see field_extraction.py, which only routes
clinical_history/provisional_diagnosis/medication through this module.
"""

import re

from terminology_normalize import AMBIGUOUS_ABBREVIATIONS, _rank_ambiguous_candidates
from text_normalize import abbrev_key

# Diagnosis/symptom/history shorthand — NOT lab tests (those stay in
# terminology_normalize.ABBREVIATIONS). Seeded from common, genuinely
# unambiguous clinical dictation shorthand.
CLINICAL_ABBREVIATIONS: dict[str, dict] = {
    "sob": {"canonical_name": "Shortness of Breath", "domain": "symptom"},
    "soboe": {"canonical_name": "Shortness of Breath on Exertion", "domain": "symptom"},
    "htn": {"canonical_name": "Hypertension", "domain": "clinical_diagnosis"},
    "ckd": {"canonical_name": "Chronic Kidney Disease", "domain": "clinical_diagnosis"},
    "esrf": {"canonical_name": "End-Stage Renal Failure", "domain": "clinical_diagnosis"},
    "esrd": {"canonical_name": "End-Stage Renal Disease", "domain": "clinical_diagnosis"},
    "aki": {"canonical_name": "Acute Kidney Injury", "domain": "clinical_diagnosis"},
    "copd": {"canonical_name": "Chronic Obstructive Pulmonary Disease", "domain": "clinical_diagnosis"},
    "cad": {"canonical_name": "Coronary Artery Disease", "domain": "clinical_diagnosis"},
    "cabg": {"canonical_name": "Coronary Artery Bypass Graft", "domain": "clinical_diagnosis"},
    "pe": {"canonical_name": "Pulmonary Embolism", "domain": "clinical_diagnosis"},
    "dvt": {"canonical_name": "Deep Vein Thrombosis", "domain": "clinical_diagnosis"},
    "uti": {"canonical_name": "Urinary Tract Infection", "domain": "clinical_diagnosis"},
    "urti": {"canonical_name": "Upper Respiratory Tract Infection", "domain": "clinical_diagnosis"},
    "lrti": {"canonical_name": "Lower Respiratory Tract Infection", "domain": "clinical_diagnosis"},
    "gord": {"canonical_name": "Gastro-Oesophageal Reflux Disease", "domain": "clinical_diagnosis"},
    "gerd": {"canonical_name": "Gastro-Oesophageal Reflux Disease", "domain": "clinical_diagnosis"},
    "ibs": {"canonical_name": "Irritable Bowel Syndrome", "domain": "clinical_diagnosis"},
    "ibd": {"canonical_name": "Inflammatory Bowel Disease", "domain": "clinical_diagnosis"},
    "af": {"canonical_name": "Atrial Fibrillation", "domain": "clinical_diagnosis"},
    "ccf": {"canonical_name": "Congestive Cardiac Failure", "domain": "clinical_diagnosis"},
    "chf": {"canonical_name": "Congestive Heart Failure", "domain": "clinical_diagnosis"},
    "dka": {"canonical_name": "Diabetic Ketoacidosis", "domain": "clinical_diagnosis"},
    "t1dm": {"canonical_name": "Type 1 Diabetes Mellitus", "domain": "clinical_diagnosis"},
    "t2dm": {"canonical_name": "Type 2 Diabetes Mellitus", "domain": "clinical_diagnosis"},
    "oa": {"canonical_name": "Osteoarthritis", "domain": "clinical_diagnosis"},
    "hiv": {"canonical_name": "Human Immunodeficiency Virus", "domain": "clinical_diagnosis"},
    "nkda": {"canonical_name": "No Known Drug Allergies", "domain": "allergy_history"},
    "nad": {"canonical_name": "No Abnormality Detected", "domain": "clinical_finding"},
    "loc": {"canonical_name": "Loss of Consciousness", "domain": "symptom"},
    "n&v": {"canonical_name": "Nausea and Vomiting", "domain": "symptom"},
    "pmh": {"canonical_name": "Past Medical History", "domain": "history"},
    "fhx": {"canonical_name": "Family History", "domain": "history"},
    "shx": {"canonical_name": "Social History", "domain": "history"},
    "nbm": {"canonical_name": "Nil by Mouth", "domain": "clinical_instruction"},
    "bp": {"canonical_name": "Blood Pressure", "domain": "vital_sign"},
    "spo2": {"canonical_name": "Oxygen Saturation", "domain": "vital_sign"},
    "sats": {"canonical_name": "Oxygen Saturation", "domain": "vital_sign"},
    "gcs": {"canonical_name": "Glasgow Coma Scale", "domain": "clinical_finding"},
    "bmi": {"canonical_name": "Body Mass Index", "domain": "clinical_finding"},
    "bsa": {"canonical_name": "Body Surface Area", "domain": "clinical_finding"},
    "tia": {"canonical_name": "Transient Ischaemic Attack", "domain": "clinical_diagnosis"},
    "pci": {"canonical_name": "Percutaneous Coronary Intervention", "domain": "clinical_diagnosis"},
    "acs": {"canonical_name": "Acute Coronary Syndrome", "domain": "clinical_diagnosis"},
    "stemi": {"canonical_name": "ST-Elevation Myocardial Infarction", "domain": "clinical_diagnosis"},
    "nstemi": {"canonical_name": "Non-ST-Elevation Myocardial Infarction", "domain": "clinical_diagnosis"},
    "hld": {"canonical_name": "Hyperlipidaemia", "domain": "clinical_diagnosis"},
    "osa": {"canonical_name": "Obstructive Sleep Apnoea", "domain": "clinical_diagnosis"},
    "gdm": {"canonical_name": "Gestational Diabetes Mellitus", "domain": "clinical_diagnosis"},
    "pcos": {"canonical_name": "Polycystic Ovary Syndrome", "domain": "clinical_diagnosis"},
    "pud": {"canonical_name": "Peptic Ulcer Disease", "domain": "clinical_diagnosis"},
    "aaa": {"canonical_name": "Abdominal Aortic Aneurysm", "domain": "clinical_diagnosis"},
    "mva": {"canonical_name": "Motor Vehicle Accident", "domain": "history"},
    "nof": {"canonical_name": "Neck of Femur (Fracture)", "domain": "clinical_diagnosis"},
    "ftt": {"canonical_name": "Failure to Thrive", "domain": "clinical_diagnosis"},
    "iugr": {"canonical_name": "Intrauterine Growth Restriction", "domain": "clinical_diagnosis"},
    "pph": {"canonical_name": "Postpartum Haemorrhage", "domain": "clinical_diagnosis"},
    "aph": {"canonical_name": "Antepartum Haemorrhage", "domain": "clinical_diagnosis"},
    "ihd": {"canonical_name": "Ischaemic Heart Disease", "domain": "clinical_diagnosis"},
    "pvd": {"canonical_name": "Peripheral Vascular Disease", "domain": "clinical_diagnosis"},
    "crf": {"canonical_name": "Chronic Renal Failure", "domain": "clinical_diagnosis"},
    "arf": {"canonical_name": "Acute Renal Failure", "domain": "clinical_diagnosis"},
    "ild": {"canonical_name": "Interstitial Lung Disease", "domain": "clinical_diagnosis"},
    "n/v": {"canonical_name": "Nausea and Vomiting", "domain": "symptom"},
    # Deliberately NOT added: "HR" for Heart Rate — collides with the very
    # common "hr"/"hrs" abbreviation for "hour(s)" in ordinary clinical
    # prose (e.g. "pain for 6 hrs"), which would get wrongly expanded.
    # "RR" (Respiratory Rate vs. Regular Rhythm on cardiac exam) is
    # genuinely ambiguous, so it lives in the shared
    # terminology_normalize.AMBIGUOUS_ABBREVIATIONS instead.
}

# Dosing/route/frequency shorthand — a third, distinct vocabulary domain
# from both lab tests and clinical diagnoses. Real dosing shorthand in
# common use doesn't have competing medical meanings the way TB/DM/MS do,
# so there's no AMBIGUOUS_MEDICATION_ABBREVIATIONS dict yet; one could be
# added later with zero changes to resolve_field_text().
MEDICATION_ABBREVIATIONS: dict[str, dict] = {
    "od": {"canonical_name": "Once Daily", "domain": "dosing_frequency"},
    "bd": {"canonical_name": "Twice Daily", "domain": "dosing_frequency"},
    "tds": {"canonical_name": "Three Times Daily", "domain": "dosing_frequency"},
    "tid": {"canonical_name": "Three Times Daily", "domain": "dosing_frequency"},
    "qds": {"canonical_name": "Four Times Daily", "domain": "dosing_frequency"},
    "qid": {"canonical_name": "Four Times Daily", "domain": "dosing_frequency"},
    "prn": {"canonical_name": "As Needed", "domain": "dosing_frequency"},
    "stat": {"canonical_name": "Immediately", "domain": "dosing_frequency"},
    "mane": {"canonical_name": "In the Morning", "domain": "dosing_frequency"},
    "nocte": {"canonical_name": "At Night", "domain": "dosing_frequency"},
    "iv": {"canonical_name": "Intravenous", "domain": "dosing_route"},
    "im": {"canonical_name": "Intramuscular", "domain": "dosing_route"},
    "po": {"canonical_name": "By Mouth", "domain": "dosing_route"},
    "sc": {"canonical_name": "Subcutaneous", "domain": "dosing_route"},
    "sl": {"canonical_name": "Sublingual", "domain": "dosing_route"},
    "pr": {"canonical_name": "Per Rectum", "domain": "dosing_route"},
    "ac": {"canonical_name": "Before Meals", "domain": "dosing_frequency"},
    "pc": {"canonical_name": "After Meals", "domain": "dosing_frequency"},
    "hs": {"canonical_name": "At Bedtime", "domain": "dosing_frequency"},
    "ud": {"canonical_name": "As Directed", "domain": "dosing_frequency"},
    "gtt": {"canonical_name": "Drops", "domain": "dosing_form"},
    "ung": {"canonical_name": "Ointment", "domain": "dosing_form"},
    "supp": {"canonical_name": "Suppository", "domain": "dosing_form"},
    "neb": {"canonical_name": "Nebulised", "domain": "dosing_route"},
    "ng": {"canonical_name": "Nasogastric", "domain": "dosing_route"},
    "ppi": {"canonical_name": "Proton Pump Inhibitor", "domain": "drug_class"},
    "nsaid": {"canonical_name": "Non-Steroidal Anti-Inflammatory Drug", "domain": "drug_class"},
    "acei": {"canonical_name": "ACE Inhibitor", "domain": "drug_class"},
    "arb": {"canonical_name": "Angiotensin Receptor Blocker", "domain": "drug_class"},
    "ccb": {"canonical_name": "Calcium Channel Blocker", "domain": "drug_class"},
    "abx": {"canonical_name": "Antibiotics", "domain": "drug_class"},
    "coc": {"canonical_name": "Combined Oral Contraceptive", "domain": "drug_class"},
    # Deliberately NOT added: "top" for "Topically" — collides with the
    # ordinary phrase "top dose" (e.g. "furosemide top dose reached"),
    # which would get wrongly expanded. Spell out "topically" instead.
}


def _base_result(raw_phrase: str, field: str) -> dict:
    return {
        "raw_phrase": raw_phrase,
        "normalized_term": None,
        "field": field,
        "terminology_system": None,
        "code": None,
        "match_type": None,
        "confidence": None,
        "status": "unrecognized",
        "confirmation_status": "pending",
        "candidates": None,
        "reason": None,
        "provenance": None,
    }


def _resolve_clinical_term(
    raw_phrase: str,
    key: str,
    unambiguous_dict: dict,
    field: str,
    context_text: str,
    provenance: str,
) -> dict | None:
    """Returns a resolution dict if `key` is a recognized abbreviation
    (curated unambiguous or curated ambiguous), else None — a None result
    means the caller should leave the matched text completely untouched
    (this only happens for AMBIGUOUS_ABBREVIATIONS keys that aren't
    actually reachable here, which can't occur since the scan regex is
    built from exactly these two dictionaries; kept for safety)."""
    result = _base_result(raw_phrase, field)

    if key in unambiguous_dict:
        entry = unambiguous_dict[key]
        result.update(
            normalized_term=entry["canonical_name"],
            match_type="known_abbreviation",
            confidence=1.0,
            status="confirmed",
            confirmation_status="automatic",
            provenance=provenance,
        )
        return result

    if key in AMBIGUOUS_ABBREVIATIONS:
        ranked = _rank_ambiguous_candidates(AMBIGUOUS_ABBREVIATIONS[key], context_text)
        candidates_out = []
        for cand in ranked:
            reason = (
                "context matched: " + ", ".join(cand["matched_keywords"])
                if cand["matched_keywords"]
                else "no supporting context found"
            )
            candidates_out.append(
                {
                    "canonical_name": cand["canonical_name"],
                    "domain": cand["domain"],
                    "terminology_system": None,
                    "code": None,
                    "reason": reason,
                }
            )
        result.update(
            match_type="ambiguous_abbreviation",
            status="ambiguous",
            confirmation_status="pending",
            candidates=candidates_out,
            provenance="curated ambiguous abbreviation dictionary (shared across fields)",
        )
        return result

    return None


_FIELD_REGEX_CACHE: dict[int, re.Pattern] = {}


def _field_regex(unambiguous_dict: dict) -> re.Pattern:
    cache_key = id(unambiguous_dict)
    if cache_key not in _FIELD_REGEX_CACHE:
        keys = sorted(
            set(unambiguous_dict.keys()) | set(AMBIGUOUS_ABBREVIATIONS.keys()),
            key=len,
            reverse=True,
        )
        pattern = r"\b(?:" + "|".join(re.escape(k) for k in keys) + r")\b"
        _FIELD_REGEX_CACHE[cache_key] = re.compile(pattern, re.IGNORECASE)
    return _FIELD_REGEX_CACHE[cache_key]


def _context_text_for(context: dict | None, exclude_field: str) -> str:
    """Joins raw values of sibling fields (never the field being resolved
    itself) for ranking-only ambiguity candidates — same philosophy as
    terminology_normalize._context_text, generalized across fields. Reads
    the "_raw" keys for clinical_history/provisional_diagnosis/
    tests_required specifically because, during extract_fields()'s second
    pass, siblings may not have been resolved into their final dict shape
    yet depending on iteration order — the raw text is always present by
    then, regardless of order."""
    if not context:
        return ""
    parts = []
    for raw_key in ("clinical_history_raw", "provisional_diagnosis_raw", "tests_required_raw"):
        field_key = raw_key.removesuffix("_raw")
        if field_key == exclude_field:
            continue
        parts.append(context.get(raw_key) or "")
    for field_key in ("specimen_type", "department"):
        if field_key == exclude_field:
            continue
        field = context.get(field_key)
        if isinstance(field, dict):
            parts.append(field.get("raw") or field.get("value") or "")
    return " ".join(parts).lower()


def resolve_field_text(
    raw_text: str, field: str, unambiguous_dict: dict, context: dict | None = None
) -> dict:
    raw_text = raw_text or ""
    provenance = (
        "curated clinical abbreviation dictionary"
        if unambiguous_dict is CLINICAL_ABBREVIATIONS
        else "curated medication abbreviation dictionary"
    )
    context_text = _context_text_for(context, field)
    regex = _field_regex(unambiguous_dict)

    resolved_terms: list[dict] = []
    pieces: list[str] = []
    last_end = 0

    for m in regex.finditer(raw_text):
        raw_phrase = m.group(0)
        key = abbrev_key(raw_phrase)
        resolution = _resolve_clinical_term(
            raw_phrase, key, unambiguous_dict, field, context_text, provenance
        )
        if resolution is None:
            continue

        pieces.append(raw_text[last_end : m.start()])
        if resolution["status"] == "confirmed":
            if raw_phrase.strip().lower() != resolution["normalized_term"].strip().lower():
                pieces.append(f"{resolution['normalized_term']} ({raw_phrase})")
            else:
                pieces.append(resolution["normalized_term"])
        else:  # ambiguous
            pieces.append(f"{raw_phrase} [ambiguous — please confirm]")
        last_end = m.end()
        resolved_terms.append(resolution)

    pieces.append(raw_text[last_end:])
    annotated_text = "".join(pieces)

    return {
        "raw": raw_text,
        "value": annotated_text,
        "status": "extracted",
        "resolved_terms": resolved_terms,
    }
