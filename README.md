# Pathology Dictation POC — Speech to Text

A proof of concept: a clinician logs in, records a spoken pathology request on
a phone, the audio is sent to a server, [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
transcribes it, and the text comes back along with suggested NHLS test /
LOINC code matches for the clinician to review and confirm.

No patient identifiers, no server-side database (the doctor's profile and
history live in the browser's localStorage only), no SNOMED CT yet (see
"Out of scope" below).

## Architecture

```
  Phone / browser (PWA)                          Server
  ┌───────────────────────┐   POST audio   ┌────────────────────────────┐
  │ MediaRecorder → Blob   │ ─────────────► │ FastAPI  /transcribe       │
  │ shows transcript +     │ ◄───────────── │  1. faster-whisper (Whisper)│
  │ suggested test matches │  JSON {text,   │  2. matching.py: NHLS/LOINC │
  │ doctor confirms        │   matches}     │     fuzzy-match candidates │
  └───────────────────────┘                └────────────────────────────┘
```

Whisper and the matching step both run on the **server**, so the phone does no
heavy work — it only records, displays, and lets the doctor confirm/reject
suggested matches. The same backend runs on a laptop CPU or a GPU machine with
no code changes (controlled by environment variables).

## Repo layout

```
backend/       FastAPI app (main.py), matching.py, scripts/build_terminology_index.py
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

# spaCy's language model is a separate download, not covered by pip install:
python -m spacy download en_core_web_sm

uvicorn main:app --host 0.0.0.0 --port 8000
```

First start downloads the Whisper model weights (large-v3 is ~3 GB) and
caches them, so the first run is slow. Open <http://localhost:8000/> to see a
health/config readout confirming the model, device, compute type, and whether
terminology matching data loaded (`terminology_loaded`).

If you skip the terminology-matching setup below, transcription still works
fine — `/transcribe` just returns an empty `matches` array.

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

After transcription, `backend/matching.py` extracts candidate phrases from
the transcript (spaCy noun-chunking + n-grams) and fuzzy-matches them
(rapidfuzz) against the NHLS test index and LOINC, returning ranked
suggestions in `/transcribe`'s `matches` field for the doctor to confirm.

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
  "text": "full transcript",
  "segments": [{ "start": 0.0, "end": 3.2, "text": "..." }],
  "language": "en",
  "duration": 3.2,
  "matches": [
    {
      "match_id": "nhls:C-reactive protein (CRP)",
      "source": "nhls",
      "candidate_phrase": "c-reactive protein",
      "test_name": "C-reactive protein (CRP)",
      "specimen_type": "5 mL clotted blood (yellow top tube)",
      "instructions": "...",
      "score": 81.0
    },
    {
      "match_id": "loinc:1988-5",
      "source": "loinc",
      "candidate_phrase": "reactive protein",
      "loinc_num": "1988-5",
      "long_common_name": "C reactive protein [Mass/volume] in Serum or Plasma",
      "shortname": "CRP SerPl-mCnc",
      "score": 100.0
    }
  ]
}
```

`matches` is always present; it's an empty array if terminology data hasn't
been built (see "Terminology matching" above) or nothing matched.

## Out of scope (deliberately)

SNOMED CT matching, patient identifiers, server-side storage (all doctor
profiles, history, and confirmed matches live in the browser's localStorage
only — the server is stateless). NHLS test index and LOINC matching are now
implemented; SNOMED CT is a planned fast-follow (its license and ~3.6GB size
need more preprocessing than fit this pass).
