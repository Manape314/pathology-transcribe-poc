"""
Matches phrases in a Whisper transcript against the NHLS test index and
LOINC — turning free-text dictation into candidate structured codes for the
doctor to review and confirm.

Heavy resources (the lookup tables built by
backend/scripts/build_terminology_index.py, and the spaCy model) are loaded
ONCE at import time, the same way backend/main.py loads the Whisper model
once at startup rather than per-request.

If the lookup files haven't been built yet (see
backend/scripts/build_terminology_index.py) or the spaCy model hasn't been
downloaded, matching is disabled gracefully rather than crashing the whole
app — main.py imports this module, so a failure here must not also break
plain transcription.
"""

import json
import os
import re
from pathlib import Path

from rapidfuzz import fuzz, process

DATA_DIR = Path(__file__).parent / "data"

_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")

# Below this rapidfuzz score (0-100), a candidate match is discarded as too
# weak to be worth showing the doctor. token_set_ratio ignores word order
# and extra/missing words, which suits matching a spoken phrase against a
# controlled-vocabulary synonym (e.g. "full blood count" against "Complete
# blood count panel" scores 81) — WRatio was tried first and rejected: its
# partial-ratio component gave short junk fragments (e.g. a 2-character
# synonym "co") absurdly high scores against unrelated long phrases.
MIN_MATCH_SCORE = 80
_SCORER = fuzz.token_set_ratio

# How many candidate phrases we pull out of a transcript before matching,
# and how many ranked matches we return — bounds for per-request cost.
MAX_CANDIDATE_PHRASES = 25
MAX_MATCHES_RETURNED = 8

# Words too common to usefully narrow down candidates via the inverted
# index (they'd match a huge fraction of the corpus and provide no
# discriminating power).
_STOPWORDS = {
    "and", "or", "the", "for", "with", "without", "from", "into", "onto",
    "test", "panel", "level", "levels", "blood", "serum", "plasma",
}

_READY = False
_NLP = None
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
    global _READY, _NLP, _NHLS_TESTS, _NHLS_CHOICES, _NHLS_WORD_INDEX
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

    import spacy

    model_name = os.getenv("SPACY_MODEL", "en_core_web_sm")
    try:
        _NLP = spacy.load(model_name)
    except OSError:
        print(
            f"matching: spaCy model '{model_name}' not found — run "
            f"`python -m spacy download {model_name}`. Matching disabled."
        )
        return

    _READY = True
    print(
        f"matching: loaded {len(_NHLS_TESTS)} NHLS tests, "
        f"{len(_LOINC_TESTS)} LOINC tests ({len(_LOINC_SYNONYMS)} synonyms), "
        f"spaCy model '{model_name}'. Ready."
    )


try:
    _load()
except Exception as exc:  # noqa: BLE001 - matching must never take down transcription
    print(f"matching: failed to load terminology data ({exc}). Matching disabled.")


def _generate_candidates(doc) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    def add(phrase: str):
        phrase = phrase.strip().lower()
        if len(phrase) < 3 or phrase in seen:
            return
        seen.add(phrase)
        candidates.append(phrase)

    for chunk in doc.noun_chunks:
        add(chunk.text)

    # Fallback: 1-4 word sliding windows over alphabetic tokens within each
    # sentence, to catch list-style phrasing (e.g. "urea and electrolytes,
    # creatinine, CRP") that generic noun-chunking tends to miss.
    for sent in doc.sents:
        words = [t.text for t in sent if _WORD_RE.fullmatch(t.text)]
        for n in (1, 2, 3, 4):
            for i in range(len(words) - n + 1):
                add(" ".join(words[i : i + n]))

    if len(candidates) > MAX_CANDIDATE_PHRASES:
        candidates = sorted(candidates, key=len, reverse=True)[:MAX_CANDIDATE_PHRASES]
    return candidates


def _candidate_indices(phrase: str, word_index: dict[str, set[int]]) -> dict[int, str]:
    """
    Union of choice indices sharing at least one significant word with
    `phrase`, via the prebuilt inverted index. Scoring all ~1.3M LOINC
    synonyms per candidate phrase (confirmed by benchmarking) takes well
    over a minute — this prefilter is what makes matching fast enough to
    run inline on every transcription.
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
    canonical answer, as opposed to find_matches()'s browsable multi-match
    suggestion list at the looser MIN_MATCH_SCORE.
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


def find_matches(text: str) -> list[dict]:
    if not _READY or not text or not text.strip():
        return []

    doc = _NLP(text)
    candidates = _generate_candidates(doc)

    best_by_key: dict[tuple[str, str], dict] = {}

    for phrase in candidates:
        nhls_hit = _best_match(phrase, _NHLS_CHOICES, _NHLS_WORD_INDEX)
        if nhls_hit is not None:
            _, score, idx = nhls_hit
            test = _NHLS_TESTS[idx]
            key = ("nhls", test["test_name"])
            if key not in best_by_key or score > best_by_key[key]["score"]:
                best_by_key[key] = {
                    "match_id": f"nhls:{test['test_name']}",
                    "source": "nhls",
                    "candidate_phrase": phrase,
                    "test_name": test["test_name"],
                    "specimen_type": test["specimen_type"],
                    "instructions": test["instructions"],
                    "score": round(score, 1),
                }

        loinc_hit = _best_match(phrase, _LOINC_CHOICES, _LOINC_WORD_INDEX)
        if loinc_hit is not None:
            _, score, idx = loinc_hit
            synonym = _LOINC_SYNONYMS[idx]
            test = _LOINC_TESTS[synonym["test_index"]]
            key = ("loinc", test["loinc_num"])
            if key not in best_by_key or score > best_by_key[key]["score"]:
                best_by_key[key] = {
                    "match_id": f"loinc:{test['loinc_num']}",
                    "source": "loinc",
                    "candidate_phrase": phrase,
                    "loinc_num": test["loinc_num"],
                    "long_common_name": test["long_common_name"],
                    "shortname": test["shortname"],
                    "score": round(score, 1),
                }

    results = sorted(best_by_key.values(), key=lambda m: m["score"], reverse=True)
    return results[:MAX_MATCHES_RETURNED]
