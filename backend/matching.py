"""
NHLS/LOINC fuzzy-matching infrastructure: loads the lookup tables built by
backend/scripts/build_terminology_index.py once at import time (the same
way backend/main.py loads the Whisper model once at startup rather than
per-request), and exposes best_single_match() — the one entry point
terminology_normalize.py uses to resolve a single "tests required" item to
a canonical NHLS/LOINC name.

If the lookup files haven't been built yet (see
backend/scripts/build_terminology_index.py), matching is disabled
gracefully rather than crashing the whole app — main.py imports this
module, so a failure here must not also break plain transcription.

Previously this module also ran fuzzy matching over the ENTIRE raw
transcript (find_matches(), using spaCy to generate candidate noun-chunk/
n-gram phrases) to produce a browsable "suggested tests" list. That path
has been retired: it had no way to tell prose ("clinical history",
"provisional diagnosis") apart from an actual test-required item, so it
regularly suggested unrelated LOINC codes from ordinary sentence fragments.
The correct scope for terminology matching is the already-extracted
`tests_required` field (see field_extraction.py), which is what
terminology_normalize.py does. Since nothing else needed spaCy, it — and
the whole-transcript candidate generation — were removed rather than left
as dead code inviting the unsafe path to get wired back in.
"""

import json
import re
from pathlib import Path

from rapidfuzz import fuzz, process

DATA_DIR = Path(__file__).parent / "data"

_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")

# Below this rapidfuzz score (0-100), a candidate match is discarded as too
# weak. token_set_ratio ignores word order and extra/missing words, which
# suits matching a spoken phrase against a controlled-vocabulary synonym
# (e.g. "full blood count" against "Complete blood count panel" scores 81)
# — WRatio was tried first and rejected: its partial-ratio component gave
# short junk fragments (e.g. a 2-character synonym "co") absurdly high
# scores against unrelated long phrases.
MIN_MATCH_SCORE = 80
_SCORER = fuzz.token_set_ratio

# Words too common to usefully narrow down candidates via the inverted
# index (they'd match a huge fraction of the corpus and provide no
# discriminating power).
_STOPWORDS = {
    "and", "or", "the", "for", "with", "without", "from", "into", "onto",
    "test", "panel", "level", "levels", "blood", "serum", "plasma",
}

_READY = False
_NHLS_TESTS: list[dict] = []
_NHLS_CHOICES: list[str] = []
_NHLS_WORD_INDEX: dict[str, set[int]] = {}
_LOINC_TESTS: list[dict] = []
_LOINC_SYNONYMS: list[dict] = []
_LOINC_CHOICES: list[str] = []
_LOINC_WORD_INDEX: dict[str, set[int]] = {}


def _stem(word: str) -> str:
    """Light plural-stripping so "culture" and "cultures" share an index
    key — confirmed necessary: without it, a query for "blood culture"
    found ZERO NHLS candidates (index only had "cultures") and fell through
    to an unrelated LOINC synonym match instead. This is a prefilter only;
    the real fuzzy scorer still has the final say, so a slightly
    over-eager stem just means a few extra candidates get scored, not a
    few extra false matches returned."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _build_word_index(choices: list[str]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for i, text in enumerate(choices):
        for word in _WORD_RE.findall(text.lower()):
            if len(word) < 3 or word in _STOPWORDS:
                continue
            index.setdefault(_stem(word), set()).add(i)
    return index


def _load():
    global _READY, _NHLS_TESTS, _NHLS_CHOICES, _NHLS_WORD_INDEX
    global _LOINC_TESTS, _LOINC_SYNONYMS, _LOINC_CHOICES, _LOINC_WORD_INDEX

    nhls_path = DATA_DIR / "nhls_tests.json"
    loinc_path = DATA_DIR / "loinc_terms.json"

    if not nhls_path.exists() or not loinc_path.exists():
        print(
            "matching: terminology data not found under backend/data/ — run "
            "backend/scripts/build_terminology_index.py first. Matching disabled."
        )
        return

    _NHLS_TESTS = json.loads(nhls_path.read_text(encoding="utf-8"))
    _NHLS_CHOICES = [t["test_name"] for t in _NHLS_TESTS]
    _NHLS_WORD_INDEX = _build_word_index(_NHLS_CHOICES)

    loinc_index = json.loads(loinc_path.read_text(encoding="utf-8"))
    _LOINC_TESTS = loinc_index["tests"]
    _LOINC_SYNONYMS = loinc_index["synonyms"]
    _LOINC_CHOICES = [s["text"] for s in _LOINC_SYNONYMS]
    _LOINC_WORD_INDEX = _build_word_index(_LOINC_CHOICES)

    _READY = True
    print(
        f"matching: loaded {len(_NHLS_TESTS)} NHLS tests, "
        f"{len(_LOINC_TESTS)} LOINC tests ({len(_LOINC_SYNONYMS)} synonyms). Ready."
    )


try:
    _load()
except Exception as exc:  # noqa: BLE001 - matching must never take down transcription
    print(f"matching: failed to load terminology data ({exc}). Matching disabled.")


def _candidate_indices(phrase: str, word_index: dict[str, set[int]]) -> set[int]:
    """
    Union of choice indices sharing at least one significant word with
    `phrase`, via the prebuilt inverted index. Scoring all ~1.1M LOINC
    synonyms per query (confirmed by benchmarking) takes well over a
    minute — this prefilter is what makes matching fast enough to run
    inline on every transcription.
    """
    indices: set[int] = set()
    for word in _WORD_RE.findall(phrase):
        indices |= word_index.get(_stem(word), set())
    return indices


def _best_match(phrase, choices, word_index):
    # Choices/index are built from lowercased text (_build_word_index); the
    # scorer itself is also case-sensitive (confirmed: fuzz.ratio('Blood',
    # 'blood') == 80, not 100) — callers may pass original-case text (e.g.
    # terminology_normalize.py's raw "tests required" items), so normalize
    # once here rather than relying on every caller to remember to.
    phrase = phrase.lower()
    subset_indices = _candidate_indices(phrase, word_index)
    if not subset_indices:
        return None
    subset = {i: choices[i] for i in subset_indices}
    return process.extractOne(
        phrase, subset, scorer=_SCORER, score_cutoff=MIN_MATCH_SCORE
    )


def best_single_match(phrase: str, min_score: float = 88.0) -> dict | None:
    """
    Single best NHLS-or-LOINC hit for one phrase, at a caller-chosen
    (typically stricter) score threshold — used by
    terminology_normalize.py to resolve one "tests required" item to one
    canonical answer.
    """
    if not _READY:
        return None

    best = None

    # NHLS wins whenever it clears min_score at all — never overridden by a
    # numerically higher LOINC score. Confirmed necessary: exploding all of
    # LOINC's RELATEDNAMES2 into synonyms means a short query can land an
    # exact-string 100% hit against an obscure, unrelated LOINC code (e.g.
    # "Blood culture" hit a niche platelet-product FISH assay at 100%,
    # purely because that code's synonym list happened to contain the
    # literal string "Blood culture") — LOINC's synonym corpus is huge and
    # noisy, NHLS's is small and locally curated, so for a single-answer
    # field NHLS is the more trustworthy source whenever it's confident at
    # all, even if LOINC's coincidental match scores higher.
    nhls_hit = _best_match(phrase, _NHLS_CHOICES, _NHLS_WORD_INDEX)
    if nhls_hit is not None:
        _, score, idx = nhls_hit
        if score >= min_score:
            best = {
                "source": "nhls",
                "name": _NHLS_TESTS[idx]["test_name"],
                "score": round(score, 1),
            }

    if best is not None:
        return best

    loinc_hit = _best_match(phrase, _LOINC_CHOICES, _LOINC_WORD_INDEX)
    if loinc_hit is not None:
        _, score, idx = loinc_hit
        if score >= min_score:
            synonym = _LOINC_SYNONYMS[idx]
            test = _LOINC_TESTS[synonym["test_index"]]
            best = {
                "source": "loinc",
                "name": test["long_common_name"],
                "score": round(score, 1),
            }

    return best
