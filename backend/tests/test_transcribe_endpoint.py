"""
Integration test through the REAL POST /transcribe route (the same
endpoint the frontend calls) — not just the extraction/normalization
functions in isolation.

Whisper's actual speech-recognition step is monkeypatched to return a
canned transcript, since speech-recognition accuracy isn't what's being
tested here; importing `main` still triggers one real (cached) Whisper
model load, and every line of the new field-extraction/normalization code,
plus the existing matching.find_matches call, run for real through the
actual FastAPI route handler.
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


def _fake_transcribe(path, **kwargs):
    return [_FakeSegment(TRANSCRIPT)], _FakeInfo()


def test_transcribe_endpoint_returns_matches_and_structured(monkeypatch):
    monkeypatch.setattr(main.model, "transcribe", _fake_transcribe)

    client = TestClient(main.app)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()

    # Raw transcript is untouched.
    assert data["text"] == TRANSCRIPT

    # Existing "suggested tests" panel is unaffected by this change.
    assert isinstance(data["matches"], list)

    structured = data["structured"]

    assert structured["date_of_birth"] is None
    assert structured["date_of_birth_status"] == "ambiguous"
    assert structured["date_of_birth_raw"] == "1998-8-August 14th, 6 May"

    assert structured["date_requested"] == "2026-09-22"
    assert structured["time_requested"] == "14:35"
    assert structured["date_collected"] == "2026-09-22"
    assert structured["time_collected"] == "14:40"

    normalized = {t["raw"]: t["normalized"] for t in structured["tests_required"]}
    assert normalized["FBC"] == "Full Blood Count"
    assert normalized["CRP"] == "C-reactive protein"
    assert normalized["U and E"] == "Urea and Electrolytes"
    assert normalized["Blood culture"] == "Blood cultures"

    assert structured["patient_name"]["value"] == "Gabelo Mukwena"
    assert structured["hpcsa_number"]["value"] == "808080"

    assert any("staphylococcus" in u for u in structured["unparsed_text"])


def test_transcribe_endpoint_empty_text_has_empty_structured(monkeypatch):
    def fake_empty(path, **kwargs):
        return [], _FakeInfo()

    monkeypatch.setattr(main.model, "transcribe", fake_empty)

    client = TestClient(main.app)
    response = client.post(
        "/transcribe",
        files={"file": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == ""
    assert data["matches"] == []
    assert data["structured"]["unparsed_text"] == []
