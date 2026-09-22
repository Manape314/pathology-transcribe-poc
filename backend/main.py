"""
Pathology dictation POC — backend.

Accepts an uploaded audio file, runs faster-whisper on it (on the SERVER),
and returns the transcribed text plus suggested NHLS/LOINC code matches
(see matching.py) as JSON. No patient identifiers, no database — the server
is stateless; confirmed matches are saved client-side only.

Run it with:
    uvicorn main:app --host 0.0.0.0 --port 8000

The model choice, device, and compute type are all set via environment variables
so the SAME code runs on a plain laptop CPU and on a GPU machine.
"""

import os
import tempfile

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel

import matching

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
    }


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

        text = "".join(text_parts).strip()

        # Matching is additive — a bug or edge case here must never break
        # the transcription response, which is the core, already-working
        # value of this endpoint.
        try:
            matches = matching.find_matches(text)
        except Exception as exc:  # noqa: BLE001
            print(f"matching: find_matches failed ({exc})")
            matches = []

        return {
            "text": text,
            "segments": seg_list,
            "language": info.language,
            "duration": info.duration,
            "matches": matches,
        }
    finally:
        # Always clean up the temp file, even if transcription raises.
        os.remove(tmp_path)
