"""
Fills the NHLS "Specimen Request Form" PDF (backend/templates/Request_Form.pdf,
an AcroForm with 278 fillable fields) from a finalized request.

SCOPE — deliberately limited to what the web page actually captures. The
form has many more fields than the app collects (patient address, medical
aid, ICD-10, Pap-smear / histology pages, microbiology specimen-type boxes,
...). Those are left blank on purpose, "for future reference": they appear
in NOT_YET_CAPTURED below so extending the app later is a matter of adding
a mapping, not rediscovering what the form contains. Nothing here ever
invents a value for a field the doctor didn't dictate.

What IS filled (source -> PDF field):
  patient_name           -> row_Surname / row_First Name (split, see _split_name)
  patient_id             -> row_Patient Hospital Number
  date_of_birth          -> row_D.O.B.            (only if confirmed)
  hospital               -> row_Hospital/Clinic
  ward                   -> row_Ward
  reason / provisional diagnosis / clinical history
                         -> diagnosis_reason       (one combined box)
  medication             -> med_warfarin / med_heparin tick boxes
  priority               -> mark_urgent tick box   (urgent/stat/emergency)
  specimen_type + site   -> row_Type of Specimen
  date_collected         -> row_Date Taken         (only if confirmed)
  time_collected         -> row_Time               (only if confirmed)
  requesting doctor      -> row_Requesting Health Care Worker
  hpcsa_number           -> row_HPCSA/SANC Number
  doctor phone           -> row_Contact Numbers
  tests_required         -> the test tick boxes (see _TEST_BOXES), and any
                            confirmed test the printed form has no box for
                            is written into the "Other tests" box instead.

Test ticking is driven by the CONFIRMED canonical name of each test (the
same `normalized` value test-table-render.js compares against), so it
can only tick a box the doctor's confirmed request actually maps to. The
(canonical -> box) table below is generated from frontend/test-table-data.js
and checked against it by tests/test_request_form.py, so the two can't
silently drift apart.
"""

import io
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "Request_Form.pdf"

# ---------------------------------------------------------------------------
# Test tick boxes: (PDF checkbox field, canonical names that tick it).
# Generated from frontend/test-table-data.js's `canonical` values.
# ---------------------------------------------------------------------------
_TEST_BOXES: list[tuple[str, tuple[str, ...]]] = [
    ('chem_General_Blood gases', ('Blood gas',)),
    ('chem_General_U+E', ('Urea and Electrolytes',)),
    ('chem_General_Sodium', ('Sodium (serum)',)),
    ('chem_General_Potassium', ('Potassium (serum)',)),
    ('chem_General_Chloride', ('Chloride (serum)',)),
    ('chem_General_Urea', ('Urea',)),
    ('chem_General_Creatinine', ('Creatinine (serum)',)),
    ('chem_General_Calcium', ('Calcium (total)',)),
    ('chem_General_Magnesium', ('Magnesium (serum)',)),
    ('chem_General_Inorganic phosphate', ('Phosphate (serum)',)),
    ('chem_General_Total protein', ('Protein (total) (serum)',)),
    ('chem_General_Albumin', ('Albumin (serum)',)),
    ('chem_General_Conjugated bilirubin', ('Bilirubin (conjugated/direct)',)),
    ('chem_General_ALP', ('Alkaline Phosphatase',)),
    ('chem_General_GGT', ('Gamma-Glutamyl Transferase',)),
    ('chem_General_ALT', ('Alanine Transaminase',)),
    ('chem_General_AST', ('Aspartate Transaminase',)),
    ('chem_General_LDH', ('Lactate Dehydrogenase',)),
    ('chem_General_Amylase', ('Amylase (serum)',)),
    ('chem_General_Lipase', ('Lipase',)),
    ('chem_General_Pseudocholinesterase', ('Cholinesterase (pseudo) (serum)',)),
    ('chem_General_Red cell cholinesterase', ('Cholinesterase (red cell)',)),
    ('chem_Tumour Markers_Alpha feto protein', ('Alpha-1-Fetoprotein [Mass/volume] in Body fluid',)),
    ('chem_Tumour Markers_PSA', ('Prostate-Specific Antigen',)),
    ('chem_Tumour Markers_CEA', ('Carcinoembryonic Antigen',)),
    ('chem_Endocrinology_Thyroid function', ('Thyroid Function Tests',)),
    ('chem_Endocrinology_TSH', ('Thyroid Stimulating Hormone',)),
    ('chem_Endocrinology_Free T4', ('Free T4 (Thyroxine)',)),
    ('chem_Endocrinology_Free T3', ('Free T3 (Tri-iodothyronine)',)),
    ('chem_Endocrinology_FSH', ('Follicle Stimulating Hormone',)),
    ('chem_Endocrinology_Estradiol', ('Oestradiol (E2)',)),
    ('chem_Endocrinology_LH', ('Luteinizing Hormone',)),
    ('chem_Endocrinology_Progesterone', ('Progesterone',)),
    ('chem_Endocrinology_Prolactin', ('Prolactin',)),
    ('chem_Endocrinology_Testosterone', ('Testosterone',)),
    ('chem_Endocrinology_SHBG', ('Sex hormone binding globulin (SHBG)',)),
    ('chem_Endocrinology_PTH', ('Parathyroid Hormone',)),
    ('chem_Endocrinology_Cortisol', ('Cortisol (serum)',)),
    ('chem_Endocrinology_Insulin', ('Insulin',)),
    ('chem_Cardiac / Muscle_Aldolase', ('Aldolase',)),
    ('chem_Cardiac / Muscle_Creatine kinase', ('Creatine kinase',)),
    ('chem_Cardiac / Muscle_CK-MB fraction', ('Creatine kinase MB (CK-MB)',)),
    ('chem_Cardiac / Muscle_Troponin', ('Troponin I',)),
    ('chem_Diabetes / Metabolic_Osmolality', ('Osmolality (serum)',)),
    ('chem_Diabetes / Metabolic_Glucose fasting', ('Glucose (plasma)',)),
    ('chem_Diabetes / Metabolic_Glucose random', ('Glucose (plasma)',)),
    ('chem_Diabetes / Metabolic_HbA1c', ('Glycated Haemoglobin',)),
    ('chem_Diabetes / Metabolic_Lactate', ('Lactate (plasma)',)),
    ('chem_Lipids_Lipogram', ('Lipogram (HDL, LDL, total cholesterol & triglycerides)',)),
    ('chem_Lipids_Triglycerides', ('Lipogram (HDL, LDL, total cholesterol & triglycerides)',)),
    ('chem_Lipids_Total cholesterol', ('Cholesterol (serum) (total)',)),
    ('chem_Lipids_HDL cholesterol', ('Lipogram (HDL, LDL, total cholesterol & triglycerides)',)),
    ('chem_Lipids_LDL cholesterol', ('Lipogram (HDL, LDL, total cholesterol & triglycerides)',)),
    ('chem_Anaemia_Iron studies', ('Iron',)),
    ('chem_Anaemia_Iron', ('Iron',)),
    ('chem_Anaemia_Transferrin', ('Transferrin',)),
    ('chem_Anaemia_Ferritin', ('Ferritin',)),
    ('chem_Anaemia_Vitamin B12', ('Vitamin B12',)),
    ('chem_Anaemia_Haptoglobin', ('Haptoglobin',)),
    ('chem_Urine_Na', ('Sodium (urine)',)),
    ('chem_Urine_K', ('Potassium (urine)',)),
    ('chem_Urine_Cl', ('Chloride (urine)',)),
    ('chem_Urine_Urea', ('Urea (urine)',)),
    ('chem_Urine_Creat', ('Creatinine (urine)',)),
    ('chem_Urine_Protein S', ('Protein (total) (urine)',)),
    ('chem_Urine_BJP', ('Protein electrophoresis (Bence Jones protein) (urine)',)),
    ('chem_Urine_Microalbumin', ('Microalbumin [Mass/time] in 24 hour Urine by Detection limit <= 3.0 mg/L',)),
    ('chem_Urine_Creat clearance', ('Creatinine clearance',)),
    ('chem_Urine_Dipstick urinalysis', ('Urinalysis (dipsticks)',)),
    ('chem_Urine_VMA/NMA/HVA', ('Vanillyl mandelic acid (VMA) (quantitative)', 'Homovanillic acid (HVA)')),
    ('chem_Urine_Osmolality', ('Osmolality (urine)',)),
    ('chem_Cerebrospinal Fluid_CSF chemistry', ('Fructose/Creatinine [Molar ratio] in Urine',)),
    ('chem_Cerebrospinal Fluid_CSF ADA', ('Adenosine deaminase (CSF)',)),
    ('chem_Other_Coombs', ('Coombs (Direct - screening)',)),
    ('chem_Other_ABO', ('Blood Grouping: ABO Blood',)),
    ('chem_Other_RH', ('Blood Grouping: Rhesus (Rh typing)',)),
    ('drugs_Paracetamol', ('Acetaminophen (Paracetamol)',)),
    ('drugs_Lithium', ('Lithium',)),
    ('drugs_Tricyclic antidepressants', ('Tricyclic antidepressants (serum)',)),
    ('drugs_Phenytoin', ('Phenytoin (Epanutin)',)),
    ('drugs_Phenobarbitone', ('Phenobarbital (Gardenal)',)),
    ('drugs_Valproate', ('Valproate, sodium (Convulex or Epilim)',)),
    ('drugs_Carbamazepine', ('Carbamazepine (Tegretol)',)),
    ('drugs_Digoxin', ('Digoxin (Lanoxin)',)),
    ('drugs_Theophylline', ('Theophylline',)),
    ('drugs_Urine mandrax', ('Mandrax (methaqualone)',)),
    ('drugs_Salicylates', ('Salicylates [Mass/volume] in Serum or Plasma',)),
    ('drugs_Vancomycin', ('Vancomycin',)),
    ('haem_General_FBC + Platelets', ('Full Blood Count', 'Full Blood Examination')),
    ('haem_General_Differential count', ('Differential white cell count',)),
    ('haem_General_Morphology', ('Morphology',)),
    ('haem_General_Haemoglobin', ('Haemoglobin',)),
    ('haem_General_White cell count', ('White cell count and white cell differential',)),
    ('haem_General_Platelet count', ('Platelet count',)),
    ('haem_General_Reticulocytes', ('Reticulocyte count',)),
    ('haem_General_ESR', ('Erythrocyte Sedimentation Rate',)),
    ('haem_General_CD4 count', ('Immunophenotyping (Flow cytometry): CD4',)),
    ('haem_Coagulation_INR', ('International Normalized Ratio',)),
    ('haem_Coagulation_PTT', ('Activated Partial Thromboplastin Time',)),
    ('haem_Coagulation_Fibrinogen', ('Fibrinogen',)),
    ('haem_Coagulation_D Dimers', ('Fibrin D-dimer [Presence] in Platelet poor plasma by Latex agglutination',)),
    ('haem_Coagulation_Anti thrombin', ('Anti - Thrombin',)),
    ('haem_Coagulation_Protein C', ('Protein C',)),
    ('haem_Coagulation_Protein S', ('Protein S',)),
    ('haem_Coagulation_Lupus anticoagulant', ('Lupus Anticoagulant',)),
    ('haem_Coagulation_Thrombin time', ('Thrombin Time',)),
    ('imm_Inflammation_CRP', ('C-reactive protein',)),
    ('imm_Inflammation_IgG', ('Immunoglobulin G (IgG)',)),
    ('imm_Inflammation_IgA', ('Immunoglobulin A (IgA)',)),
    ('imm_Inflammation_IgM', ('Immunoglobulin M (IgM)',)),
    ('imm_Inflammation_SPEP', ('Protein electrophoresis (serum)',)),
    ('imm_Inflammation_C3', ('Complement C3',)),
    ('imm_Inflammation_C4', ('Complement C4',)),
    ('imm_Allergology_Total IgE', ('Immunoglobulin E (IgE Total)',)),
    ('imm_Auto Immune_RF', ('Rheumatoid Factor',)),
    ('imm_Auto Immune_ANA', ('Antinuclear Antibody',)),
    ('imm_Auto Immune_ENA', ('Antigen (ENA) Antibodies',)),
    ('imm_Auto Immune_Anti double stranded DNA', ('Anti - double stranded DNA',)),
    ('imm_Auto Immune_Anti cardiolipin', ('Anti - Cardiolipin (ACLA)',)),
    ('imm_Auto Immune_Anti mitochondria', ('Anti - Mitochondrial',)),
    ('imm_Auto Immune_Anti smooth muscle', ('Anti - Smooth Muscle',)),
    ('microinv_Parasites', ('Ova and parasites identified in Stool by Baermann concentration',)),
    ('microinv_Malaria', ('Malaria rapid screen',)),
    ('microinv_Anaerobic culture', ('Microorganism preliminary growth [Presence] in Blood by Anaerobic culture with antimicrobial neutralizer',)),
    ('microinv_TB culture', ('TB (Tuberculosis): Culture & sensitivity',)),
    ('microinv_TB sensitivity', ('TB (Tuberculosis): Culture & sensitivity',)),
    ('microbact_Chlamydia PCR (urine/swab)', ('Chlamydia polyvalent',)),
    ('microbact_C. difficile toxin ELISA', ('Clostridioides difficile Ab [Units/volume] in Serum',)),
    ('microbact_ASOT', ('Streptolysin O Ab [Mass/volume] in Serum',)),
    ('microbact_Anti-DNAse B', ('Anti - DNase B titre',)),
    ('microbact_RPR', ('Rapid Plasma Reagin',)),
    ('microbact_TPHA', ('Treponema Pallidum Haemagglutination Assay',)),
    ('microbact_FTA', ('Syphilis: Fluorescent treponemal antibody (FTA)',)),
    ('microbact_VDRL', ('Venereal Disease Research Laboratory Test',)),
    ('microbact_Amoebic IHA', ('Entamoeba histolytica IgG',)),
    ('microbact_Hydatid IHA', ('Echinococcus Antibodies',)),
    ('microbact_Cysticercosis IgG', ('Taenia solium larva Ab [Units/volume] in Serum by Hemagglutination',)),
    ('microbact_Pneumocystis IFA', ('Pneumocystis jiroveci (PCP) antigen detection (IFA)',)),
    ('microbact_Mycoplasma ELISA', ('Mycoplasma IgG & IgM',)),
    ('microbact_Rickettsia IFA', ('Rickettsia conorii (tick-bite fever) Antibodies (IFA)',)),
    ('hep_Clinical hepatitis A', ('Hepatitis A virus (HAV) IgM',)),
    ('hep_Clinical hepatitis B', ('Hepatitis B virus (HBV) surface Antigen (HBsAg)',)),
    ('hep_Clinical hepatitis C', ('Hepatitis C Total Antibody',)),
    ('hep_Hepatitis B Ab', ('Hepatitis B virus (HBV) surface Antibodies (anti-HBs)',)),
    ('hep_Hepatitis B Ag', ('Hepatitis B virus (HBV) surface Antigen (HBsAg)',)),
    ('hiv_HIV ELISA', ('HIV EIA (3rd generation)',)),
    ('hiv_HIV rapid', ('HIV rapid (4th generation)',)),
    ('hiv_HIV Viral load', ('HIV-1 viral load',)),
    ('torch_Toxo IgG', ('Toxoplasma gondii IgG',)),
    ('torch_Toxo IgM', ('Toxoplasma gondii IgM',)),
    ('torch_Rubella IgG', ('Rubella virus IgG Antibodies',)),
    ('torch_Rubella IgM', ('Rubella virus IgM Antibodies',)),
    ('torch_CMV IgG', ('Cytomegalovirus (CMV) IgG',)),
    ('torch_CMV IgM', ('Cytomegalovirus (CMV) IgM',)),
    ('torch_Herpes IgG', ('Herpes simplex virus (HSV) types 1 & 2 IgG antibodies',)),
    ('torch_Herpes IgM', ('Herpes simplex virus (HSV) types 1 & 2 IgM antibodies',)),
    ('othersero_EBV IgG', ('Epstein-Barr virus (EBV) IgG',)),
    ('othersero_EBV IgM', ('Epstein-Barr virus (EBV) IgM',)),
    ('othersero_VZV IgG', ('Varicella zoster virus IgG Ab index [Units/volume] in Serum and CSF',)),
    ('othersero_VZV IgM', ('Varicella zoster virus IgM Ab [Units/volume] in Serum by Immunoassay',)),
    ('othersero_Measles IgG', ('Measles virus IgG Antibodies',)),
    ('othersero_Measles IgM', ('Measles virus IgM Antibodies for measles surveillance',)),
    ('othersero_Mumps IgG', ('Mumps virus IgG Antibodies',)),
    ('othersero_Mumps IgM', ('Mumps virus IgM Antibodies',)),
]

# A few canonical names are shared by several printed boxes (the catalogue
# doesn't distinguish them). Rather than guess, these are resolved by what
# the doctor actually said (see _pick_box), else by the entry below, else
# the test goes to "Other tests" for the pathologist to read.
#   value = PDF field to tick by default, None = never guess (Other tests),
#   "ALL" = tick every sharing box (the one catalogue test is genuinely
#   all of them).
_SHARED_DEFAULT = {
    "Lipogram (HDL, LDL, total cholesterol & triglycerides)": "chem_Lipids_Lipogram",
    "Iron": "chem_Anaemia_Iron",
    "Hepatitis B virus (HBV) surface Antigen (HBsAg)": "hep_Hepatitis B Ag",
    "Glucose (plasma)": None,
    "TB (Tuberculosis): Culture & sensitivity": "ALL",
}

# Form content the web page does not capture yet — left blank on purpose.
NOT_YET_CAPTURED = [
    "row_Patient I.D Number", "row_Class", "patient_address", "row_Tel No.", "row_Race",
    "row_Age", "sex_m / sex_f", "icd10", "row_Medical Aid*", "row_Dep Code", "row_Employer",
    "row_Account To / Principal Member", "member_address", "row_Member Tel No. (H)",
    "row_Member I.D. Number", "row_Taken By", "row_E-mail Address", "row_Signature",
    "Microbiology specimen-type boxes (microspec_*) and fluid_specify",
    "Lab-use boxes (row_Date Received, row_Received In Lab By, row_Signature_2)",
    "Page 5-6 histology / cytology / gynae fields (spec_*, type_origin, clinical_info, "
    "first_spec_*, row_Previous *, prev_treatment, row_LMP, row_Parity, row_Pregnant, "
    "row_Postmenopausal, smear_*, takenfrom_*, cervix_*, wound_site)",
    "Tests whose catalogue match is generic/absent on the web table (CMP, DIC screen, ...)",
]

_BOX_BY_FIELD = {field: canon for field, canon in _TEST_BOXES}
_BOXES_BY_CANON: dict[str, list[str]] = {}
for _field, _canons in _TEST_BOXES:
    for _c in _canons:
        _BOXES_BY_CANON.setdefault(_c, []).append(_field)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _box_label(field: str) -> str:
    return field.rsplit("_", 1)[1]


def _pick_boxes(canonical: str, raw: str) -> list[str] | None:
    """Which PDF box(es) a confirmed test ticks. None -> no safe box
    (caller writes the test into "Other tests")."""
    boxes = _BOXES_BY_CANON.get(canonical)
    if not boxes:
        return None
    if len(boxes) == 1:
        return boxes
    # Several printed boxes share this catalogue name — prefer the one whose
    # own printed label is fully contained in the words the doctor said.
    raw_tokens = _tokens(raw)
    scored = [(len(_tokens(_box_label(b))), b) for b in boxes if _tokens(_box_label(b)) <= raw_tokens]
    if scored:
        return [max(scored)[1]]
    default = _SHARED_DEFAULT.get(canonical)
    if default == "ALL":
        return boxes
    if default:
        return [default]
    return None


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------
def _field_value(structured: dict, key: str) -> str:
    field = structured.get(key)
    if isinstance(field, dict):
        return str(field.get("value") or "").strip()
    return ""


def _confirmed(structured: dict, key: str) -> str:
    if structured.get(f"{key}_status") == "confirmed" and structured.get(key):
        return str(structured[key])
    return ""


def _split_name(full_name: str) -> tuple[str, str]:
    """(surname, first names). The dictation captures one free-text name, the
    form has two boxes. "Surname, First" is honoured; otherwise the LAST word
    is taken as the surname and everything before it as first name(s). A
    single word goes in Surname. Multi-word surnames ("van der Merwe") will
    split imperfectly — the form stays editable for the lab to correct."""
    name = " ".join(full_name.split())
    if not name:
        return "", ""
    if "," in name:
        surname, _, first = name.partition(",")
        return surname.strip(), first.strip()
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _dob_display(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else iso


_URGENT_RE = re.compile(r"\b(urgent|stat|emergency|immediate|asap)\b", re.I)
_NOT_URGENT_RE = re.compile(r"\b(non[- ]?urgent|not urgent|routine)\b", re.I)


def _is_urgent(priority: str) -> bool:
    return bool(_URGENT_RE.search(priority)) and not _NOT_URGENT_RE.search(priority)


def _join(parts: list[tuple[str, str]]) -> str:
    return "  ".join(f"{label}: {value.rstrip('. ')}." for label, value in parts if value)


def collect_values(record: dict) -> tuple[dict[str, str], set[str]]:
    """(text field values, checkbox fields to tick) for one stored record.
    Pure — no PDF involved — so it is directly unit-testable."""
    s = record.get("structured") or {}
    text: dict[str, str] = {}
    ticks: set[str] = set()

    surname, first = _split_name(_field_value(s, "patient_name"))
    text["row_Surname"] = surname
    text["row_First Name"] = first
    text["row_Patient Hospital Number"] = _field_value(s, "patient_id")
    text["row_D.O.B."] = _dob_display(_confirmed(s, "date_of_birth"))
    text["row_Hospital/Clinic"] = _field_value(s, "hospital")
    text["row_Ward"] = _field_value(s, "ward")

    text["diagnosis_reason"] = _join([
        ("Provisional diagnosis", _field_value(s, "provisional_diagnosis")),
        ("Reason", _field_value(s, "reason_for_request")),
        ("Clinical history", _field_value(s, "clinical_history")),
    ])

    specimen = _field_value(s, "specimen_type")
    site = _field_value(s, "specimen_site")
    text["row_Type of Specimen"] = f"{specimen} ({site})" if specimen and site else (specimen or site)
    text["row_Date Taken"] = _dob_display(_confirmed(s, "date_collected"))
    text["row_Time"] = _confirmed(s, "time_collected")

    text["row_Requesting Health Care Worker"] = _field_value(s, "requesting_doctor") or (record.get("doctor_name") or "")
    text["row_HPCSA/SANC Number"] = _field_value(s, "hpcsa_number") or (record.get("hpcsa_number") or "")
    text["row_Contact Numbers"] = record.get("doctor_phone") or ""

    medication = _field_value(s, "medication")
    if re.search(r"\bwarfarin\b", medication, re.I):
        ticks.add("med_warfarin")
    if re.search(r"\bheparin\b", medication, re.I):
        ticks.add("med_heparin")
    if _is_urgent(_field_value(s, "priority")):
        ticks.add("mark_urgent")

    other: list[str] = []
    for item in s.get("tests_required") or []:
        raw = str(item.get("raw") or "")
        if item.get("status") == "confirmed":
            canonical = str(item.get("normalized") or "")
            boxes = _pick_boxes(canonical, raw)
            if boxes:
                ticks.update(boxes)
            elif canonical not in other:
                other.append(canonical)
        elif raw:
            flagged = f"{raw} [unconfirmed]"
            if flagged not in other:
                other.append(flagged)
    text["other_tests"] = ", ".join(other)

    return {k: v for k, v in text.items() if v}, ticks


# ---------------------------------------------------------------------------
# PDF writing
# ---------------------------------------------------------------------------
def _safe(value: str) -> str:
    """The form's Helvetica is WinAnsi — anything outside it would render as
    garbage, so substitute rather than crash."""
    return value.encode("cp1252", "replace").decode("cp1252")


def _fit_size(value: str, width: float, height: float, multiline: bool) -> float:
    """Largest font size (8 down to 5) at which the text, wrapped, fits the
    field box — Helvetica averages ~0.5 em per character; 0.53 is used so the
    estimate errs on the cramped side."""
    for size in (8, 7.5, 7, 6.5, 6, 5.5, 5):
        chars_per_line = max(1, int((width - 4) / (size * 0.53)))
        if multiline:
            lines = sum(max(1, -(-len(p) // chars_per_line)) for p in value.split("\n"))
            if lines * size * 1.15 <= height - 2:
                return size
        elif len(value) <= chars_per_line:
            return size
    return 5


def _draw_tick(annot) -> None:
    """The template's own "on" appearance is a tiny glyph that is barely
    visible at print size, so a ticked box is given a bold, unmistakable
    tick (box outline + thick check mark) scaled to the box's BBox. The
    field's /V and /AS (set by pypdf) are unaffected — it is still a real
    checkbox the lab can untick."""
    normal = annot["/AP"]["/N"]
    stream = normal["/Yes"].get_object()
    x0, y0, x1, y1 = (float(v) for v in stream.get("/BBox", [0, 0, 8, 8]))
    w, h = x1 - x0, y1 - y0
    p = lambda fx, fy: f"{x0 + fx * w:.3f} {y0 + fy * h:.3f}"
    content = (
        "q 1 g 1 G "
        f"{x0:.3f} {y0:.3f} {w:.3f} {h:.3f} re f Q\n"
        f"q .1 .1 .1 RG 0.75 w {x0 + .375:.3f} {y0 + .375:.3f} {w - .75:.3f} {h - .75:.3f} re S Q\n"
        "q .05 .05 .05 RG 1.4 w 1 J 1 j "
        f"{p(.18, .52)} m {p(.40, .22)} l {p(.84, .82)} l S Q\n"
    )
    stream.set_data(content.encode("ascii"))


def build_request_form(record: dict) -> bytes:
    text_values, ticks = collect_values(record)

    reader = PdfReader(str(TEMPLATE_PATH))
    writer = PdfWriter(clone_from=reader)

    on_state = NameObject("/Yes")
    text_done = set()
    for page in writer.pages:
        for annot_ref in page.get("/Annots", []):
            annot = annot_ref.get_object()
            name = annot.get("/T")
            if name in text_values and name not in text_done:
                value = _safe(text_values[name])
                x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
                multiline = bool(int(annot.get("/Ff", 0)) & 4096)
                size = _fit_size(value, x1 - x0, y1 - y0, multiline)
                annot[NameObject("/DA")] = TextStringObject(f"/Helv {size:g} Tf .1 .1 .1 rg")
                text_done.add(name)
                writer.update_page_form_field_values(page, {name: value}, auto_regenerate=False)
            elif name in ticks:
                _draw_tick(annot)
                writer.update_page_form_field_values(page, {name: on_state}, auto_regenerate=False)

    # NeedAppearances is deliberately NOT set: viewers would then regenerate
    # every checkbox from the template's /MK (a ZapfDingbats glyph some
    # renderers lack, which draws a stray stroke) instead of using the bold
    # tick drawn above. pypdf writes real appearance streams for text fields.
    if "/NeedAppearances" in writer._root_object["/AcroForm"]:
        del writer._root_object["/AcroForm"]["/NeedAppearances"]
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
