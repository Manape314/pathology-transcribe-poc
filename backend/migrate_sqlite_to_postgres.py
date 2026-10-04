"""
One-off migration: copies every row from the project's original local
SQLite store (backend/data/print_records.db, old schema — `structured_json`
column, no `created_at`) into whatever DATABASE_URL currently points at
(intended for a hosted PostgreSQL instance, but works against any target
print_records.py's engine supports).

Not part of the app or the test suite — run manually, once, when you're
ready to move an existing prototype's SQLite data into the new shared
store. The old .db file is never modified or deleted by this script; it's
left on disk as a backup. Safe to re-run: existing request_ids in the
target are skipped (ON CONFLICT style — see _insert_or_skip), not
duplicated or overwritten.

Usage:
    DATABASE_URL=postgresql+psycopg://user:pass@host/db python migrate_sqlite_to_postgres.py
    # or, to migrate from a different old file:
    python migrate_sqlite_to_postgres.py --source path/to/old_print_records.db
"""

import argparse
import json
import os
import sqlite3

from sqlalchemy.exc import IntegrityError

import print_records

_DEFAULT_SOURCE = os.path.join(os.path.dirname(__file__), "data", "print_records.db")


def _read_old_rows(source_path: str) -> list[dict]:
    conn = sqlite3.connect(source_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM print_records").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _insert_or_skip(old_row: dict) -> bool:
    """Returns True if inserted, False if it already existed in the target."""
    structured_raw = old_row.get("structured") or old_row.get("structured_json")
    structured = json.loads(structured_raw) if isinstance(structured_raw, str) else (structured_raw or {})

    with print_records._engine.begin() as conn:
        try:
            conn.execute(
                print_records.print_records.insert().values(
                    request_id=old_row["request_id"],
                    doctor_name=old_row["doctor_name"],
                    hpcsa_number=old_row["hpcsa_number"],
                    doctor_phone=old_row.get("doctor_phone"),
                    raw_text=old_row["raw_text"],
                    normalized_text=old_row["normalized_text"],
                    structured=structured,
                    print_status=old_row["print_status"],
                    print_error=old_row.get("print_error"),
                    label_image_base64=old_row["label_image_base64"],
                    printed_at=old_row["printed_at"],
                )
            )
            return True
        except IntegrityError:
            return False


def migrate(source_path: str) -> None:
    if not os.path.exists(source_path):
        print(f"No SQLite file at {source_path} — nothing to migrate.")
        return

    print_records.init_db()
    old_rows = _read_old_rows(source_path)
    print(f"Found {len(old_rows)} row(s) in {source_path}.")

    inserted = skipped = 0
    for old_row in old_rows:
        if _insert_or_skip(old_row):
            inserted += 1
        else:
            skipped += 1

    print(f"Migration complete: {inserted} inserted, {skipped} already present (skipped).")
    print(f"The original file at {source_path} was left untouched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=_DEFAULT_SOURCE,
        help="Path to the old SQLite print_records.db (default: backend/data/print_records.db)",
    )
    args = parser.parse_args()
    migrate(args.source)
