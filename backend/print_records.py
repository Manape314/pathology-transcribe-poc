"""
The digital counterpart of every printed barcode label — a SQLite-backed
store (stdlib sqlite3, no new dependency) at backend/data/print_records.db
(already gitignored via the existing `backend/data/` rule).

This is a deliberate, confirmed change to this project's original
"no server-side storage" stance (see README's "Out of scope"): scanning a
printed barcode has to look something up against, and that something —
the doctor's confirmed transcript, which may include patient details the
doctor dictated — can't live only in the doctor's own browser the way
everything else in this project does. Records are kept indefinitely for
now (no automatic deletion); see main.py's /print-label and
/print-lookup/{request_id}.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "print_records.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """Creates the table if it doesn't exist yet. Called once at backend
    startup (main.py), same "set up once" style as loading the Whisper
    model or matching.py's NHLS/LOINC data."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS print_records (
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


def save_record(
    request_id: str,
    doctor_name: str,
    hpcsa_number: str,
    raw_text: str,
    normalized_text: str,
    structured: dict,
    print_status: str,
    label_image_base64: str,
    print_error: str | None = None,
) -> None:
    """Raises sqlite3.IntegrityError on a request_id collision — callers
    (main.py) retry with a freshly generated id rather than treating that
    as fatal; collisions are expected to be vanishingly rare, not
    impossible. `label_image_base64` is the exact same image sent to the
    printer (see label_printing.image_to_data_url) — stored so the digital
    copy always has its barcode too, not just the transcript text."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO print_records (
                request_id, doctor_name, hpcsa_number, raw_text,
                normalized_text, structured_json, printed_at,
                print_status, print_error, label_image_base64
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                doctor_name,
                hpcsa_number,
                raw_text,
                normalized_text,
                json.dumps(structured),
                datetime.now(timezone.utc).isoformat(),
                print_status,
                print_error,
                label_image_base64,
            ),
        )


def update_print_status(request_id: str, print_status: str, print_error: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE print_records SET print_status = ?, print_error = ? WHERE request_id = ?",
            (print_status, print_error, request_id),
        )


def get_record(request_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM print_records WHERE request_id = ?", (request_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["structured"] = json.loads(record.pop("structured_json"))
    record["label_image"] = record.pop("label_image_base64")
    return record
