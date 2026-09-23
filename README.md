# Pathology Dictation POC — Speech to Text

A proof of concept: a clinician logs in, records a spoken pathology request on
a phone, the audio is sent to a server, [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
transcribes it, and a clinician-facing **normalized transcript** comes back —
dates/times converted to unambiguous values, test abbreviations (FBC, CRP,
U&E, ...) expanded to their canonical names — for the clinician to review and
confirm. The untouched raw transcript is always kept alongside it.

No patient identifiers, no server-side database (the doctor's profile and
history live in the browser's localStorage only), no SNOMED CT yet (see
"Out of scope" below).

## Architecture

```
  Phone / browser (PWA)                            Server
  ┌────────────────────────┐   POST audio   ┌───────────────────────────────┐
  │ MediaRecorder → Blob    │ ─────────────► │ FastAPI  /transcribe          │
  │ shows normalized        │ ◄───────────── │  1. faster-whisper (Whisper)  │
  │ transcript (raw         │  JSON {         │  2. field_extraction.py:      │
  │ available via toggle) + │   raw_text,     │     proforma → named fields   │
  │ tests-required list,    │   normalized_   │  3. datetime_normalize.py +   │
  │ doctor confirms         │   text,         │     terminology_normalize.py: │
  │                         │   structured}   │     per-field normalization   │
  └────────────────────────┘                 └───────────────────────────────┘
```

Whisper and the normalization pipeline both run on the **server**, so the
phone does no heavy work — it only records, displays, and lets the doctor
confirm/reject the extracted tests. The same backend runs on a laptop CPU or
a GPU machine with no code changes (controlled by environment variables).

## Repo layout

```
backend/       FastAPI app (main.py), matching.py (NHLS/LOINC fuzzy suggestions),
                field_extraction.py + terminology_normalize.py + datetime_normalize.py
                (structured pathology-request parsing), scripts/build_terminology_index.py,
                tests/
frontend/      PWA — plain HTML/CSS/JS, no build step
requirements.txt
```

## Prerequisites

- **Python 3.9+** (3.10 or 3.11 recommended).
- **FFmpeg libraries** for decoding the browser's WebM/Opus (and iOS MP4/AAC).
  faster-whisper reads audio through PyAV, which ships its own FFmpeg binaries,
  so this usually works out of the box. If you hit a decoding error, install
  system FFmpeg:
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Windows: download from ffmpeg.org and add it to PATH.
- For GPU: an NVIDIA GPU with CUDA + cuDNN installed.

## Backend — install & run

```bash
cd backend
python -m venv ../venv
source ../venv/bin/activate        # Windows: ..\venv\Scripts\activate
pip install -r ../requirements.txt

uvicorn main:app --host 0.0.0.0 --port 8000
```

First start downloads the Whisper model weights (large-v3 is ~3 GB) and
caches them, so the first run is slow. Open <http://localhost:8000/> to see a
health/config readout confirming the model, device, compute type, and whether
terminology matching data loaded (`terminology_loaded`).

If you skip the terminology-matching setup below, transcription and field
extraction still work fine — `tests_required` items just come back with
`status: "unmatched"` instead of a canonical name.

### CPU vs GPU

Everything is set by environment variables — no code edits:

| Variable       | Default (CPU)     | For GPU        | Notes                                   |
|----------------|-------------------|----------------|-----------------------------------------|
| `WHISPER_MODEL`| `large-v3`        | `large-v3`     | try `small`/`medium` on CPU while dev'ing |
| `DEVICE`       | `cpu`             | `cuda`         |                                         |
| `COMPUTE_TYPE` | `int8` (auto)     | `float16` (auto)| auto-picked from DEVICE if unset        |

```bash
# CPU (default) — just run it:
uvicorn main:app --host 0.0.0.0 --port 8000

# GPU — much faster:
DEVICE=cuda COMPUTE_TYPE=float16 uvicorn main:app --host 0.0.0.0 --port 8000

# Faster iteration on a CPU laptop with a smaller model:
WHISPER_MODEL=small uvicorn main:app --host 0.0.0.0 --port 8000
```

> **Note:** `large-v3` on CPU is *slow but functional* — expect several times
> real-time for a short clip. Setting `DEVICE=cuda` with `COMPUTE_TYPE=float16`
> is dramatically faster and is the recommended way to run the full model.

### Medical vocabulary bias

`backend/main.py` has a clearly-labelled `MEDICAL_PROMPT` constant seeded with
common South African pathology terms (FBC, U&E, creatinine, CRP, HbA1c, EDTA
tube, etc.). Whisper uses it as an `initial_prompt` to nudge spelling/word
choice. Edit it freely — it biases, it does not restrict.

### Terminology matching (NHLS + LOINC)

`backend/matching.py` loads the NHLS test index and LOINC once at startup
and exposes `best_single_match()`: fuzzy-matching (rapidfuzz) a single
phrase against both, at a caller-chosen confidence threshold. It's called
by `terminology_normalize.py` to resolve each individual item **already
extracted from the "tests required" field** (see "Structured field
extraction" below) — never over the whole transcript. An earlier version
of this module also ran fuzzy matching over the entire raw transcript to
produce a browsable suggestion list; that was removed because it couldn't
tell prose apart from an actual test name and regularly suggested
unrelated LOINC codes from ordinary sentence fragments.

This needs reference data that is **not** in the repo (large, and partly
licensed — see below), and a one-time local build step:

1. Place your own copy of the reference data in `Medical_Terminologies/` at
   the repo root (gitignored):
   - `GPQ0064v3.pdf` — the NHLS test handbook (or your local lab's
     equivalent), containing a TEST NAME / SPECIMEN TYPE / SPECIAL
     INSTRUCTIONS table.
   - `Loinc_2.83/LoincTable/Loinc.csv` — a [LOINC](https://loinc.org) release
     (LOINC is free to use with registration; redistributing the raw files
     is against its license, which is why it isn't in this repo).
   - Requires the `pdftotext` binary (poppler) on PATH.
2. Run the build script once:
   ```bash
   python backend/scripts/build_terminology_index.py
   ```
   This writes small lookup files to `backend/data/` (also gitignored —
   regenerate locally rather than committing). The backend loads only these
   small files at startup, never the raw multi-GB sources.
3. Restart the backend. `GET /` should now show `"terminology_loaded": true`.

The NHLS PDF parser is **best-effort**: it's reconstructing a table from
PDF text layout, and some entries (especially ones with wrapped, multi-line
cells) come out with imperfect specimen/instructions text. This is an
accepted limitation, not a bug to chase down — the doctor visually confirms
every suggested match in the app before it's saved, so an occasional messy
NHLS entry just won't get picked.

SNOMED CT is deliberately not wired up yet (see "Out of scope").

### Structured field extraction, and the normalized transcript

`backend/field_extraction.py` parses a dictated proforma transcript
("Patient name, X. Patient ID, Y. ...") into named fields, returned in
`/transcribe`'s `structured` object:

- **Dates/times** (`date_of_birth`, `date_requested`/`time_requested`,
  `date_collected`/`time_collected`) go through `datetime_normalize.py` —
  fully deterministic (regex/lookup, no LLM), producing ISO 8601
  (`YYYY-MM-DD` / 24h `HH:MM`). Anything not confidently parseable (an
  ambiguous or malformed phrase, e.g. two different month names in the same
  raw string, or a time with no am/pm) is reported as `status: "ambiguous"`
  with `value: null` and the **raw spoken text preserved** — never silently
  guessed.
- **`tests_required`** goes through `terminology_normalize.py`, which reuses
  `matching.py`'s already-loaded NHLS/LOINC data (no duplicate terminology
  system) via a matching order: curated abbreviation dictionary (FBC, CRP,
  U&E, ...) → exact canonical match → fuzzy match via `matching.
  best_single_match()` at a stricter cutoff than a generic search. Each item
  keeps both `raw` and `normalized` values; anything that doesn't clear the
  confidence bar is `status: "unmatched"` rather than guessed.
- **Everything else** (patient/doctor names, patient ID, HPCSA number,
  ward, hospital, specimen type/site, medication, priority) is **pure raw
  passthrough** — terminology/fuzzy matching never touches identifiers or
  names.
- Text not claimed by any recognized field (e.g. a corrupted trailing
  sentence from a speech-recognition glitch) is collected into
  `unparsed_text` for clinician review, rather than being dropped or
  attached to the wrong field.

`field_extraction.build_normalized_text()` then reconstructs a clinician-
readable transcript from `structured`: one `"Label: value"` line per field
that was actually dictated, substituting confident date/time/test values,
and showing `"{raw} [unconfirmed — please verify]"` / `"{raw}
[unrecognized]"` for anything ambiguous or unmatched. This — not the raw
Whisper output — is what `/transcribe` returns as `normalized_text` and
what the PWA shows by default; `raw_text` is always included too and stays
one click away behind the "View raw transcript" toggle.

### Running the tests

```bash
pytest backend/tests -v
```

Covers date/time normalization, terminology normalization (including
negative cases — fuzzy matching must not "correct" unrelated words into
test names), field extraction and normalized-transcript construction, and
integration tests that post through the **real** `POST /transcribe` route
with Whisper's recognition step stubbed (so it's fast/deterministic) but
every line of the extraction/normalization/matching code running for real
— including a regression test confirming a decoy phrase embedded in prose
(e.g. "urea analysis urine microscopy") never surfaces as a suggested test.

## Frontend — serve it

The frontend is static files; serve the `frontend/` folder with any static
server:

```bash
cd frontend
python -m http.server 5173
```

Then open <http://localhost:5173/>. Set the backend address at the top of
`frontend/app.js`:

```js
const BACKEND_URL = "http://localhost:8000";
```

### Running it from a phone (the HTTPS caveat)

Browsers only grant microphone access on a **secure context**: `https://` **or**
`http://localhost`. So:

- On the same machine, `http://localhost` works.
- From a phone hitting your laptop's LAN IP over plain `http://`, the mic will
  be **blocked**. Options: put both behind HTTPS (e.g. a reverse proxy with a
  self-signed cert, or a tunnel like ngrok/Cloudflare Tunnel), then point
  `BACKEND_URL` at the HTTPS backend.

CORS is enabled wide-open (`*`) on the backend for POC convenience — lock it
down to your real frontend origin before using this anywhere real.

## API

`POST /transcribe` — `multipart/form-data`, field name `file`.

Returns:

```json
{
  "raw_text": "Patient name, Gabelo Mukwena. ... Tests required, FBC, CRP, U and E. ...",
  "normalized_text": "Patient name: Gabelo Mukwena\n...\nTests required: Full Blood Count, C-reactive protein, Urea and Electrolytes\n...",
  "segments": [{ "start": 0.0, "end": 3.2, "text": "..." }],
  "language": "en",
  "duration": 3.2,
  "structured": {
    "patient_name": { "raw": "Gabelo Mukwena", "value": "Gabelo Mukwena", "status": "extracted" },
    "date_of_birth_raw": "1998-8-August 14th, 6 May",
    "date_of_birth": null,
    "date_of_birth_status": "ambiguous",
    "date_requested_raw": "2026-22nd September",
    "date_requested": "2026-09-22",
    "date_requested_status": "confirmed",
    "time_requested_raw": "25 minutes to 3 p.m",
    "time_requested": "14:35",
    "time_requested_status": "confirmed",
    "tests_required_raw": "FBC, CRP, U and E. Blood culture",
    "tests_required": [
      { "raw": "FBC", "normalized": "Full Blood Count", "source": "abbreviation", "status": "confirmed" },
      { "raw": "CRP", "normalized": "C-reactive protein", "source": "abbreviation", "status": "confirmed" },
      { "raw": "U and E", "normalized": "Urea and Electrolytes", "source": "abbreviation", "status": "confirmed" }
    ],
    "unparsed_text": []
  }
}
```

`raw_text` is the untouched Whisper output; `normalized_text` is the
clinician-facing reconstructed transcript (what the PWA shows by default).
`structured` is always present (an empty object `{}` if field extraction
hit an unexpected error — it fails safe, falling back to `raw_text` for
`normalized_text` too); see "Structured field extraction" above for the
full field list and what each status value means.

## Out of scope (deliberately)

SNOMED CT matching, patient identifiers, server-side storage (all doctor
profiles, history, and confirmed tests live in the browser's localStorage
only — the server is stateless). NHLS test index and LOINC matching are now
implemented; SNOMED CT is a planned fast-follow (its license and ~3.6GB size
need more preprocessing than fit this pass).
