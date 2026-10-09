# Smart Pathology Request

A proof of concept: a clinician logs in, records a spoken pathology request on
a phone, the audio is sent to a server, which sends it to
[Groq's hosted speech-to-text API](https://console.groq.com/docs/speech-to-text)
(`whisper-large-v3-turbo`) for transcription, and a clinician-facing
**normalized transcript** comes back — dates/times converted to unambiguous
values, test abbreviations (FBC, CRP, U&E, ...) expanded to their canonical
names — for the clinician to review and confirm. The untouched raw transcript
is always kept alongside it.

Transcription is **hosted, not local**: this server never loads a speech
model into its own process — it forwards the audio to Groq and gets a
transcript back. That's a deliberate choice, not the original design (see
"Speech-to-text (Groq)" below) — it's what keeps the FastAPI backend light
enough to run on a free hosted tier (Render Free and similar), where running
a multi-gigabyte model in-process isn't realistic.

No SNOMED CT yet (see "Out of scope" below). Transcription itself is
fully stateless — nothing about a dictation in progress is ever sent to
the server except the audio and the field-level edits the doctor makes
while reviewing it. The doctor's account (`backend/doctors.py`) and
History (a doctor's own finalized requests) live server-side, so the
same login and the same request history work from any device — see
"Doctor accounts" under the API section below. The one thing that makes
a request exist centrally at all is finalizing it ("Done — Print
label"): that confirmed transcript IS stored server-side, because that's
what makes the printed barcode scannable/look-up-able by lab staff
afterwards, AND what History reads back — see "Barcode label printing"
below for why, and what that changes.

## Architecture

```
  Phone / browser (PWA)                Server                      Groq (hosted)
  ┌────────────────────┐  POST audio  ┌──────────────────────┐  POST audio  ┌─────────────────┐
  │ MediaRecorder→Blob  │ ───────────► │ FastAPI  /transcribe │ ───────────► │ whisper-large-   │
  │ shows normalized    │ ◄─────────── │  1. transcription.py │ ◄─────────── │ v3-turbo         │
  │ transcript (raw     │  JSON {       │  2. field_extraction │  raw_text    └─────────────────┘
  │ available via       │   raw_text,   │     .py: proforma →  │
  │ toggle) + tests-    │   normalized_ │     named fields      │
  │ required list,      │   text,       │  3. datetime_normalize│
  │ doctor confirms      │   structured} │     .py + terminology_│
  │                     │               │     normalize.py      │
  └────────────────────┘               └──────────────────────┘
```

Speech-to-text runs on **Groq's hosted infrastructure**, not in this process
— `transcription.py` is the one module that talks to Groq; everything from
`field_extraction.py` onward only ever sees a plain transcript string and has
no idea where it came from. The phone does no heavy work either way — it only
records, displays, and lets the doctor confirm/reject the extracted tests.

## Repo layout

```
backend/       FastAPI app (main.py), transcription.py (Groq speech-to-text
                adapter), matching.py (NHLS/LOINC fuzzy suggestions),
                field_extraction.py + terminology_normalize.py + clinical_terminology.py +
                datetime_normalize.py (structured pathology-request parsing),
                label_printing.py + print_records.py (barcode printing + lookup),
                doctors.py (server-side doctor accounts + sessions),
                scripts/build_terminology_index.py, tests/
frontend/      PWA — plain HTML/CSS/JS, no build step (lookup.html/lookup.js
                is the separate lab-facing barcode lookup page)
requirements.txt
```

## Prerequisites

- **Python 3.9+** (3.10 or 3.11 recommended).
- **A Groq API key** for speech-to-text — get one free at
  [console.groq.com/keys](https://console.groq.com/keys). No local FFmpeg,
  GPU, or CUDA setup is needed for transcription — that all runs on Groq's
  infrastructure now (see "Speech-to-text (Groq)" below).

## Backend — install & run

```bash
cd backend
python -m venv ../venv
source ../venv/bin/activate        # Windows: ..\venv\Scripts\activate
pip install -r ../requirements.txt

GROQ_API_KEY=<your-key> uvicorn main:app --host 0.0.0.0 --port 8000
```

No model download on startup — there's nothing to cache, since no speech
model runs in this process. Open <http://localhost:8000/> to see a
health/config readout confirming the configured Groq model
(`transcription_model`), whether `GROQ_API_KEY` is actually set
(`groq_api_key_configured`), and whether terminology matching data loaded
(`terminology_loaded`).

If you skip the terminology-matching setup below, transcription and field
extraction still work fine — `tests_required` items just come back with
`status: "unmatched"` instead of a canonical name.

`requirements.txt` covers everything except the barcode-printer client
(`niimprint`), which needs one extra command — see "Barcode label
printing" below. Skipping it is fine too: `/transcribe` and everything
else works unaffected; only "Done — Print label" needs it.

### Speech-to-text (Groq)

`backend/transcription.py` is the one module that talks to a speech-to-text
provider — `backend/main.py`'s `/transcribe` endpoint calls its
`transcribe_audio()` and gets back a plain `(raw_text, language, duration,
segments)` tuple; `field_extraction.py` and everything downstream of it only
ever sees `raw_text` as a string and has no idea it came from Groq.

| Variable            | Default                  | Notes                                                        |
|----------------------|---------------------------|---------------------------------------------------------------|
| `GROQ_API_KEY`       | (unset — **required**)   | server-side only; `/transcribe` returns a controlled error if unset |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | only set this if you want a different Groq-hosted model       |

**This was originally a locally-loaded [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
model (`large-v3`, run in-process on this server's own CPU/GPU).** It was
migrated to Groq's hosted API for one reason: `faster-whisper` pulls in
`ctranslate2`/`onnxruntime`/PyAV and a ~3GB model download, which doesn't fit
a free hosted tier's memory/CPU budget (Render Free, for instance). Moving
inference to Groq means this FastAPI process now only ever runs application
logic — no model weights, no GPU/CPU inference, no multi-gigabyte download —
while the doctor-facing behavior (one continuous dictation → one transcript →
the same six-block structured review) is unchanged.

The browser's `MediaRecorder` produces `audio/webm` (Chrome/Firefox/Android)
or `audio/mp4` (iOS Safari) with no forced codec; Groq's API accepts both
formats directly, so no audio conversion step exists in this project, before
or after this migration.

Groq's `prompt` parameter (same role as faster-whisper's old
`initial_prompt` — nudges ambiguous audio toward expected spelling without
restricting what can be transcribed) carries the same `MEDICAL_PROMPT` text
as before, now defined in `transcription.py`. Groq's community forum has
reported intermittent server errors tied specifically to that parameter —
if you see transcription failures that disappear when you remove the
`prompt` argument in `transcription.py`, that's a known upstream quirk, not
a bug in this code.

**Automated tests never contact Groq.** `backend/tests/test_transcribe_endpoint.py`
monkeypatches `transcription.transcribe_audio()` directly — no `GROQ_API_KEY`
is needed to run the test suite, and no test makes a real network call or
spends Groq free-tier quota. See "Running the tests" below.

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
- **Everything else** (patient/doctor names, patient hospital number,
  HPCSA number, ward, hospital, specimen type/site, reason for request,
  priority) is **pure raw passthrough** — terminology/fuzzy matching
  never touches identifiers or names. `patient_id` recognizes both
  "Patient ID," and the dictation proforma's "Patient hospital number,"
  phrasing — same field, same storage key, either wording works.
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

### Tubes Required

`structured.specimen_requirements` is a small piece of **derived,
additive** metadata, computed once `tests_required` is normalized
(`backend/specimen_mapping.py`, called from `field_extraction.py`'s second
pass): it groups the **confirmed** tests_required items by which
tube/specimen they need, e.g.

```
PURPLE — EDTA
  Full Blood Count
YELLOW — Serum
  C-reactive protein
  Urea and Electrolytes
  Liver Function Tests
```

This reads `tests_required` and nothing else, and writes only this one
new key — it never edits `tests_required` itself, never touches
`clinical_history`/`provisional_diagnosis`/`medication`/identifiers/dates,
and never appears in `normalized_text` (the reconstructed transcript is
completely unchanged by this feature; the PWA shows it nested inside the
confirmed-request card as its own "Tubes Required" section, separate from
"Tests required"). Only `status: "confirmed"` items are mapped — an
ambiguous or unrecognized test is excluded, same as it would be from any
other confirmed-only view.

Resolving an inline ambiguous test (e.g. clicking "TB" → "Tuberculosis" or
"Total Bilirubin" in the Tests required panel) changes `tests_required`
client-side *after* the initial `/transcribe` response, so the frontend
calls `POST /specimen-requirements` (body: `{tests_required}`) to
recompute this group from the updated list — it re-runs the same
`specimen_mapping.map_tests_to_specimens()` used server-side, so the
curated dictionary has exactly one definition, never a JS copy that could
drift out of sync. This is also why some of the resolved meanings behind
`AMBIGUOUS_ABBREVIATIONS` are themselves in the curated dictionary now
(e.g. "TB" → Total Bilirubin lands under YELLOW — Serum, "CT" → Chlamydia
Trachomatis lands under URINE CONTAINER — Sterile, "BM" → Bone Marrow gets
its own BONE MARROW — Aspirate/Biopsy Kit category) — but only where the
resolved meaning genuinely is an orderable lab test. Where it resolves to
a diagnosis or procedure instead (e.g. "TB" → Tuberculosis, "CT" →
Computed Tomography), it correctly stays "Unmapped" — which test/specimen
is right depends on which one was actually meant, and guessing would be a
patient-safety risk, not a convenience.

**Why this doesn't reuse `backend/data/nhls_tests.json`'s `specimen_type`
field**: checked directly against this feature's own worked example and
found unreliable — "Full Blood Count" incorrectly shows specimen_type
"Specimen from potentially infected site in a universal container" (a
clear PDF-extraction row-misalignment bug; FBC is a standard EDTA/
purple-top draw), and panel names like "Urea and Electrolytes" or "Liver
Function Tests" don't exist as single rows in that data at all (only
their individual components do). A wrong tube colour is a real
patient-safety issue (rejected specimen or invalid result), so
`specimen_mapping.py` uses a small **hand-curated** dictionary instead —
same trusted model as `terminology_normalize.ABBREVIATIONS` — seeded from
standard phlebotomy tube-colour convention for the tests already in that
dictionary. A confirmed test with no curated mapping yet (imaging/
procedures like ECG or MRI, skin tests like PPD, or genuinely
lab-convention-variable ones like ESR or crossmatch, which are
deliberately excluded rather than guessed) shows up in its own
"Unmapped — verify specimen requirements" group — never silently dropped,
never force-assigned a colour. It's a starting set, extensible the same
way the abbreviation dictionaries are.

### Single dictation, six-block review

An always-visible **dictation guide** (`.dictation-guide` in
`index.html`) sits above the `Record` button on the main screen — one
worked example covering every field in order ("Specimen type: blood.
Specimen site of collection: left antecubital fossa. ..."), with an
explicit note that the punctuation shown is for readability only and
is never actually spoken. It mirrors the dictation proforma's canonical
wording exactly ("Specimen site of collection,", "Date of collection,",
"Time of collection,", "Hospital or clinic name,", "Patient hospital
number,") — label patterns in `_LABEL_DEFS` were extended to match that
exact wording (optional trailing "of collection"/"or clinic name"
consumed as part of the label itself, not left to leak into the value;
separate "Date of collection,"/"Time of collection," labels — storing
into the same `date_collected`/`time_collected` keys — added alongside
the older combined "Date, time collected," phrasing) so the guide shown
to the doctor and what the parser actually accepts never drift apart.

**The doctor is never required to dictate punctuation** ("comma",
"colon") — `_is_real_label()` accepts a label match on either of two
independent signals: a comma/colon right after the label word itself
("Hospital, Ubuntu..."), **or** the label simply starting a fresh
sentence — preceded by the transcript's start or by a period left over
from the previous field ("...blood. Specimen site of collection left
antecubital fossa." — no comma anywhere near the label, but Whisper
still punctuates the pause between fields even when the doctor never
says "comma"). The second signal deliberately checks for a **period**
only, never a comma: a comma right before a candidate match is usually
the current field's own label/value separator, not a boundary between
two different fields — e.g. "Ward, Medical Ward 3B." has the word
"Ward" appearing a second time inside its own value, directly after
that comma, and must not be mistaken for a second field starting there.
"Specimen type blood", "Specimen type: blood", and "Specimen type,
blood" all now resolve to the same field.

The doctor dictates the whole request **once** — one `Record` press, one
`/transcribe` call, one Whisper pass. `extract_fields()` already parses
that single transcript into every field above by anchoring on the spoken
labels; there is no per-field or per-block recording step anywhere in the
code. What the PWA shows afterward is purely a different *rendering* of
that one result: six labeled review blocks (Specimen, Hospital, Patient,
Clinical, Tests required + Tubes required, Priority) instead of one
flowing paragraph, so the doctor can scan what was captured and fix only
what needs fixing — never repeat the whole dictation for one missing
field (`frontend/app.js`'s `renderAllBlocks()`/`BLOCK_DEFS`).

A fixed required-field list (specimen type/site, reason for request,
hospital, ward, patient hospital number, clinical history, provisional
diagnosis, a non-empty tests-required list, priority, and a confirmed —
not merely dictated — collection date/time) is checked after every
transcription and every in-block field edit/resolve
(`getIncompleteFields()`/`refreshValidation()`). While anything's
missing or still ambiguous, a "Request incomplete" banner names exactly
what, and the final "Done — Print label" button stays disabled.

**Every field in every block is directly editable in place** — never a
separate free-text re-parse step. A missing field (e.g. Hospital was
never dictated) shows as an empty input with a "Not dictated — click to
add" placeholder, right there in its block; a typo is fixed by typing
over it. Saving a field never touches any other field, and never
re-parses the system's own reconstructed display text as if it were a
fresh dictation — the exact mechanism behind every round-trip bug found
during testing (a leading colon never stripped, a reconstructed label
the parser didn't recognize, a confirmed time silently losing its AM/PM,
test names compounding into nested garbage on repeated saves). Each
field kind has its own save path, dispatching to the same deterministic
normalization function `extract_fields()` already calls internally —
just reachable per-field instead of only via a whole-transcript re-parse:

- **Plain fields** (specimen type/site, reason for request, hospital,
  ward, patient hospital number, priority, ...) — pure raw passthrough,
  no backend call at all.
- **Date/time fields** — `POST /normalize-date` / `POST /normalize-time`
  (see below). A collection time dictated without am/pm is never guessed
  (`datetime_normalize.normalize_time()` already refuses to); the field
  shows two quick-pick buttons ("10:30 AM" / "10:30 PM") alongside the
  input as a one-click shortcut, both ending at the same endpoint.
- **Clinical history / Provisional diagnosis / Relevant medication** —
  `POST /resolve-clinical-field`, re-running only that field's
  abbreviation expansion and ambiguous-term flagging from its own raw
  text. An unresolved ambiguous term shows as a small chip —
  `"TB" — choose what you meant` — opening the same candidate popup
  tests_required's own ambiguous items use.
- **Tests required** — an unrecognized/mistyped item gets an inline
  text fix; a "+ Add test" input covers a test that was never dictated
  at all. Both resolve via `POST /normalize-test-item`, re-matching only
  that one test name against NHLS/LOINC without touching any other
  already-confirmed item in the list.

Block 5 (Tests required / Tubes required) also carries a required
confirmation checkbox — *"I confirm that I have checked the
specimen/container requirements and used the correct collection
tubes/containers"* — that's part of the same Done gate, and is
automatically re-unchecked whenever the tube grouping changes underneath
it (e.g. resolving an ambiguous test or fixing a test name), so a stale
confirmation can never silently survive a change to what it was
confirming.

None of this touches the existing printing/queue architecture: "Done"
still calls the same `POST /print-label`, the same print-job queue, the
same local print agent and NIIMBOT label, and the same Request-ID/QR/
lookup/SMS behaviour on the receiving end — this is entirely a review/
validation layer in front of that unchanged submission step.

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
with `transcription.transcribe_audio()` (the Groq adapter) monkeypatched to
a canned transcript — so it's fast/deterministic, needs no `GROQ_API_KEY`,
and makes no network call or Groq API usage — but every line of the
extraction/normalization/matching code still runs for real
— including a regression test confirming a decoy phrase embedded in prose
(e.g. "urea analysis urine microscopy") never surfaces as a suggested
test, and one confirming an ambiguous abbreviation reaches the frontend
with its full candidate list through the actual endpoint.

Also covers specimen/tube mapping (`test_specimen_mapping.py` — matches
this feature's own worked example, confirms only `status: "confirmed"`
tests are mapped, confirms an unmapped test is shown in its own group
rather than dropped or guessed at; `test_field_extraction.py` additionally
confirms `specimen_requirements` never leaks into `tests_required`, any
other field, or the reconstructed `normalized_text`), barcode label
generation and printing (`test_label_printing.py`
— request ID format/uniqueness, label image sizing, shrink-to-fit for long
doctor names, and `niimprint` calls mocked so no physical printer is
needed), the shared request store including its column backfills
(`test_print_records.py` — save/get roundtrip, the
print-failure-still-saves-a-record case, a record saved with no phone
number, and simulating a pre-migration database file to confirm
`init_db()` backfills missing columns — including `created_at` for rows
that predate it — without touching existing rows; all against a disposable
temp SQLite engine so the real `print_records.db` is never touched), the
SQLite → PostgreSQL migration script (`test_migration_script.py` — row
copying, idempotent re-runs, a historical record missing an optional
field), and the real `/print-label` + `/print-lookup` endpoints — phone
number included, plus malformed-ID (`400`) and database-unreachable
(`503`) handling (`test_print_endpoints.py`).

The print-job queue has its own two files: `test_print_jobs.py` (unit
tests against `print_jobs.py` directly — atomic claim, a second claim on
an already-claimed job getting nothing back, a stale "printing" job
becoming claimable again, auto-fail past `MAX_PRINT_ATTEMPTS`,
retry-reuses-the-same-row vs. reprint-creates-a-new-one) and
`test_print_job_endpoints.py` (through the real HTTP routes — agent-token
rejection including the fail-closed-when-unconfigured case, the claimed
job's payload containing no clinical fields at all, mismatched-station
`409`s, and the doctor-facing status/retry/reprint endpoints). Neither
requires real NIIMBOT hardware — `print_agent.py` is a separate,
standalone script the hosted backend never imports.

The Whisper-dependent `test_transcribe_endpoint.py`, `test_print_endpoints.py`,
and `test_print_job_endpoints.py` (each imports `main`, which loads
Whisper) need enough free RAM to load `large-v3` fresh (confirmed on this
machine: fails with `mkl_malloc: failed to allocate memory` under ~2GB
free) — the other test files have no such dependency and run in a few
seconds.

## Frontend — serve it

The frontend is static files; serve the `frontend/` folder with any static
server:

```bash
cd frontend
python -m http.server 5173
```

Then open <http://localhost:5173/>. Set the backend address in
`frontend/config.js` — the ONE place it's configured; `app.js` and
`lookup.js` both read this same global, so you never need to edit more
than one file to point the whole frontend at a different backend:

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

## Barcode label printing

Once a doctor presses **"Done — Print label"**, the backend renders ONE
label image and produces **two identical copies of it**: one sent
straight to a physical **NIIMBOT B21** thermal printer, the other returned
to the PWA and saved onto the History entry (`backend/label_printing.py`'s
`image_to_data_url` — the exact same PNG bytes both times, never
re-rendered separately, so the two can't drift apart) — no manual step in
the NIIMBOT app, ever. The label has the doctor's name as plain text, a QR
code below it, and the request ID printed as plain text under the QR code
too (readable/typeable by hand if no scanner is available). Lab staff scan
(or type the ID) on a separate page (`frontend/lookup.html`, which also
displays the same label image) to see the doctor's confirmed transcript.

**Why a QR code, not a classic 1D barcode**: an earlier iteration of this
feature encoded a JSON payload (`{id, name, phone, email}`) in it, which a
1D barcode (Code128) measured **~330mm wide** to hold at a reliably
scannable density — the B21 physically maxes out at 53mm, so that
genuinely didn't fit. That contact-info-in-the-barcode design was then
**deliberately reverted for privacy** (see "Contacting the requesting
doctor" below). The QR code now encodes a **lookup URL** —
`{FRONTEND_URL}/lookup.html?id={request_id}` — rather than the bare ID:
scanning it takes a pathologist on any device straight to that request
(`frontend/lookup.js` reads the `?id=` query param on load and looks it
up automatically — no separate "paste the ID in" step needed), which is
what makes retrieval work from a different device with no shared
network. It's still only ever a **reference** to the server-side record,
never the request/transcript itself (that's still too much data at a
scannable size, and would defeat the privacy point of looking it up
server-side) — and the request ID is also printed as plain text under
the QR so it's still typeable by hand if scanning fails or the QR points
at a stale `FRONTEND_URL`. The QR code format itself (over a 1D barcode)
was kept from that earlier iteration regardless of payload shape, per an
explicit decision to keep using it going forward. The tradeoff versus a
classic 1D barcode: lab staff need a 2D/camera-capable scanner — most
modern handheld scanners and any smartphone camera can read QR codes, but
older 1D-only laser scanners can't.

**Why physical printing is NOT done by the hosted backend**: iOS Safari
supports neither the Web Bluetooth nor the WebUSB APIs (so the *browser*
was always a dead end on iPhone/iPad), and once the backend itself moved
to a hosted server (Render), it stopped being on the same machine as the
printer too — a hosted FastAPI process simply has no USB/Bluetooth path
to a NIIMBOT sitting at the hospital. So `/print-label` doesn't print at
all: it saves the record, queues a **print job**, and returns
immediately. A separate, small, standalone process — `print_agent.py`,
running on whichever machine the B21 is actually plugged into — polls
that queue over plain HTTPS and does the real printing. See "Local print
agent" below for the full picture; this is what makes one click from
Windows, macOS, Android, *or* iOS all work identically, without the
doctor's own device needing to touch the printer, Bluetooth, USB, or
even be on the same network as it.

**This is also why the project is no longer fully stateless**: scanning a
barcode has to look something up against, and that can't live only in the
doctor's own browser the way everything else here does. The confirmed
transcript (which may include patient details the doctor dictated) is
stored centrally — hosted **PostgreSQL** in a real deployment, or a local
SQLite file if `DATABASE_URL` is unset (`backend/print_records.py`, see
"Hosted deployment" below) — and kept indefinitely. This was a
deliberate, discussed tradeoff, not an oversight — see
`print_records.py`'s docstring.

### Local print agent

`backend/print_agent.py` is a **deliberately small, separate** program —
it is not "the backend running locally," it's a different, much smaller
process. It contains no Whisper, no transcription, no terminology
normalization, no specimen mapping, no pathology-request database, no
pathologist lookup UI, no doctor authentication — it imports exactly one
module from this project, `label_printing.py` (pure label rendering plus
the niimprint hardware adapter), and nothing else of the app. It knows
three things about a job: a `print_job_id`, the `request_id` and
`doctor_name` to put on the label, and the `qr_payload` URL to encode —
never clinical content.

It polls, never gets polled (no inbound connections, nothing public-
facing on the printing machine):

```
loop:
    POST /print-jobs/claim {station_id}   (Authorization: Bearer <AGENT_TOKEN>)
    nothing pending (204) -> sleep POLL_INTERVAL_SECONDS, loop again
    a job (200)           -> render the label, print it via niimprint,
                              report success/failure, loop again
```

**Run it** on the Windows PC physically connected to the B21:

```bash
pip install -r print_agent_requirements.txt
pip install --ignore-requires-python "niimprint @ git+https://github.com/AndBondStyle/niimprint.git"
python print_agent.py
```

(`niimprint`'s own package metadata declares Python `<3.12`, which is
overly conservative — nothing in its actual code is 3.12-incompatible,
confirmed by installing and using it under 3.12 here; kept out of
`print_agent_requirements.txt` for the same reason it's kept out of the
main `requirements.txt` — it would make a plain `pip install -r
...txt` fail outright on 3.12.) Left running in a terminal is enough for
this prototype; if you want it to survive reboots, point Windows Task
Scheduler at it — no service installer is built here.

Configuration — all environment variables, same pattern as everything
else in this project:

| Variable                  | Default            | Notes                                      |
|---------------------------|--------------------|---------------------------------------------|
| `BACKEND_URL`              | `http://localhost:8000` | the hosted (or local) backend to poll |
| `AGENT_TOKEN`              | (unset — **required**)  | must match the backend's `PRINT_AGENT_TOKEN` |
| `STATION_ID`               | `POC_PRINTER_01`   | this station's identity (see "Printer/station identity" below) |
| `POLL_INTERVAL_SECONDS`    | `5`                | how often to check for a new job when idle |
| `PRINTER_CONNECTION`       | `usb`              | `usb` or `bluetooth`                       |
| `PRINTER_USB_PORT`         | `auto`             | or an explicit COM port                    |
| `PRINTER_BT_ADDRESS`       | (unset)            | required if `PRINTER_CONNECTION=bluetooth` |
| `PRINTER_DENSITY`          | `3`                | 1–5, per niimprint                         |
| `PRINTER_LABEL_WIDTH_MM`   | `50`               | B21 supports 20–53mm                       |
| `PRINTER_LABEL_HEIGHT_MM`  | `30`               |                                             |

If the printer isn't connected (or the agent itself isn't running at
all), nothing is lost: the request is already saved, and the print job
just sits as `"pending"` in the queue until an agent is available to
claim it — restart the agent (or reconnect the printer and let the next
poll pick the job back up) and it prints with no doctor action needed.
A genuine print failure (reported via `POST /print-jobs/{id}/fail`)
shows the doctor the specific error and a **Retry Print** button; retry
reuses the exact same job/request — never a new Request ID.

**Printer/station identity.** `STATION_ID` exists even though this
prototype only has one printer, so a future multi-station rollout (a
busier lab, more than one printing location) is a schema/config
addition, not a redesign: `print_jobs.station_id` already records which
station handled each job, and `PRINT_AGENT_TOKEN` would become one row
per station in a small `print_stations` table instead of a single shared
secret. Not built now — flagged so the one-station version doesn't
quietly make a multi-station future harder.

#### Manual hardware test plan

The automated suite covers the queue's logic without real hardware; these
confirm the actual end-to-end physical behavior once a B21 and an agent
are available:

1. **Doctor on Windows (Chrome/Edge)** → Done/Print → NIIMBOT prints.
2. **Doctor on Android (Chrome)** → same.
3. **Doctor on iPhone (Safari)** → same.
4. **Doctor on Mac (Safari/Chrome)** → same.
5. **Agent offline**: stop `print_agent.py`, submit a request (confirm
   it's saved, `print_status` stays `"pending"`), restart the agent —
   the label prints with no further doctor action.
6. **Printer disconnected**: unplug the B21, submit (confirm `"failed"`
   with a clear error and a **Retry Print** button), reconnect, press
   **Retry Print** — the same Request ID prints.
7. **Pathologist on a different device** scans the printed QR — the
   correct centrally-stored request opens.
8. **Pathologist manually types** the printed Request ID on a different
   device — same request opens.

Every one of these requires exactly **one** doctor action (press
"Done" once) — printing itself is never gated on a second confirmation
at the print station.

### Lab lookup page

`frontend/lookup.html` is a **separate, unauthenticated** static page,
reachable three ways:

- **Scan the QR with the device's own camera app** — it encodes
  `?id={request_id}` (see above), so a phone's native camera recognizes
  it and offers to open the link; the page reads that param on load and
  looks the request up automatically. No typing, no "Find Request"
  click needed.
- **Scan the QR in-page** — a **Scan QR** button opens the camera right
  on this page and decodes the code in-browser via
  [jsQR](https://github.com/cozmo/jsQR) (MIT, loaded from a pinned CDN
  `<script>` — the frontend's first external script dependency; nothing
  is sent anywhere until a code is actually found). Useful on a
  desktop/laptop with a webcam, or for anyone who'd rather not leave the
  page for their phone's own camera app. Same secure-context requirement
  as the microphone (`app.js`'s `startRecording()`): camera access
  REJECTS on plain `http://` that isn't `localhost`. The camera stream
  is stopped immediately on a successful decode or on **Cancel** — it's
  never left running in the background.
- **Type/paste it manually** — a request-ID input (auto-focused, so a
  2D-capable handheld scanner acting as a keyboard-wedge device can scan
  directly into it and auto-submit on Enter too). `extractRequestId()`
  accepts a bare ID, a pasted full lookup URL, or — for backward
  compatibility with any label printed by an earlier version of this
  feature — a legacy `{id, ...}` JSON payload. All three paths funnel
  into the same `runLookup()` function, so there's one lookup code path,
  not three that could drift apart.

Every path calls the same `GET /print-lookup/{request_id}` and displays
the doctor's name, HPCSA number, contact actions (see below), and the
confirmed transcript. No login is required — the request ID itself is the
access key. That's an accepted tradeoff for this POC stage, **and a
materially bigger one once this is hosted**: reachable only on a trusted
LAN before, reachable from anywhere on the internet after — see "Hosted
deployment" below for what that actually changes and what it doesn't fix.

### Contacting the requesting doctor

The lab lookup page shows the requesting doctor's name, HPCSA number, and
phone number, with three actions next to them: **Call**, **Message**, and
**Copy number**. This is deliberately three thin OS hand-offs, not a
custom calling/messaging system:

- **Call** is a plain `<a href="tel:+27821234567">` link — the browser/OS
  decides what happens (opens the phone app on Android/iOS; on Windows/
  macOS, whatever's registered to handle `tel:`, or nothing if nothing is).
- **Message** is the same idea with `sms:`, pre-filled with a body —
  `Regarding pathology request PR261002-A7KM:` — that always names
  whichever request is **currently displayed** (`renderDoctorContact()`
  runs fresh on every lookup, so it can't carry over a previous lookup's
  ID). No patient name, identifiers, history, diagnosis, or results are
  ever included — just the reference line. The OS's own SMS composer
  opens with this pre-filled; nothing is ever sent automatically, the
  pathologist still reviews and sends it themselves. No WhatsApp or other
  third-party scheme — just the standard one, per the request.
- **Copy number** uses the Clipboard API (`navigator.clipboard.writeText`)
  with a `document.execCommand("copy")` fallback for browsers/contexts
  where that API is unavailable, and a clear status message either way
  (`frontend/lookup.js`'s `copyNumberBtn` handler) — this is the one
  guaranteed-to-work option on a Windows/macOS machine with no phone/SMS
  app registered at all.

`frontend/lookup.js`'s `toTelUri()` is the one helper responsible for
turning however the doctor's number is stored/displayed (spaces, hyphens,
whatever they typed at registration) into the digits-and-leading-`+`-only
form `tel:`/`sms:` expect — it never invents or guesses a number. If a
request has no phone number on file, the Call/Message/Copy actions don't
render at all and the page shows "Contact number unavailable." instead of
a broken link (`renderDoctorContact()` in the same file).

**Never in the QR/barcode itself.** The doctor's phone number is stored
server-side (`print_records.py`'s `doctor_phone` column — added via a
safe migration, see "Database schema" in the API section below) and only
ever reaches the lookup page via `GET /print-lookup/{request_id}`,
specifically so a physical label someone finds or
photographs doesn't expose a doctor's personal number — the request ID
is still the only thing that's scannable, and it's also the only access
control this lookup has, same as everything else on this page.

CORS defaults to wide-open (`*`) for local/POC convenience; set
`ALLOWED_ORIGINS` (comma-separated) to lock it down to your real frontend
origin(s) before using this anywhere real — see "Hosted deployment" below.

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

### Doctor accounts

Doctor accounts and History live **server-side** (`backend/doctors.py`),
not in the browser — this is what makes "log in with the same HPCSA
number and password on a different device" work at all; it used to be
pure `localStorage`, a blank slate on every new device/browser. See
`doctors.py`'s own docstring for the full design reasoning (PBKDF2-
HMAC-SHA256 password hashing, a unique salt per password, and —
importantly — a doctor's session tokens live in their own table, one row
per login, so logging in on a phone never invalidates a PC's
already-stored token).

`POST /register` — `{"name": "Dr. ...", "hpcsa_number": "...", "cell":
"...", "email": "...", "password": "..."}` → `201 {"token": "...",
"doctor": {"hpcsa_number", "name", "cell", "email"}}` (never includes
the password). `409` if that HPCSA number is already registered.

`POST /login` — `{"hpcsa_number": "...", "password": "..."}` → `200
{"token": "...", "doctor": {...}}`. `401` with the generic "Incorrect
HPCSA number or password." either way — never reveals which field was
wrong.

`GET /me` (`Authorization: Bearer <token>`) — resolves a stored session
token back to a doctor profile. Called on page load to resume a session,
now that there's nothing left to resume from locally except the token
itself.

`PUT /profile` / `PUT /password` (token required) — update name/cell/
email, or change password (`{"current_password", "new_password"}`,
`401` if the current password is wrong).

`GET /requests` (token required) — this doctor's own History:
`{"requests": [...]}`, each entry the same shape `GET
/print-lookup/{request_id}` already returns, newest first. Deliberately
**finalized/printed requests only** — there's no separate server-side
store of in-progress or abandoned dictations; a request only exists here
once "Done — Print label" has actually been pressed (see
`print_records.get_records_for_doctor()`'s docstring). The History
screen's search box filters this list client-side; nothing server-side
needs its own search query for a per-doctor list of this size.

### Clinician disambiguation UI

Every ambiguous abbreviation — in `tests_required`, `clinical_history`,
`provisional_diagnosis`, or `medication` — surfaces as a small clickable
chip, `"TB" — choose what you meant`, next to its field (either inside
the field's block, via `frontend/app.js`'s `renderAmbiguousChip()`, or
inline in the Tests required panel's own list for a `tests_required`
item) rather than highlighted inline in running text — a chip works
regardless of whether that field is a static display or a live editable
textarea. Tapping it (`openAmbiguousPopup()`) pops up its candidate
list — canonical name, domain hint, and the ranking `reason` — plus a
**"None of these / keep original"** option, none pre-selected. Picking a
candidate mutates that exact item's resolution (`resolveAmbiguousItem()`,
field + raw + the underlying item reference — never ambiguous markers
re-matched against transcript text) and immediately regenerates the flat
transcript (`POST /rebuild-transcript`) and saves it to the History
entry. If the same abbreviation is ambiguous in two different fields
(e.g. "TB" in both Provisional diagnosis and Tests required), each is
its own chip, tracked and resolved independently. `tests_required`
additionally keeps its checkbox-based "Tests required" panel for
including/excluding confirmed (non-ambiguous) items before saving — an
item resolved via its chip becomes checkable there too, exactly like any
other confirmed test.

### Finalizing a request: printing + lookup

`POST /print-label` — JSON body:

```json
{
  "doctor_name": "Dr. Thato Manapi",
  "doctor_phone": "+27821234567",
  "hpcsa_number": "808080",
  "raw_text": "...",
  "normalized_text": "...",
  "structured": { "...": "..." }
}
```

`doctor_phone` is stored (see `print_records.py`'s `doctor_phone` column)
and returned by `/print-lookup` below — never encoded into the QR/barcode
itself, see "Contacting the requesting doctor" above.

Generates a request ID, renders the label image once, **saves the digital
record (including that image) first** (so it's never lost to a printer
failure), then queues a print job referencing the same request ID (see
`backend/print_jobs.py` / "Local print agent" above) and returns
**immediately** — it never waits on, or even attempts, physical printing
itself:

```json
{
  "request_id": "PR260929-7K4M",
  "print_status": "pending",
  "print_job_id": "a1b2c3d4e5f6...",
  "print_error": null,
  "label_image": "data:image/png;base64,..."
}
```

If the database itself can't be reached, this returns `503` instead —
the request is explicitly **not** reported as saved, never a misleading
success.

`GET /print-status/{request_id}` — polled by the doctor's PWA after
`/print-label` while a local print agent claims and processes the job.
Returns `{"print_status": "pending"|"printing"|"printed"|"failed",
"print_error": ..., "attempt_count": ...}` — the live state of the
request's most recent print job. `400`/`404` same as `/print-lookup`
below.

`POST /print-jobs/{request_id}/retry` — only valid when the latest job is
`"failed"` (`409` otherwise); resets that **same** job back to pending —
nothing was physically printed yet, so nothing to duplicate. Same
Request ID, same job, continued.

`POST /print-jobs/{request_id}/reprint` — an intentional "print another
physical copy," valid any time the request exists; creates a **new**
print-job row (same `request_id`, never a new Request ID) so the
original job's history (was it ever actually printed, and when) stays
intact rather than being overwritten.

**Agent-only** (require `Authorization: Bearer <PRINT_AGENT_TOKEN>`;
`401` without it — see "Local print agent" above):
- `POST /print-jobs/claim` — body `{"station_id": "..."}`; `204` if
  nothing's pending, else the minimal job payload (`print_job_id`,
  `request_id`, `qr_payload`, `doctor_name`, `printer_id` — never
  clinical content). Atomically claims the job (safe even if two agents
  poll at once — see `print_jobs.claim_next_job`'s docstring), so no two
  agents can ever print the same job.
- `POST /print-jobs/{print_job_id}/complete` / `.../fail` — body
  `{"station_id": "...", "error": "..."}` (fail only); `409` if
  `station_id` doesn't match whoever actually claimed it.

`GET /print-lookup/{request_id}` — returns the full saved record
(`doctor_name`, `hpcsa_number`, `doctor_phone`, `raw_text`,
`normalized_text`, `structured`, `created_at`, `printed_at`,
`print_status`, `print_error`, `label_image` — the identical image, not
re-rendered). `400` if `request_id` doesn't match the expected format
(validated before the database is even queried), `404` if it's
well-formed but no such request exists, `503` if the database itself is
unreachable. `doctor_phone` is `null` for a record saved before this
column existed, or an empty string if a request was genuinely saved
without one — `frontend/lookup.js` treats both the same way (shows
"Contact number unavailable.").

`POST /specimen-requirements` — JSON body `{"tests_required": [...]}`,
returns `{"specimen_requirements": [...]}` in the same shape
`field_extraction.py` already computes. Exists because resolving an
inline ambiguous test (e.g. "TB" → Tuberculosis) changes `tests_required`
client-side, after the initial `/transcribe` response already computed
`specimen_requirements` once — `app.js`'s `resolveAmbiguousItem()` calls
this to recompute it from the now-current `tests_required`, reusing
`specimen_mapping.py`'s one curated dictionary rather than duplicating it
in JavaScript.

`POST /normalize-time` — JSON body `{"raw": "10:30 AM"}`, returns
`{"value": "10:30", "status": "confirmed"}` (or `{"value": null,
"status": "ambiguous"}` if `raw` still has no explicit am/pm). A thin
wrapper around `datetime_normalize.normalize_time()` — zero new
normalization logic. Backs every `time_*` field's inline editor
(including the AM/PM quick-pick shortcut), so "what counts as a valid
time" has exactly one definition, never duplicated in JavaScript.

`POST /normalize-date` — the date counterpart to the above, JSON body
`{"raw": "5 October 2026"}`, returns `{"value": "2026-10-05", "status":
"confirmed"}`. A thin wrapper around `datetime_normalize.normalize_date()`.
Backs every `date_*` field's inline editor.

`POST /resolve-clinical-field` — JSON body `{"field_key":
"clinical_history", "raw_text": "...", "structured": {...}}`, returns
`{"field": {...}}` in the same shape `extract_fields()` already produces
for that field (`raw`/`value`/`status`/`resolved_terms`). A thin wrapper
around `clinical_terminology.resolve_field_text()`, dispatching the same
`CLINICAL_ABBREVIATIONS`/`MEDICATION_ABBREVIATIONS` dictionary per
`field_key` that `extract_fields()`'s own second pass already uses.
Backs clinical_history/provisional_diagnosis/medication's inline
textarea — re-resolving only the one edited field from its own raw text,
never the whole transcript.

`POST /normalize-test-item` — JSON body `{"raw": "FBC", "structured":
{...}}`, returns `{"item": {...}}` — one `tests_required` list entry, in
the same shape `/transcribe` already produces. A thin wrapper around
`terminology_normalize.normalize_tests_required()` for exactly one test
name (`split_test_items()` on a single name returns a one-item list, so
the first result is the resolved item). Backs fixing an
unrecognized/mistyped test inline and "+ Add test", both replacing or
appending exactly one entry in `tests_required` without touching any
other already-confirmed item. `400` if `raw` is empty or doesn't contain
a recognizable test name.

`POST /rebuild-transcript` — JSON body `{"structured": {...}}`, returns
`{"normalized_text": "..."}` — a thin wrapper around
`field_extraction.build_normalized_text()`. Called after any in-block
field save (every one of the endpoints above mutates one field of
`structured` directly), so the flat `normalized_text` string — what's
actually saved to history and printed on the label — is regenerated from
the same reconstruction logic every other path already uses, instead of
being hand-patched in JavaScript.

#### Database schema

`backend/print_records.py` uses **SQLAlchemy Core** (not the ORM — this
project's style is hand-rolled, explicit SQL, and Core preserves that
while adding connection pooling and one code path that works against
both PostgreSQL and SQLite without dialect branching). `init_db()` —
called every time the backend starts — creates the table if it's
missing, then backfills any columns an already-existing database doesn't
have yet (`_add_missing_columns()`). It never drops or rewrites existing
rows, so a database from before a given column existed keeps working:
old rows just come back with that column `null` (or, for `created_at`,
backfilled from `printed_at` — the closest real timestamp old rows have).

```
print_records
├── request_id          TEXT PRIMARY KEY
├── doctor_name         TEXT NOT NULL
├── hpcsa_number        TEXT NOT NULL
├── doctor_phone        TEXT              -- nullable
├── raw_text            TEXT NOT NULL
├── normalized_text     TEXT NOT NULL
├── structured          JSON NOT NULL     -- JSONB on PostgreSQL; includes
│                                            specimen_requirements as a
│                                            point-in-time snapshot (see
│                                            "Specimens" endpoint above —
│                                            never re-mapped on lookup)
├── print_status        TEXT NOT NULL
├── print_error         TEXT
├── label_image_base64  TEXT NOT NULL
├── created_at           TIMESTAMPTZ NOT NULL
└── printed_at          TEXT NOT NULL
```

`backend/print_jobs.py` adds a second table in the same database (reuses
`print_records`'s engine/metadata rather than a second connection pool),
tracking physical print *attempts* separately from the pathology request
itself — never duplicating the request, only referencing it:

```
print_jobs
├── print_job_id        TEXT PRIMARY KEY   -- uuid4 hex, internal only
├── request_id          TEXT NOT NULL REFERENCES print_records(request_id)
├── status              TEXT NOT NULL      -- pending | printing | printed | failed
├── station_id          TEXT               -- which agent claimed it
├── created_at          TIMESTAMPTZ NOT NULL
├── claimed_at          TIMESTAMPTZ
├── printed_at          TIMESTAMPTZ
├── attempt_count       INTEGER NOT NULL DEFAULT 0
└── last_error          TEXT
```
A request can have more than one `print_jobs` row over its lifetime (a
retry reuses the same row; an intentional reprint adds a new one — see
"Finalizing a request" above) — `print_records.print_status`/
`print_error` are kept in sync with the *latest* job as a convenience so
`GET /print-lookup/{id}` (the pathologist side) never needs to know
`print_jobs` exists at all.

See "Barcode label printing" and "Local print agent" above, and "Hosted
deployment" below, for the full picture (why this exists, how printing
actually happens, and where it actually runs).

## Hosted deployment

The doctor and the pathologist don't necessarily share a device, a
network, or a country — so the shared request store (above) and the
frontend/backend address need to work over the public internet, not just
`localhost`/a LAN. What changes for a hosted deployment, and what
deliberately doesn't:

**Backend** — any host that runs a long-lived Python process works (this
was built against [Render](https://render.com) as the reference target,
no Render-specific code anywhere): build `pip install -r
requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`.
Configure via environment variables (see `backend/.env.example` for the
full list with examples):

| Variable            | Purpose                                                              |
|----------------------|-----------------------------------------------------------------------|
| `GROQ_API_KEY`       | **Required.** Server-side only — see "Speech-to-text (Groq)" above. Never set this in any frontend file. |
| `GROQ_WHISPER_MODEL` | Default `whisper-large-v3-turbo`. Only set if you want a different Groq-hosted model. |
| `DATABASE_URL`       | PostgreSQL connection string. Unset → falls back to the local SQLite file, so local dev/tests need no Postgres at all. Independent of where the backend itself is hosted — e.g. a free [Neon](https://neon.tech) Postgres instance works here regardless of whether the backend runs on Render or anywhere else. |
| `ALLOWED_ORIGINS`    | Comma-separated frontend origin(s) for CORS. Unset → `*` (development-only — see `main.py`'s comment). |
| `FRONTEND_URL`       | Where `frontend/lookup.html` is actually served — used to build the QR's lookup URL. |
| `PRINT_AGENT_TOKEN`  | Shared secret the local print agent authenticates with. Unset → every agent-facing endpoint rejects everything (fails closed, never silently open). |
| `STALE_JOB_TIMEOUT_SECONDS` | Default `120` — how long a job can sit "printing" before it's considered an abandoned/crashed claim and re-offered. |
| `MAX_PRINT_ATTEMPTS` | Default `5` — a job reclaimed this many times without succeeding is auto-marked `"failed"` instead of retried forever. |

Because speech-to-text is now a hosted API call rather than an in-process
model, the backend itself needs comparatively little memory/CPU — this is
what makes a free hosted tier (e.g. Render Free) realistic for this
prototype, with Groq handling the one actually heavy part (speech
inference) and Neon (or any standard PostgreSQL host) handling storage.

**Frontend** — still just static files, no build step; any static host
works (Render Static Site, GitHub Pages, Netlify, ...). Point it at the
hosted backend by editing the one line in `frontend/config.js` (see
"Frontend — serve it" above).

**Database migration** — `backend/data/print_records.db` (the project's
original local SQLite store) is never deleted or overwritten by any of
this. To move its rows into a new PostgreSQL instance:

```bash
DATABASE_URL="postgresql+psycopg://user:pass@host/db" python backend/migrate_sqlite_to_postgres.py
```

Safe to re-run (existing `request_id`s are skipped, not duplicated), and
the original file is left on disk afterward as a backup.

**NIIMBOT printing stays local, by design — and still works when the
backend is hosted.** A hosted backend has no USB/Bluetooth path to a
printer sitting on a doctor's desk, so it doesn't try: `/print-label`
only ever queues a print job (`backend/print_jobs.py`); a separate
standalone `print_agent.py` (see "Local print agent" above), running on
whichever machine the B21 is actually plugged into, polls that queue over
plain HTTPS and does the real printing. Central request storage and local
physical printing are two different concerns talking to the same hosted
backend, not one process trying to do both — which is exactly what makes
a doctor on Windows, macOS, Android, *or* iOS able to trigger printing
with one click regardless of where any of this is hosted.

**Security — honestly, not just checked off.** This is a synthetic-data
university prototype; none of the below makes it suitable for real
patient data on its own:

- `GET /print-lookup/{request_id}` is still **unauthenticated** — same as
  before, but now reachable from anywhere on the internet instead of just
  a trusted LAN. Anyone who has or guesses a request ID can read that
  confirmed request from any device. This is the single biggest thing
  worth hardening before any real deployment; not addressed here since it
  needs its own discussion (not a drop-in addition).
- PostgreSQL credentials live only in the backend's environment
  (`DATABASE_URL`) — never in any frontend file. The frontend has no
  database code at all; it only ever talks to FastAPI.
- No secrets are committed to git — `backend/.env.example` documents the
  shape with no real values, and `.gitignore` excludes a real `.env`.
- Every query goes through SQLAlchemy Core's parameterized statements —
  no raw string interpolation, on either backend.
- `GET /print-lookup/{request_id}` validates the ID's format (`400` if
  malformed) before touching the database, and a database connection
  failure returns a generic `503` — never a raw stack trace or database
  error to the client.
- The print-job queue's agent-facing endpoints (`/print-jobs/claim`,
  `.../complete`, `.../fail`) require `Authorization: Bearer
  <PRINT_AGENT_TOKEN>`, checked with a constant-time comparison, and
  **fail closed** — an unconfigured token rejects every agent request
  rather than silently allowing them. One shared token for one station
  is a prototype-appropriate simplification, not a final-state auth
  model — see "Local print agent" above for the natural per-station
  upgrade path.
- **Speech-to-text now leaves this server entirely.** Dictated audio is
  sent to Groq's hosted API for transcription — a materially different
  data flow from a locally-loaded model, where audio never left the
  server's own process. This prototype only ever uses synthetic
  dictation, so that's acceptable here; it is **not** acceptable for real
  patient audio without first reviewing Groq's own data handling/retention
  terms, a data-processing agreement, POPIA (or the applicable local
  privacy law) compliance, institutional/security review, audit
  requirements, and clinical governance sign-off. None of that review is
  done or assumed by this code — it's a separate undertaking, not
  addressed here.

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
- **In-progress/abandoned dictations are never stored server-side** —
  only a finalized (printed) request is (see "Doctor accounts" and
  "Barcode label printing" above). A dictation the doctor never finishes
  reviewing leaves no trace once the tab closes.
- Authentication on the lab lookup page — the request ID is the only
  access control for now (see "Hosted deployment" above for why this
  matters more once the backend is reachable over the internet).
