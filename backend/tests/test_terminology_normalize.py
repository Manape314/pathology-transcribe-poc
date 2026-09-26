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
        # Known abbreviations (from the request's list).
        ("FBC", "Full Blood Count"),
        ("CRP", "C-reactive protein"),
        ("U&E", "Urea and Electrolytes"),
        ("U and E", "Urea and Electrolytes"),
        ("ESR", "Erythrocyte Sedimentation Rate"),
        ("INR", "International Normalized Ratio"),
        ("PTT", "Activated Partial Thromboplastin Time"),
        ("TSH", "Thyroid Stimulating Hormone"),
        ("T3", "Free T3"),
        ("T4", "Free T4"),
        ("AST", "Aspartate Transaminase"),
        ("ALT", "Alanine Transaminase"),
        ("LDH", "Lactate Dehydrogenase"),
        ("CK-MB", "Creatine Kinase MB"),
        # Canonical (already full) forms.
        ("Full Blood Count", "Full Blood Count"),
        ("C-reactive protein", "C-reactive protein"),
        ("Urea and Electrolytes", "Urea and Electrolytes"),
    ],
)
def test_normalize_test_item_known_terms(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"
    assert result["confirmation_status"] == "automatic"
    assert result["raw"] == raw  # raw traceability preserved


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("PT", "Prothrombin Time"),
        ("aPTT", "Activated Partial Thromboplastin Time"),
        ("Hb", "Haemoglobin"),
        ("RBC", "Red Cell Count"),
        ("HbA1c", "Glycated Haemoglobin"),
        ("HB1c", "Glycated Haemoglobin"),  # common mishearing of HbA1c
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


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("F B C", "Full Blood Count"),
        ("C R P", "C-reactive protein"),
        ("U and E", "Urea and Electrolytes"),
    ],
)
def test_spoken_letter_by_letter_forms(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("fbc", "Full Blood Count"),
        ("F.B.C.", "Full Blood Count"),
        ("crp", "C-reactive protein"),
        ("C-R-P", "C-reactive protein"),
    ],
)
def test_case_and_punctuation_variants(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"


def test_despace_letters_does_not_touch_ordinary_multiword_phrases():
    # "C-reactive protein" must NOT be mangled into "Creactiveprotein" —
    # _despace_letters only collapses runs where EVERY token is a single
    # letter.
    result = normalize_test_item("C-reactive protein")
    assert result["normalized"] == "C-reactive protein"


# --------------------------------------------------------------------------- #
# Ambiguous abbreviations — NEVER auto-resolved, regardless of context.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected_candidate_names",
    [
        ("TB", {"Total Bilirubin", "Tuberculosis"}),
        ("UA", {"Uric Acid", "Urinalysis"}),
        ("PCR", {"Polymerase Chain Reaction", "Protein Creatinine Ratio"}),
        ("MS", {"Multiple Sclerosis", "Mitral Stenosis", "Morphine Sulphate"}),
        ("CA", {"Calcium", "Cancer"}),
        ("BS", {"Blood Sugar", "Bowel Sounds"}),
        ("BM", {"Bone Marrow", "Blood Glucose Monitoring"}),
        ("CP", {"C-Peptide", "Chest Pain"}),
        ("RA", {"Rheumatoid Arthritis", "Renal Artery Stenosis"}),
        ("MI", {"Myocardial Infarction", "Mitral Insufficiency"}),
        ("CVA", {"Cerebrovascular Accident", "Costovertebral Angle Tenderness"}),
        ("PID", {"Pelvic Inflammatory Disease", "Prolapsed Intervertebral Disc"}),
        ("DM", {"Diabetes Mellitus", "Dermatomyositis"}),
        ("CF", {"Cystic Fibrosis", "Cardiac Failure", "Complement Fixation"}),
        ("HD", {"Hemodialysis", "Huntington's Disease", "Heart Disease"}),
    ],
)
def test_ambiguous_abbreviations_return_full_candidate_list(raw, expected_candidate_names):
    result = normalize_test_item(raw)
    assert result["status"] == "ambiguous"
    assert result["normalized"] is None
    assert result["confirmation_status"] == "pending"
    assert {c["canonical_name"] for c in result["candidates"]} == expected_candidate_names


@pytest.mark.parametrize(
    "raw, context_text, expected_top_candidate",
    [
        ("TB", "patient has chronic cough and chest pain", "Tuberculosis"),
        ("TB", "deranged lft, jaundice noted, likely hepatic cause", "Total Bilirubin"),
        ("UA", "suspected gout, joint pain", "Uric Acid"),
        ("PCR", "urine protein creatinine ratio requested for proteinuria", "Protein Creatinine Ratio"),
    ],
)
def test_ambiguous_abbreviations_context_ranks_but_never_auto_resolves(
    raw, context_text, expected_top_candidate
):
    # Context changes the ORDER and the explanation, never the status.
    result = normalize_test_item(raw, context_text=context_text)
    assert result["status"] == "ambiguous"
    assert result["normalized"] is None
    assert result["confirmation_status"] == "pending"
    assert result["candidates"][0]["canonical_name"] == expected_top_candidate
    assert "context matched" in result["candidates"][0]["reason"]

    # Even with strong, unambiguous supporting context, it is STILL
    # "ambiguous" — automatic contextual disambiguation is deliberately
    # not implemented (deferred until validated against a clinically
    # reviewed dataset). This is the core safety property this test locks in.
    assert result["status"] != "confirmed"


def test_ambiguous_abbreviation_with_no_context_still_returns_ranked_candidates():
    result = normalize_test_item("TB")
    assert result["status"] == "ambiguous"
    # No evidence either way — every candidate explains that plainly.
    assert all(c["reason"] == "no supporting context found" for c in result["candidates"])


def test_ambiguity_dataset_is_a_plain_swappable_data_structure():
    # Locks in the "expandable without rewriting the engine" requirement:
    # the resolver must not special-case any particular abbreviation key.
    # Adding a brand-new ambiguous entry at runtime must work with zero
    # changes to normalize_test_item.
    import terminology_normalize as tn

    tn.AMBIGUOUS_ABBREVIATIONS["zz"] = [
        {"canonical_name": "Zebra Zest Test", "domain": "chemical_pathology", "keywords": ["zest"]},
        {"canonical_name": "Zoster Zoster", "domain": "microbiology", "keywords": ["shingles"]},
    ]
    try:
        result = normalize_test_item("ZZ")
        assert result["status"] == "ambiguous"
        assert {c["canonical_name"] for c in result["candidates"]} == {
            "Zebra Zest Test",
            "Zoster Zoster",
        }
    finally:
        del tn.AMBIGUOUS_ABBREVIATIONS["zz"]


# --------------------------------------------------------------------------- #
# Negative / safety cases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("K", "Potassium"),
        ("Na", "Sodium"),
        ("Mg", "Magnesium"),
    ],
)
def test_electrolyte_symbols_resolve_unambiguously(raw, expected_normalized):
    # K/Na/Mg are standard electrolyte test abbreviations. Matching only
    # ever compares a WHOLE already-split "tests required" list item (see
    # split_test_items) — never a substring search inside a dosage string
    # like "500mg" — so a bare "mg" as its own list item is unambiguously
    # the Magnesium test, not a stray dosage unit. ("Ca" is handled
    # separately as ambiguous — Calcium vs Cancer — since "cancer" is a
    # real competing interpretation in a way "milligrams" isn't for a
    # standalone list item.)
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"
    assert result["source"] == "abbreviation"


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("GFR", "Estimated Glomerular Filtration Rate"),
        ("Urea", "Urea"),
        ("Cr", "Creatinine"),
        ("D-Dimer", "D-Dimer"),
        ("Amy", "Amylase"),
        ("Lip", "Lipase"),
        ("Alb", "Albumin"),
        ("TP", "Total Protein"),
        ("ACR", "Albumin Creatinine Ratio"),
        ("ANCA", "Antineutrophil Cytoplasmic Antibody"),
        ("C3", "Complement C3"),
        ("C4", "Complement C4"),
        ("HCV", "Hepatitis C Virus Antibody"),
        ("CA15-3", "Cancer Antigen 15-3"),
        ("Cort", "Cortisol"),
        ("PRL", "Prolactin"),
        ("Fol", "Folate"),
    ],
)
def test_second_expansion_pass_new_lab_abbreviations(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"


@pytest.mark.parametrize(
    "raw, expected_candidate_names",
    [
        ("CD", {"Crohn's Disease", "Contact Dermatitis"}),
        ("UC", {"Ulcerative Colitis", "Urinary Catheter"}),
        ("RR", {"Respiratory Rate", "Regular Rhythm"}),
    ],
)
def test_second_expansion_pass_new_ambiguous_abbreviations(raw, expected_candidate_names):
    result = normalize_test_item(raw)
    assert result["status"] == "ambiguous"
    assert {c["canonical_name"] for c in result["candidates"]} == expected_candidate_names


@pytest.mark.parametrize(
    "raw, expected_normalized",
    [
        ("Cl", "Chloride"),
        ("CO2", "Bicarbonate"),
        ("Zn", "Zinc"),
        ("Cu", "Copper"),
        ("NH3", "Ammonia"),
        ("Lact", "Lactate"),
        ("pH", "pH"),
        ("ABG", "Arterial Blood Gas"),
        ("VBG", "Venous Blood Gas"),
        ("FDP", "Fibrin Degradation Products"),
        ("Hgb", "Haemoglobin"),
        ("Plts", "Platelet Count"),
        ("ANC", "Absolute Neutrophil Count"),
        ("ALC", "Absolute Lymphocyte Count"),
        ("Ferr", "Ferritin"),
        ("Hapto", "Haptoglobin"),
        ("Trf", "Transferrin"),
        ("AFB", "Acid-Fast Bacilli"),
        ("CrAg", "Cryptococcal Antigen"),
        ("HSV", "Herpes Simplex Virus"),
        ("CMV", "Cytomegalovirus"),
        ("EBV", "Epstein-Barr Virus"),
        ("VZV", "Varicella Zoster Virus"),
        ("TPHA", "Treponema Pallidum Haemagglutination Assay"),
        ("MP", "Malaria Parasite (Blood Film)"),
        ("NSE", "Neuron-Specific Enolase"),
        ("Pap", "Papanicolaou Smear"),
        ("IGF-1", "Insulin-like Growth Factor 1"),
        ("GH", "Growth Hormone"),
        ("PRA", "Plasma Renin Activity"),
        ("Aldo", "Aldosterone"),
        ("ECG", "Electrocardiogram"),
        ("EKG", "Electrocardiogram"),
        ("G&S", "Group and Save"),
        ("T&S", "Type and Screen"),
    ],
)
def test_third_expansion_pass_new_lab_abbreviations(raw, expected_normalized):
    result = normalize_test_item(raw)
    assert result["normalized"] == expected_normalized
    assert result["status"] == "confirmed"


@pytest.mark.parametrize(
    "raw, expected_candidate_names",
    [
        ("PCT", {"Procalcitonin", "Packed Cell Volume"}),
        ("GC", {"Gonococcus (Neisseria gonorrhoeae)", "Glucocorticoid"}),
        ("CT", {"Chlamydia Trachomatis", "Computed Tomography"}),
        ("ROM", {"Range of Motion", "Rupture of Membranes"}),
        ("PET", {"Pre-eclampsia", "Positron Emission Tomography"}),
    ],
)
def test_third_expansion_pass_new_ambiguous_abbreviations(raw, expected_candidate_names):
    result = normalize_test_item(raw)
    assert result["status"] == "ambiguous"
    assert {c["canonical_name"] for c in result["candidates"]} == expected_candidate_names


def test_normalize_test_item_unrecognized_is_flagged_not_guessed():
    result = normalize_test_item("xyzzy nonsense term")
    assert result["normalized"] is None
    assert result["status"] == "unrecognized"
    assert result["raw"] == "xyzzy nonsense term"
    assert result["confirmation_status"] == "pending"


def test_fuzzy_matching_does_not_correct_unrelated_words():
    # Guards point 9: an ordinary, clearly-unrelated word/phrase must not
    # get "corrected" into an arbitrary test name just because some
    # substring happens to overlap with something in the corpus.
    result = normalize_test_item("the weather today")
    assert result["status"] == "unrecognized"
    assert result["normalized"] is None


def test_identifiers_are_never_passed_through_this_module():
    # terminology_normalize.py only ever receives whole "tests required"
    # items — patient names/IDs are handled entirely in field_extraction.py
    # as raw passthrough and never reach this module at all. This test
    # documents that boundary: even something name-shaped isn't "corrected"
    # into a test if it were ever (incorrectly) passed in.
    result = normalize_test_item("Gabelo Mukwena")
    assert result["status"] == "unrecognized"


# --------------------------------------------------------------------------- #
# Full pipeline (split + normalize)
# --------------------------------------------------------------------------- #


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


def test_normalize_tests_required_with_ambiguous_item_mixed_in():
    results = normalize_tests_required("FBC, TB")
    by_raw = {r["raw"]: r for r in results}
    assert by_raw["FBC"]["status"] == "confirmed"
    assert by_raw["TB"]["status"] == "ambiguous"
    assert len(by_raw["TB"]["candidates"]) == 2


# --------------------------------------------------------------------------- #
# Performance
# --------------------------------------------------------------------------- #


def test_normalize_tests_required_latency():
    import time

    raw = "FBC, CRP, U and E, TB, LFT, PSA"
    start = time.perf_counter()
    for _ in range(20):
        normalize_tests_required(raw)
    elapsed_ms = (time.perf_counter() - start) / 20 * 1000
    # Generous bound for an interactive clinical PWA — indexing-first
    # architecture (dict lookups before any fuzzy scan) should be well
    # under this in practice.
    assert elapsed_ms < 200, f"normalize_tests_required took {elapsed_ms:.1f}ms on average"
