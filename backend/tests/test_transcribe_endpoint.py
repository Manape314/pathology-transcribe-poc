"""
Integration test through the REAL POST /transcribe route (the same
endpoint the frontend calls) — not just the extraction/normalization
functions in isolation.

Whisper's actual speech-recognition step is monkeypatched to return a
canned transcript, since speech-recognition accuracy isn't what's being
tested here; importing `main` still triggers one real (cached) Whisper
model load, and every line of the new field-extraction/normalization code
runs for real through the actual FastAPI route handler.
"""

from fastapi.testclient import TestClient

import main

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


class _FakeSegment:
    def __init__(self, text):
        self.start = 0.0
        self.end = 10.0
        self.text = text


class _FakeInfo:
    language = "en"
    duration = 10.0


def _transcribe_with(monkeypatch, text):
    def fake_transcribe(path, **kwargs):
        return [_FakeSegment(text)], _FakeInfo()

    monkeypatch.setattr(main.model, "transcribe", fake_transcribe)
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
    assert "14:35" in normalized_text

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
