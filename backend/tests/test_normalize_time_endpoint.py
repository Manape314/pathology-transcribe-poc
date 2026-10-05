"""
POST /normalize-time — a thin wrapper around datetime_normalize.normalize_time(),
used by the frontend's AM/PM quick-pick when a doctor resolves a time that
was dictated without am/pm. Importing `main` triggers one real (cached)
Whisper model load, same constraint as the other endpoint test files; this
endpoint itself touches no database and no hardware.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import main
import print_records


def test_normalize_time_with_am_is_confirmed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-time", json={"raw": "10:30 AM"})
    assert response.status_code == 200
    assert response.json() == {"value": "10:30", "status": "confirmed"}


def test_normalize_time_with_pm_converts_to_24_hour(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-time", json={"raw": "10:30 PM"})
    assert response.status_code == 200
    assert response.json() == {"value": "22:30", "status": "confirmed"}


def test_normalize_time_without_am_pm_stays_ambiguous_never_guessed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-time", json={"raw": "10:30"})
    assert response.status_code == 200
    assert response.json() == {"value": None, "status": "ambiguous"}


def test_extract_fields_endpoint_reparses_edited_text(tmp_path, monkeypatch):
    # Used by the frontend's "Save edits" action — re-parsing doctor-typed
    # text must produce the SAME structured shape /transcribe already
    # does, so the six-block view and the completeness check stay in
    # sync with whatever was just typed.
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post(
        "/extract-fields",
        json={"text": "Reason for request, suspected infection. Priority, routine."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["structured"]["reason_for_request"]["value"] == "suspected infection"
    assert "Reason for request: suspected infection" in data["normalized_text"]


def test_rebuild_transcript_endpoint_reflects_updated_structured(tmp_path, monkeypatch):
    # Used after the AM/PM quick-pick updates one field client-side — the
    # flat normalized_text must be regenerated from the SAME reconstruction
    # logic build_normalized_text() already uses everywhere else.
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    structured = {
        "time_collected": "10:30",
        "time_collected_status": "confirmed",
        "time_collected_raw": "10:30",
    }
    response = client.post("/rebuild-transcript", json={"structured": structured})
    assert response.status_code == 200
    assert "Time collected: 10:30" in response.json()["normalized_text"]
