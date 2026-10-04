"""
The central print-job queue — the piece that lets a doctor on any device
(Windows/macOS/Android/iOS) trigger printing with one click, even though
the hosted backend has no physical connection to the NIIMBOT B21.

Previously, /print-label called label_printing.print_label_image()
directly, in-process — which only ever worked because the backend
happened to run on the same machine as the printer. Now /print-label just
creates a row here (status="pending") and returns immediately; a
separate, standalone process — print_agent.py, running on whichever
machine the B21 is actually plugged into — polls this queue over HTTPS,
claims a job, prints it, and reports back. See print_agent.py's docstring
for why that process deliberately knows nothing about pathology requests,
transcripts, or this database beyond the one row it claims.

Reuses print_records.py's engine/metadata (same database, one connection
pool) rather than standing up a second one. request_id is the only link
back to print_records — this table never duplicates the pathology
request itself, only what's needed to track a physical print attempt.

Four statuses: pending -> printing -> printed | failed. "printing" covers
both "an agent claimed this" and "an agent is actively working on it" —
claimed_at already records when that happened, so a separate "claimed"
status would be redundant for a one-station prototype. A job stuck in
"printing" past STALE_JOB_TIMEOUT_SECONDS (an agent crashed mid-print) is
treated as claimable again automatically; past MAX_PRINT_ATTEMPTS it's
auto-failed instead of being retried forever.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    and_,
    or_,
    select,
)

import label_printing
import print_records

STALE_JOB_TIMEOUT_SECONDS = int(os.getenv("STALE_JOB_TIMEOUT_SECONDS", "120"))
MAX_PRINT_ATTEMPTS = int(os.getenv("MAX_PRINT_ATTEMPTS", "5"))


def _engine():
    # A function, not a cached module-level alias: print_records._engine
    # gets swapped out by tests (monkeypatch.setattr(print_records,
    # "_engine", ...)) — reading it fresh every call, rather than
    # snapshotting a reference at import time, is what makes this module
    # actually follow that swap instead of silently keeping the old one.
    return print_records._engine


print_jobs = Table(
    "print_jobs",
    print_records.metadata,
    Column("print_job_id", String, primary_key=True),  # uuid4 hex — internal only, never shown to a doctor/pathologist
    Column("request_id", String, ForeignKey("print_records.request_id"), nullable=False),
    Column("status", String, nullable=False),  # pending | printing | printed | failed
    Column("station_id", String, nullable=True),  # set when claimed
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("claimed_at", DateTime(timezone=True), nullable=True),
    Column("printed_at", DateTime(timezone=True), nullable=True),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("last_error", Text, nullable=True),
)
Index("ix_print_jobs_status_created", print_jobs.c.status, print_jobs.c.created_at)


def init_db() -> None:
    """Creates print_jobs if it doesn't exist yet. Scoped to just this
    table (tables=[print_jobs]) since print_records.init_db() already
    owns creating/migrating print_records itself — calling this never
    touches that table."""
    print_records.metadata.create_all(_engine(), tables=[print_jobs], checkfirst=True)


def create_job(request_id: str) -> str:
    """Used both for the initial job created by /print-label AND for an
    intentional reprint (see reprint_job) — always a brand-new row, never
    reusing an existing print_job_id."""
    print_job_id = uuid.uuid4().hex
    with _engine().begin() as conn:
        conn.execute(
            print_jobs.insert().values(
                print_job_id=print_job_id,
                request_id=request_id,
                status="pending",
                created_at=datetime.now(timezone.utc),
                attempt_count=0,
            )
        )
    return print_job_id


def _job_payload(conn, print_job_id: str) -> dict:
    """The minimum a print agent needs to render and print a label —
    never the clinical request itself. doctor_name is fetched from
    print_records at response time (not stored in print_jobs) so this
    table never duplicates the pathology request."""
    job = conn.execute(
        select(print_jobs).where(print_jobs.c.print_job_id == print_job_id)
    ).mappings().first()
    doctor_name = conn.execute(
        select(print_records.print_records.c.doctor_name).where(
            print_records.print_records.c.request_id == job["request_id"]
        )
    ).scalar()
    return {
        "print_job_id": job["print_job_id"],
        "request_id": job["request_id"],
        "qr_payload": f"{label_printing.FRONTEND_URL}/lookup.html?id={job['request_id']}",
        "doctor_name": doctor_name,
        "printer_id": job["station_id"],
    }


def claim_next_job(station_id: str) -> dict | None:
    """Atomically claims the oldest claimable job for `station_id`, or
    returns None if there's nothing to do right now. Claimable means
    status="pending", OR status="printing" but stuck past
    STALE_JOB_TIMEOUT_SECONDS (an agent presumably crashed mid-print).

    Safe under real concurrency (two agents polling at once) because the
    actual claim is a single UPDATE ... WHERE <still claimable> — if two
    requests race, only the one that commits first actually changes a
    row; the second's WHERE re-evaluates after that commit, finds the
    row no longer claimable, and affects zero rows (checked via
    `result.rowcount`). No SELECT ... FOR UPDATE SKIP LOCKED needed for
    a single-station prototype, and this pattern works identically on
    SQLite (used by the test suite) and PostgreSQL."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_TIMEOUT_SECONDS)
    stale_printing = and_(print_jobs.c.status == "printing", print_jobs.c.claimed_at < stale_cutoff)

    with _engine().begin() as conn:
        # A job stuck in "printing" too many times is abandoned, not
        # retried forever — mark it failed instead of handing it out
        # again.
        conn.execute(
            print_jobs.update()
            .where(stale_printing, print_jobs.c.attempt_count >= MAX_PRINT_ATTEMPTS)
            .values(status="failed", last_error="Exceeded max print attempts")
        )

        candidate = conn.execute(
            select(print_jobs.c.print_job_id)
            .where(or_(print_jobs.c.status == "pending", stale_printing))
            .order_by(print_jobs.c.created_at)
            .limit(1)
        ).scalar()
        if candidate is None:
            return None

        result = conn.execute(
            print_jobs.update()
            .where(
                print_jobs.c.print_job_id == candidate,
                or_(print_jobs.c.status == "pending", stale_printing),
            )
            .values(
                status="printing",
                station_id=station_id,
                claimed_at=datetime.now(timezone.utc),
                attempt_count=print_jobs.c.attempt_count + 1,
            )
        )
        if result.rowcount == 0:
            return None  # another agent claimed it between our SELECT and UPDATE

        return _job_payload(conn, candidate)


def mark_printed(print_job_id: str, station_id: str) -> bool:
    """Returns False (caller returns 409) if print_job_id doesn't exist
    or wasn't claimed by this station_id — stops a misconfigured second
    agent from marking someone else's job done."""
    with _engine().begin() as conn:
        result = conn.execute(
            print_jobs.update()
            .where(print_jobs.c.print_job_id == print_job_id, print_jobs.c.station_id == station_id)
            .values(status="printed", printed_at=datetime.now(timezone.utc), last_error=None)
        )
        if result.rowcount == 0:
            return False
        request_id = conn.execute(
            select(print_jobs.c.request_id).where(print_jobs.c.print_job_id == print_job_id)
        ).scalar()
    # Denormalized convenience so GET /print-lookup/{id} (the pathologist
    # side, unchanged by this feature) keeps reporting accurate print
    # status without needing to know print_jobs exists.
    print_records.update_print_status(request_id, "printed")
    return True


def mark_failed(print_job_id: str, station_id: str, error: str) -> bool:
    with _engine().begin() as conn:
        result = conn.execute(
            print_jobs.update()
            .where(print_jobs.c.print_job_id == print_job_id, print_jobs.c.station_id == station_id)
            .values(status="failed", last_error=error)
        )
        if result.rowcount == 0:
            return False
        request_id = conn.execute(
            select(print_jobs.c.request_id).where(print_jobs.c.print_job_id == print_job_id)
        ).scalar()
    print_records.update_print_status(request_id, "print_failed", error)
    return True


def latest_job_for_request(request_id: str) -> dict | None:
    with _engine().connect() as conn:
        row = conn.execute(
            select(print_jobs)
            .where(print_jobs.c.request_id == request_id)
            .order_by(print_jobs.c.created_at.desc())
            .limit(1)
        ).mappings().first()
    return dict(row) if row else None


def retry_job(request_id: str) -> dict | None:
    """Resets the latest job back to pending — only valid if it's
    currently "failed" (nothing was physically printed yet, so there's
    nothing to duplicate; it's the same attempt, continued). Returns
    None if there's no job, or the latest one isn't failed — the caller
    (main.py) turns that into a 409, never silently no-ops."""
    latest = latest_job_for_request(request_id)
    if latest is None or latest["status"] != "failed":
        return None
    with _engine().begin() as conn:
        conn.execute(
            print_jobs.update()
            .where(print_jobs.c.print_job_id == latest["print_job_id"])
            .values(
                status="pending", station_id=None, claimed_at=None,
                printed_at=None, last_error=None,
            )
        )
    return latest_job_for_request(request_id)


def reprint_job(request_id: str) -> str:
    """An intentional 'print another physical copy' — unlike retry, this
    creates a brand-new job row rather than reusing the latest one, so a
    request's print history (how many labels were actually produced, and
    when) stays an honest, inspectable sequence of rows rather than
    something a single mutable row's history could blur. Never touches
    print_records / never generates a new request_id."""
    return create_job(request_id)
