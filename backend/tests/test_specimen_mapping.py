import specimen_mapping


def _item(raw, normalized, status="confirmed"):
    return {"raw": raw, "normalized": normalized, "status": status}


def test_matches_the_features_own_worked_example():
    tests_required = [
        _item("FBC", "Full Blood Count"),
        _item("CRP", "C-reactive protein"),
        _item("U and E", "Urea and Electrolytes"),
        _item("LFT", "Liver Function Tests"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    by_label = {g["label"]: g["tests"] for g in groups}

    assert by_label["PURPLE — EDTA"] == ["Full Blood Count"]
    assert by_label["YELLOW — Serum"] == [
        "C-reactive protein",
        "Urea and Electrolytes",
        "Liver Function Tests",
    ]


def test_only_confirmed_tests_enter_specimen_mapping():
    tests_required = [
        _item("FBC", "Full Blood Count", status="confirmed"),
        _item("TB", None, status="ambiguous"),
        _item("xyzzy", None, status="unrecognized"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    all_tests = [t for g in groups for t in g["tests"]]
    assert all_tests == ["Full Blood Count"]


def test_unmapped_confirmed_test_shown_not_dropped_not_guessed():
    # PPD is a skin test, not a blood draw — deliberately not in the
    # curated colour table. It must still be SHOWN (in its own group),
    # never silently dropped and never force-assigned a wrong colour.
    tests_required = [
        _item("PPD", "Purified Protein Derivative (Tuberculin Skin Test)"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [
        {
            "label": specimen_mapping.UNMAPPED_LABEL,
            "tests": ["Purified Protein Derivative (Tuberculin Skin Test)"],
        }
    ]


def test_coagulation_tests_grouped_under_citrate():
    tests_required = [
        _item("INR", "International Normalized Ratio"),
        _item("APTT", "Activated Partial Thromboplastin Time"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert len(groups) == 1
    assert groups[0]["label"] == "BLUE — Sodium Citrate"
    assert set(groups[0]["tests"]) == {
        "International Normalized Ratio",
        "Activated Partial Thromboplastin Time",
    }


def test_glucose_grouped_separately_from_other_chemistry():
    # Glucose needs fluoride oxalate (grey), not the same serum/SST tube
    # as most other chemistry — must not be lumped in with YELLOW.
    tests_required = [
        _item("Glu", "Glucose"),
        _item("CRP", "C-reactive protein"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    by_label = {g["label"]: g["tests"] for g in groups}
    assert by_label["GREY — Fluoride Oxalate"] == ["Glucose"]
    assert by_label["YELLOW — Serum"] == ["C-reactive protein"]


def test_duplicate_confirmed_test_not_listed_twice():
    tests_required = [
        _item("FBC", "Full Blood Count"),
        _item("Full Blood Count", "Full Blood Count"),
    ]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [{"label": "PURPLE — EDTA", "tests": ["Full Blood Count"]}]


def test_lookup_is_case_insensitive():
    tests_required = [_item("fbc", "full blood count")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups[0]["label"] == "PURPLE — EDTA"


def test_empty_tests_required_produces_no_groups():
    assert specimen_mapping.map_tests_to_specimens([]) == []


def test_all_ambiguous_or_unrecognized_produces_no_groups():
    tests_required = [
        _item("TB", None, status="ambiguous"),
        _item("xyzzy", None, status="unrecognized"),
    ]
    assert specimen_mapping.map_tests_to_specimens(tests_required) == []


# --------------------------------------------------------------------------- #
# Resolved ambiguous-abbreviation candidates — a doctor resolving "TB" or
# "CT" inline (frontend/app.js's resolveAmbiguousItem) must land on the
# real specimen requirement for whichever meaning they picked, not a
# blanket "Unmapped", when that meaning genuinely is an orderable lab
# test. When it resolves to a diagnosis/procedure instead, "Unmapped" is
# the honest, correct answer — not a gap.
# --------------------------------------------------------------------------- #


def test_tb_resolved_to_total_bilirubin_is_mapped_to_serum():
    tests_required = [_item("TB", "Total Bilirubin")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [{"label": "YELLOW — Serum", "tests": ["Total Bilirubin"]}]


def test_tb_resolved_to_tuberculosis_stays_honestly_unmapped():
    # Which specimen is right (sputum / blood IGRA / skin test) genuinely
    # depends on which TB test was meant — never guessed.
    tests_required = [_item("TB", "Tuberculosis")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [{"label": specimen_mapping.UNMAPPED_LABEL, "tests": ["Tuberculosis"]}]


def test_ct_resolved_to_chlamydia_trachomatis_is_mapped_to_urine():
    tests_required = [_item("CT", "Chlamydia Trachomatis")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [{"label": "URINE CONTAINER — Sterile", "tests": ["Chlamydia Trachomatis"]}]


def test_ct_resolved_to_computed_tomography_stays_unmapped():
    # An imaging procedure, not a lab test — there is no specimen at all.
    tests_required = [_item("CT", "Computed Tomography")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [{"label": specimen_mapping.UNMAPPED_LABEL, "tests": ["Computed Tomography"]}]


def test_bone_marrow_uses_its_own_non_tube_category():
    tests_required = [_item("BM", "Bone Marrow")]
    groups = specimen_mapping.map_tests_to_specimens(tests_required)
    assert groups == [
        {"label": "BONE MARROW — Aspirate/Biopsy Kit", "tests": ["Bone Marrow"]}
    ]
