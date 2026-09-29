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
request, its confirmed transcript IS stored server-side (see
print_records.py), because that's what makes the printed barcode
scannable/look-up-able by lab staff later (GET /print-lookup/{id}). See
print_records.py's docstring for why this was a considered, confirmed
change from this project's original fully-stateless design, not an
oversight.

Run it with:
    uvicorn main:app --host 0.0.0.0 --port 8000

The model choice, device, and compute type are all set via environment variables
so the SAME code runs on a plain laptop CPU and on a GPU machine.
"""

import os
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel
from pydantic import BaseModel

import field_extraction
import label_printing
import matching
import print_records

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

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Pathology Dictation POC", version="0.1.0")

# CORS: the PWA is served from a different origin (e.g. a phone browser or a
# static file server on another port), so the browser needs permission to call
# this API. "*" is fine for a POC on a trusted network. LOCK THIS DOWN before
# anything real — restrict allow_origins to your actual frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


class PrintLabelRequest(BaseModel):
    doctor_name: str
    hpcsa_number: str
    raw_text: str
    normalized_text: str
    structured: dict = {}


@app.post("/print-label")
def print_label(body: PrintLabelRequest):
    """Finalizes a confirmed request: renders ONE label image, saves the
    digital record with it FIRST (so it can never be lost to a printer
    hiccup), then sends that SAME image to the physical printer — two
    identical barcodes for the one request, never rendered twice (so they
    can't drift), one printed and one returned here + stored for the PWA/
    lookup page to display. A printer failure is reported back, never
    silently swallowed — the frontend shows "digital copy saved, printer
    error: ..." rather than looking like the whole action failed."""
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
            )
            break
        except Exception as exc:  # noqa: BLE001 — sqlite3.IntegrityError on PK collision
            if "UNIQUE" not in str(exc).upper():
                raise
    else:
        raise HTTPException(status_code=500, detail="Could not allocate a unique request ID")

    try:
        label_printing.print_label_image(image)
    except label_printing.PrinterConnectionError as exc:
        print_records.update_print_status(request_id, "print_failed", str(exc))
        return {
            "request_id": request_id, "print_status": "print_failed",
            "print_error": str(exc), "label_image": label_image,
        }
    except Exception as exc:  # noqa: BLE001 — never let a printer failure crash the request
        print_records.update_print_status(request_id, "print_failed", str(exc))
        return {
            "request_id": request_id, "print_status": "print_failed",
            "print_error": str(exc), "label_image": label_image,
        }

    print_records.update_print_status(request_id, "printed")
    return {
        "request_id": request_id, "print_status": "printed",
        "print_error": None, "label_image": label_image,
    }


@app.get("/print-lookup/{request_id}")
def print_lookup(request_id: str):
    """For lab staff: scan (or type) the printed barcode's request ID to
    retrieve the doctor's confirmed transcript. Deliberately unauthenticated
    (see README) — acceptable for this POC stage since the ID itself is the
    access key, not something to harden further without a real deployment."""
    record = print_records.get_record(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No request found with that ID")
    return record


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
