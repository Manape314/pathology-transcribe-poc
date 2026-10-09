"""
The per-field resolution endpoints behind inline, in-block editing:
POST /normalize-time, /normalize-date, /resolve-clinical-field,
/normalize-test-item, and /rebuild-transcript. Each is a thin wrapper
around an existing, already-tested normalization function — these tests
only confirm the HTTP plumbing (request shape in, response shape out),
not the underlying normalization logic itself (covered elsewhere:
test_datetime_normalize.py, test_clinical_terminology.py,
test_terminology_normalize.py).
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


def test_normalize_date_with_valid_date_is_confirmed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-date", json={"raw": "5 October 2026"})
    assert response.status_code == 200
    assert response.json() == {"value": "2026-10-05", "status": "confirmed"}


def test_normalize_date_with_two_month_names_stays_ambiguous_never_guessed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-date", json={"raw": "8 August 14th, 6 May"})
    assert response.status_code == 200
    assert response.json() == {"value": None, "status": "ambiguous"}


def test_resolve_clinical_field_expands_known_abbreviation(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post(
        "/resolve-clinical-field",
        json={"field_key": "clinical_history", "raw_text": "SOB and fever", "structured": {}},
    )
    assert response.status_code == 200
    field = response.json()["field"]
    assert field["value"] == "Shortness of Breath (SOB) and fever"
    assert field["resolved_terms"][0]["status"] == "confirmed"


def test_resolve_clinical_field_rejects_unknown_field_key(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post(
        "/resolve-clinical-field",
        json={"field_key": "not_a_real_field", "raw_text": "x", "structured": {}},
    )
    assert response.status_code == 400


def test_normalize_test_item_resolves_known_abbreviation(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-test-item", json={"raw": "FBC", "structured": {}})
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "confirmed"
    assert item["normalized"] == "Full Blood Count"


def test_normalize_test_item_unrecognized_stays_unrecognized_never_guessed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-test-item", json={"raw": "FPC", "structured": {}})
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "unrecognized"
    assert item["raw"] == "FPC"


def test_normalize_test_item_rejects_empty_raw(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()

    client = TestClient(main.app)
    response = client.post("/normalize-test-item", json={"raw": "   ", "structured": {}})
    assert response.status_code == 400


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
    assert "Time of collection: 10:30" in response.json()["normalized_text"]
