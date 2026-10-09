"""
The digital counterpart of every printed barcode label — the one shared
store a doctor's device and a pathologist's device both reach over HTTPS,
regardless of whether they're on the same network or even the same
continent. Backed by SQLAlchemy Core (not the ORM — this project's style
is hand-rolled, explicit SQL, and Core preserves that while adding
pooling and a single code path that works against both backends below).

Reads DATABASE_URL from the environment. Two shapes are supported:

  - A PostgreSQL URL (e.g. Render's injected DATABASE_URL) — the intended
    shared store for a hosted deployment. Only this backend process ever
    talks to it; the browser never sees credentials or a connection
    string (see main.py — the frontend only ever calls FastAPI).
  - Unset -> falls back to the SAME local SQLite file this project always
    used (backend/data/print_records.db, already gitignored via the
    existing backend/data/ rule) — so local development and the test
    suite need no Postgres instance at all.

This remains a deliberate, confirmed change to this project's original
"no server-side storage" stance (see README's "Out of scope"): scanning
a printed barcode has to look something up against, and that something —
the doctor's confirmed transcript, which may include patient details the
doctor dictated — can't live only in the doctor's own browser. Records
are kept indefinitely for now (no automatic deletion). This is a
synthetic-data prototype; see README's hosted-deployment section for
what real patient data would additionally require.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

_DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "print_records.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_SQLITE_PATH}")


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        os.makedirs(os.path.dirname(_DEFAULT_SQLITE_PATH), exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    # pool_pre_ping specifically guards against a hosted Postgres instance
    # (e.g. Render) silently dropping an idle connection between requests.
    return create_engine(url, pool_pre_ping=True)


_engine = _make_engine(DATABASE_URL)

metadata = MetaData()

# JSON on every backend, but the real JSONB type on PostgreSQL
# specifically (queryable/indexable there) — SQLAlchemy dispatches by
# dialect at compile time, so no branching in our own code.
_JSONType = JSON().with_variant(JSONB, "postgresql")

print_records = Table(
    "print_records",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("doctor_name", String, nullable=False),
    Column("hpcsa_number", String, nullable=False),
    Column("doctor_phone", String, nullable=True),
    Column("raw_text", Text, nullable=False),
    Column("normalized_text", Text, nullable=False),
    # The confirmed structured request, including whatever
    # specimen_requirements looked like at save time — see
    # specimen_mapping.py's docstring: this is already a point-in-time
    # SNAPSHOT (field_extraction.py computes it once, during /transcribe,
    # and it's never recomputed on lookup), so no separate column is
    # needed just to satisfy "don't re-map historical requests on open."
    Column("structured", _JSONType, nullable=False),
    Column("print_status", String, nullable=False),
    Column("print_error", Text, nullable=True),
    Column("label_image_base64", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    # Kept as the original ISO-8601 string (not a DateTime column) so its
    # format — and every existing reader of it (main.py, lookup.js's
    # `new Date(record.printed_at)`) — is unchanged by this migration.
    Column("printed_at", String, nullable=False),
)
Index("ix_print_records_print_status", print_records.c.print_status)

# For the one in-place SQLite rename this migration introduces
# (structured_json -> structured) — see _add_missing_columns below.
_RENAMED_FROM = {"structured": "structured_json"}


def init_db() -> None:
    """Creates the table (and its index) if they don't exist yet, then
    backfills any columns missing from an already-existing database —
    e.g. the project's pre-existing local print_records.db, which
    predates `created_at` and still has it stored under the old
    `structured_json` column name. Generalizes this project's established
    additive-migration pattern (originally written just for
    `doctor_phone`) rather than dropping it: old rows are never lost or
    rewritten, only ever added to. Safe to call every startup."""
    metadata.create_all(_engine, checkfirst=True)
    _add_missing_columns()


def _add_missing_columns() -> None:
    inspector = inspect(_engine)
    if "print_records" not in inspector.get_table_names():
        return  # metadata.create_all above already made a fully up-to-date table
    existing = {col["name"] for col in inspector.get_columns("print_records")}
    with _engine.begin() as conn:
        for column in print_records.columns:
            if column.name in existing:
                continue
            ddl_type = column.type.compile(dialect=_engine.dialect)
            # No DEFAULT clause here (SQLite rejects a non-constant
            # default in ADD COLUMN on some versions, and it would only
            # matter for created_at anyway) — save_record() sets
            # created_at explicitly on every insert rather than relying
            # on the column's server_default, same explicit-over-implicit
            # approach already used for printed_at, so a column added to
            # an already-existing table behaves correctly regardless.
            conn.execute(text(f"ALTER TABLE print_records ADD COLUMN {column.name} {ddl_type}"))

            legacy_name = _RENAMED_FROM.get(column.name)
            if legacy_name and legacy_name in existing:
                # Same shape, new name (both store json.dumps(...) text) —
                # copy rather than leaving old rows with an empty request,
                # then drop the old column: its own NOT NULL constraint
                # would otherwise block every future insert (which only
                # ever populates the new column name), and its data is
                # already safely copied above, so nothing is lost.
                conn.execute(text(f"UPDATE print_records SET {column.name} = {legacy_name}"))
                conn.execute(text(f"ALTER TABLE print_records DROP COLUMN {legacy_name}"))
            elif column.name == "created_at":
                # Old rows predate this column entirely; printed_at is the
                # closest real timestamp we have for them.
                conn.execute(
                    text("UPDATE print_records SET created_at = printed_at WHERE created_at IS NULL")
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
    doctor_phone: str | None = None,
    print_error: str | None = None,
) -> None:
    """Raises sqlalchemy.exc.IntegrityError on a request_id collision —
    callers (main.py) retry with a freshly generated id rather than
    treating that as fatal; collisions are expected to be vanishingly
    rare, not impossible. `label_image_base64` is the exact same image
    sent to the printer (see label_printing.image_to_data_url) — stored
    so the digital copy always has its barcode too, not just the
    transcript text. `doctor_phone` is stored — never encoded into the
    QR/barcode itself — so the lookup page's Call/Message/Copy actions
    (frontend/lookup.js) have a number to work with without exposing it
    on the physical label to anyone who merely finds/photographs it."""
    with _engine.begin() as conn:
        conn.execute(
            print_records.insert().values(
                request_id=request_id,
                doctor_name=doctor_name,
                hpcsa_number=hpcsa_number,
                doctor_phone=doctor_phone,
                raw_text=raw_text,
                normalized_text=normalized_text,
                structured=structured,
                print_status=print_status,
                print_error=print_error,
                label_image_base64=label_image_base64,
                printed_at=datetime.now(timezone.utc).isoformat(),
                # Set explicitly rather than relying solely on the
                # column's server_default: a column added by
                # _add_missing_columns (to a database that already
                # existed before created_at did) may not carry that
                # default at the actual DB level on every backend/
                # migration path, so the application layer guarantees it
                # instead — same explicit-over-implicit approach already
                # used for printed_at above.
                created_at=datetime.now(timezone.utc),
            )
        )


def update_print_status(request_id: str, print_status: str, print_error: str | None = None) -> None:
    with _engine.begin() as conn:
        conn.execute(
            print_records.update()
            .where(print_records.c.request_id == request_id)
            .values(print_status=print_status, print_error=print_error)
        )


def get_record(request_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            select(print_records).where(print_records.c.request_id == request_id)
        ).mappings().first()
    if row is None:
        return None
    record = dict(row)
    record["label_image"] = record.pop("label_image_base64")
    return record


def get_records_for_doctor(hpcsa_number: str) -> list[dict]:
    """Backs GET /requests (doctors.py's token auth gates who can call
    it) — a doctor's own History, newest first. Deliberately returns only
    FINALIZED/printed requests, the same ones GET /print-lookup/{id}
    already serves individually: there is no separate store of
    in-progress or abandoned dictations server-side, by design (see this
    module's docstring)."""
    with _engine.connect() as conn:
        rows = conn.execute(
            select(print_records)
            .where(print_records.c.hpcsa_number == hpcsa_number)
            .order_by(desc(print_records.c.created_at))
        ).mappings().all()
    records = []
    for row in rows:
        record = dict(row)
        record["label_image"] = record.pop("label_image_base64")
        records.append(record)
    return records
