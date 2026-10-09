"""
POST /register, /login, /me, /requests — the HTTP layer over doctors.py
(already unit-tested directly in test_doctors.py). Importing `main`
triggers one real (cached) Groq-free import, same constraint as the
other endpoint test files — no network call happens just by importing it
(see transcription.py: the Groq client is built lazily, on first real
use, never at import time).
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import doctors
import main
import print_records

FAKE_LABEL_IMAGE = "data:image/png;base64,FAKEBASE64DATA=="


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    doctors.init_db()
    return TestClient(main.app)


def test_register_then_login_roundtrip(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    r1 = client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe",
            "hpcsa_number": "808080",
            "cell": "0821234567",
            "email": "jane@example.com",
            "password": "hunter22",
        },
    )
    assert r1.status_code == 201
    assert r1.json()["doctor"]["hpcsa_number"] == "808080"
    assert "password" not in r1.json()["doctor"]

    r2 = client.post("/login", json={"hpcsa_number": "808080", "password": "hunter22"})
    assert r2.status_code == 200
    assert r2.json()["token"] != r1.json()["token"]  # a fresh token, not the same one


def test_duplicate_registration_is_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = {
        "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
        "email": "jane@example.com", "password": "hunter22",
    }
    assert client.post("/register", json=body).status_code == 201
    assert client.post("/register", json=body).status_code == 409


def test_login_with_wrong_password_is_generic_401(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
            "email": "jane@example.com", "password": "hunter22",
        },
    )
    r = client.post("/login", json={"hpcsa_number": "808080", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect HPCSA number or password."

    # Unknown HPCSA gets the exact same message — never reveals which
    # field was wrong.
    r2 = client.post("/login", json={"hpcsa_number": "999999", "password": "wrong"})
    assert r2.json()["detail"] == r.json()["detail"]


def test_me_resolves_a_valid_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
            "email": "jane@example.com", "password": "hunter22",
        },
    )
    token = r.json()["token"]

    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["doctor"]["hpcsa_number"] == "808080"

    bad = client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert bad.status_code == 401

    missing = client.get("/me")
    assert missing.status_code == 401


def test_two_devices_can_stay_logged_in_at_once(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
            "email": "jane@example.com", "password": "hunter22",
        },
    )
    token_pc = client.post("/login", json={"hpcsa_number": "808080", "password": "hunter22"}).json()["token"]
    token_phone = client.post("/login", json={"hpcsa_number": "808080", "password": "hunter22"}).json()["token"]

    assert client.get("/me", headers={"Authorization": f"Bearer {token_pc}"}).status_code == 200
    assert client.get("/me", headers={"Authorization": f"Bearer {token_phone}"}).status_code == 200


def test_requests_only_lists_this_doctors_finalized_requests(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    token = client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
            "email": "jane@example.com", "password": "hunter22",
        },
    ).json()["token"]

    # A finalized request for THIS doctor.
    print_records.save_record(
        request_id="PR260929-7K4M",
        doctor_name="Dr. Jane Doe",
        hpcsa_number="808080",
        raw_text="raw",
        normalized_text="Specimen type: blood.",
        structured={"tests_required": []},
        print_status="pending",
        label_image_base64=FAKE_LABEL_IMAGE,
    )
    # A finalized request for a DIFFERENT doctor — must not leak across.
    print_records.save_record(
        request_id="PR260929-9X2Q",
        doctor_name="Dr. Someone Else",
        hpcsa_number="999999",
        raw_text="raw",
        normalized_text="Specimen type: urine.",
        structured={"tests_required": []},
        print_status="pending",
        label_image_base64=FAKE_LABEL_IMAGE,
    )

    r = client.get("/requests", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    requests_list = r.json()["requests"]
    assert len(requests_list) == 1
    assert requests_list[0]["request_id"] == "PR260929-7K4M"


def test_requests_requires_a_valid_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/requests")
    assert r.status_code == 401


def test_profile_and_password_update(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    token = client.post(
        "/register",
        json={
            "name": "Dr. Jane Doe", "hpcsa_number": "808080", "cell": "0821234567",
            "email": "jane@example.com", "password": "hunter22",
        },
    ).json()["token"]

    r1 = client.put(
        "/profile",
        json={"name": "Dr. Jane Updated", "cell": "0829999999", "email": "new@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["doctor"]["name"] == "Dr. Jane Updated"

    r2 = client.put(
        "/password",
        json={"current_password": "wrong", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 401

    r3 = client.put(
        "/password",
        json={"current_password": "hunter22", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 200

    # Old password no longer works; new one does.
    assert client.post("/login", json={"hpcsa_number": "808080", "password": "hunter22"}).status_code == 401
    assert client.post("/login", json={"hpcsa_number": "808080", "password": "newpass123"}).status_code == 200
