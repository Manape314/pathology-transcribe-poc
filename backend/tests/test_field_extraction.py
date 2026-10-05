from field_extraction import build_normalized_text, extract_fields

TRANSCRIPT = (
    "Patient name, Gabelo Mukwena. Patient ID, 2026-00482. Date of birth, "
    "1998-8-August 14th, 6 May. Medical ward, 3B. Hospital, Ubuntu Academic "
    "Hospital. Date requested, 2026-22nd September. Time requested, 25 "
    "minutes to 3 p.m. Priority agent, specimen type, venous blood. Specimen "
    "site, left antecubinal vein. Date, time collected, 22 September 2026, "
    "20 minutes to 3 p.m. Clinical history, 38-year-old male presented with "
    "fatigue, fever, weakness and reduced appetite for four days. Possible "
    "infection and anemia. Provisional diagnosis, suspected bacterial "
    "infection, rule out anemia. Tests required, FBC, CRP, U and E. Blood "
    "culture. Relevant medication, paracetamol, 1 gram PRN. Requesting "
    "doctor, Dr. Tato Manapi. HPCSA number, 808080. Department, internal "
    "medicine. The using of staphylococcus agencies to cure 다양 turtle virus."
)


def test_full_example_transcript():
    result = extract_fields(TRANSCRIPT)

    # Ambiguous date of birth must never be guessed — raw preserved, value null.
    assert result["date_of_birth"] is None
    assert result["date_of_birth_status"] == "ambiguous"
    assert result["date_of_birth_raw"] == "1998-8-August 14th, 6 May"

    # Deterministic date/time normalization.
    assert result["date_requested"] == "2026-09-22"
    assert result["date_requested_status"] == "confirmed"
    assert result["time_requested"] == "14:35"
    assert result["time_requested_status"] == "confirmed"
    assert result["date_collected"] == "2026-09-22"
    assert result["date_collected_status"] == "confirmed"
    assert result["time_collected"] == "14:40"
    assert result["time_collected_status"] == "confirmed"

    # Run-on "Priority agent, specimen type, ..." must not bleed into priority.
    assert result["priority"]["raw"] == "agent"
    assert result["specimen_type"]["raw"] == "venous blood"

    # Identifiers/names are raw passthrough — never touched by fuzzy matching.
    assert result["patient_name"]["value"] == "Gabelo Mukwena"
    assert result["patient_id"]["value"] == "2026-00482"
    assert result["hpcsa_number"]["value"] == "808080"
    assert result["requesting_doctor"]["value"] == "Dr. Tato Manapi"

    # Terminology normalization on tests_required only.
    normalized = {t["raw"]: t["normalized"] for t in result["tests_required"]}
    assert normalized["FBC"] == "Full Blood Count"
    assert normalized["CRP"] == "C-reactive protein"
    assert normalized["U and E"] == "Urea and Electrolytes"
    assert normalized["Blood culture"] == "Blood cultures"

    # Department must not swallow the trailing garbled sentence.
    assert result["department"]["value"] == "internal medicine"
    assert any("staphylococcus" in u for u in result["unparsed_text"])
    assert "staphylococcus" not in result["department"]["value"]

    # Medication dosing shorthand is expanded through the real pipeline —
    # the fixture transcript's "1 gram PRN" becomes "1 gram As Needed
    # (PRN)"; identifiers elsewhere are unaffected by this.
    assert result["medication"]["value"] == "paracetamol, 1 gram As Needed (PRN)"
    assert result["medication"]["resolved_terms"][0]["normalized_term"] == "As Needed"

    # clinical_history/provisional_diagnosis in this fixture contain no
    # recognized clinical abbreviations — pass through byte-for-byte.
    assert result["clinical_history"]["value"] == result["clinical_history"]["raw"]
    assert result["clinical_history"]["resolved_terms"] == []
    assert result["provisional_diagnosis"]["resolved_terms"] == []


def test_build_normalized_text_substitutes_confident_values():
    structured = extract_fields(TRANSCRIPT)
    normalized_text = build_normalized_text(structured)

    # Abbreviations are expanded, with the originally-spoken form kept
    # visible alongside the expansion.
    assert (
        "Tests required: Full Blood Count (FBC), C-reactive protein (CRP), "
        "Urea and Electrolytes (U and E), Blood cultures (Blood culture)"
        in normalized_text
    )

    # Confident date/time substitutions.
    assert "Date requested: 2026-09-22" in normalized_text
    assert "Time requested: 14:35" in normalized_text
    assert "Date collected: 2026-09-22" in normalized_text
    assert "Time collected: 14:40" in normalized_text

    # Ambiguous DOB keeps its raw phrase, clearly flagged, never guessed.
    assert "1998-8-August 14th, 6 May [unconfirmed" in normalized_text

    # Identifiers/names pass through untouched.
    assert "Patient name: Gabelo Mukwena" in normalized_text
    assert "Requesting doctor: Dr. Tato Manapi" in normalized_text
    assert "HPCSA number: 808080" in normalized_text

    # The garbled trailing sentence is visible, not silently dropped.
    assert "staphylococcus" in normalized_text


def test_tests_required_label_accepts_requested_and_needed_variants():
    # Real-world bug: a dictation saying "Test requested" (not "Tests
    # required") previously matched no label at all, so the entire test
    # list silently vanished into whatever field preceded it (clinical_
    # history) as unprocessed raw text — nothing was normalized, no
    # abbreviations expanded. Locks in the fix.
    for phrase in ("Test requested", "Tests requested", "Test needed", "Tests required"):
        result = extract_fields(f"Clinical history, cough. {phrase}, FBC, CRP.")
        assert "tests_required" in result, f"{phrase!r} was not recognized as a label"
        normalized = {t["raw"]: t["normalized"] for t in result["tests_required"]}
        assert normalized["FBC"] == "Full Blood Count"
        assert normalized["CRP"] == "C-reactive protein"


def test_context_threading_changes_ambiguous_candidate_ranking_not_status():
    # Proves context actually flows from clinical_history into
    # tests_required's ambiguity ranking (via extract_fields' two-pass
    # restructure), not just that the ranking code exists in isolation —
    # and that it NEVER changes "ambiguous" into "confirmed", regardless
    # of how one-sided the supporting context is.
    cough_transcript = (
        "Clinical history, patient has chronic cough and chest pain. "
        "Tests required, TB."
    )
    liver_transcript = (
        "Clinical history, deranged LFT with jaundice, likely hepatic cause. "
        "Tests required, TB."
    )

    cough_result = extract_fields(cough_transcript)
    liver_result = extract_fields(liver_transcript)

    cough_tb = cough_result["tests_required"][0]
    liver_tb = liver_result["tests_required"][0]

    assert cough_tb["status"] == "ambiguous"
    assert liver_tb["status"] == "ambiguous"

    assert cough_tb["candidates"][0]["canonical_name"] == "Tuberculosis"
    assert liver_tb["candidates"][0]["canonical_name"] == "Total Bilirubin"

    # The normalized transcript surfaces the ambiguity marker either way —
    # it never silently picks one.
    assert "TB [ambiguous — please confirm]" in build_normalized_text(cough_result)
    assert "TB [ambiguous — please confirm]" in build_normalized_text(liver_result)


def test_clinical_history_and_provisional_diagnosis_abbreviations_end_to_end():
    transcript = (
        "Clinical history, longstanding SOB and HTN, now query DM. "
        "Provisional diagnosis, suspected PID versus UTI. "
        "Tests required, FBC."
    )
    result = extract_fields(transcript)

    assert result["clinical_history"]["value"] == (
        "longstanding Shortness of Breath (SOB) and Hypertension (HTN), "
        "now query DM [ambiguous — please confirm]"
    )
    ch_statuses = [t["status"] for t in result["clinical_history"]["resolved_terms"]]
    assert ch_statuses == ["confirmed", "confirmed", "ambiguous"]

    assert "PID [ambiguous — please confirm]" in result["provisional_diagnosis"]["value"]
    assert "Urinary Tract Infection (UTI)" in result["provisional_diagnosis"]["value"]

    normalized_text = build_normalized_text(result)
    assert "Clinical history: longstanding Shortness of Breath (SOB)" in normalized_text
    assert "DM [ambiguous — please confirm]" in normalized_text
    assert "Provisional diagnosis: suspected PID [ambiguous — please confirm]" in normalized_text


def test_identifiers_remain_untouched_by_clinical_terminology():
    # patient_name/patient_id/hpcsa_number/requesting_doctor/ward/hospital/
    # specimen_type/specimen_site/priority never go through
    # clinical_terminology.py — they keep the plain {"raw","value","status"}
    # passthrough shape with no "resolved_terms" key at all.
    result = extract_fields(TRANSCRIPT)
    for field_key in (
        "patient_name", "patient_id", "hpcsa_number", "requesting_doctor",
        "ward", "hospital", "specimen_type", "specimen_site", "priority",
    ):
        assert "resolved_terms" not in result[field_key]
        assert result[field_key]["value"] == result[field_key]["raw"]


def test_specimen_requirements_derived_without_touching_other_fields():
    transcript = (
        "Patient name, Jane Doe. Clinical history, hypertension, HTN. "
        "Tests required, FBC, CRP, U and E, LFT."
    )
    result = extract_fields(transcript)

    by_label = {g["label"]: g["tests"] for g in result["specimen_requirements"]}
    assert by_label["PURPLE — EDTA"] == ["Full Blood Count"]
    assert by_label["YELLOW — Serum"] == [
        "C-reactive protein",
        "Urea and Electrolytes",
        "Liver Function Tests",
    ]

    # tests_required itself is completely unaffected.
    normalized = {t["raw"]: t["normalized"] for t in result["tests_required"]}
    assert normalized["FBC"] == "Full Blood Count"
    assert normalized["LFT"] == "Liver Function Tests"

    # Every other field is untouched — no specimen-mapping leakage.
    assert result["clinical_history"]["value"] == "hypertension, Hypertension (HTN)"
    assert result["patient_name"]["value"] == "Jane Doe"

    # The reconstructed clinician-facing transcript never mentions
    # specimens — this is additive structured metadata only, not another
    # transcript-normalization stage.
    normalized_text = build_normalized_text(result)
    assert "specimen" not in normalized_text.lower()
    assert "PURPLE" not in normalized_text
    assert "Tests required: Full Blood Count (FBC), C-reactive protein (CRP)" in normalized_text


def test_specimen_requirements_excludes_ambiguous_tests():
    transcript = "Clinical history, cough. Tests required, FBC, TB."
    result = extract_fields(transcript)

    by_raw = {t["raw"]: t for t in result["tests_required"]}
    assert by_raw["TB"]["status"] == "ambiguous"  # unaffected — still ambiguous

    all_specimen_tests = [t for g in result["specimen_requirements"] for t in g["tests"]]
    assert all_specimen_tests == ["Full Blood Count"]  # TB excluded, never guessed


def test_empty_transcript_returns_empty_structure():
    result = extract_fields("")
    assert result["unparsed_text"] == []
    assert "patient_name" not in result


def test_transcript_with_no_recognized_labels_is_all_unparsed():
    result = extract_fields("just some random unrelated speech")
    assert result["unparsed_text"] == ["just some random unrelated speech"]


def test_reason_for_request_field_extracted():
    result = extract_fields("Reason for request, suspected infection. Priority, routine.")
    assert result["reason_for_request"]["value"] == "suspected infection"
    assert "Reason for request: suspected infection" in build_normalized_text(result)


def test_patient_hospital_number_phrasing_recognized_as_patient_id():
    # The dictation proforma's canonical wording — not the original
    # "Patient ID" phrasing — must resolve to the SAME field.
    result = extract_fields("Patient hospital number, H123456. Priority, routine.")
    assert result["patient_id"]["value"] == "H123456"
    assert "Patient hospital number: H123456" in build_normalized_text(result)

    # The original phrasing still works too — this is an added alias,
    # not a replacement.
    old_phrasing = extract_fields("Patient ID, H123456. Priority, routine.")
    assert old_phrasing["patient_id"]["value"] == "H123456"


def test_patient_hospital_number_does_not_collide_with_hospital_field():
    # Regression lock: "patient hospital number" contains the word
    # "hospital" — before the negative lookbehind guard, the hospital
    # field's own bare `\bhospital\b` pattern would mistake that word for
    # a second, bogus "Hospital:" label mid-phrase, corrupting both
    # fields. Both must extract correctly, independently, in the same
    # transcript.
    transcript = (
        "Hospital, Ubuntu Academic Hospital. Patient hospital number, "
        "H123456. Priority, routine."
    )
    result = extract_fields(transcript)
    assert result["hospital"]["value"] == "Ubuntu Academic Hospital"
    assert result["patient_id"]["value"] == "H123456"


def test_dictation_proforma_exact_wording_extracts_every_field_cleanly():
    # Regression lock for the dictation proforma's EXACT field wording —
    # before this fix, "Specimen site of collection,"/"Hospital or clinic
    # name," leaked their trailing words into the value, and "Date of
    # collection,"/"Time of collection," weren't recognized as labels at
    # all (they fell into unparsed_text).
    transcript = (
        "Specimen type, Blood. Specimen site of collection, Left arm vein. "
        "Date of collection, 5 October 2026. Time of collection, 10:30 AM. "
        "Reason for request, suspected anaemia. Hospital or clinic name, "
        "Greenside Clinic. Ward, Medical Ward 3B. Patient hospital number, "
        "H123456. Clinical history, patient presents with fatigue. "
        "Provisional diagnosis, iron deficiency anaemia. "
        "Tests required, full blood count. Priority, routine."
    )
    result = extract_fields(transcript)

    assert result["specimen_type"]["value"] == "Blood"
    assert result["specimen_site"]["value"] == "Left arm vein"
    assert result["date_collected"] == "2026-10-05"
    assert result["date_collected_status"] == "confirmed"
    assert result["time_collected"] == "10:30"
    assert result["time_collected_status"] == "confirmed"
    assert result["reason_for_request"]["value"] == "suspected anaemia"
    assert result["hospital"]["value"] == "Greenside Clinic"
    assert result["ward"]["value"] == "Medical Ward 3B"
    assert result["patient_id"]["value"] == "H123456"
    assert result["unparsed_text"] == []


def test_bare_specimen_site_and_hospital_labels_still_work():
    # "of collection"/"or clinic name" are optional trailing phrases on
    # the label itself — the original bare "Specimen site,"/"Hospital,"
    # phrasing must keep working unchanged.
    result = extract_fields(
        "Specimen site, Left arm vein. Hospital, Ubuntu Academic Hospital. "
        "Priority, routine."
    )
    assert result["specimen_site"]["value"] == "Left arm vein"
    assert result["hospital"]["value"] == "Ubuntu Academic Hospital"


def test_time_of_collection_without_am_pm_stays_ambiguous_never_guessed():
    result = extract_fields("Time of collection, 10:30. Priority, routine.")
    assert result["time_collected"] is None
    assert result["time_collected_status"] == "ambiguous"
    assert result["time_collected_raw"] == "10:30"
