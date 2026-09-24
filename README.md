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
phrase against both, at a caller-chosen confidence threshold, via a
word-index prefilter (never linearly fuzzy-scans the full ~45k LOINC
list). It's called by `terminology_normalize.py` to resolve each
individual item **already extracted from the "tests required" field**
(see "Structured field extraction" below) — never over the whole
transcript. An earlier version of this module also ran fuzzy matching over
the entire raw transcript to produce a browsable suggestion list; that was
removed because it couldn't tell prose apart from an actual test name and
regularly suggested unrelated LOINC codes from ordinary sentence
fragments.

`fuzz.token_set_ratio` (the scorer used) is case-sensitive by default, and
NHLS/LOINC entries keep their original capitalization — `best_single_match`
passes rapidfuzz's `utils.default_process` so query and candidates are
compared case/punctuation-insensitively. Without this, a properly-
capitalized canonical name like `"Full Blood Count"` scored 41 against the
correct NHLS entry (case mismatch alone) instead of the true 100 — found
and fixed while building the terminology/code lookup below.

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
  system) via a matching order: curated **unambiguous** abbreviation
  dictionary (101 entries — FBC, CRP, U&E, PT, TSH, PSA, ... — across
  haematology, coagulation, chemical pathology, endocrine, cardiac,
  microbiology, tumour markers, immunology, blood bank) → curated
  **ambiguous** abbreviation dictionary (see below) → exact canonical
  match → fuzzy match via `matching.best_single_match()` at a stricter
  cutoff than a generic search. Each item keeps both `raw` and `normalized`
  values, plus `terminology_system` (`"NHLS"`/`"LOINC"`/`null`) and `code`
  — looked up from the already-loaded data at resolution time, **never
  hardcoded per abbreviation**, so nothing is invented; an NHLS match
  always has `code: null` since the source handbook has no test codes.
  Anything that doesn't clear the confidence bar is `status:
  "unrecognized"` rather than guessed. Spoken/punctuated letter-by-letter
  forms ("F B C", "F.B.C.", "C-R-P") are normalized to the plain form
  before lookup.

  **Ambiguous abbreviations** (an abbreviation with more than one
  legitimate medical meaning — e.g. `TB` could mean Total Bilirubin or
  Tuberculosis) are never silently resolved by string similarity or
  context, no matter how one-sided the evidence looks. They always come
  back as `status: "ambiguous"` with a ranked `candidates` list, each
  carrying a plain-language `reason` (e.g. `"context matched: cough,
  chest"` vs `"no supporting context found"`) — context (surrounding
  `clinical_history`, `provisional_diagnosis`, `specimen_type`,
  `department`, and sibling `tests_required` items) only ever **ranks and
  explains** candidates for the frontend's disambiguation UI, never
  auto-selects one. Automatic contextual disambiguation is intentionally
  deferred until it can be validated against a clinically reviewed
  dataset. 15 well-documented genuinely-ambiguous abbreviations are
  covered — `TB`, `UA`, `PCR`, `MS`, `CA`, `BS`, `BM`, `CP`, `RA`, `MI`,
  `CVA`, `PID`, `DM`, `CF`, `HD` — not a final vocabulary:
  `AMBIGUOUS_ABBREVIATIONS` in
  `terminology_normalize.py` is a plain `{abbreviation: [{canonical_name,
  domain, keywords}, ...]}` dict with no abbreviation-specific logic
  anywhere in the resolver, so it can grow substantially (or be generated/
  imported from a validated terminology resource) without touching the
  matching engine.
- **`clinical_history`, `provisional_diagnosis`, and `medication`** go
  through `clinical_terminology.py` — a sibling to `terminology_normalize.py`
  for free-text clinical fields rather than a discrete list like
  `tests_required`. It scans the raw prose for **exact, word-boundary**
  matches against curated dictionaries only (never fuzzy matching against a
  corpus — that's what makes it safe to run over free text at all, unlike
  the whole-transcript fuzzy matching this project deliberately removed
  earlier): `CLINICAL_ABBREVIATIONS` (diagnosis/symptom/history shorthand —
  SOB, HTN, CKD, COPD, UTI, DVT, PE, PMH, NKDA, ...) for the first two
  fields, and `MEDICATION_ABBREVIATIONS` (dosing/route/frequency shorthand —
  OD, BD, TDS, PRN, IV, IM, PO, ...) for `medication` — three genuinely
  different vocabulary domains, each with its own dictionary, resolved
  through the same engine. A word not in any of these dictionaries is
  copied through completely untouched — nothing is scored or flagged just
  because it appears in free text.

  **Ambiguity is not duplicated per field.** Genuinely ambiguous
  abbreviations (the same 15-entry `AMBIGUOUS_ABBREVIATIONS` used for
  `tests_required`) are reused as-is for clinical/medication scanning too —
  `DM`, `MI`, `MS`, `PID`, etc. always come back `status: "ambiguous"`
  wherever they're found, with **no field-specific "dominant meaning"
  shortcut** (e.g. `DM` in a clinical history is *not* auto-expanded to
  "Diabetes Mellitus" just because that's the statistically likely reading —
  it still requires clinician confirmation, exactly like `tests_required`).
  Confirmed matches use a different item shape from `tests_required`
  (`raw_phrase`, `normalized_term`, `field`, `terminology_system`, `code`,
  `match_type`, `confidence`, `status`, `confirmation_status`, `candidates`,
  `reason`, `provenance`) — `terminology_system`/`code` are always `null`
  here (SNOMED CT isn't integrated — see "Out of scope" — and nothing here
  ever fabricates a code); the shape is deliberately SNOMED-pluggable, so a
  future coded lookup could populate them for confirmed matches without
  changing anything else. Each field's resolved value (`structured.
  clinical_history.value`, etc.) is the prose with confirmed abbreviations
  expanded in place (`"Shortness of Breath (SOB)"`) and ambiguous ones
  marked (`"DM [ambiguous — please confirm]"`) — `resolved_terms` carries
  the full per-item detail for the frontend's disambiguation UI.
- **Everything else** (patient/doctor names, patient ID, HPCSA number,
  ward, hospital, specimen type/site, priority) is **pure raw
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

Covers date/time normalization, terminology normalization (known
abbreviations, spoken letter-by-letter forms, case/punctuation variants,
the ambiguous-abbreviation cases — confirming context re-ranks candidates
but never changes `status` away from `"ambiguous"` — and negative cases:
fuzzy matching must not "correct" unrelated words into test names),
free-text clinical/medication abbreviation scanning
(`test_clinical_terminology.py` — unambiguous expansion in prose,
ambiguous abbreviations always flagged regardless of field, word-boundary
safety, plain prose passing through untouched, medication dosing
shorthand), context threading through `field_extraction.py` (the same
ambiguous abbreviation ranks differently depending on `clinical_history`,
proving context actually flows end-to-end, not just that the ranking
function works in isolation), normalized-transcript construction, and
integration tests that post through the **real** `POST /transcribe` route
with Whisper's recognition step stubbed (so it's fast/deterministic) but
every line of the extraction/normalization/matching code running for real
— including a regression test confirming a decoy phrase embedded in prose
(e.g. "urea analysis urine microscopy") never surfaces as a suggested
test, and one confirming an ambiguous abbreviation reaches the frontend
with its full candidate list through the actual endpoint.

The Whisper-dependent `test_transcribe_endpoint.py` needs enough free RAM
to load `large-v3` fresh (confirmed on this machine: fails with `mkl_malloc:
failed to allocate memory` under ~2GB free) — the other test files have no
such dependency and run in a few seconds.

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
    "tests_required_raw": "FBC, CRP, U and E, TB",
    "tests_required": [
      {
        "raw": "FBC", "normalized": "Full Blood Count", "source": "abbreviation",
        "terminology_system": "NHLS", "code": null, "match_type": "known_abbreviation",
        "confidence": 1.0, "status": "confirmed", "confirmation_status": "automatic",
        "candidates": null, "reason": null
      },
      {
        "raw": "TB", "normalized": null, "source": null,
        "terminology_system": null, "code": null, "match_type": "ambiguous_abbreviation",
        "confidence": null, "status": "ambiguous", "confirmation_status": "pending",
        "candidates": [
          { "canonical_name": "Total Bilirubin", "domain": "chemical_pathology", "terminology_system": "NHLS", "code": null, "reason": "no supporting context found" },
          { "canonical_name": "Tuberculosis", "domain": "clinical_diagnosis", "terminology_system": "NHLS", "code": null, "reason": "no supporting context found" }
        ],
        "reason": null
      }
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

### Clinician disambiguation UI

When a `tests_required` item comes back `status: "ambiguous"`, the
"Tests required" panel (`frontend/app.js`) renders a radio-button group —
one option per candidate (canonical name + domain hint + the ranking
`reason`) plus a **"None of these / keep original"** option, none
pre-selected. On **Confirm & save**, the doctor's pick (or "none") resolves
that item into the final structured record and patches the `"[ambiguous —
please confirm]"` marker in the displayed/saved transcript with the chosen
expansion — both the transcript text and the saved History entry reflect
the clinician's decision, with the original raw phrase preserved
alongside it (`match_type: "ambiguous_abbreviation"`,
`confirmation_status: "clinician_confirmed"`).

Clinical history, provisional diagnosis, and medication each get their own
**independent** confirmation panel (same radio-button/"none of these"
pattern, via `setupFieldPanel()` in `frontend/app.js`) — separate from
"Tests required" and from each other, so confirming one never affects the
others. Only ambiguous terms ever appear in these panels; confirmed
(unambiguous) expansions are already embedded directly in the displayed
transcript, with nothing further for the doctor to check off. A panel with
no ambiguous terms simply doesn't appear.

## Out of scope (deliberately)

- **SNOMED CT matching** — the raw SNOMED CT files exist under
  `Medical_Terminologies/` but were never processed into `backend/data/`
  (unlike NHLS/LOINC) — there's nothing loaded to "integrate." Building
  that index is a separate undertaking comparable in size to the original
  LOINC build, not a small addition.
- **A separate terminology-resolution endpoint** — `structured.
  tests_required` already returns full per-item resolution (including
  ambiguous candidates) as part of `POST /transcribe`; the doctor's
  selection is resolved entirely client-side from data already delivered,
  so a round-trip endpoint would add nothing.
- **Automatic contextual disambiguation** — context ranks and explains
  ambiguous candidates but never auto-selects one, even with strong
  one-sided evidence; deferred until validated against a clinically
  reviewed dataset.
- Patient identifiers, server-side storage (all doctor profiles, history,
  and confirmed tests live in the browser's localStorage only — the server
  is stateless).
