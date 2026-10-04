import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import print_records

FAKE_LABEL_IMAGE = "data:image/png;base64,FAKEBASE64DATA=="


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    # Same disposable-file-per-test spirit as this project always used,
    # just routed through the module's SQLAlchemy engine instead of a raw
    # sqlite3 connection — swapping `_engine` is the equivalent of the old
    # DB_PATH monkeypatch, and needs no real Postgres instance to run.
    db_path = tmp_path / "test_print_records.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
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
        doctor_phone="+27821234567",
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
    assert record["created_at"] is not None
    # The stored image is the exact same one sent to the printer — never
    # re-rendered, so the physical and digital barcodes can't drift apart.
    assert record["label_image"] == FAKE_LABEL_IMAGE
    # The phone number is stored server-side (never in the QR/barcode
    # itself — see label_printing.py) so the lab lookup page's Call/
    # Message/Copy actions have a number to work with.
    assert record["doctor_phone"] == "+27821234567"


def test_save_record_without_phone_stores_null_not_an_error():
    # A doctor profile is expected to always have a phone (required at
    # registration), but the column itself must tolerate a missing one —
    # save_record's doctor_phone defaults to None, not a required field.
    print_records.save_record(
        request_id="PR260929-NOPH",
        doctor_name="Dr. No Phone",
        hpcsa_number="333333",
        raw_text="raw",
        normalized_text="norm",
        structured={},
        print_status="printed",
        label_image_base64=FAKE_LABEL_IMAGE,
    )
    record = print_records.get_record("PR260929-NOPH")
    assert record["doctor_phone"] is None


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
    # sqlalchemy.exc.IntegrityError wraps the underlying driver's
    # collision error on EITHER backend (sqlite3 locally, psycopg against
    # hosted Postgres) — main.py's /print-label catches this same type.
    with pytest.raises(IntegrityError):
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


# --------------------------------------------------------------------------- #
# Migration: a print_records.db created before `created_at` and the
# `structured` column (back when it was named `structured_json`, and
# before `doctor_phone`) existed must keep working — old rows readable,
# new rows saveable — after init_db() backfills the gaps. Simulates this
# by building the OLD schema directly against the same temp DB the
# autouse fixture already pointed `_engine` at, then re-running init_db()
# as main.py would on a normal backend restart.
# --------------------------------------------------------------------------- #


def _create_pre_migration_schema_and_insert_old_row():
    with print_records._engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS print_records"))
        conn.execute(
            text(
                """
                CREATE TABLE print_records (
                    request_id     TEXT PRIMARY KEY,
                    doctor_name    TEXT NOT NULL,
                    hpcsa_number   TEXT NOT NULL,
                    raw_text       TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    structured_json TEXT NOT NULL,
                    printed_at     TEXT NOT NULL,
                    print_status   TEXT NOT NULL,
                    print_error    TEXT,
                    label_image_base64 TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO print_records (
                    request_id, doctor_name, hpcsa_number, raw_text,
                    normalized_text, structured_json, printed_at,
                    print_status, print_error, label_image_base64
                ) VALUES (:request_id, :doctor_name, :hpcsa_number, :raw_text,
                    :normalized_text, :structured_json, :printed_at,
                    :print_status, :print_error, :label_image_base64)
                """
            ),
            {
                "request_id": "PR260101-OLD1",
                "doctor_name": "Dr. Pre Migration",
                "hpcsa_number": "444444",
                "raw_text": "raw",
                "normalized_text": "norm",
                "structured_json": '{"tests_required": []}',
                "printed_at": "2026-01-01T00:00:00+00:00",
                "print_status": "printed",
                "print_error": None,
                "label_image_base64": FAKE_LABEL_IMAGE,
            },
        )


def test_migration_backfills_missing_columns_without_touching_existing_rows():
    _create_pre_migration_schema_and_insert_old_row()

    # Re-running init_db() is exactly what happens on a normal backend
    # restart — this must not raise, and must not drop/alter the old row.
    print_records.init_db()

    old_record = print_records.get_record("PR260101-OLD1")
    assert old_record is not None
    assert old_record["doctor_name"] == "Dr. Pre Migration"
    assert old_record["doctor_phone"] is None  # backfilled NULL, not an error
    # Copied over from the old structured_json column under its new name.
    assert old_record["structured"] == {"tests_required": []}
    # Old rows predate created_at entirely — backfilled from printed_at
    # rather than left NULL (this column is NOT NULL going forward).
    assert old_record["created_at"] is not None

    # New rows saved after the migration work normally, phone included.
    print_records.save_record(
        request_id="PR260929-NEW1",
        doctor_name="Dr. Post Migration",
        hpcsa_number="555555",
        raw_text="raw",
        normalized_text="norm",
        structured={},
        print_status="printed",
        label_image_base64=FAKE_LABEL_IMAGE,
        doctor_phone="+27827654321",
    )
    new_record = print_records.get_record("PR260929-NEW1")
    assert new_record["doctor_phone"] == "+27827654321"
    # Regression check: a column ADDed to an already-existing table (as
    # opposed to one created fresh via create_all) must still carry its
    # server_default — otherwise every row saved after a migration would
    # silently get created_at=NULL forever.
    assert new_record["created_at"] is not None


def test_migration_is_idempotent_across_repeated_startups():
    # main.py calls init_db() every time the backend starts — running it
    # against an already-migrated (or already-fresh) database repeatedly
    # must never raise "duplicate column" or similar.
    print_records.init_db()
    print_records.init_db()
    print_records.init_db()
