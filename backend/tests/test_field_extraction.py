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


def test_empty_transcript_returns_empty_structure():
    result = extract_fields("")
    assert result["unparsed_text"] == []
    assert "patient_name" not in result


def test_transcript_with_no_recognized_labels_is_all_unparsed():
    result = extract_fields("just some random unrelated speech")
    assert result["unparsed_text"] == ["just some random unrelated speech"]
