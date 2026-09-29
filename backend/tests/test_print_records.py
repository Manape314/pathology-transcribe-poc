import sqlite3

import pytest

import print_records

FAKE_LABEL_IMAGE = "data:image/png;base64,FAKEBASE64DATA=="


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(print_records, "DB_PATH", str(tmp_path / "test_print_records.db"))
    print_records.init_db()


def test_save_and_get_record_roundtrip():
    print_records.save_record(
        request_id="PR260929-7K4M",
        doctor_name="Dr. Thato Manapi",
        hpcsa_number="808080",
        raw_text="raw transcript",
        normalized_text="normalized transcript",
        structured={"tests_required": [{"raw": "FBC"}]},
        print_status="printed",
        label_image_base64=FAKE_LABEL_IMAGE,
    )

    record = print_records.get_record("PR260929-7K4M")
    assert record["doctor_name"] == "Dr. Thato Manapi"
    assert record["hpcsa_number"] == "808080"
    assert record["raw_text"] == "raw transcript"
    assert record["normalized_text"] == "normalized transcript"
    assert record["structured"] == {"tests_required": [{"raw": "FBC"}]}
    assert record["print_status"] == "printed"
    assert record["print_error"] is None
    assert record["printed_at"]  # ISO timestamp present
    # The stored image is the exact same one sent to the printer — never
    # re-rendered, so the physical and digital barcodes can't drift apart.
    assert record["label_image"] == FAKE_LABEL_IMAGE


def test_get_record_returns_none_for_unknown_id():
    assert print_records.get_record("NOPE") is None


def test_print_failure_still_saves_a_retrievable_record():
    # The digital copy — including its barcode image — must exist
    # regardless of whether the physical print succeeded. This is the
    # core safety property of the whole feature.
    print_records.save_record(
        request_id="PR260929-FAIL",
        doctor_name="Dr. Test",
        hpcsa_number="111111",
        raw_text="raw",
        normalized_text="norm",
        structured={},
        print_status="print_failed",
        label_image_base64=FAKE_LABEL_IMAGE,
        print_error="printer not connected",
    )
    record = print_records.get_record("PR260929-FAIL")
    assert record["print_status"] == "print_failed"
    assert record["print_error"] == "printer not connected"
    assert record["label_image"] == FAKE_LABEL_IMAGE


def test_update_print_status():
    print_records.save_record(
        request_id="PR260929-UPD8",
        doctor_name="Dr. Test",
        hpcsa_number="111111",
        raw_text="raw",
        normalized_text="norm",
        structured={},
        print_status="pending",
        label_image_base64=FAKE_LABEL_IMAGE,
    )
    print_records.update_print_status("PR260929-UPD8", "printed")
    assert print_records.get_record("PR260929-UPD8")["print_status"] == "printed"


def test_duplicate_request_id_raises_integrity_error():
    print_records.save_record(
        request_id="PR260929-DUPE",
        doctor_name="Dr. A",
        hpcsa_number="111111",
        raw_text="a",
        normalized_text="a",
        structured={},
        print_status="printed",
        label_image_base64=FAKE_LABEL_IMAGE,
    )
    with pytest.raises(sqlite3.IntegrityError):
        print_records.save_record(
            request_id="PR260929-DUPE",
            doctor_name="Dr. B",
            hpcsa_number="222222",
            raw_text="b",
            normalized_text="b",
            structured={},
            print_status="printed",
            label_image_base64=FAKE_LABEL_IMAGE,
        )
