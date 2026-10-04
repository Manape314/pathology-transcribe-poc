"""
Integration tests for the print-job queue's HTTP surface: the
agent-facing endpoints (claim/complete/fail, all requiring
PRINT_AGENT_TOKEN) and the doctor-facing ones (print-status, retry,
reprint). Importing `main` triggers one real (cached) Whisper model
load, same constraint as test_print_endpoints.py. No real NIIMBOT
hardware is involved anywhere here — these tests only exercise the
queue; print_agent.py (which actually calls the hardware) is a separate,
standalone script not imported by the hosted backend at all.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import main
import print_jobs
import print_records

AGENT_TOKEN = "test-agent-token"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_print_jobs.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    print_jobs.init_db()
    monkeypatch.setenv("PRINT_AGENT_TOKEN", AGENT_TOKEN)


BODY = {
    "doctor_name": "Dr. Thato Manapi",
    "doctor_phone": "+27821234567",
    "hpcsa_number": "808080",
    "raw_text": "Patient name, Jane Doe. Tests required, FBC.",
    "normalized_text": "Patient name: Jane Doe\nTests required: Full Blood Count (FBC)",
    "structured": {"tests_required": [{"raw": "FBC", "normalized": "Full Blood Count"}]},
}


def _agent_headers(token=AGENT_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _submit_request(client) -> str:
    response = client.post("/print-label", json=BODY)
    assert response.status_code == 200
    return response.json()["request_id"]


# --------------------------------------------------------------------------- #
# Agent authentication
# --------------------------------------------------------------------------- #


def test_claim_without_token_is_rejected():
    client = TestClient(main.app)
    response = client.post("/print-jobs/claim", json={"station_id": "POC_PRINTER_01"})
    assert response.status_code == 401


def test_claim_with_wrong_token_is_rejected():
    client = TestClient(main.app)
    response = client.post(
        "/print-jobs/claim",
        json={"station_id": "POC_PRINTER_01"},
        headers=_agent_headers("wrong-token"),
    )
    assert response.status_code == 401


def test_claim_fails_closed_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("PRINT_AGENT_TOKEN", raising=False)
    client = TestClient(main.app)
    # Even a blank/empty Authorization header must not be accepted as
    # "no auth required" just because the server has nothing configured.
    response = client.post(
        "/print-jobs/claim",
        json={"station_id": "POC_PRINTER_01"},
        headers=_agent_headers(""),
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Claim / complete / fail
# --------------------------------------------------------------------------- #


def test_claim_with_nothing_pending_returns_204():
    client = TestClient(main.app)
    response = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    )
    assert response.status_code == 204


def test_claim_returns_minimum_payload_no_clinical_data():
    client = TestClient(main.app)
    request_id = _submit_request(client)

    response = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    )
    assert response.status_code == 200
    job = response.json()
    assert job["request_id"] == request_id
    assert job["doctor_name"] == "Dr. Thato Manapi"
    assert job["printer_id"] == "POC_PRINTER_01"
    assert job["qr_payload"].endswith(f"/lookup.html?id={request_id}")
    assert set(job.keys()) == {"print_job_id", "request_id", "qr_payload", "doctor_name", "printer_id"}
    # The things that must NEVER reach a print agent.
    serialized = str(job)
    assert "Jane Doe" not in serialized
    assert "FBC" not in serialized
    assert "hpcsa" not in serialized.lower()


def test_complete_with_wrong_station_id_is_rejected():
    client = TestClient(main.app)
    request_id = _submit_request(client)
    job = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()

    response = client.post(
        f"/print-jobs/{job['print_job_id']}/complete",
        json={"station_id": "SOME_OTHER_STATION"},
        headers=_agent_headers(),
    )
    assert response.status_code == 409


def test_complete_marks_job_printed_and_status_reflects_it():
    client = TestClient(main.app)
    request_id = _submit_request(client)
    job = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()

    response = client.post(
        f"/print-jobs/{job['print_job_id']}/complete",
        json={"station_id": "POC_PRINTER_01"},
        headers=_agent_headers(),
    )
    assert response.status_code == 200

    status = client.get(f"/print-status/{request_id}").json()
    assert status["print_status"] == "printed"
    assert status["print_error"] is None


def test_fail_marks_job_failed_and_preserves_the_saved_request():
    # The core safety property this whole feature depends on: a printer
    # failure never affects whether the request was saved centrally.
    client = TestClient(main.app)
    request_id = _submit_request(client)
    job = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()

    response = client.post(
        f"/print-jobs/{job['print_job_id']}/fail",
        json={"station_id": "POC_PRINTER_01", "error": "printer not connected"},
        headers=_agent_headers(),
    )
    assert response.status_code == 200

    status = client.get(f"/print-status/{request_id}").json()
    assert status["print_status"] == "failed"
    assert status["print_error"] == "printer not connected"

    lookup = client.get(f"/print-lookup/{request_id}")
    assert lookup.status_code == 200
    assert lookup.json()["normalized_text"] == BODY["normalized_text"]


# --------------------------------------------------------------------------- #
# Doctor-facing: status / retry / reprint
# --------------------------------------------------------------------------- #


def test_print_status_for_unknown_request_is_404():
    client = TestClient(main.app)
    response = client.get("/print-status/PR000000-9999")
    assert response.status_code == 404


def test_print_status_for_malformed_id_is_400():
    client = TestClient(main.app)
    response = client.get("/print-status/not-an-id")
    assert response.status_code == 400


def test_print_status_before_any_claim_is_pending():
    client = TestClient(main.app)
    request_id = _submit_request(client)
    status = client.get(f"/print-status/{request_id}").json()
    assert status["print_status"] == "pending"


def test_retry_rejected_unless_latest_job_failed():
    client = TestClient(main.app)
    request_id = _submit_request(client)

    response = client.post(f"/print-jobs/{request_id}/retry")
    assert response.status_code == 409


def test_retry_after_failure_requeues_same_job():
    client = TestClient(main.app)
    request_id = _submit_request(client)
    job = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()
    client.post(
        f"/print-jobs/{job['print_job_id']}/fail",
        json={"station_id": "POC_PRINTER_01", "error": "printer not connected"},
        headers=_agent_headers(),
    )

    response = client.post(f"/print-jobs/{request_id}/retry")
    assert response.status_code == 200
    assert response.json()["print_status"] == "pending"

    # Same request_id, same underlying job row — confirmed by re-claiming
    # it and checking it's the SAME print_job_id as before.
    reclaimed = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()
    assert reclaimed["print_job_id"] == job["print_job_id"]


def test_reprint_creates_a_new_job_for_an_existing_request():
    client = TestClient(main.app)
    request_id = _submit_request(client)
    first_job = client.post(
        "/print-jobs/claim", json={"station_id": "POC_PRINTER_01"}, headers=_agent_headers()
    ).json()
    client.post(
        f"/print-jobs/{first_job['print_job_id']}/complete",
        json={"station_id": "POC_PRINTER_01"},
        headers=_agent_headers(),
    )

    response = client.post(f"/print-jobs/{request_id}/reprint")
    assert response.status_code == 200
    assert response.json()["print_job_id"] != first_job["print_job_id"]

    status = client.get(f"/print-status/{request_id}").json()
    assert status["print_status"] == "pending"  # the new job, not the old "printed" one


def test_reprint_for_unknown_request_is_404():
    client = TestClient(main.app)
    response = client.post("/print-jobs/PR000000-9999/reprint")
    assert response.status_code == 404
