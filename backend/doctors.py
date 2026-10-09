"""
Server-side doctor accounts — what makes "log in with the same HPCSA
number and password on a different device" actually possible. Before
this module existed, a doctor's profile (and the password check) lived
entirely in that one browser's localStorage; a different device, or even
just a different tunnel URL, was a blank slate with no way to verify a
password at all. See main.py's /register, /login, /profile, /password
endpoints — this module only ever talks to the database, never HTTP.

Reuses print_records.py's engine/metadata (same database, one connection
pool), same pattern print_jobs.py already established for its own table.

Password hashing: PBKDF2-HMAC-SHA256 (stdlib hashlib.pbkdf2_hmac), a
unique random salt per password, verified with secrets.compare_digest()
(constant-time — same primitive main.py's require_agent_token already
uses). No external auth framework, no new dependency — this project's
one genuinely sensitive piece of data gets a deliberately stronger
algorithm than a single hash pass, nothing more elaborate than that.

Session tokens live in their own table (doctor_sessions), one row per
login/register — NOT a single column on doctors that each new login
would overwrite. That distinction matters here specifically: the whole
point of this module is a doctor using the SAME account from more than
one device (PC and phone) at once, so logging in on the phone must not
silently invalidate the PC's already-stored token. There's still no
expiry/revocation scheme — a prototype-appropriate simplification
consistent with this project's existing documented security posture
(the same "synthetic-data POC" framing already applied to
GET /print-lookup/{request_id} in main.py). Logging out just drops that
one token client-side; the row is harmless left behind (no cleanup job),
same "no automatic deletion" stance print_records.py already documents.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, select, text

import print_records

PBKDF2_ITERATIONS = 600_000  # OWASP's current minimum recommendation for PBKDF2-SHA256


def _engine():
    # A function, not a cached module-level alias — see print_jobs.py's
    # identical comment: print_records._engine gets swapped out by tests
    # (monkeypatch.setattr(print_records, "_engine", ...)), and reading it
    # fresh every call is what makes this module actually follow that
    # swap instead of silently keeping a stale reference.
    return print_records._engine


doctors = Table(
    "doctors",
    print_records.metadata,
    Column("hpcsa_number", String, primary_key=True),  # normalized uppercase
    Column("name", String, nullable=False),
    Column("cell", String, nullable=False),
    Column("email", String, nullable=False),
    Column("password_salt", String, nullable=False),  # hex-encoded
    Column("password_hash", String, nullable=False),  # hex-encoded PBKDF2 digest
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)

doctor_sessions = Table(
    "doctor_sessions",
    print_records.metadata,
    Column("token", String, primary_key=True),
    Column("hpcsa_number", String, ForeignKey("doctors.hpcsa_number"), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)


def init_db() -> None:
    """Creates doctors and doctor_sessions if they don't exist yet.
    Scoped to just these tables since print_records.init_db() already
    owns creating/migrating print_records itself — calling this never
    touches that table."""
    print_records.metadata.create_all(_engine(), tables=[doctors, doctor_sessions], checkfirst=True)


def normalize_hpcsa(value: str) -> str:
    return value.strip().upper()


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def _public_doctor(row: dict) -> dict:
    """Strips password_salt/password_hash before a row ever goes into an
    HTTP response — main.py's endpoints always return this, never the
    raw row."""
    return {
        "hpcsa_number": row["hpcsa_number"],
        "name": row["name"],
        "cell": row["cell"],
        "email": row["email"],
    }


def create_doctor(name: str, hpcsa_number: str, cell: str, email: str, password: str) -> dict:
    """Raises sqlalchemy.exc.IntegrityError on an hpcsa_number collision —
    main.py maps that to 409, same pattern print_records.save_record's
    callers already use for request_id collisions."""
    hpcsa_number = normalize_hpcsa(hpcsa_number)
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    with _engine().begin() as conn:
        conn.execute(
            doctors.insert().values(
                hpcsa_number=hpcsa_number,
                name=name,
                cell=cell,
                email=email,
                password_salt=salt.hex(),
                password_hash=password_hash.hex(),
                created_at=datetime.now(timezone.utc),
            )
        )
    return _public_doctor(
        {"hpcsa_number": hpcsa_number, "name": name, "cell": cell, "email": email}
    )


def _get_row(hpcsa_number: str) -> dict | None:
    hpcsa_number = normalize_hpcsa(hpcsa_number)
    with _engine().connect() as conn:
        row = conn.execute(
            select(doctors).where(doctors.c.hpcsa_number == hpcsa_number)
        ).mappings().first()
    return dict(row) if row else None


def verify_password(hpcsa_number: str, password: str) -> dict | None:
    """Returns the public doctor dict on success, None on any failure
    (unknown hpcsa_number OR wrong password) — main.py deliberately can't
    tell the two apart from this return value alone, matching the
    existing frontend's generic "Incorrect HPCSA number or password."""
    row = _get_row(hpcsa_number)
    if row is None:
        return None
    salt = bytes.fromhex(row["password_salt"])
    expected = bytes.fromhex(row["password_hash"])
    actual = _hash_password(password, salt)
    if not secrets.compare_digest(actual, expected):
        return None
    return _public_doctor(row)


def issue_session_token(hpcsa_number: str) -> str:
    """Adds a NEW row rather than overwriting anything — a doctor logging
    in on their phone must not invalidate a token already issued to
    their PC (see this module's docstring)."""
    hpcsa_number = normalize_hpcsa(hpcsa_number)
    token = secrets.token_urlsafe(32)
    with _engine().begin() as conn:
        conn.execute(
            doctor_sessions.insert().values(
                token=token,
                hpcsa_number=hpcsa_number,
                created_at=datetime.now(timezone.utc),
            )
        )
    return token


def get_doctor_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _engine().connect() as conn:
        row = conn.execute(
            select(doctors)
            .select_from(doctors.join(doctor_sessions, doctors.c.hpcsa_number == doctor_sessions.c.hpcsa_number))
            .where(doctor_sessions.c.token == token)
        ).mappings().first()
    return _public_doctor(dict(row)) if row else None


def update_profile(hpcsa_number: str, name: str, cell: str, email: str) -> dict:
    """Takes an already-authenticated hpcsa_number — main.py's
    require_doctor_token dependency resolves the session token to a
    doctor dict (including hpcsa_number) once, up front, so this never
    needs to look the token up again itself."""
    hpcsa_number = normalize_hpcsa(hpcsa_number)
    with _engine().begin() as conn:
        conn.execute(
            doctors.update()
            .where(doctors.c.hpcsa_number == hpcsa_number)
            .values(name=name, cell=cell, email=email)
        )
    return _public_doctor({"hpcsa_number": hpcsa_number, "name": name, "cell": cell, "email": email})


def update_password(hpcsa_number: str, current_password: str, new_password: str) -> bool:
    """Returns False if current_password is wrong — main.py maps that to
    401. hpcsa_number itself is already authenticated (see update_profile's
    docstring above), so only the current password needs re-checking
    here."""
    if verify_password(hpcsa_number, current_password) is None:
        return False
    hpcsa_number = normalize_hpcsa(hpcsa_number)
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(new_password, salt)
    with _engine().begin() as conn:
        conn.execute(
            doctors.update()
            .where(doctors.c.hpcsa_number == hpcsa_number)
            .values(password_salt=salt.hex(), password_hash=password_hash.hex())
        )
    return True
