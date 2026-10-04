"""
Pathology dictation POC — backend.

Accepts an uploaded audio file, runs faster-whisper on it (on the SERVER),
then runs field_extraction.py to turn the transcript into a structured
pathology request (dates/times deterministically normalized via
datetime_normalize.py, "tests required" resolved to canonical NHLS/LOINC
names via terminology_normalize.py) and a clinician-facing reconstructed
transcript. Transcription itself is stateless — nothing from /transcribe
is stored server-side.

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

The model choice, device, and compute type are all set via environment variables
so the SAME code runs on a plain laptop CPU and on a GPU machine.
"""

import os
import re
import secrets
import tempfile

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

import field_extraction
import label_printing
import matching
import print_jobs
import print_records
import specimen_mapping

# --------------------------------------------------------------------------- #
# Configuration (all overridable via environment variables)
# --------------------------------------------------------------------------- #
# WHISPER_MODEL : which model to load. "large-v3" is the most accurate but the
#                 slowest. On a CPU-only laptop you may want "small" or "medium"
#                 while developing, then switch to "large-v3" on a GPU box.
# DEVICE        : "cpu" (default) or "cuda" (needs an NVIDIA GPU + CUDA libs).
# COMPUTE_TYPE  : the numeric precision CTranslate2 uses.
#                 - "int8"    -> best for CPU (small + reasonably fast)
#                 - "float16" -> best for GPU (fast + accurate)
#                 If you don't set it, we pick a sensible default from DEVICE.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
DEVICE = os.getenv("DEVICE", "cpu")

# Default the compute type off the device unless the user overrode it explicitly.
_default_compute_type = "float16" if DEVICE == "cuda" else "int8"
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", _default_compute_type)

# --------------------------------------------------------------------------- #
# Medical vocabulary bias
# --------------------------------------------------------------------------- #
# Whisper accepts an "initial_prompt": a short bit of text that primes the model
# toward the words/spelling you expect. It does NOT restrict what can be
# transcribed — it just nudges ambiguous audio toward these terms. Edit this
# freely to match the tests and specimen types you dictate most often.
MEDICAL_PROMPT = (
    "Pathology request. Full blood count, urea and electrolytes, creatinine, "
    "C-reactive protein, HbA1c, haematocrit, EDTA tube, serum separator tube, "
    "cerebrospinal fluid, D-dimer."
)

# --------------------------------------------------------------------------- #
# Load the model ONCE at startup.
# --------------------------------------------------------------------------- #
# Loading is expensive, so we do it a single time when the process starts, not
# on every request. The first run for a given model also downloads the weights
# from Hugging Face and caches them (default: ~/.cache/huggingface).
print(f"Loading Whisper model '{WHISPER_MODEL}' on {DEVICE} ({COMPUTE_TYPE})...")
model = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)
print("Model loaded. Ready.")

print_records.init_db()
print_jobs.init_db()

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
        "model": WHISPER_MODEL,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
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


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    Accept a multipart/form-data upload (field name: "file"), transcribe it,
    and return the text.

    The browser's MediaRecorder produces WebM/Opus. We don't decode it by hand —
    faster-whisper reads the file via PyAV (which bundles the FFmpeg libraries),
    so WebM/Opus, MP4/AAC, WAV, etc. all just work. See the README note on
    FFmpeg if you hit a decoding error.
    """
    # Preserve the original extension so the decoder can sniff the format.
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"

    # Write the upload to a temp file on disk. faster-whisper takes a path (or a
    # file-like object); a temp path is the simplest, most robust route.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # transcribe() returns:
        #   segments -> a LAZY generator of Segment objects (start, end, text, ...)
        #   info     -> metadata (detected language, probability, audio duration)
        #
        # vad_filter=True runs Silero VAD first to strip silence. This is the
        # single most effective switch for stopping Whisper from "hallucinating"
        # phantom words during quiet gaps.
        segments, info = model.transcribe(
            tmp_path,
            vad_filter=True,
            initial_prompt=MEDICAL_PROMPT,
        )

        # The generator only does work as we iterate it — this loop is where the
        # actual transcription happens.
        seg_list = []
        text_parts = []
        for seg in segments:
            seg_list.append(
                {"start": seg.start, "end": seg.end, "text": seg.text}
            )
            text_parts.append(seg.text)

        raw_text = "".join(text_parts).strip()

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
            "language": info.language,
            "duration": info.duration,
            "structured": structured,
        }
    finally:
        # Always clean up the temp file, even if transcription raises.
        os.remove(tmp_path)
