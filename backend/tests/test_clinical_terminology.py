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
        ("PE", "Pulmonary Embolism"),
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
        ("OD", "Once Daily"),
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


def test_medication_field_end_to_end():
    result = resolve_field_text("paracetamol, 1 gram PRN", "medication", MEDICATION_ABBREVIATIONS)
    assert result["value"] == "paracetamol, 1 gram As Needed (PRN)"


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
