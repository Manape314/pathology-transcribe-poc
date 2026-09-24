import pytest

from terminology_normalize import (
    normalize_test_item,
    normalize_tests_required,
    split_test_items,
)


def test_split_test_items_protects_u_and_e():
    items = split_test_items("FBC, CRP, U and E. Blood culture.")
    assert items == ["FBC", "CRP", "U and E", "Blood culture"]


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("FBC", "Full Blood Count"),
        ("CRP", "C-reactive protein"),
        ("U&E", "Urea and Electrolytes"),
        ("U and E", "Urea and Electrolytes"),
        ("Full Blood Count", "Full Blood Count"),
        ("C-reactive protein", "C-reactive protein"),
        ("urea and electrolytes", "Urea and Electrolytes"),
    ],
)
def test_normalize_test_item_known_terms(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"
    assert result["raw"] == raw  # raw traceability preserved


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("PT", "Prothrombin Time"),
        ("aPTT", "Activated Partial Thromboplastin Time"),
        ("Hb", "Haemoglobin"),
        ("RBC", "Red Cell Count"),
        ("HbA1c", "Glycated Haemoglobin"),
        ("TSH", "Thyroid Stimulating Hormone"),
        ("LH", "Luteinizing Hormone"),
        ("Trop", "Troponin"),
        ("PSA", "Prostate-Specific Antigen"),
        ("Vit D", "Vitamin D"),
        ("HCG", "Human Chorionic Gonadotropin"),
        ("VDRL", "Venereal Disease Research Laboratory Test"),
    ],
)
def test_normalize_test_item_expanded_abbreviation_set(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"
    assert result["source"] == "abbreviation"


@pytest.mark.parametrize("raw", ["TB", "UA", "mg", "K", "Na", "Ca"])
def test_deliberately_ambiguous_abbreviations_are_not_guessed(raw):
    # TB (Total Bilirubin vs. Tuberculosis) and UA (Uric Acid vs.
    # Urinalysis) are genuinely ambiguous; single-letter electrolytes and
    # "mg" collide with a common dosage unit — none of these are in the
    # curated dictionary on purpose, and none should resolve via fuzzy
    # matching either (too short/generic to fuzzy-match safely).
    result = normalize_test_item(raw)
    assert result["status"] == "unmatched"
    assert result["normalized"] is None


def test_normalize_test_item_unmatched_is_flagged_not_guessed():
    result = normalize_test_item("xyzzy nonsense term")
    assert result["normalized"] is None
    assert result["status"] == "unmatched"
    assert result["raw"] == "xyzzy nonsense term"


def test_fuzzy_matching_does_not_correct_unrelated_words():
    # Guards point 9: an ordinary, clearly-unrelated word/phrase must not
    # get "corrected" into an arbitrary test name just because some
    # substring happens to overlap with something in the corpus.
    result = normalize_test_item("the weather today")
    assert result["status"] == "unmatched"
    assert result["normalized"] is None


def test_normalize_tests_required_end_to_end():
    results = normalize_tests_required("FBC, CRP, U and E. Blood culture.")
    normalized = [r["normalized"] for r in results]
    assert normalized == [
        "Full Blood Count",
        "C-reactive protein",
        "Urea and Electrolytes",
        "Blood cultures",
    ]
    assert all(r["status"] == "confirmed" for r in results)
    # Every item keeps its raw phrase for traceability.
    assert [r["raw"] for r in results] == ["FBC", "CRP", "U and E", "Blood culture"]
