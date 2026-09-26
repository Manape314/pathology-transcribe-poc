import time

import pytest

from clinical_terminology import (
    CLINICAL_ABBREVIATIONS,
    MEDICATION_ABBREVIATIONS,
    resolve_field_text,
)


# --------------------------------------------------------------------------- #
# Unambiguous clinical abbreviations — expanded in place, raw kept visible.
# --------------------------------------------------------------------------- #


def test_unambiguous_clinical_abbreviations_expand_in_prose():
    result = resolve_field_text("SOB, HTN", "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["value"] == "Shortness of Breath (SOB), Hypertension (HTN)"
    assert result["raw"] == "SOB, HTN"
    statuses = {t["raw_phrase"]: t["status"] for t in result["resolved_terms"]}
    assert statuses == {"SOB": "confirmed", "HTN": "confirmed"}


def test_case_insensitive_and_lowercase_forms():
    result = resolve_field_text("sob and htn noted", "clinical_history", CLINICAL_ABBREVIATIONS)
    assert "Shortness of Breath (sob)" in result["value"]
    assert "Hypertension (htn)" in result["value"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SOB", "Shortness of Breath"),
        ("CKD", "Chronic Kidney Disease"),
        ("COPD", "Chronic Obstructive Pulmonary Disease"),
        ("UTI", "Urinary Tract Infection"),
        ("DVT", "Deep Vein Thrombosis"),
        ("PMH", "Past Medical History"),
        ("NKDA", "No Known Drug Allergies"),
    ],
)
def test_seeded_clinical_abbreviation_set(raw, expected):
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    term = result["resolved_terms"][0]
    assert term["normalized_term"] == expected
    assert term["status"] == "confirmed"
    assert term["field"] == "clinical_history"
    assert term["provenance"] == "curated clinical abbreviation dictionary"
    # No terminology system exists for this layer yet (SNOMED not
    # integrated) — never fabricated.
    assert term["terminology_system"] is None
    assert term["code"] is None


# --------------------------------------------------------------------------- #
# Ambiguous abbreviations — NEVER auto-resolved, in ANY field, no exceptions.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", ["DM", "MI", "PID", "TB", "MS", "CVA", "RA", "CP"])
def test_ambiguous_abbreviations_always_flagged_regardless_of_field(raw):
    for field in ("clinical_history", "provisional_diagnosis"):
        result = resolve_field_text(raw, field, CLINICAL_ABBREVIATIONS)
        assert f"{raw} [ambiguous — please confirm]" in result["value"]
        term = result["resolved_terms"][0]
        assert term["status"] == "ambiguous"
        assert term["normalized_term"] is None
        assert term["confirmation_status"] == "pending"
        assert len(term["candidates"]) >= 2


def test_ambiguous_abbreviation_context_ranks_but_never_auto_resolves():
    result = resolve_field_text(
        "query DM",
        "provisional_diagnosis",
        CLINICAL_ABBREVIATIONS,
        context={"clinical_history_raw": "polyuria, polydipsia, raised glucose"},
    )
    term = result["resolved_terms"][0]
    assert term["status"] == "ambiguous"
    assert term["candidates"][0]["canonical_name"] == "Diabetes Mellitus"
    assert "context matched" in term["candidates"][0]["reason"]
    # Even with one-sided evidence, it's still ambiguous — never confirmed.
    assert term["status"] != "confirmed"


def test_ambiguous_abbreviation_shared_candidate_set_matches_tests_required():
    # DM/MI/etc. are not duplicated per field — the same candidate pool as
    # tests_required's AMBIGUOUS_ABBREVIATIONS is reused here.
    import terminology_normalize as tn

    result = resolve_field_text("DM", "clinical_history", CLINICAL_ABBREVIATIONS)
    candidate_names = {c["canonical_name"] for c in result["resolved_terms"][0]["candidates"]}
    expected_names = {c["canonical_name"] for c in tn.AMBIGUOUS_ABBREVIATIONS["dm"]}
    assert candidate_names == expected_names


# --------------------------------------------------------------------------- #
# Safety: word-boundary matching, untouched prose, non-dictionary words.
# --------------------------------------------------------------------------- #


def test_word_boundary_prevents_partial_matches():
    # "DMV" and "HTNx" must not trigger "DM"/"HTN" matches.
    result = resolve_field_text("patient works at the DMV and has HTNx pending workup", "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"] == []
    assert result["value"] == result["raw"]


def test_ordinary_prose_with_no_recognized_abbreviations_passes_through_untouched():
    raw = "38-year-old male presented with fatigue, fever, weakness and reduced appetite."
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["value"] == raw
    assert result["resolved_terms"] == []


def test_mixed_confirmed_ambiguous_and_plain_prose():
    raw = "Longstanding SOB and HTN, now query DM, generally well otherwise."
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert "Shortness of Breath (SOB)" in result["value"]
    assert "Hypertension (HTN)" in result["value"]
    assert "DM [ambiguous — please confirm]" in result["value"]
    assert "generally well otherwise." in result["value"]
    statuses = [t["status"] for t in result["resolved_terms"]]
    assert statuses == ["confirmed", "confirmed", "ambiguous"]


# --------------------------------------------------------------------------- #
# Medication dosing shorthand — a separate vocabulary domain.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("PRN", "As Needed"),
        ("BD", "Twice Daily"),
        ("IV", "Intravenous"),
        ("PO", "By Mouth"),
        ("STAT", "Immediately"),
    ],
)
def test_medication_dosing_abbreviations(raw, expected):
    result = resolve_field_text(raw, "medication", MEDICATION_ABBREVIATIONS)
    term = result["resolved_terms"][0]
    assert term["normalized_term"] == expected
    assert term["status"] == "confirmed"
    assert term["provenance"] == "curated medication abbreviation dictionary"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BP", "Blood Pressure"),
        ("SpO2", "Oxygen Saturation"),
        ("GCS", "Glasgow Coma Scale"),
        ("BMI", "Body Mass Index"),
        ("TIA", "Transient Ischaemic Attack"),
        ("PCI", "Percutaneous Coronary Intervention"),
        ("ACS", "Acute Coronary Syndrome"),
        ("STEMI", "ST-Elevation Myocardial Infarction"),
        ("OSA", "Obstructive Sleep Apnoea"),
        ("GDM", "Gestational Diabetes Mellitus"),
        ("PCOS", "Polycystic Ovary Syndrome"),
        ("PUD", "Peptic Ulcer Disease"),
    ],
)
def test_second_expansion_pass_new_clinical_abbreviations(raw, expected):
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


def test_hr_is_deliberately_not_expanded_due_to_hour_collision():
    # "HR" was deliberately left out of CLINICAL_ABBREVIATIONS because it
    # collides with the extremely common "hr"/"hrs" shorthand for "hour(s)"
    # in ordinary clinical prose — expanding it would be wrong here.
    result = resolve_field_text("pain for 6 hrs, no HR documented", "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"] == []


def test_rr_is_ambiguous_not_unambiguously_respiratory_rate():
    result = resolve_field_text("RR noted on exam", "clinical_history", CLINICAL_ABBREVIATIONS)
    term = result["resolved_terms"][0]
    assert term["status"] == "ambiguous"
    assert {c["canonical_name"] for c in term["candidates"]} == {
        "Respiratory Rate",
        "Regular Rhythm",
    }


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AC", "Before Meals"),
        ("PC", "After Meals"),
        ("HS", "At Bedtime"),
        ("UD", "As Directed"),
        ("GTT", "Drops"),
        ("UNG", "Ointment"),
        ("SUPP", "Suppository"),
        ("NEB", "Nebulised"),
        ("NG", "Nasogastric"),
    ],
)
def test_second_expansion_pass_new_medication_abbreviations(raw, expected):
    result = resolve_field_text(raw, "medication", MEDICATION_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


def test_top_is_deliberately_not_expanded_due_to_top_dose_collision():
    result = resolve_field_text("furosemide top dose reached", "medication", MEDICATION_ABBREVIATIONS)
    assert result["resolved_terms"] == []


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AAA", "Abdominal Aortic Aneurysm"),
        ("MVA", "Motor Vehicle Accident"),
        ("NOF", "Neck of Femur (Fracture)"),
        ("FTT", "Failure to Thrive"),
        ("IUGR", "Intrauterine Growth Restriction"),
        ("PPH", "Postpartum Haemorrhage"),
        ("APH", "Antepartum Haemorrhage"),
        ("IHD", "Ischaemic Heart Disease"),
        ("PVD", "Peripheral Vascular Disease"),
        ("CRF", "Chronic Renal Failure"),
        ("ARF", "Acute Renal Failure"),
        ("ILD", "Interstitial Lung Disease"),
    ],
)
def test_third_expansion_pass_new_clinical_abbreviations(raw, expected):
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("PPI", "Proton Pump Inhibitor"),
        ("NSAID", "Non-Steroidal Anti-Inflammatory Drug"),
        ("ACEI", "ACE Inhibitor"),
        ("ARB", "Angiotensin Receptor Blocker"),
        ("CCB", "Calcium Channel Blocker"),
        ("Abx", "Antibiotics"),
        ("COC", "Combined Oral Contraceptive"),
    ],
)
def test_third_expansion_pass_new_medication_abbreviations(raw, expected):
    result = resolve_field_text(raw, "medication", MEDICATION_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


def test_ct_is_ambiguous_not_unambiguously_computed_tomography():
    # "CT" is genuinely ambiguous (Computed Tomography vs Chlamydia
    # Trachomatis) — always flagged, even though imaging is the far more
    # common real-world reading, per the standing "never auto-resolve
    # genuine ambiguity" rule.
    result = resolve_field_text("CT scan requested", "clinical_history", CLINICAL_ABBREVIATIONS)
    term = result["resolved_terms"][0]
    assert term["status"] == "ambiguous"
    assert {c["canonical_name"] for c in term["candidates"]} == {
        "Chlamydia Trachomatis",
        "Computed Tomography",
    }


def test_medication_field_end_to_end():
    result = resolve_field_text("paracetamol, 1 gram PRN", "medication", MEDICATION_ABBREVIATIONS)
    assert result["value"] == "paracetamol, 1 gram As Needed (PRN)"


# --------------------------------------------------------------------------- #
# Spelled-out forms in free text ("P.O.", "P-R-N", "S O B") — the real bug
# hit in testing: Whisper transcribed dictated "PO OD" as "P-O-O-D", which
# the plain word-boundary regex couldn't recognize at all.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("P.O.", "By Mouth"),
        ("P-O", "By Mouth"),
        ("P O", "By Mouth"),
        ("p.o.", "By Mouth"),
    ],
)
def test_spelled_out_forms_are_recognized_in_medication_field(raw, expected):
    result = resolve_field_text(raw, "medication", MEDICATION_ABBREVIATIONS)
    assert result["resolved_terms"], f"{raw!r} was not recognized"
    assert result["resolved_terms"][0]["normalized_term"] == expected


def test_spelled_out_forms_preserve_original_raw_phrase_in_output():
    # The bracketed display keeps exactly what was heard, not a collapsed
    # form — "By Mouth (P.O.)", never "By Mouth (po)".
    result = resolve_field_text("P.O.", "medication", MEDICATION_ABBREVIATIONS)
    assert result["value"] == "By Mouth (P.O.)"


def test_reproduces_the_original_po_od_bug_report():
    # The exact real-world sequence that surfaced this gap: Whisper
    # rendered dictated "PO OD" as "P-O-O-D" — which correctly splits into
    # "P-O" and "O-D" (the middle hyphen is the shared boundary between
    # the two abbreviations, consumed by neither).
    result = resolve_field_text("P-O-O-D", "medication", MEDICATION_ABBREVIATIONS)
    terms = {t["raw_phrase"]: t["normalized_term"] for t in result["resolved_terms"]}
    assert terms.get("P-O") == "By Mouth"
    # "OD" is now genuinely ambiguous (Once Daily vs. Right Eye) rather
    # than auto-expanded — confirm it's flagged, not silently guessed.
    ambiguous_raw = [t["raw_phrase"] for t in result["resolved_terms"] if t["status"] == "ambiguous"]
    assert "O-D" in ambiguous_raw


def test_spelled_out_form_does_not_match_across_unrelated_words():
    # "is on break" must NOT be mis-parsed as a spelled-out abbreviation
    # just because it contains isolated letters in a similar pattern.
    result = resolve_field_text(
        "patient is on breakthrough pain medication", "clinical_history", CLINICAL_ABBREVIATIONS
    )
    assert result["resolved_terms"] == []
    assert result["value"] == result["raw"]


# --------------------------------------------------------------------------- #
# New entries cross-checked against an external medical abbreviation
# reference (also caught the OD/PE/BM corrections above).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ACL", "Anterior Cruciate Ligament"),
        ("ADHD", "Attention-Deficit/Hyperactivity Disorder"),
        ("ADL", "Activities of Daily Living"),
        ("AFib", "Atrial Fibrillation"),
        ("AIDS", "Acquired Immunodeficiency Syndrome"),
        ("ALS", "Amyotrophic Lateral Sclerosis"),
        ("AMA", "Against Medical Advice"),
        ("AMI", "Acute Myocardial Infarction"),
        ("ARDS", "Acute Respiratory Distress Syndrome"),
        ("CC", "Chief Complaint"),
        ("CNS", "Central Nervous System"),
        ("CPR", "Cardiopulmonary Resuscitation"),
        ("CSF", "Cerebrospinal Fluid"),
        ("DNR", "Do Not Resuscitate"),
        ("Dx", "Diagnosis"),
        ("ENT", "Ear, Nose, and Throat"),
        ("FX", "Fracture"),
        ("GI", "Gastrointestinal"),
        ("HX", "History"),
        ("ICU", "Intensive Care Unit"),
        ("MRSA", "Methicillin-Resistant Staphylococcus Aureus"),
        ("MVP", "Mitral Valve Prolapse"),
        ("NPO", "Nil by Mouth"),
        ("NSR", "Normal Sinus Rhythm"),
        ("ROS", "Review of Systems"),
        ("RX", "Prescription/Treatment"),
        ("SIDS", "Sudden Infant Death Syndrome"),
        ("SLE", "Systemic Lupus Erythematosus"),
        ("STD", "Sexually Transmitted Disease"),
        ("Sx", "Symptoms"),
        ("TBI", "Traumatic Brain Injury"),
        ("VSD", "Ventricular Septal Defect"),
        ("VTach", "Ventricular Tachycardia"),
        ("WNL", "Within Normal Limits"),
    ],
)
def test_fourth_expansion_pass_new_clinical_abbreviations(raw, expected):
    result = resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BID", "Twice Daily"),
        ("QD", "Once Daily"),
        ("QH", "Every Hour"),
        ("OS", "Left Eye (Oculus Sinister)"),
        ("OU", "Both Eyes (Oculus Uterque)"),
        ("ASA", "Acetylsalicylic Acid (Aspirin)"),
    ],
)
def test_fourth_expansion_pass_new_medication_abbreviations(raw, expected):
    result = resolve_field_text(raw, "medication", MEDICATION_ABBREVIATIONS)
    assert result["resolved_terms"][0]["normalized_term"] == expected


# --------------------------------------------------------------------------- #
# Deliberately-skipped collision-risk abbreviations from the same
# reference — confirm they are NOT expanded/flagged at all.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sentence",
    [
        "patient AKA presented for a routine check",  # AKA = "also known as"
        "discussed treatment options or referral",  # OR = conjunction
        "please send us the report",  # US = pronoun
        "unclear who ordered the test",  # WHO = question word
        "the lesion measures 15 mm in diameter",  # MM = millimetre unit
        "the mass was estimated at 20 mm",  # MM again, different phrasing
    ],
)
def test_collision_risk_abbreviations_are_never_expanded(sentence):
    result = resolve_field_text(sentence, "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"] == []
    assert result["value"] == result["raw"]


def test_medication_ms_is_still_ambiguous_not_auto_resolved_to_morphine_sulphate():
    # "MS" is in the shared AMBIGUOUS_ABBREVIATIONS pool (Multiple
    # Sclerosis / Mitral Stenosis / Morphine Sulphate) — even though
    # Morphine Sulphate is medication-relevant, it must still require
    # clinician confirmation like every other ambiguous abbreviation.
    result = resolve_field_text("MS 10mg", "medication", MEDICATION_ABBREVIATIONS)
    term = result["resolved_terms"][0]
    assert term["status"] == "ambiguous"
    assert {c["canonical_name"] for c in term["candidates"]} == {
        "Multiple Sclerosis",
        "Mitral Stenosis",
        "Morphine Sulphate",
    }


# --------------------------------------------------------------------------- #
# Identifiers are never routed through this module (structural guarantee).
# --------------------------------------------------------------------------- #


def test_identifiers_never_reach_this_module_by_construction():
    # field_extraction.py only ever calls resolve_field_text for
    # clinical_history/provisional_diagnosis/medication — patient/doctor
    # names, IDs, ward, hospital, specimen type/site, and priority never
    # do. This documents the boundary the same way
    # test_terminology_normalize.py documents it for tests_required.
    result = resolve_field_text("Gabelo Mukwena", "clinical_history", CLINICAL_ABBREVIATIONS)
    assert result["resolved_terms"] == []
    assert result["value"] == "Gabelo Mukwena"


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #


def test_resolve_field_text_latency():
    raw = (
        "Longstanding history of SOB, HTN, CKD, COPD and previous DVT, now "
        "presenting with query DM, provisional diagnosis of UTI versus PE, "
        "background of AF and CCF, no known drug allergies."
    )
    start = time.perf_counter()
    for _ in range(20):
        resolve_field_text(raw, "clinical_history", CLINICAL_ABBREVIATIONS)
    elapsed_ms = (time.perf_counter() - start) / 20 * 1000
    assert elapsed_ms < 200, f"resolve_field_text took {elapsed_ms:.1f}ms on average"
