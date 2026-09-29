"""
Integration tests through the REAL POST /print-label and
GET /print-lookup/{id} routes. Importing `main` triggers one real (cached)
Whisper model load, same as test_transcribe_endpoint.py — subject to the
same memory constraint documented there. label_printing.print_label_image
is monkeypatched so no physical printer is needed.
"""

import pytest
from fastapi.testclient import TestClient

import label_printing
import main
import print_records


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(print_records, "DB_PATH", str(tmp_path / "test_print_records.db"))
    print_records.init_db()


BODY = {
    "doctor_name": "Dr. Thato Manapi",
    "hpcsa_number": "808080",
    "raw_text": "Patient name, Jane Doe. Tests required, FBC.",
    "normalized_text": "Patient name: Jane Doe\nTests required: Full Blood Count (FBC)",
    "structured": {"tests_required": [{"raw": "FBC", "normalized": "Full Blood Count"}]},
}


def test_print_label_success_saves_record_and_reports_printed(monkeypatch):
    monkeypatch.setattr(label_printing, "print_label_image", lambda image: None)
    client = TestClient(main.app)

    response = client.post("/print-label", json=BODY)

    assert response.status_code == 200
    data = response.json()
    assert data["print_status"] == "printed"
    assert data["print_error"] is None
    assert data["request_id"].startswith("PR")
    assert data["label_image"].startswith("data:image/png;base64,")

    lookup = client.get(f"/print-lookup/{data['request_id']}")
    assert lookup.status_code == 200
    record = lookup.json()
    assert record["doctor_name"] == "Dr. Thato Manapi"
    assert record["hpcsa_number"] == "808080"
    assert record["normalized_text"] == BODY["normalized_text"]
    assert record["print_status"] == "printed"
    # Two identical barcodes: the printed one and the stored/displayed one
    # must be the exact same image, not separately re-rendered.
    assert record["label_image"] == data["label_image"]


def test_print_label_failure_still_saves_the_digital_record(monkeypatch):
    def fail(image):
        raise label_printing.PrinterConnectionError("printer not connected")

    monkeypatch.setattr(label_printing, "print_label_image", fail)
    client = TestClient(main.app)

    response = client.post("/print-label", json=BODY)

    assert response.status_code == 200
    data = response.json()
    assert data["print_status"] == "print_failed"
    assert "printer not connected" in data["print_error"]

    # The digital copy must exist regardless of the printer failure — this
    # is the core safety property the whole feature depends on.
    lookup = client.get(f"/print-lookup/{data['request_id']}")
    assert lookup.status_code == 200
    record = lookup.json()
    assert record["print_status"] == "print_failed"
    assert record["normalized_text"] == BODY["normalized_text"]
    # The label image is still generated/saved even though the physical
    # print failed — only the printer step failed, not the rendering.
    assert record["label_image"].startswith("data:image/png;base64,")


def test_print_lookup_returns_404_for_unknown_id():
    client = TestClient(main.app)
    response = client.get("/print-lookup/PR000000-0000")
    assert response.status_code == 404
