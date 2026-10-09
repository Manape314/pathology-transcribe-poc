"""
Pathology dictation POC — backend.

Accepts an uploaded audio file, sends it to Groq's hosted
whisper-large-v3-turbo for transcription (see transcription.py — the one
module that knows anything about Groq), then runs field_extraction.py to
turn the transcript into a structured pathology request (dates/times
deterministically normalized via datetime_normalize.py, "tests required"
resolved to canonical NHLS/LOINC names via terminology_normalize.py) and a
clinician-facing reconstructed transcript. Transcription itself is
stateless — nothing from /transcribe is stored server-side.

The one deliberate exception is /print-label: once a doctor finalizes a
request, its confirmed transcript IS stored centrally (see
print_records.py — SQLAlchemy Core against hosted PostgreSQL in
production, or a local SQLite file if DATABASE_URL is unset), because
that's what makes the printed barcode scannable/look-up-able by a
pathologist on a completely different device later (GET
/print-lookup/{id}). See print_records.py's docstring for why this was a
considered, confirmed change from this project's original fully-stateless
design, not an oversight.

Physical printing is NOT done by this process. A hosted FastAPI instance
has no USB/Bluetooth path to a NIIMBOT B21 sitting at a hospital, so
/print-label only ever creates a print_jobs row (see print_jobs.py) and
returns immediately — a separate, standalone print_agent.py, running on
whichever machine actually has the printer attached, polls that queue
over HTTPS and does the real printing. This process never imports
niimprint (label_printing.print_label_image does a LAZY import inside
the function, and nothing here calls that function at all), so "the
cloud server manages print jobs, not hardware" holds structurally, not
just by convention.

Run it with:
    uvicorn main:app --host 0.0.0.0 --port 8000

Transcription runs on Groq's hosted infrastructure (see transcription.py),
not in this process — this server never loads a speech model into its own
RAM, which is what keeps it light enough to run on a free hosted tier.
"""

import os
import re
import secrets
import tempfile

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

import clinical_terminology
import datetime_normalize
import doctors
import field_extraction
import label_printing
import matching
import print_jobs
import print_records
import specimen_mapping
import terminology_normalize
import transcription

print_records.init_db()
print_jobs.init_db()
doctors.init_db()

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Smart Pathology Request", version="0.1.0")

# CORS: the PWA is served from a different origin than this API (a hosted
# frontend talking to a hosted backend, a phone browser, a static file
# server on another port, ...), so the browser needs permission to call
# this API. Set ALLOWED_ORIGINS (comma-separated) to restrict this to your
# actual frontend URL(s) once you have one — e.g.
# "https://my-frontend.onrender.com". Unset, this falls back to "*", which
# is DEVELOPMENT-ONLY — do not leave it as "*" for a real deployment.
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
ALLOWED_ORIGINS = (
    [origin.strip() for origin in _allowed_origins_env.split(",") if origin.strip()]
    if _allowed_origins_env
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    """Quick health/config check — open this in a browser to confirm it's up."""
    return {
        "status": "ok",
        "transcription_provider": "groq",
        "transcription_model": transcription.GROQ_WHISPER_MODEL,
        "groq_api_key_configured": bool(os.getenv("GROQ_API_KEY")),
        "terminology_loaded": matching._READY,
        "printer_connection": label_printing.PRINTER_CONNECTION,
    }


# Matches label_printing.generate_request_id()'s exact output shape:
# "PR" + 6-digit date + "-" + 4 chars from its unambiguous alphabet
# (digits 2-9, uppercase letters excluding O/I).
_REQUEST_ID_RE = re.compile(r"^PR\d{6}-[2-9A-HJ-NP-Z]{4}$")


class PrintLabelRequest(BaseModel):
    doctor_name: str
    doctor_phone: str
    hpcsa_number: str
    raw_text: str
    normalized_text: str
    structured: dict = {}


@app.post("/print-label")
def print_label(body: PrintLabelRequest):
    """Finalizes a confirmed request: renders ONE label image, saves the
    digital record with it FIRST (so it can never be lost to a printer
    hiccup), then queues a print_jobs row referencing the SAME
    request_id and returns immediately — physical printing happens
    asynchronously, on a separate machine (see print_jobs.py /
    print_agent.py), not in this request. The frontend polls
    GET /print-status/{request_id} for the outcome; this response is
    always print_status="pending", never a final printed/failed state,
    since printing hasn't been attempted yet by the time this returns."""
    for _ in range(5):  # retry only on the (very unlikely) id collision
        request_id = label_printing.generate_request_id()
        image = label_printing.render_label_image(body.doctor_name, request_id)
        label_image = label_printing.image_to_data_url(image)
        try:
            print_records.save_record(
                request_id=request_id,
                doctor_name=body.doctor_name,
                hpcsa_number=body.hpcsa_number,
                raw_text=body.raw_text,
                normalized_text=body.normalized_text,
                structured=body.structured,
                print_status="pending",
                label_image_base64=label_image,
                doctor_phone=body.doctor_phone,
            )
            break
        except IntegrityError:
            continue  # request_id collision (vanishingly rare) — try another
        except OperationalError as exc:
            # The database itself is unreachable (network blip, hosted
            # Postgres asleep/restarting, ...) — never claim the request
            # was saved when it wasn't.
            raise HTTPException(
                status_code=503,
                detail="Could not reach the request database — the request "
                "was NOT saved centrally. Please try again shortly.",
            ) from exc
    else:
        raise HTTPException(status_code=500, detail="Could not allocate a unique request ID")

    print_job_id = print_jobs.create_job(request_id)
    return {
        "request_id": request_id, "print_status": "pending",
        "print_job_id": print_job_id, "print_error": None, "label_image": label_image,
    }


@app.get("/print-status/{request_id}")
def print_status(request_id: str):
    """Polled by the doctor's PWA after /print-label while it waits for
    print_agent.py to claim and process the job — see print_jobs.py for
    the actual queue/claim logic. 404 if the request itself doesn't
    exist; a request with no job yet (shouldn't normally happen, since
    /print-label always creates one) reports "pending" rather than
    erroring, since that's still an honest description of "not printed
    yet"."""
    if not _REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=400, detail="Malformed request ID")
    if print_records.get_record(request_id) is None:
        raise HTTPException(status_code=404, detail="No request found with that ID")
    job = print_jobs.latest_job_for_request(request_id)
    if job is None:
        return {"print_status": "pending", "print_error": None, "attempt_count": 0}
    return {
        "print_status": job["status"],
        "print_error": job["last_error"],
        "attempt_count": job["attempt_count"],
    }


@app.post("/print-jobs/{request_id}/retry")
def retry_print_job(request_id: str):
    """Only valid when the request's latest job is "failed" — resets
    that SAME job back to pending (see print_jobs.retry_job's docstring
    for why retry reuses the row rather than creating a new one)."""
    job = print_jobs.retry_job(request_id)
    if job is None:
        raise HTTPException(
            status_code=409,
            detail="Can only retry a request whose latest print job failed",
        )
    return {"print_status": job["status"]}


@app.post("/print-jobs/{request_id}/reprint")
def reprint_print_job(request_id: str):
    """An intentional 'print another physical copy' — always creates a
    NEW print_jobs row (never a new request_id, never touches the
    pathology request itself). Valid any time the request exists."""
    if print_records.get_record(request_id) is None:
        raise HTTPException(status_code=404, detail="No request found with that ID")
    new_job_id = print_jobs.reprint_job(request_id)
    return {"print_job_id": new_job_id, "print_status": "pending"}


# --------------------------------------------------------------------------- #
# Print-agent-only endpoints — require PRINT_AGENT_TOKEN, never usable by a
# doctor's or pathologist's browser. See print_jobs.py / print_agent.py.
# --------------------------------------------------------------------------- #


def require_agent_token(authorization: str = Header(default="")) -> None:
    """Fails CLOSED: if PRINT_AGENT_TOKEN isn't configured at all, every
    agent-facing request is rejected — "no token configured" is never
    treated as "no auth required". Constant-time comparison so response
    timing can't be used to guess the token."""
    expected = os.getenv("PRINT_AGENT_TOKEN", "")
    provided = authorization.removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing agent token")


# --------------------------------------------------------------------------- #
# Doctor accounts — what makes "log in with the same HPCSA number and
# password on a different device" possible (see doctors.py). Unlike
# require_agent_token above (one static shared secret), each doctor's
# token is issued at login/register and looked up per-request.
# --------------------------------------------------------------------------- #


def require_doctor_token(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    doctor = doctors.get_doctor_by_token(token) if token else None
    if doctor is None:
        raise HTTPException(status_code=401, detail="Invalid or missing session token")
    return doctor


class RegisterRequest(BaseModel):
    name: str
    hpcsa_number: str
    cell: str
    email: str
    password: str


@app.post("/register", status_code=201)
def register(body: RegisterRequest):
    try:
        doctor = doctors.create_doctor(body.name, body.hpcsa_number, body.cell, body.email, body.password)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="This HPCSA number is already registered.",
        )
    token = doctors.issue_session_token(doctor["hpcsa_number"])
    return {"token": token, "doctor": doctor}


class LoginRequest(BaseModel):
    hpcsa_number: str
    password: str


@app.post("/login")
def login(body: LoginRequest):
    # Generic error either way — never reveal which field was wrong, same
    # UX the frontend already had when this check was purely local.
    doctor = doctors.verify_password(body.hpcsa_number, body.password)
    if doctor is None:
        raise HTTPException(status_code=401, detail="Incorrect HPCSA number or password.")
    token = doctors.issue_session_token(doctor["hpcsa_number"])
    return {"token": token, "doctor": doctor}


class UpdateProfileRequest(BaseModel):
    name: str
    cell: str
    email: str


@app.put("/profile")
def update_profile(body: UpdateProfileRequest, doctor: dict = Depends(require_doctor_token)):
    return {"doctor": doctors.update_profile(doctor["hpcsa_number"], body.name, body.cell, body.email)}


class UpdatePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.put("/password")
def update_password(body: UpdatePasswordRequest, doctor: dict = Depends(require_doctor_token)):
    ok = doctors.update_password(doctor["hpcsa_number"], body.current_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    return {"status": "ok"}


@app.get("/me")
def me(doctor: dict = Depends(require_doctor_token)):
    """Resolves the stored session token back to a doctor profile —
    called on page load to resume a session now that there's nothing
    left to resume FROM locally (the token is the only thing the client
    still holds; everything else needs confirming server-side)."""
    return {"doctor": doctor}


@app.get("/requests")
def list_requests(doctor: dict = Depends(require_doctor_token)):
    """Backs the History screen — only requests THIS doctor has finalized
    via "Done — Print label" (see print_records.get_records_for_doctor's
    docstring for why there's no separate store of in-progress/abandoned
    dictations to list here)."""
    return {"requests": print_records.get_records_for_doctor(doctor["hpcsa_number"])}


class ClaimJobRequest(BaseModel):
    station_id: str


@app.post("/print-jobs/claim", dependencies=[Depends(require_agent_token)])
def claim_print_job(body: ClaimJobRequest):
    """Atomically claims the oldest pending (or stale-abandoned) job for
    this station — see print_jobs.claim_next_job for the concurrency-safe
    claim logic. 204 if there's nothing to do right now; the agent just
    waits and polls again."""
    job = print_jobs.claim_next_job(body.station_id)
    if job is None:
        return Response(status_code=204)
    return job


class ReportJobRequest(BaseModel):
    station_id: str
    error: str | None = None


@app.post("/print-jobs/{print_job_id}/complete", dependencies=[Depends(require_agent_token)])
def complete_print_job(print_job_id: str, body: ReportJobRequest):
    if not print_jobs.mark_printed(print_job_id, body.station_id):
        raise HTTPException(
            status_code=409,
            detail="Job not found, or not claimed by this station_id",
        )
    return {"status": "ok"}


@app.post("/print-jobs/{print_job_id}/fail", dependencies=[Depends(require_agent_token)])
def fail_print_job(print_job_id: str, body: ReportJobRequest):
    if not print_jobs.mark_failed(print_job_id, body.station_id, body.error or "Unknown error"):
        raise HTTPException(
            status_code=409,
            detail="Job not found, or not claimed by this station_id",
        )
    return {"status": "ok"}


@app.get("/print-lookup/{request_id}")
def print_lookup(request_id: str):
    """For lab staff: scan (or type) the printed barcode's request ID to
    retrieve the doctor's confirmed transcript. Deliberately unauthenticated
    (see README) — acceptable for this POC stage since the ID itself is the
    access key. NOTE: now that this is reachable over the public internet
    rather than just a LAN, that's a materially bigger exposure than before
    — see README's hosted-deployment section before using this with
    anything but synthetic data."""
    if not _REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=400, detail="Malformed request ID")
    try:
        record = print_records.get_record(request_id)
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not reach the request database — try again shortly.",
        ) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="No request found with that ID")
    return record


class SpecimenRequirementsRequest(BaseModel):
    tests_required: list[dict] = []


@app.post("/specimen-requirements")
def specimen_requirements(body: SpecimenRequirementsRequest):
    """Recomputes structured.specimen_requirements for a given
    tests_required list — called by the frontend (app.js's
    resolveAmbiguousItem) whenever resolving an inline ambiguous test
    (e.g. "TB" -> Tuberculosis) changes tests_required client-side after
    the initial /transcribe response. Keeps specimen_mapping.py as the
    single source of truth for the curated test->tube mapping, rather
    than duplicating that dictionary in JavaScript where it could drift
    out of sync with the Python one."""
    return {"specimen_requirements": specimen_mapping.map_tests_to_specimens(body.tests_required)}


class NormalizeTimeRequest(BaseModel):
    raw: str


@app.post("/normalize-time")
def normalize_time_endpoint(body: NormalizeTimeRequest):
    """Thin wrapper around datetime_normalize.normalize_time() — called by
    the frontend's inline time-field editor (app.js) whenever a time_*
    field is typed/edited directly in its block, including the one-click
    AM/PM quick-pick. Keeps normalize_time() as the one source of truth
    for what counts as a valid time, rather than duplicating that parsing
    in JavaScript."""
    value, status = datetime_normalize.normalize_time(body.raw)
    return {"value": value, "status": status}


class NormalizeDateRequest(BaseModel):
    raw: str


@app.post("/normalize-date")
def normalize_date_endpoint(body: NormalizeDateRequest):
    """Thin wrapper around datetime_normalize.normalize_date() — the date
    counterpart to /normalize-time above, called by the frontend's inline
    date-field editor (date_of_birth/date_requested/date_collected)
    whenever one is typed/edited directly in its block."""
    value, status = datetime_normalize.normalize_date(body.raw)
    return {"value": value, "status": status}


class ResolveClinicalFieldRequest(BaseModel):
    field_key: str
    raw_text: str
    structured: dict = {}


_CLINICAL_FIELD_DICTS = {
    "clinical_history": clinical_terminology.CLINICAL_ABBREVIATIONS,
    "provisional_diagnosis": clinical_terminology.CLINICAL_ABBREVIATIONS,
    "medication": clinical_terminology.MEDICATION_ABBREVIATIONS,
}


@app.post("/resolve-clinical-field")
def resolve_clinical_field_endpoint(body: ResolveClinicalFieldRequest):
    """Thin wrapper around clinical_terminology.resolve_field_text() —
    called by the frontend's inline editor for clinical_history/
    provisional_diagnosis/medication, re-resolving ONLY the one edited
    field (abbreviation expansion + ambiguous-term flagging) from its own
    raw text, rather than re-parsing the whole transcript. Dispatches the
    same unambiguous_dict per field_key that field_extraction.py's own
    second pass already uses — one definition, reused here, not
    duplicated."""
    unambiguous_dict = _CLINICAL_FIELD_DICTS.get(body.field_key)
    if unambiguous_dict is None:
        raise HTTPException(status_code=400, detail=f"Unknown clinical field: {body.field_key}")
    result = clinical_terminology.resolve_field_text(
        body.raw_text, body.field_key, unambiguous_dict, context=body.structured
    )
    return {"field": result}


class NormalizeTestItemRequest(BaseModel):
    raw: str
    structured: dict = {}


@app.post("/normalize-test-item")
def normalize_test_item_endpoint(body: NormalizeTestItemRequest):
    """Thin wrapper around terminology_normalize.normalize_tests_required()
    for exactly ONE test name — called by the frontend's inline fix for an
    unrecognized/mistyped test, and by "+ Add test", so a single test can
    be (re)resolved without touching any other already-confirmed item in
    tests_required. Reuses normalize_tests_required() (not the lower-level
    normalize_test_item() directly) so context-based ambiguous-candidate
    ranking is computed identically to how a fresh dictation's
    tests_required gets it."""
    raw = body.raw.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="raw must not be empty")
    items = terminology_normalize.normalize_tests_required(raw, context=body.structured)
    if not items:
        raise HTTPException(status_code=400, detail="raw did not contain a recognizable test name")
    return {"item": items[0]}


class RebuildTranscriptRequest(BaseModel):
    structured: dict = {}


@app.post("/rebuild-transcript")
def rebuild_transcript_endpoint(body: RebuildTranscriptRequest):
    """Thin wrapper around field_extraction.build_normalized_text() —
    called after the AM/PM quick-pick updates a single field in
    currentStructured client-side, so the flat normalized_text string
    (what's actually saved/printed/looked-up) is regenerated from the
    SAME reconstruction logic rather than hand-patched in JavaScript."""
    return {"normalized_text": field_extraction.build_normalized_text(body.structured)}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    Accept a multipart/form-data upload (field name: "file"), transcribe it
    via Groq's hosted whisper-large-v3-turbo (transcription.py), and return
    the text.

    The browser's MediaRecorder produces WebM/Opus (Chrome/Firefox/Android)
    or MP4/AAC (iOS Safari) — Groq's API accepts both formats directly, so
    no conversion step is needed here; the uploaded bytes are sent through
    unchanged.
    """
    # Preserve the original extension so Groq can sniff the format.
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"

    # Write the upload to a temp file on disk — transcription.transcribe_audio()
    # takes a path; a temp file is the simplest, most robust route (same
    # pattern as before this migration).
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        try:
            raw_text, language, duration, seg_list = transcription.transcribe_audio(tmp_path)
        except transcription.TranscriptionError as exc:
            # Never leak the underlying cause (missing/invalid key, Groq's
            # own error text, a network failure) to the browser — log it
            # server-side and give the doctor one generic, retryable
            # message instead. No automatic retry here: the doctor
            # pressing Record again IS the retry, which costs exactly one
            # more request rather than an unbounded loop silently
            # spending free-tier quota during a sustained outage.
            print(f"transcription: Groq request failed ({exc})")
            raise HTTPException(
                status_code=503,
                detail="Transcription is temporarily unavailable. Please try again.",
            ) from exc

        # Field extraction (and, inside it, terminology/date-time
        # normalization) is additive — a bug or edge case here must never
        # break the transcription response, which is the core,
        # already-working value of this endpoint. Falling back to the raw
        # text keeps the response at least as useful, never less.
        try:
            structured = field_extraction.extract_fields(raw_text)
        except Exception as exc:  # noqa: BLE001
            print(f"field_extraction: extract_fields failed ({exc})")
            structured = {}

        try:
            normalized_text = field_extraction.build_normalized_text(structured)
        except Exception as exc:  # noqa: BLE001
            print(f"field_extraction: build_normalized_text failed ({exc})")
            normalized_text = raw_text

        return {
            "raw_text": raw_text,
            "normalized_text": normalized_text or raw_text,
            "segments": seg_list,
            "language": language,
            "duration": duration,
            "structured": structured,
        }
    finally:
        # Always clean up the temp file, even if transcription raises.
        os.remove(tmp_path)
