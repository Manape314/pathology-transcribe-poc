"""
Speech-to-text boundary — the ONLY place in this project that talks to a
transcription provider. backend/main.py's /transcribe endpoint calls
transcribe_audio() and gets back a plain (raw_text, language, duration,
segments) tuple; everything downstream of that (field_extraction.py and
everything after it) only ever sees raw_text as a plain string. Nothing
downstream — and nothing in the frontend — needs to know or care that
transcription happens via Groq's hosted API rather than a model loaded
in-process.

Migrated from local faster-whisper (an in-process model, ~3GB of weights,
loaded into this server's own RAM) to Groq's hosted whisper-large-v3-turbo
specifically so this FastAPI process stays light enough to run on a free
hosted tier (Render Free and similar) — the model now runs on Groq's
infrastructure, never on this server.
"""

import os

import groq

GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

# Same role as faster-whisper's old "initial_prompt": a short bit of text
# that nudges ambiguous audio toward expected spelling/vocabulary. It does
# NOT restrict what can be transcribed. Groq's API calls this parameter
# "prompt" rather than "initial_prompt" — same concept, different name.
MEDICAL_PROMPT = (
    "Pathology request. Full blood count, urea and electrolytes, creatinine, "
    "C-reactive protein, HbA1c, haematocrit, EDTA tube, serum separator tube, "
    "cerebrospinal fluid, D-dimer."
)

# Built lazily, on first real use — never at import time. Importing this
# module (or main.py, which imports it) must never require GROQ_API_KEY to
# be set, so every test file that imports main for unrelated endpoints
# (print jobs, time normalization, ...) keeps working with zero Groq
# configuration and makes zero network calls.
_client = None


class TranscriptionError(Exception):
    """Raised for any transcription failure the caller should turn into a
    single generic, client-safe message — never leaks the underlying cause
    (a missing/invalid key, a network error, a Groq-side failure) past this
    module. The real cause is chained via `raise ... from exc` so it's
    still visible in server-side logs/tracebacks, just never returned to
    the browser."""


def _get_client() -> "groq.Groq":
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise TranscriptionError("GROQ_API_KEY is not configured.")
        # A finite timeout turns a hung connection into a controlled,
        # retryable error (caught as APIConnectionError below) instead of
        # the request hanging indefinitely.
        _client = groq.Groq(api_key=api_key, timeout=60.0)
    return _client


def transcribe_audio(path: str) -> tuple[str, str, float, list[dict]]:
    """
    Sends ONE audio file to Groq's hosted whisper-large-v3-turbo and
    returns (raw_text, language, duration, segments) — everything
    backend/main.py's /transcribe endpoint needs to keep building the
    exact same response shape it always has. One call in, one call out:
    this never splits a single recording into multiple Groq requests, and
    never retries automatically (a failed request surfaces as
    TranscriptionError; the doctor pressing Record again is the retry,
    not an automatic loop silently spending free-tier quota).
    """
    client = _get_client()

    try:
        with open(path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                file=audio_file,
                model=GROQ_WHISPER_MODEL,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                prompt=MEDICAL_PROMPT,
            )
    except groq.RateLimitError as exc:
        raise TranscriptionError("Groq rate limit exceeded.") from exc
    except groq.APIConnectionError as exc:
        raise TranscriptionError("Could not reach Groq (network error or timeout).") from exc
    except groq.APIStatusError as exc:
        raise TranscriptionError(f"Groq returned an error (status {exc.status_code}).") from exc

    raw_text = (response.text or "").strip()
    language = getattr(response, "language", None) or "unknown"
    duration = getattr(response, "duration", None) or 0.0
    # Confirmed by actually running this against the real API: unlike the
    # top-level response fields above, each item in response.segments is a
    # plain dict (e.g. {"start": ..., "end": ..., "text": ...}), not an
    # object with attribute access — the groq SDK does not wrap segments
    # in their own model class. _seg_field() tolerates either shape so a
    # future SDK version changing this doesn't silently break transcription.
    def _seg_field(seg, key):
        return seg.get(key) if isinstance(seg, dict) else getattr(seg, key, None)

    segments = [
        {"start": _seg_field(seg, "start"), "end": _seg_field(seg, "end"), "text": _seg_field(seg, "text")}
        for seg in (getattr(response, "segments", None) or [])
    ]
    return raw_text, language, duration, segments
