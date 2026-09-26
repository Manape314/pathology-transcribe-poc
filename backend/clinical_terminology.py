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
    # Cross-checked against an external medical abbreviation reference.
    "acl": {"canonical_name": "Anterior Cruciate Ligament", "domain": "anatomical"},
    "adhd": {"canonical_name": "Attention-Deficit/Hyperactivity Disorder", "domain": "clinical_diagnosis"},
    "adl": {"canonical_name": "Activities of Daily Living", "domain": "history"},
    "aed": {"canonical_name": "Automated External Defibrillator", "domain": "history"},
    "afib": {"canonical_name": "Atrial Fibrillation", "domain": "clinical_diagnosis"},
    "aids": {"canonical_name": "Acquired Immunodeficiency Syndrome", "domain": "clinical_diagnosis"},
    "als": {"canonical_name": "Amyotrophic Lateral Sclerosis", "domain": "clinical_diagnosis"},
    "ama": {"canonical_name": "Against Medical Advice", "domain": "history"},
    "ami": {"canonical_name": "Acute Myocardial Infarction", "domain": "clinical_diagnosis"},
    "ards": {"canonical_name": "Acute Respiratory Distress Syndrome", "domain": "clinical_diagnosis"},
    "bcg": {"canonical_name": "Bacillus Calmette-Guerin (Vaccine)", "domain": "vaccination_history"},
    "bmr": {"canonical_name": "Basal Metabolic Rate", "domain": "clinical_finding"},
    "bph": {"canonical_name": "Benign Prostatic Hyperplasia", "domain": "clinical_diagnosis"},
    "c/o": {"canonical_name": "Complains of", "domain": "history"},
    "cc": {"canonical_name": "Chief Complaint", "domain": "history"},
    "cns": {"canonical_name": "Central Nervous System", "domain": "anatomical"},
    "cpap": {"canonical_name": "Continuous Positive Airway Pressure", "domain": "history"},
    "cpr": {"canonical_name": "Cardiopulmonary Resuscitation", "domain": "history"},
    "csf": {"canonical_name": "Cerebrospinal Fluid", "domain": "anatomical"},
    "dnr": {"canonical_name": "Do Not Resuscitate", "domain": "clinical_instruction"},
    "doa": {"canonical_name": "Dead on Arrival", "domain": "clinical_finding"},
    "dx": {"canonical_name": "Diagnosis", "domain": "history"},
    "ems": {"canonical_name": "Emergency Medical Services", "domain": "history"},
    "ent": {"canonical_name": "Ear, Nose, and Throat", "domain": "anatomical"},
    "fhr": {"canonical_name": "Fetal Heart Rate", "domain": "obstetric"},
    "fx": {"canonical_name": "Fracture", "domain": "clinical_diagnosis"},
    "gi": {"canonical_name": "Gastrointestinal", "domain": "anatomical"},
    "heent": {"canonical_name": "Head, Eyes, Ears, Nose, and Throat", "domain": "history"},
    "hx": {"canonical_name": "History", "domain": "history"},
    "i&o": {"canonical_name": "Intakes and Outputs", "domain": "monitoring"},
    "icu": {"canonical_name": "Intensive Care Unit", "domain": "history"},
    "iddm": {"canonical_name": "Type 1 Diabetes Mellitus", "domain": "clinical_diagnosis"},  # older term
    "iop": {"canonical_name": "Intraocular Pressure", "domain": "ophthalmology"},
    "jvd": {"canonical_name": "Jugular Venous Distension", "domain": "clinical_finding"},
    "l&d": {"canonical_name": "Labor and Delivery", "domain": "obstetric"},
    "llq": {"canonical_name": "Left Lower Quadrant", "domain": "anatomical"},
    "luq": {"canonical_name": "Left Upper Quadrant", "domain": "anatomical"},
    "lvad": {"canonical_name": "Left Ventricular Assist Device", "domain": "cardiac"},
    "mdr-tb": {"canonical_name": "Multidrug-Resistant Tuberculosis", "domain": "clinical_diagnosis"},
    "mmr": {"canonical_name": "Measles, Mumps, and Rubella (Vaccine)", "domain": "vaccination_history"},
    "mrsa": {"canonical_name": "Methicillin-Resistant Staphylococcus Aureus", "domain": "microbiology"},
    "mvp": {"canonical_name": "Mitral Valve Prolapse", "domain": "clinical_diagnosis"},
    "niddm": {"canonical_name": "Type 2 Diabetes Mellitus", "domain": "clinical_diagnosis"},  # older term
    "npo": {"canonical_name": "Nil by Mouth", "domain": "clinical_instruction"},
    "nsr": {"canonical_name": "Normal Sinus Rhythm", "domain": "clinical_finding"},
    "pac": {"canonical_name": "Premature Atrial Contraction", "domain": "cardiac"},
    "pku": {"canonical_name": "Phenylketonuria", "domain": "clinical_diagnosis"},
    "pns": {"canonical_name": "Peripheral Nervous System", "domain": "anatomical"},
    "ppe": {"canonical_name": "Personal Protective Equipment", "domain": "infection_control"},
    "px": {"canonical_name": "Prognosis", "domain": "history"},
    "rds": {"canonical_name": "Respiratory Distress Syndrome (Neonatal)", "domain": "clinical_diagnosis"},
    "rem": {"canonical_name": "Rapid Eye Movement (Sleep)", "domain": "history"},
    "rlq": {"canonical_name": "Right Lower Quadrant", "domain": "anatomical"},
    "rmr": {"canonical_name": "Resting Metabolic Rate", "domain": "clinical_finding"},
    "ros": {"canonical_name": "Review of Systems", "domain": "history"},
    "ruq": {"canonical_name": "Right Upper Quadrant", "domain": "anatomical"},
    "rx": {"canonical_name": "Prescription/Treatment", "domain": "history"},
    "sao2": {"canonical_name": "Oxygen Saturation", "domain": "vital_sign"},
    "sids": {"canonical_name": "Sudden Infant Death Syndrome", "domain": "clinical_diagnosis"},
    "sirs": {"canonical_name": "Systemic Inflammatory Response Syndrome", "domain": "clinical_diagnosis"},
    "sle": {"canonical_name": "Systemic Lupus Erythematosus", "domain": "clinical_diagnosis"},
    "snf": {"canonical_name": "Skilled Nursing Facility", "domain": "history"},
    "std": {"canonical_name": "Sexually Transmitted Disease", "domain": "clinical_diagnosis"},
    "sti": {"canonical_name": "Sexually Transmitted Infection", "domain": "clinical_diagnosis"},
    "sx": {"canonical_name": "Symptoms", "domain": "history"},
    "tah": {"canonical_name": "Total Abdominal Hysterectomy", "domain": "history"},
    "tbi": {"canonical_name": "Traumatic Brain Injury", "domain": "clinical_diagnosis"},
    "tmj": {"canonical_name": "Temporomandibular Joint", "domain": "anatomical"},
    "tpr": {"canonical_name": "Temperature, Pulse, and Respiration", "domain": "vital_sign"},
    "uri": {"canonical_name": "Upper Respiratory Tract Infection", "domain": "clinical_diagnosis"},
    "vsd": {"canonical_name": "Ventricular Septal Defect", "domain": "clinical_diagnosis"},
    "vre": {"canonical_name": "Vancomycin-Resistant Enterococcus", "domain": "microbiology"},
    "vtach": {"canonical_name": "Ventricular Tachycardia", "domain": "cardiac"},
    "wnl": {"canonical_name": "Within Normal Limits", "domain": "clinical_finding"},
    "xrt": {"canonical_name": "External Beam Radiation Therapy", "domain": "oncology"},
    "yf": {"canonical_name": "Yellow Fever", "domain": "clinical_diagnosis"},
    "ze": {"canonical_name": "Zollinger-Ellison Syndrome", "domain": "clinical_diagnosis"},
    # Deliberately NOT added — real collision risk with ordinary English
    # words/filler or units, confirmed against the same reference:
    #   HR  (Heart Rate) collides with "hr/hrs" = hour(s)
    #   AKA (Above-Knee Amputation) collides with "also known as"
    #   OR  (Operating Room) collides with the conjunction "or"
    #   OT  (Occupational Therapy) collides with "overtime"
    #   ER  (Emergency Room) collides with the speech filler "er"
    #   PAD (Peripheral Artery Disease) collides with the word "pad"
    #   MAP (Mean Arterial Pressure) collides with the word "map"
    #   MM  (Multiple Myeloma / Malignant Melanoma) collides with the
    #       millimetre unit ("the lesion measures 15 mm")
    #   US  (Ultrasound) collides with the pronoun "us"
    #   WHO (World Health Organization) collides with the question word "who"
    #   PT  (Physical Therapy) collides with "patient" shorthand — kept
    #       unambiguous in terminology_normalize.ABBREVIATIONS instead,
    #       where it's safe (tests_required matches a whole list item,
    #       never scans free prose)
    # "RR" (Respiratory Rate vs. Regular Rhythm on cardiac exam), "PCP",
    # "ED", "PE", "TX", "RT" and others ARE genuinely ambiguous and live in
    # the shared terminology_normalize.AMBIGUOUS_ABBREVIATIONS instead.
}

# Dosing/route/frequency shorthand — a third, distinct vocabulary domain
# from both lab tests and clinical diagnoses. Most real dosing shorthand
# doesn't have competing medical meanings the way TB/DM/MS do, so there's
# no full AMBIGUOUS_MEDICATION_ABBREVIATIONS dict — but "OD" is a real
# exception (once daily vs. "right eye" in an ophthalmology prescription,
# per an external medical abbreviation reference), so it lives in the
# shared terminology_normalize.AMBIGUOUS_ABBREVIATIONS instead, not here.
MEDICATION_ABBREVIATIONS: dict[str, dict] = {
    "bd": {"canonical_name": "Twice Daily", "domain": "dosing_frequency"},
    "bid": {"canonical_name": "Twice Daily", "domain": "dosing_frequency"},  # US usage
    "qd": {"canonical_name": "Once Daily", "domain": "dosing_frequency"},
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
    "qh": {"canonical_name": "Every Hour", "domain": "dosing_frequency"},
    "os": {"canonical_name": "Left Eye (Oculus Sinister)", "domain": "dosing_route"},
    "ou": {"canonical_name": "Both Eyes (Oculus Uterque)", "domain": "dosing_route"},
    "ppi": {"canonical_name": "Proton Pump Inhibitor", "domain": "drug_class"},
    "nsaid": {"canonical_name": "Non-Steroidal Anti-Inflammatory Drug", "domain": "drug_class"},
    "acei": {"canonical_name": "ACE Inhibitor", "domain": "drug_class"},
    "arb": {"canonical_name": "Angiotensin Receptor Blocker", "domain": "drug_class"},
    "ccb": {"canonical_name": "Calcium Channel Blocker", "domain": "drug_class"},
    "abx": {"canonical_name": "Antibiotics", "domain": "drug_class"},
    "coc": {"canonical_name": "Combined Oral Contraceptive", "domain": "drug_class"},
    "asa": {"canonical_name": "Acetylsalicylic Acid (Aspirin)", "domain": "drug_name"},
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
_ALPHA_KEY_RE = re.compile(r"^[a-z]+$")


def _key_pattern(key: str) -> str:
    """Builds one dictionary key's match fragment. Pure-alpha keys ("po",
    "prn", "sob") tolerate spoken/punctuated letter-by-letter forms
    ("P.O.", "P-R-N", "S O B") — real dictation regularly renders spoken
    abbreviations this way (confirmed: Whisper transcribed "PO OD" as
    "P-O-O-D" in testing), and text_normalize.abbrev_key already collapses
    whatever this matches back into the plain key for dictionary lookup —
    this just teaches the SCAN regex to find those forms in free text in
    the first place. Each letter may be followed by an optional "."/"-"
    and optional whitespace before the next; the whole thing is bounded so
    it can't match inside or across ordinary words (e.g. "of" can't match
    "o" from a "po"/"od" pattern, since "f" isn't an allowed separator).
    Keys containing digits or symbols ("co2", "d-dimer", "d/c") use plain
    literal word-boundary matching instead — spelling those out
    letter-by-letter isn't a real dictation pattern."""
    if _ALPHA_KEY_RE.match(key):
        letters = r"[.\-]?\s*".join(re.escape(c) for c in key)
        return r"(?<![A-Za-z])" + letters + r"[.\-]?(?![A-Za-z])"
    return r"\b" + re.escape(key) + r"\b"


def _field_regex(unambiguous_dict: dict) -> re.Pattern:
    cache_key = id(unambiguous_dict)
    if cache_key not in _FIELD_REGEX_CACHE:
        keys = sorted(
            set(unambiguous_dict.keys()) | set(AMBIGUOUS_ABBREVIATIONS.keys()),
            key=len,
            reverse=True,
        )
        pattern = "|".join(f"(?:{_key_pattern(k)})" for k in keys)
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

        gap = raw_text[last_end : m.start()]
        if gap and re.fullmatch(r"[.\-]+", gap):
            # Two spelled-out abbreviations glued together with no space
            # ("P.O.,O.D." transcribed as one run) leave a bare punctuation
            # leftover between them once each claims its own letters —
            # render it as a plain space instead of a stray "-"/".".
            gap = " "
        pieces.append(gap)
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
