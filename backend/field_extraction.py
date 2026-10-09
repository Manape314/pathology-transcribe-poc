"""
Parses a dictated pathology-request transcript ("Patient name, X. Patient
ID, Y. ...") into named fields, by anchoring on known label phrases.

`tests_required` goes through terminology normalization against NHLS/LOINC
(terminology_normalize.py); `clinical_history`, `provisional_diagnosis`,
and `medication` go through curated clinical/medication abbreviation
scanning (clinical_terminology.py); date/time fields go through
datetime_normalize.py. Every other field (patient name, doctor name, IDs,
ward, hospital, specimen type/site, reason for request, priority) is pure
raw passthrough.
Fuzzy/terminology matching must never touch identifiers or names (see
terminology_normalize.py's and clinical_terminology.py's docstrings, and
point 16 of the originating request).

`specimen_requirements` is DERIVED, additive metadata computed once
`tests_required` is normalized (specimen_mapping.py) — it groups the
CONFIRMED tests by tube/specimen type for display as its own "Specimens /
Tubes Required" section. It reads only `tests_required` and writes only
this one new key; it's not a transcript-normalization stage, and it never
feeds back into `tests_required` or any other field.

Anything not claimed by a recognized field (a run of text with no label
before it, or trailing text after the last field's sentence) is collected
into `unparsed_text` rather than being silently dropped or attached to the
wrong field — this is deliberate: garbled speech-recognition output (e.g. a
corrupted trailing sentence) must stay visible for clinician review, not
disappear into an unrelated field.
"""

import re

import clinical_terminology
import specimen_mapping
from datetime_normalize import normalize_date, normalize_time
from terminology_normalize import normalize_tests_required

# (field_key, pattern matching the label phrase itself — a following
# comma/colon, within a short lookahead, is what actually confirms a match
# is a real label instance rather than incidental text; see _is_real_label).
_LABEL_DEFS = [
    ("patient_name", r"patient\s+name"),
    # Accepts both phrasings — "Patient ID" (original) and "Patient
    # hospital number" (the dictation proforma's canonical wording) —
    # under the SAME storage key, so nothing downstream needs to know
    # which one was actually spoken.
    ("patient_id", r"patient\s+id|patient\s+hospital\s+number"),
    ("date_of_birth", r"date\s+of\s+birth"),
    ("ward", r"medical\s+ward|\bward\b"),
    # "of clinic name" is an optional trailing phrase on the label itself —
    # the dictation proforma says "Hospital or clinic name,"; without
    # consuming it here, it would leak into the VALUE (e.g. "or clinic
    # name, Greenside Clinic" instead of "Greenside Clinic"). Negative
    # lookbehind still excludes the word "hospital" when it's part of
    # "patient hospital number" (added above as a patient_id alias).
    ("hospital", r"(?<!patient\s)hospital(?:\s+or\s+clinic\s+name)?\b"),
    ("date_time_collected", r"date,?\s+time\s+collected|date\s+and\s+time\s+collected"),
    # Separate "Date of collection,"/"Time of collection," labels — the
    # dictation proforma's canonical wording, dictated as two distinct
    # fields rather than the combined "Date, time collected," phrasing
    # above. Both produce the SAME date_collected/time_collected storage
    # keys (see _DATE_FIELDS/_TIME_FIELDS below) — either phrasing works.
    ("date_collected", r"date\s+of\s+collection"),
    ("time_collected", r"time\s+of\s+collection"),
    ("date_requested", r"date\s+requested"),
    ("time_requested", r"time\s+requested"),
    ("priority", r"\bpriority\b"),
    ("specimen_type", r"specimen\s+type"),
    # "of collection" is an optional trailing phrase on the label itself —
    # same leak-prevention reasoning as "hospital" above: without
    # consuming it, "Specimen site of collection, Left arm vein" would
    # capture "of collection, Left arm vein" as the value.
    ("specimen_site", r"specimen\s+site(?:\s+of\s+collection)?"),
    ("reason_for_request", r"reason\s+for\s+request"),
    ("clinical_history", r"clinical\s+history"),
    ("provisional_diagnosis", r"provisional\s+diagnosis"),
    ("tests_required", r"tests?\s+(?:required|requested|needed)"),
    ("medication", r"relevant\s+medication|\bmedication\b"),
    ("requesting_doctor", r"requesting\s+doctor"),
    ("hpcsa_number", r"hpcsa\s+number"),
    ("department", r"\bdepartment\b"),
]

# Fields whose value can legitimately span multiple sentences (a list, or
# free-text prose) — capped only by the NEXT recognized label, never by a
# sentence-ending period.
_LONG_FIELDS = {"clinical_history", "provisional_diagnosis", "tests_required"}

# Fields whose terminology resolution is deferred to extract_fields()'s
# second pass, once every field's raw value is available as context.
_DEFERRED_TERMINOLOGY_FIELDS = {
    "tests_required", "clinical_history", "provisional_diagnosis", "medication",
}

_DATE_FIELDS = {"date_of_birth", "date_requested", "date_collected"}
_TIME_FIELDS = {"time_requested", "time_collected"}

_LOOKAHEAD = 25  # chars, for deciding whether a label match is "real"

# Periods that must NOT be treated as sentence boundaries — the trailing
# letters run immediately up to (and including) the period is checked
# against these, case-insensitive.
_NO_SPLIT_SUFFIXES = [
    "dr.", "mr.", "mrs.", "ms.", "prof.", "a.m.", "p.m.", "vs.", "etc.",
    "no.", "jr.", "sr.",
]
_SENTENCE_END_RE = re.compile(r"\.(?=\s+[A-Z]|\s*$)")


def _is_real_label(text: str, match_start: int, match_end: int) -> bool:
    """A label match counts as real if EITHER of two independent signals
    says so — the doctor is never required to dictate any punctuation at
    all (no "comma", no "colon"), but Whisper reliably punctuates the
    natural pauses in real speech, and this leans on whichever pause the
    doctor actually made:

    1. A comma/colon appears before any period within a short lookahead
       — the doctor paused right after the label word itself ("Hospital,
       Ubuntu..." or "Hospital: Ubuntu...").
    2. The label is immediately preceded by the start of the transcript,
       or by a PERIOD left over from the previous field's sentence
       ending — the doctor paused BETWEEN fields instead, which is the
       natural rhythm of reading down a list of fields aloud without
       narrating punctuation ("...blood. Specimen site of collection
       left antecubital fossa." — no comma anywhere near the label, but
       Whisper still punctuates the pause before it).

    Signal 2 deliberately checks for a PERIOD only, never a comma —
    a comma right before a match is usually the current field's OWN
    label/value separator (e.g. "Ward, Medical Ward 3B." — the second,
    coincidental "Ward" inside "Medical Ward" is directly preceded by
    that very comma, and must NOT be mistaken for a fresh field
    boundary). It's also what distinguishes the real "Hospital," LABEL
    from "Ubuntu Academic Hospital." (the word appearing inside a value,
    with an ordinary word — not a period — immediately before it)
    without relying on signal 1's comma at all.
    """
    window = text[match_end : match_end + _LOOKAHEAD]
    comma_pos = min((p for p in (window.find(","), window.find(":")) if p != -1), default=-1)
    period_pos = window.find(".")
    if comma_pos != -1 and (period_pos == -1 or comma_pos < period_pos):
        return True

    preceding = text[:match_start].rstrip()
    return not preceding or preceding[-1] == "."


def _is_abbreviation_period(text: str, pos: int) -> bool:
    upto = text[: pos + 1].lower()
    return any(upto.endswith(suf) for suf in _NO_SPLIT_SUFFIXES)


def _find_sentence_end(text: str, start: int, end: int) -> int:
    for m in _SENTENCE_END_RE.finditer(text[start:end]):
        pos = start + m.start()
        if _is_abbreviation_period(text, pos):
            continue
        return pos
    return -1


def _find_label_matches(text: str) -> list[tuple[int, int, str]]:
    matches = []
    for field_key, pattern in _LABEL_DEFS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if _is_real_label(text, m.start(), m.end()):
                matches.append((m.start(), m.end(), field_key))
    matches.sort(key=lambda t: t[0])
    return matches


def _store_field(result: dict, field_key: str, raw_value: str) -> None:
    if field_key == "date_time_collected":
        date_part, _, time_part = raw_value.partition(",")
        date_value, date_status = normalize_date(date_part.strip())
        result["date_collected_raw"] = date_part.strip()
        result["date_collected"] = date_value
        result["date_collected_status"] = date_status

        time_value, time_status = normalize_time(time_part.strip())
        result["time_collected_raw"] = time_part.strip()
        result["time_collected"] = time_value
        result["time_collected_status"] = time_status
        return

    if field_key in _DATE_FIELDS:
        value, status = normalize_date(raw_value)
        result[f"{field_key}_raw"] = raw_value
        result[field_key] = value
        result[f"{field_key}_status"] = status
        return

    if field_key in _TIME_FIELDS:
        value, status = normalize_time(raw_value)
        result[f"{field_key}_raw"] = raw_value
        result[field_key] = value
        result[f"{field_key}_status"] = status
        return

    if field_key in _DEFERRED_TERMINOLOGY_FIELDS:
        # Normalization is deferred to a second pass in extract_fields(),
        # run after every field has been extracted, so context-based
        # candidate ranking (for ambiguous abbreviations) can see
        # clinical_history/provisional_diagnosis/specimen_type/department/
        # tests_required regardless of what order they were dictated in.
        result[f"{field_key}_raw"] = raw_value
        return

    # Everything else: pure raw passthrough. No fuzzy/terminology matching
    # ever touches patient/doctor names, IDs, ward, hospital, specimen
    # type/site, or priority.
    result[field_key] = {"raw": raw_value, "value": raw_value, "status": "extracted"}


def extract_fields(transcript: str) -> dict:
    text = (transcript or "").strip()
    result: dict = {}
    unparsed: list[str] = []

    if not text:
        result["unparsed_text"] = unparsed
        return result

    matches = _find_label_matches(text)

    if not matches:
        unparsed.append(text)
        result["unparsed_text"] = unparsed
        return result

    leading = text[: matches[0][0]].strip(" .,:")
    if leading:
        unparsed.append(leading)

    for i, (_, end, field_key) in enumerate(matches):
        value_start = end
        next_start = matches[i + 1][0] if i + 1 < len(matches) else len(text)

        if field_key in _LONG_FIELDS:
            value_end = next_start
        else:
            sentence_end = _find_sentence_end(text, value_start, next_start)
            value_end = next_start if sentence_end == -1 else min(next_start, sentence_end)

        # Strips a leading ":" as well as the original " ,." set — a label
        # recognized via its own colon (e.g. "Specimen type: blood.", or
        # "Hospital or clinic name: Greenside Clinic.") must not leave that
        # colon sitting at the front of the extracted value; _is_real_label()
        # already accepts a colon as a valid label/value separator, so the
        # value side must consistently strip it too.
        raw_value = text[value_start:value_end].strip(" ,.:")
        _store_field(result, field_key, raw_value)

        gap_text = text[value_end:next_start].strip(" .,:")
        if gap_text:
            unparsed.append(gap_text)

    # Second pass: now that every field is extracted, resolve
    # clinical_history/provisional_diagnosis/medication first — they read
    # sibling context straight off the "_raw" keys (see
    # clinical_terminology._context_text_for), so ordering doesn't matter
    # to them — and tests_required.normalize_tests_required's own
    # _context_text reads clinical_history/provisional_diagnosis as
    # already-resolved dicts (field["value"]), so it must run AFTER them.
    for field_key, unambiguous_dict in (
        ("clinical_history", clinical_terminology.CLINICAL_ABBREVIATIONS),
        ("provisional_diagnosis", clinical_terminology.CLINICAL_ABBREVIATIONS),
        ("medication", clinical_terminology.MEDICATION_ABBREVIATIONS),
    ):
        if f"{field_key}_raw" in result:
            result[field_key] = clinical_terminology.resolve_field_text(
                result[f"{field_key}_raw"], field_key, unambiguous_dict, context=result
            )

    if "tests_required_raw" in result:
        result["tests_required"] = normalize_tests_required(
            result["tests_required_raw"], context=result
        )
        # Derived, additive metadata ONLY — reads the just-computed
        # tests_required and nothing else, writes a new key and nothing
        # else. See specimen_mapping.py's docstring for why this can't
        # read backend/data/nhls_tests.json's specimen_type field, and why
        # this must never be confused with a transcript-normalization
        # stage (it doesn't touch clinical_history/provisional_diagnosis/
        # medication/identifiers/dates, and never feeds back into them).
        result["specimen_requirements"] = specimen_mapping.map_tests_to_specimens(
            result["tests_required"]
        )

    result["unparsed_text"] = unparsed
    return result


# --------------------------------------------------------------------------- #
# Clinician-facing normalized transcript
# --------------------------------------------------------------------------- #
# Reconstructs a readable, structured summary from `structured` (the dict
# extract_fields() returns) — same field order as the dictation proforma.
# Confirmed date/time/test values are substituted in; anything ambiguous or
# unrecognized keeps its raw text with an explicit marker, never guessed.

_DISPLAY_FIELDS = [
    ("patient_name", "Patient name"),
    ("patient_id", "Patient hospital number"),
    ("date_of_birth", "Date of birth"),
    ("ward", "Medical ward"),
    ("hospital", "Hospital"),
    ("date_requested", "Date requested"),
    ("time_requested", "Time requested"),
    ("priority", "Priority"),
    ("specimen_type", "Specimen type"),
    ("specimen_site", "Specimen site"),
    ("reason_for_request", "Reason for request"),
    # Matches the actual parseable label wording (_LABEL_DEFS: "date of
    # collection"/"time of collection") — not just any human-readable
    # label, so the displayed transcript stays consistent with what the
    # dictation guide tells the doctor to say and what /transcribe
    # actually recognizes.
    ("date_collected", "Date of collection"),
    ("time_collected", "Time of collection"),
    ("clinical_history", "Clinical history"),
    ("provisional_diagnosis", "Provisional diagnosis"),
    ("tests_required", "Tests required"),
    ("medication", "Relevant medication"),
    ("requesting_doctor", "Requesting doctor"),
    ("hpcsa_number", "HPCSA number"),
    ("department", "Department"),
]

_DATE_TIME_DISPLAY_FIELDS = {
    "date_of_birth", "date_requested", "time_requested",
    "date_collected", "time_collected",
}


def _format_test_item(item: dict) -> str:
    if item["status"] == "confirmed":
        # Keep the originally-spoken form visible alongside its expansion
        # (e.g. "Full Blood Count (FBC)") — but skip the bracket when the
        # doctor already said the full name, so it doesn't read as
        # "Full Blood Count (Full Blood Count)".
        if item["raw"].strip().lower() != item["normalized"].strip().lower():
            return f"{item['normalized']} ({item['raw']})"
        return item["normalized"]
    if item["status"] == "ambiguous":
        return f"{item['raw']} [ambiguous — please confirm]"
    return f"{item['raw']} [unrecognized]"


def _to_12h_ampm(value_24h: str) -> str:
    """Converts a confirmed 24-hour "HH:MM" value back into a human "H:MM
    AM/PM" string — unambiguously re-parseable through the same
    datetime_normalize.normalize_time() pattern an original spoken AM/PM
    time already goes through. Without this, a confirmed time embedded as
    bare 24-hour digits (e.g. "10:30") is indistinguishable from a FRESH,
    genuinely ambiguous dictation missing its AM/PM marker — re-parsing it
    (e.g. after "Edit transcript") would silently demote an
    already-confirmed time back to "ambiguous", forcing the doctor to
    re-resolve AM/PM on every single save."""
    hour, minute = (int(part) for part in value_24h.split(":"))
    ampm = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {ampm}"


def build_normalized_text(structured: dict) -> str:
    lines: list[str] = []

    for field_key, label in _DISPLAY_FIELDS:
        if field_key == "tests_required":
            items = structured.get("tests_required")
            if items is None:
                continue
            value = ", ".join(_format_test_item(item) for item in items) or "—"
            lines.append(f"{label}: {value}")
            continue

        if field_key in _DATE_TIME_DISPLAY_FIELDS:
            if field_key not in structured:
                continue
            value = structured.get(field_key)
            status = structured.get(f"{field_key}_status")
            if status == "confirmed" and value:
                display_value = _to_12h_ampm(value) if field_key in _TIME_FIELDS else value
                lines.append(f"{label}: {display_value}")
            else:
                raw = structured.get(f"{field_key}_raw", "")
                lines.append(f"{label}: {raw} [unconfirmed — please verify]")
            continue

        field = structured.get(field_key)
        if field is None:
            continue
        lines.append(f"{label}: {field['value']}")

    unparsed = structured.get("unparsed_text") or []
    if unparsed:
        lines.append("Unrecognized speech (please review): " + " / ".join(unparsed))

    return "\n".join(lines)
