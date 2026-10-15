"""
One-off migration, companion to migrate_sqlite_to_postgres.py: copies
every row from the project's original local SQLite store's `doctors`
table (backend/data/print_records.db) into whatever DATABASE_URL
currently points at. Needed because moving to a hosted database (Render
+ Neon) started from a brand-new, empty database — a doctor account
created before that move only ever existed in the old local SQLite
file, so logging in against the new hosted database with those same
credentials correctly fails (the account genuinely isn't there), not a
bug to patch around in the app itself.

Password hash/salt are copied byte-for-byte, not reset — the doctor's
existing password keeps working after migration, no "forgot password"
step needed. `doctor_sessions` (active login tokens) are deliberately
NOT migrated: a token issued under the old architecture was only ever
valid from the old tunnel/localhost origin anyway, so there is nothing
useful to carry over; logging in fresh on the new origin issues a new,
valid session token the normal way.

Not part of the app or the test suite — run manually, once. The old
.db file is never modified or deleted by this script. Safe to re-run:
existing hpcsa_numbers in the target are skipped, not duplicated or
overwritten.

Usage:
    DATABASE_URL=postgresql+psycopg://user:pass@host/db python migrate_doctors_sqlite_to_postgres.py
"""

import argparse
import os
import sqlite3

from sqlalchemy.exc import IntegrityError

import doctors

_DEFAULT_SOURCE = os.path.join(os.path.dirname(__file__), "data", "print_records.db")


def _read_old_rows(source_path: str) -> list[dict]:
    conn = sqlite3.connect(source_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM doctors").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _insert_or_skip(old_row: dict) -> bool:
    """Returns True if inserted, False if it already existed in the target."""
    with doctors._engine().begin() as conn:
        try:
            conn.execute(
                doctors.doctors.insert().values(
                    hpcsa_number=old_row["hpcsa_number"],
                    name=old_row["name"],
                    cell=old_row["cell"],
                    email=old_row["email"],
                    password_salt=old_row["password_salt"],
                    password_hash=old_row["password_hash"],
                    created_at=old_row["created_at"],
                )
            )
            return True
        except IntegrityError:
            return False


def migrate(source_path: str) -> None:
    if not os.path.exists(source_path):
        print(f"No SQLite file at {source_path} — nothing to migrate.")
        return

    doctors.init_db()
    old_rows = _read_old_rows(source_path)
    print(f"Found {len(old_rows)} doctor row(s) in {source_path}.")

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
