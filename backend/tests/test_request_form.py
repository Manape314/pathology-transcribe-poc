"""Request-form PDF filling (request_form.py + GET /request-form/{id}).

Covers: only web-page fields are filled, test tick boxes follow confirmed
canonical names, shared/ambiguous names never guess, boxes missing from the
printed form fall through to "Other tests", and the canonical->box table
stays in sync with frontend/test-table-data.js.
"""

import io
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

import request_form as rf

REPO = Path(__file__).resolve().parents[2]


def _record(**structured):
    return {
        "doctor_name": "Dr. Thato Manapi",
        "doctor_phone": "+27821234567",
        "hpcsa_number": "808080",
        "structured": structured,
    }


def _test(raw, normalized, status="confirmed"):
    return {"raw": raw, "normalized": normalized, "status": status}


FULL = _record(
    patient_name={"value": "Jane Doe"},
    patient_id={"value": "HN12345"},
    date_of_birth="1988-03-23", date_of_birth_status="confirmed",
    hospital={"value": "Ubuntu Academic Hospital"},
    ward={"value": "Medical Ward 3B"},
    priority={"value": "Urgent"},
    specimen_type={"value": "Blood"}, specimen_site={"value": "left arm"},
    date_collected="2026-10-14", date_collected_status="confirmed",
    time_collected="14:30", time_collected_status="confirmed",
    reason_for_request={"value": "Query sepsis"},
    provisional_diagnosis={"value": "Pneumonia"},
    medication={"value": "Warfarin 5 mg daily"},
    tests_required=[
        _test("FBC", "Full Blood Count"),
        _test("U&E", "Urea and Electrolytes"),
        _test("CRP", "C-reactive protein"),
    ],
)


def test_text_fields_and_doctor_details():
    text, ticks = rf.collect_values(FULL)
    assert text["row_Surname"] == "Doe" and text["row_First Name"] == "Jane"
    assert text["row_Patient Hospital Number"] == "HN12345"
    assert text["row_D.O.B."] == "23/03/1988"
    assert text["row_Type of Specimen"] == "Blood (left arm)"
    assert text["row_Date Taken"] == "14/10/2026" and text["row_Time"] == "14:30"
    assert text["row_Requesting Health Care Worker"] == "Dr. Thato Manapi"
    assert text["row_HPCSA/SANC Number"] == "808080"
    assert text["row_Contact Numbers"] == "+27821234567"
    assert "Pneumonia" in text["diagnosis_reason"] and "Query sepsis" in text["diagnosis_reason"]
    assert {"mark_urgent", "med_warfarin"} <= ticks and "med_heparin" not in ticks


def test_fields_the_web_page_does_not_capture_stay_blank():
    text, _ = rf.collect_values(FULL)
    for blank in ("row_Patient I.D Number", "patient_address", "row_Age", "icd10",
                  "row_Medical Aid", "row_Taken By", "row_E-mail Address", "row_Signature"):
        assert blank not in text


def test_unconfirmed_dates_are_not_written():
    s = dict(FULL["structured"], date_of_birth_status="ambiguous", time_collected_status="ambiguous")
    text, _ = rf.collect_values({**FULL, "structured": s})
    assert "row_D.O.B." not in text and "row_Time" not in text


@pytest.mark.parametrize("priority,expected", [
    ("Urgent", True), ("STAT", True), ("Routine", False), ("Non-urgent", False), ("", False),
])
def test_urgent_tick(priority, expected):
    _, ticks = rf.collect_values(_record(priority={"value": priority}))
    assert ("mark_urgent" in ticks) is expected


@pytest.mark.parametrize("name,expected", [
    ("Jane Doe", ("Doe", "Jane")), ("Doe, Jane", ("Doe", "Jane")),
    ("Mary Ann Smith", ("Smith", "Mary Ann")), ("Madonna", ("Madonna", "")), ("", ("", "")),
])
def test_name_split(name, expected):
    assert rf._split_name(name) == expected


def test_confirmed_tests_tick_their_boxes():
    _, ticks = rf.collect_values(FULL)
    assert {"haem_General_FBC + Platelets", "chem_General_U+E", "imm_Inflammation_CRP"} <= ticks


def test_test_with_no_printed_box_goes_to_other_tests():
    text, ticks = rf.collect_values(_record(tests_required=[
        _test("LFT", "Liver Function Tests"), _test("FBC", "Full Blood Count"),
    ]))
    assert text["other_tests"] == "Liver Function Tests"
    assert "haem_General_FBC + Platelets" in ticks


def test_unconfirmed_tests_are_flagged_never_ticked():
    text, ticks = rf.collect_values(_record(tests_required=[
        {"raw": "TB", "status": "ambiguous"}, {"raw": "xyz", "status": "unrecognized"},
    ]))
    assert text["other_tests"] == "TB [unconfirmed], xyz [unconfirmed]"
    assert not ticks


LIPO = "Lipogram (HDL, LDL, total cholesterol & triglycerides)"


def test_shared_catalogue_name_uses_what_was_said():
    assert rf._pick_boxes(LIPO, "triglycerides") == ["chem_Lipids_Triglycerides"]
    assert rf._pick_boxes(LIPO, "HDL cholesterol") == ["chem_Lipids_HDL cholesterol"]
    assert rf._pick_boxes(LIPO, "lipogram") == ["chem_Lipids_Lipogram"]
    assert rf._pick_boxes(LIPO, "cholesterol panel") == ["chem_Lipids_Lipogram"]  # default


def test_glucose_never_guesses_fasting_vs_random():
    assert rf._pick_boxes("Glucose (plasma)", "glucose") is None
    assert rf._pick_boxes("Glucose (plasma)", "fasting glucose") == ["chem_Diabetes / Metabolic_Glucose fasting"]
    text, ticks = rf.collect_values(_record(tests_required=[_test("glucose", "Glucose (plasma)")]))
    assert text["other_tests"] == "Glucose (plasma)" and not ticks


def test_every_box_and_text_field_exists_on_the_template():
    fields = PdfReader(str(rf.TEMPLATE_PATH)).get_fields()
    for box, _ in rf._TEST_BOXES:
        assert box in fields, box
    text, ticks = rf.collect_values(FULL)
    for name in [*text, *ticks]:
        assert name in fields, name


def test_canonical_table_matches_frontend_test_table():
    js = (REPO / "frontend" / "test-table-data.js").read_text(encoding="utf-8")
    js_canon = set(re.findall(r'canonical:\s*"((?:[^"\\]|\\.)*)"', js))
    js_canon |= set(re.findall(r'aliases:\s*\[\s*"([^"]+)"', js))
    allowed_extra = {"Complement C4"}  # the form's C4 box; the web table's "C3, C4" cell ticks C3
    for _, canons in rf._TEST_BOXES:
        for c in canons:
            assert c in js_canon or c in allowed_extra, c


def test_built_pdf_has_the_values_and_ticks():
    reader = PdfReader(io.BytesIO(rf.build_request_form(FULL)))
    values = {k: v.get("/V") for k, v in reader.get_fields().items()}
    assert values["row_Surname"] == "Doe"
    assert values["row_Ward"] == "Medical Ward 3B"
    assert values["mark_urgent"] == "/Yes" and values["med_warfarin"] == "/Yes"
    assert values["chem_General_U+E"] == "/Yes"
    assert values["chem_General_Sodium"] in (None, "/Off")
    assert values["row_Patient I.D Number"] in (None, "")


def test_long_text_is_shrunk_not_overflowed():
    long = "word " * 120
    record = _record(provisional_diagnosis={"value": long.strip()})
    assert rf.build_request_form(record).startswith(b"%PDF")
    assert rf._fit_size("x" * 40, 100, 12, False) < 8


# --------------------------------------------------------------------------- #
# Endpoint — through the real routes, like test_print_endpoints.py
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    import main
    import print_jobs
    import print_records

    engine = create_engine(f"sqlite:///{tmp_path / 'rf.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    print_jobs.init_db()
    return TestClient(main.app)


def test_endpoint_returns_a_filled_pdf(client):
    body = {
        "doctor_name": "Dr. Thato Manapi", "doctor_phone": "+27821234567", "hpcsa_number": "808080",
        "raw_text": "x", "normalized_text": "x", "structured": FULL["structured"],
    }
    request_id = client.post("/print-label", json=body).json()["request_id"]

    res = client.get(f"/request-form/{request_id}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert request_id in res.headers["content-disposition"]
    values = {k: v.get("/V") for k, v in PdfReader(io.BytesIO(res.content)).get_fields().items()}
    assert values["row_Surname"] == "Doe" and values["chem_General_U+E"] == "/Yes"


def test_endpoint_rejects_bad_and_unknown_ids(client):
    assert client.get("/request-form/not-an-id").status_code == 400
    assert client.get("/request-form/PR260101-AAAA").status_code == 404
