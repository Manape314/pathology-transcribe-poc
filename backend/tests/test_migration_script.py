"""
Tests migrate_sqlite_to_postgres.py against two throwaway SQLite files
standing in for "the old local store" and "the new shared store" — the
script's own logic (read old rows, insert into whatever print_records._engine
currently points at) is identical regardless of which real database sits
behind that engine, so this exercises it without needing a real Postgres
instance (consistent with this project's "tests shouldn't require a real
external service" rule applied elsewhere to NIIMBOT/Whisper).
"""

import sqlite3

import pytest
from sqlalchemy import create_engine

import migrate_sqlite_to_postgres as migrate
import print_records

FAKE_LABEL_IMAGE = "data:image/png;base64,FAKEBASE64DATA=="


@pytest.fixture(autouse=True)
def temp_target_db(tmp_path, monkeypatch):
    # Stands in for the new shared store (Postgres in production).
    engine = create_engine(
        f"sqlite:///{tmp_path / 'target.db'}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(print_records, "_engine", engine)
    yield


def _build_old_sqlite_file(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
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
            label_image_base64 TEXT NOT NULL,
            doctor_phone   TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO print_records (
            request_id, doctor_name, hpcsa_number, raw_text, normalized_text,
            structured_json, printed_at, print_status, print_error,
            label_image_base64, doctor_phone
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["request_id"], r["doctor_name"], r["hpcsa_number"], r["raw_text"],
                r["normalized_text"], r["structured_json"], r["printed_at"],
                r["print_status"], r.get("print_error"), r["label_image_base64"],
                r.get("doctor_phone"),
            )
            for r in rows
        ],
    )
    conn.commit()
    conn.close()


def test_migrate_copies_rows_into_the_target_store(tmp_path):
    source = tmp_path / "old_print_records.db"
    _build_old_sqlite_file(
        source,
        [
            {
                "request_id": "PR260101-AAAA",
                "doctor_name": "Dr. With Phone",
                "hpcsa_number": "111111",
                "raw_text": "raw one",
                "normalized_text": "norm one",
                "structured_json": '{"tests_required": [{"normalized": "Full Blood Count"}]}',
                "printed_at": "2026-01-01T00:00:00+00:00",
                "print_status": "printed",
                "label_image_base64": FAKE_LABEL_IMAGE,
                "doctor_phone": "+27821234567",
            },
            {
                # Historical record missing an optional field (doctor_phone)
                # — must migrate cleanly, not error or get skipped.
                "request_id": "PR260102-BBBB",
                "doctor_name": "Dr. No Phone",
                "hpcsa_number": "222222",
                "raw_text": "raw two",
                "normalized_text": "norm two",
                "structured_json": "{}",
                "printed_at": "2026-01-02T00:00:00+00:00",
                "print_status": "print_failed",
                "print_error": "printer not connected",
                "label_image_base64": FAKE_LABEL_IMAGE,
                "doctor_phone": None,
            },
        ],
    )

    migrate.migrate(str(source))

    first = print_records.get_record("PR260101-AAAA")
    assert first is not None
    assert first["doctor_phone"] == "+27821234567"
    assert first["structured"] == {"tests_required": [{"normalized": "Full Blood Count"}]}

    second = print_records.get_record("PR260102-BBBB")
    assert second is not None
    assert second["doctor_phone"] is None
    assert second["print_status"] == "print_failed"
    assert second["print_error"] == "printer not connected"

    # The old file is left untouched, never deleted by the script.
    assert source.exists()


def test_migrate_is_idempotent_when_rerun(tmp_path):
    source = tmp_path / "old_print_records.db"
    _build_old_sqlite_file(
        source,
        [
            {
                "request_id": "PR260101-AAAA",
                "doctor_name": "Dr. Repeat",
                "hpcsa_number": "333333",
                "raw_text": "raw",
                "normalized_text": "norm",
                "structured_json": "{}",
                "printed_at": "2026-01-01T00:00:00+00:00",
                "print_status": "printed",
                "label_image_base64": FAKE_LABEL_IMAGE,
                "doctor_phone": None,
            }
        ],
    )

    migrate.migrate(str(source))
    migrate.migrate(str(source))  # must not raise or duplicate

    assert print_records.get_record("PR260101-AAAA") is not None


def test_migrate_against_missing_source_file_is_a_safe_no_op(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    migrate.migrate(str(missing))  # must not raise
