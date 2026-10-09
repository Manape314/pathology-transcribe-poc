"""
Integration test through the REAL POST /transcribe route (the same
endpoint the frontend calls) — not just the extraction/normalization
functions in isolation.

transcription.transcribe_audio() (the one function that talks to Groq) is
monkeypatched to return a canned transcript, since speech-recognition
accuracy isn't what's being tested here and these tests must never make a
real Groq API call, require GROQ_API_KEY, or consume free-tier quota.
Every line of the field-extraction/normalization code still runs for real
through the actual FastAPI route handler.
"""

import pytest
from fastapi.testclient import TestClient

import main
import transcription

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


def _transcribe_with(monkeypatch, text):
    def fake_transcribe_audio(path):
        return text, "en", 10.0, [{"start": 0.0, "end": 10.0, "text": text}]

    monkeypatch.setattr(transcription, "transcribe_audio", fake_transcribe_audio)
    return TestClient(main.app)


def test_transcribe_endpoint_raw_vs_normalized_and_structured(monkeypatch):
    client = _transcribe_with(monkeypatch, TRANSCRIPT)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()

    # raw_text is the untouched Whisper output.
    assert data["raw_text"] == TRANSCRIPT

    # The unsafe whole-transcript suggestion path is gone, not just hidden.
    assert "matches" not in data
    assert "text" not in data

    # normalized_text has real substitutions — abbreviations expanded, with
    # the originally-spoken form kept visible in brackets — and confident
    # date/time values swapped in.
    normalized_text = data["normalized_text"]
    assert "Full Blood Count (FBC)" in normalized_text
    assert "C-reactive protein (CRP)" in normalized_text
    assert "Urea and Electrolytes (U and E)" in normalized_text
    assert "2026-09-22" in normalized_text
    assert "2:35 PM" in normalized_text

    structured = data["structured"]

    assert structured["date_of_birth"] is None
    assert structured["date_of_birth_status"] == "ambiguous"
    assert structured["date_of_birth_raw"] == "1998-8-August 14th, 6 May"

    assert structured["date_requested"] == "2026-09-22"
    assert structured["time_requested"] == "14:35"
    assert structured["date_collected"] == "2026-09-22"
    assert structured["time_collected"] == "14:40"

    normalized_tests = {t["raw"]: t["normalized"] for t in structured["tests_required"]}
    assert normalized_tests["FBC"] == "Full Blood Count"
    assert normalized_tests["CRP"] == "C-reactive protein"
    assert normalized_tests["U and E"] == "Urea and Electrolytes"
    assert normalized_tests["Blood culture"] == "Blood cultures"

    # Patient/doctor fields are never run through terminology matching —
    # they have no "normalized"/"source" keys at all, just raw passthrough.
    assert structured["patient_name"] == {
        "raw": "Gabelo Mukwena", "value": "Gabelo Mukwena", "status": "extracted",
    }
    assert structured["requesting_doctor"]["value"] == "Dr. Tato Manapi"
    assert structured["hpcsa_number"]["value"] == "808080"

    assert any("staphylococcus" in u for u in structured["unparsed_text"])

    # Medication dosing shorthand is resolved through the real endpoint too.
    assert "resolved_terms" in structured["medication"]
    assert structured["medication"]["resolved_terms"][0]["normalized_term"] == "As Needed"
    assert "As Needed (PRN)" in structured["medication"]["value"]


def test_transcribe_endpoint_does_not_suggest_unrelated_tests_from_prose(monkeypatch):
    # Regression guard for the original bug report: phrases embedded in
    # prose fields (not "tests required") must never surface as test
    # suggestions — there is no code path left that could do that, since
    # terminology matching only ever runs on the extracted tests_required
    # field, never on the whole transcript.
    transcript = (
        "Patient name, Jane Doe. Patient ID, 123. "
        "Clinical history, patient reports urea analysis urine microscopy "
        "culture and sensitivity was previously unremarkable. "
        "Tests required, FBC."
    )
    client = _transcribe_with(monkeypatch, transcript)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()
    structured = data["structured"]

    # Only the one item actually dictated under "Tests required" appears.
    assert len(structured["tests_required"]) == 1
    assert structured["tests_required"][0]["raw"] == "FBC"
    assert structured["tests_required"][0]["normalized"] == "Full Blood Count"

    # The clinical_history prose is preserved verbatim, not matched against
    # anything.
    assert "urea analysis urine microscopy" in structured["clinical_history"]["value"]


def test_transcribe_endpoint_surfaces_ambiguous_abbreviation_for_clinician(monkeypatch):
    # Through the REAL endpoint: an ambiguous abbreviation must come back
    # as status "ambiguous" with a full candidate list for the frontend to
    # render as a disambiguation choice — never silently resolved, even
    # though clinical_history here has one-sided supporting context.
    transcript = (
        "Patient name, Jane Doe. Clinical history, chronic cough and "
        "weight loss. Tests required, FBC, TB."
    )
    client = _transcribe_with(monkeypatch, transcript)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    structured = response.json()["structured"]

    by_raw = {t["raw"]: t for t in structured["tests_required"]}
    assert by_raw["FBC"]["status"] == "confirmed"
    assert by_raw["TB"]["status"] == "ambiguous"
    assert by_raw["TB"]["normalized"] is None
    candidate_names = {c["canonical_name"] for c in by_raw["TB"]["candidates"]}
    assert candidate_names == {"Total Bilirubin", "Tuberculosis"}

    # Patient identifiers are still untouched.
    assert structured["patient_name"]["value"] == "Jane Doe"


def test_transcribe_endpoint_empty_text_has_empty_structured(monkeypatch):
    client = _transcribe_with(monkeypatch, "")
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["raw_text"] == ""
    assert data["normalized_text"] == ""
    assert data["structured"]["unparsed_text"] == []


# --------------------------------------------------------------------------- #
# Error handling — whatever goes wrong inside transcription.py (missing key,
# Groq timeout/429/5xx, network failure), /transcribe must map it to ONE
# generic, retryable, client-safe message, never the underlying cause.
# --------------------------------------------------------------------------- #

def test_transcribe_endpoint_maps_transcription_error_to_safe_503(monkeypatch):
    def fake_transcribe_audio(path):
        # Stands in for any of transcription.py's real failure modes —
        # missing key, Groq rate limit, Groq 5xx, a network timeout — they
        # all raise this same TranscriptionError type.
        raise transcription.TranscriptionError("Groq rate limit exceeded.")

    monkeypatch.setattr(transcription, "transcribe_audio", fake_transcribe_audio)
    client = TestClient(main.app)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "Transcription is temporarily unavailable. Please try again."
    # The real cause must never reach the client-facing response.
    assert "rate limit" not in body["detail"].lower()
    assert "groq" not in body["detail"].lower()


def test_transcribe_audio_without_api_key_raises_before_any_network_call(monkeypatch):
    # transcription.py in isolation (not through the endpoint): a missing
    # GROQ_API_KEY must fail with the module's own TranscriptionError
    # BEFORE constructing a client or attempting any network call — this
    # is also what guarantees every OTHER test file that merely imports
    # main (print jobs, time normalization, ...) never needs
    # GROQ_API_KEY set and never makes a network call just by importing it.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(transcription, "_client", None)

    with pytest.raises(transcription.TranscriptionError, match="GROQ_API_KEY"):
        transcription.transcribe_audio("irrelevant-path.webm")
