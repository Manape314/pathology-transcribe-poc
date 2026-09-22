"""
One-time build step: turn the raw reference data in Medical_Terminologies/
(NHLS handbook PDF + LOINC release) into small JSON lookup files that
backend/matching.py loads at startup.

This is NOT run automatically and NOT run per-request — the raw sources are
multi-GB and Medical_Terminologies/ is gitignored, so every developer who
wants matching to work runs this once locally, after placing their own copy
of Medical_Terminologies/ next to the repo:

    python backend/scripts/build_terminology_index.py

Requires the `pdftotext` binary (poppler) on PATH for the NHLS PDF step.
Everything else is Python stdlib — no pip installs needed just to run this.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------- #
# NHLS test index (parsed out of the handbook PDF)
# --------------------------------------------------------------------------- #
# The table lives between these two text markers, confirmed against the
# actual document (GPQ0064v3.pdf, "Table 25-1. NHLS test list", pages ~250-326).
# Matching on text markers (not page numbers) so this survives a handbook
# revision that shifts pagination.
TABLE_START_MARKER = "Table 25-1. NHLS test list"
TABLE_END_MARKER = "SECTIONS 26.0"

# Every table page repeats these three boilerplate lines — strip them.
BOILERPLATE_PATTERNS = [
    re.compile(r"^In the event of a dispute"),
    re.compile(r"^\s*National Health Laboratory Service"),
    re.compile(r"^Q-Pulse5/docs/active/GPQ0064v3"),
]

# Every table page repeats this header line, confirming a page is part of
# the table (its own column offsets aren't used — see the note on
# _find_new_row_and_columns below for why).
HEADER_PATTERN = re.compile(r"TEST NAME\s+SPECIMEN TYPE\s+SPECIAL INSTRUCTIONS")

# A wrapped test name's continuation line (e.g. "17-hydroxyprogesterone (17-"
# / "OHP)") usually starts lowercase, or with a bracket/dash. This is a
# heuristic, not a guarantee — pdftotext's layout mode doesn't reliably keep
# multi-line cells aligned to their row when neighbouring columns wrap to
# different line counts, so some entries (especially ones with unusual
# capitalisation in their wrapped continuation) will come out imperfect. The
# doctor visually confirms every match in the app before it's used, so this
# is an accepted, low-severity limitation rather than something we try to
# perfectly solve here.
_CONTINUATION_START_CHARS = ")(-–—"


def _looks_like_continuation(text: str) -> bool:
    first = text[0]
    return first.islower() or first in _CONTINUATION_START_CHARS


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


# Splits a line into (start_column, text) segments on runs of 2+ spaces.
# The table's specimen/instructions columns do NOT start at a fixed
# character position — confirmed against the real document, where the
# same logical column starts at very different offsets on different pages
# and even between adjacent rows on the same page (this is a proportionally
# wrapped table, not a fixed-width one). So column boundaries can't be
# computed once per page or per document; instead each row is classified
# using ITS OWN column positions, tracked as they're first seen (see
# parse_nhls_pdf below).
_SEGMENT_PATTERN = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")


def _split_columns(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in _SEGMENT_PATTERN.finditer(line)]


def _classify_column(row: dict, col: int) -> str:
    """Which field (specimen_type / instructions) a segment belongs to, by
    comparing its start column to whichever columns this row has already
    established for those two fields."""
    spec_col = row["specimen_col"]
    instr_col = row["instructions_col"]
    if spec_col is None and instr_col is None:
        return "specimen_type"  # first unlabelled column after the name
    if spec_col is None:
        return "specimen_type" if col < instr_col else "instructions"
    if instr_col is None:
        return "instructions" if col > spec_col else "specimen_type"
    return "specimen_type" if abs(col - spec_col) <= abs(col - instr_col) else "instructions"


def parse_nhls_pdf(pdf_path: Path) -> list[dict]:
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=True,
        capture_output=True,
    )
    # Decode explicitly as UTF-8: on Windows, subprocess with text=True
    # decodes using the system codepage (cp1252), which chokes on the
    # UTF-8 bytes pdftotext -enc UTF-8 actually writes.
    text = result.stdout.decode("utf-8")

    start = text.index(TABLE_START_MARKER)
    end = text.index(TABLE_END_MARKER, start)
    body = text[start:end]

    rows: list[dict] = []
    current: dict | None = None

    def flush():
        nonlocal current
        if current and current["test_name"].strip():
            rows.append(
                {
                    "test_name": _clean(current["test_name"]),
                    "specimen_type": _clean(current["specimen_type"]),
                    "instructions": _clean(current["instructions"]),
                }
            )
        current = None

    for page in body.split("\f"):
        lines = page.splitlines()

        header_idx = next(
            (i for i, line in enumerate(lines) if HEADER_PATTERN.search(line)), None
        )
        if header_idx is None:
            continue

        for line in lines[header_idx + 1 :]:
            if not line.strip() or any(p.search(line) for p in BOILERPLATE_PATTERNS):
                continue

            segments = _split_columns(line)
            if not segments:
                continue

            first_col, first_text = segments[0]
            # The test-name column always starts at (or very near) the left
            # margin, whether it's a new row's name or a continuation of the
            # previous row's wrapped name.
            at_name_column = first_col <= 2

            if at_name_column and not _looks_like_continuation(first_text):
                flush()
                current = {
                    "test_name": first_text,
                    "specimen_type": "",
                    "instructions": "",
                    "specimen_col": None,
                    "instructions_col": None,
                }
                rest = segments[1:]
            elif at_name_column and current is not None:
                current["test_name"] += " " + first_text
                rest = segments[1:]
            else:
                rest = segments

            if current is None:
                # Stray column text before any row has started on this page
                # (bleed from the previous row/page) — nothing sensible to
                # attach it to, so it's dropped.
                continue

            for col, text_part in rest:
                field = _classify_column(current, col)
                col_key = "specimen_col" if field == "specimen_type" else "instructions_col"
                current[field] += (" " if current[field] else "") + text_part
                if current[col_key] is None:
                    current[col_key] = col

    flush()

    seen = set()
    deduped = []
    for row in rows:
        key = (row["test_name"].lower(), row["specimen_type"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


# --------------------------------------------------------------------------- #
# LOINC slimming
# --------------------------------------------------------------------------- #
# LOINC classes that aren't relevant to a pathology dictation context
# (radiology, document/panel metadata, provocation-test panels, surveys).
EXCLUDE_CLASSES_EXACT = {"RAD", "DOC.ONTOLOGY", "DOC.MISC", "CHAL"}
EXCLUDE_CLASS_PREFIXES = ("PANEL.SURVEY",)


def _is_excluded_class(class_value: str) -> bool:
    return class_value in EXCLUDE_CLASSES_EXACT or class_value.startswith(
        EXCLUDE_CLASS_PREFIXES
    )


def build_loinc_index(csv_path: Path) -> dict:
    tests = []
    synonyms = []
    seen_synonyms = set()

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["STATUS"] != "ACTIVE":
                continue
            if row["ORDER_OBS"] not in ("Order", "Both"):
                continue
            if _is_excluded_class(row["CLASS"]):
                continue

            test_index = len(tests)
            tests.append(
                {
                    "loinc_num": row["LOINC_NUM"],
                    "component": row["COMPONENT"],
                    "system": row["SYSTEM"],
                    "long_common_name": row["LONG_COMMON_NAME"],
                    "shortname": row["SHORTNAME"],
                }
            )

            candidate_terms = [
                row["LONG_COMMON_NAME"],
                row["SHORTNAME"],
                row["COMPONENT"],
                *row["RELATEDNAMES2"].split(";"),
            ]
            for term in candidate_terms:
                term = term.strip()
                # Short fragments (LOINC RELATEDNAMES2 includes plenty of
                # bare abbreviations like "Mut", "WB") are indistinguishable
                # noise for fuzzy matching — they cause false-positive
                # partial-string matches against unrelated candidate phrases
                # far more often than they contribute a genuine synonym hit.
                if len(term) < 4:
                    continue
                key = (term.lower(), test_index)
                if key in seen_synonyms:
                    continue
                seen_synonyms.add(key)
                synonyms.append({"text": term, "test_index": test_index})

    return {"tests": tests, "synonyms": synonyms}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nhls-pdf",
        type=Path,
        default=REPO_ROOT / "Medical_Terminologies" / "GPQ0064v3.pdf",
    )
    parser.add_argument(
        "--loinc-csv",
        type=Path,
        default=REPO_ROOT
        / "Medical_Terminologies"
        / "Loinc_2.83"
        / "LoincTable"
        / "Loinc.csv",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "backend" / "data",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.nhls_pdf.exists():
        sys.exit(f"NHLS PDF not found: {args.nhls_pdf}")
    if not args.loinc_csv.exists():
        sys.exit(f"LOINC CSV not found: {args.loinc_csv}")

    print(f"Parsing NHLS test index from {args.nhls_pdf} ...")
    nhls_tests = parse_nhls_pdf(args.nhls_pdf)
    nhls_out = args.out_dir / "nhls_tests.json"
    nhls_out.write_text(json.dumps(nhls_tests, separators=(",", ":")), encoding="utf-8")
    print(f"  {len(nhls_tests)} NHLS test rows -> {nhls_out}")

    print(f"Building LOINC index from {args.loinc_csv} ...")
    loinc_index = build_loinc_index(args.loinc_csv)
    loinc_out = args.out_dir / "loinc_terms.json"
    loinc_out.write_text(
        json.dumps(loinc_index, separators=(",", ":")), encoding="utf-8"
    )
    print(
        f"  {len(loinc_index['tests'])} LOINC tests, "
        f"{len(loinc_index['synonyms'])} synonyms -> {loinc_out} "
        f"({loinc_out.stat().st_size / 1_000_000:.1f} MB)"
    )


if __name__ == "__main__":
    main()
