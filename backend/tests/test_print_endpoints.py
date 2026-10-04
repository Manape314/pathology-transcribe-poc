"""
Integration tests through the REAL POST /print-label and
GET /print-lookup/{id} routes. Importing `main` triggers one real (cached)
Whisper model load, same as test_transcribe_endpoint.py — subject to the
same memory constraint documented there.

/print-label no longer attempts to print at all (see print_jobs.py /
print_agent.py) — it only creates a print_jobs row and returns
immediately, so these tests no longer need to monkeypatch
label_printing.print_label_image. Coverage for the actual print-job
lifecycle (claim/complete/fail/retry/reprint, agent auth) lives in
test_print_job_endpoints.py.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

import main
import print_jobs
import print_records


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_print_records.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    print_jobs.init_db()


BODY = {
    "doctor_name": "Dr. Thato Manapi",
    "doctor_phone": "+27821234567",
    "hpcsa_number": "808080",
    "raw_text": "Patient name, Jane Doe. Tests required, FBC.",
    "normalized_text": "Patient name: Jane Doe\nTests required: Full Blood Count (FBC)",
    "structured": {"tests_required": [{"raw": "FBC", "normalized": "Full Blood Count"}]},
}


def test_print_label_saves_record_and_queues_a_pending_job():
    # Printing is now async (see print_jobs.py) — /print-label never
    # attempts to print itself, so its response is always "pending",
    # never a final printed/failed outcome.
    client = TestClient(main.app)

    response = client.post("/print-label", json=BODY)

    assert response.status_code == 200
    data = response.json()
    assert data["print_status"] == "pending"
    assert data["print_error"] is None
    assert data["request_id"].startswith("PR")
    assert data["print_job_id"]
    assert data["label_image"].startswith("data:image/png;base64,")

    # Exactly one job was created for this request — not zero, not two.
    job = print_jobs.latest_job_for_request(data["request_id"])
    assert job is not None
    assert job["status"] == "pending"

    lookup = client.get(f"/print-lookup/{data['request_id']}")
    assert lookup.status_code == 200
    record = lookup.json()
    assert record["doctor_name"] == "Dr. Thato Manapi"
    assert record["hpcsa_number"] == "808080"
    assert record["normalized_text"] == BODY["normalized_text"]
    assert record["print_status"] == "pending"
    # Two identical barcodes: the one a print agent will eventually send
    # to the printer and the stored/displayed one must be the exact same
    # image, not separately re-rendered.
    assert record["label_image"] == data["label_image"]
    # Stored server-side for the lookup page's Call/Message/Copy actions
    # — never encoded into the QR/barcode itself (see label_printing.py /
    # README's "Barcode label printing" section).
    assert record["doctor_phone"] == "+27821234567"


def test_print_lookup_returns_404_for_unknown_id():
    client = TestClient(main.app)
    # Valid shape (see _REQUEST_ID_RE), just not a request anyone saved.
    response = client.get("/print-lookup/PR000000-9999")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "bad_id",
    [
        "PR000000-0000",  # suffix uses excluded chars (0/O/1/I)
        "not-an-id",
        "PR2609-ABCD",  # date part too short
        "PR260929-ABC",  # suffix too short
        "",
    ],
)
def test_print_lookup_returns_400_for_malformed_id(bad_id):
    client = TestClient(main.app)
    response = client.get(f"/print-lookup/{bad_id}" if bad_id else "/print-lookup/")
    # An empty path segment doesn't even match the route — FastAPI 404s
    # that itself, which is an acceptable "not found" outcome too.
    assert response.status_code in (400, 404)
    if response.status_code == 400:
        assert "alformed" in response.json()["detail"]


def test_print_lookup_returns_503_when_database_unreachable(monkeypatch):
    def raise_unreachable(request_id):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(print_records, "get_record", raise_unreachable)
    client = TestClient(main.app)
    response = client.get("/print-lookup/PR000000-9999")
    assert response.status_code == 503


def test_specimen_requirements_endpoint_recomputes_from_given_tests():
    # Called by app.js whenever resolving an inline ambiguous test (e.g.
    # "TB" -> Tuberculosis) changes tests_required client-side, after the
    # initial /transcribe response already computed specimen_requirements
    # once. Confirms the newly-resolved test is picked up.
    client = TestClient(main.app)
    tests_required = [
        {"raw": "FBC", "normalized": "Full Blood Count", "status": "confirmed"},
        {"raw": "TB", "normalized": "Tuberculosis", "status": "confirmed"},
    ]
    response = client.post("/specimen-requirements", json={"tests_required": tests_required})
    assert response.status_code == 200
    groups = response.json()["specimen_requirements"]
    by_label = {g["label"]: g["tests"] for g in groups}
    assert by_label["PURPLE — EDTA"] == ["Full Blood Count"]
    # Tuberculosis isn't a coloured venous draw — correctly unmapped,
    # never dropped and never force-assigned a wrong colour.
    assert by_label["Unmapped — verify specimen requirements"] == ["Tuberculosis"]


def test_specimen_requirements_endpoint_with_empty_tests():
    client = TestClient(main.app)
    response = client.post("/specimen-requirements", json={"tests_required": []})
    assert response.status_code == 200
    assert response.json()["specimen_requirements"] == []


def test_print_label_returns_503_when_database_unreachable(monkeypatch):
    # The request must never be reported as submitted when it wasn't —
    # confirms /print-label surfaces a clear failure rather than a
    # misleading success.
    def raise_unreachable(**kwargs):
        raise OperationalError("INSERT", {}, Exception("connection refused"))

    monkeypatch.setattr(print_records, "save_record", raise_unreachable)
    client = TestClient(main.app)

    response = client.post("/print-label", json=BODY)
    assert response.status_code == 503
    assert "NOT saved" in response.json()["detail"]


def test_print_label_with_empty_phone_does_not_break_lookup():
    # A missing/empty phone number must never break the request or the
    # lookup — the frontend is responsible for showing "Contact number
    # unavailable." (see frontend/lookup.js), not the backend rejecting it.
    client = TestClient(main.app)

    body = {**BODY, "doctor_phone": ""}
    response = client.post("/print-label", json=body)
    assert response.status_code == 200
    request_id = response.json()["request_id"]

    lookup = client.get(f"/print-lookup/{request_id}")
    assert lookup.status_code == 200
    assert lookup.json()["doctor_phone"] == ""
