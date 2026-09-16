# Pathology Dictation POC — Speech to Text

A minimal proof of concept: a clinician records a spoken pathology request on a
phone, the audio is sent to a server, [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
transcribes it, and the text comes back.

**That is the entire scope.** No form-field extraction, no vocabulary matching
(LOINC/SNOMED/NHLS), no patient identifiers, no database, no second model. Just
audio in → text out, working end to end.

## Architecture

```
  Phone / browser (PWA)                 Server
  ┌────────────────────┐   POST audio   ┌─────────────────────────┐
  │ MediaRecorder → Blob│ ─────────────► │ FastAPI  /transcribe    │
  │ shows transcript    │ ◄───────────── │ faster-whisper (Whisper)│
  └────────────────────┘   JSON {text}  └─────────────────────────┘
```

Whisper runs on the **server**, so the phone does no heavy work — it only
records and displays. The same backend runs on a laptop CPU or a GPU machine
with no code changes (controlled by environment variables).

## Repo layout

```
backend/       FastAPI app (main.py)
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

First start downloads the model weights (large-v3 is ~3 GB) and caches them,
so the first run is slow. Open <http://localhost:8000/> to see a health/config
readout confirming the model, device, and compute type.

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
  "duration": 3.2
}
```

## Out of scope (deliberately)

Form-field extraction, LOINC/SNOMED/NHLS matching, patient identifiers, storage.
This POC exists only to prove the transcription path works.
