"""
Derives "Tubes Required" from the CONFIRMED tests_required collection
only — never from clinical_history, provisional_diagnosis, medication,
patient/doctor identifiers, dates, or any other transcript field. See
field_extraction.py's extract_fields(), which calls
map_tests_to_specimens() once, after tests_required is normalized, and
stores the result as a NEW, separate `specimen_requirements` key — it
never mutates tests_required or any other field, and nothing here feeds
back into clinical-history/diagnosis/medication normalization. Also called
again, standalone, via POST /specimen-requirements (main.py) whenever
resolving an inline ambiguous test changes tests_required client-side
after the initial pass — see frontend/app.js's resolveAmbiguousItem().

Covers both tube-colour test names AND the subset of
terminology_normalize.AMBIGUOUS_ABBREVIATIONS candidates that are
genuinely orderable lab tests (e.g. "TB" resolved to "Total Bilirubin"),
so resolving an ambiguous test lands on its real specimen requirement
rather than defaulting to "Unmapped". Candidates that are diagnoses,
symptoms, treatments, or devices (e.g. "Cancer", "Chest Pain",
"Discontinue") are deliberately left out — they aren't lab tests, so
there's genuinely no specimen requirement to show.

The mapping is a small, HAND-CURATED dictionary (same trusted model as
terminology_normalize.ABBREVIATIONS), not derived from
backend/data/nhls_tests.json's `specimen_type` field. That field was
checked directly against this feature's own worked example and found
unreliable: "Full Blood Count" incorrectly shows specimen_type
"Specimen from potentially infected site in a universal container" (a
clear PDF-extraction row-misalignment artifact — FBC is a standard
EDTA/purple-top draw), and panel names like "Urea and Electrolytes" or
"Liver Function Tests" don't exist as single rows in that data at all
(only their individual components do). Building a colour-coded tube
guide on top of data with confirmed silent errors would be a real
patient-safety risk (wrong tube = rejected specimen or invalid result),
so this uses verified standard phlebotomy tube-colour convention instead,
seeded from the canonical test names already in
terminology_normalize.ABBREVIATIONS.

Deliberately NOT mapped here (left "Unmapped" rather than guessed):
- Tests whose correct tube is genuinely lab/convention-variable (ESR,
  Crossmatch, Group and Save, Type and Screen — blood bank samples often
  have specific positive-ID labelling requirements beyond just a colour).
- Anything that isn't a venous blood draw at all — imaging/procedures
  (ECG, Echo, EEG, EMG, MRI, Lumbar Puncture, Pulmonary Function Test,
  Esophagogastroduodenoscopy), a skin test (PPD/Tuberculin), or a
  non-blood specimen (Microscopy/Culture/Sensitivity, Midstream Urine,
  Acid-Fast Bacilli — sputum) are structurally impossible to force into a
  "tube colour" without being wrong, so they're just never in this table.
This is a starting set, not a final vocabulary — the same "extensible,
no engine change needed to grow it" shape as ABBREVIATIONS.
"""

from text_normalize import normalize_text

UNMAPPED_LABEL = "Unmapped — verify specimen requirements"

# {canonical test name (as it appears in tests_required's "normalized"
# field) -> display label for the group it belongs to}. Keys are matched
# case-insensitively (see _key()).
SPECIMEN_GROUPS: dict[str, str] = {
    # ---- PURPLE — EDTA (haematology / whole blood cell counts) --------- #
    "full blood count": "PURPLE — EDTA",
    "full blood examination": "PURPLE — EDTA",
    "white cell count": "PURPLE — EDTA",
    "platelet count": "PURPLE — EDTA",
    "haemoglobin": "PURPLE — EDTA",
    "haematocrit": "PURPLE — EDTA",
    "reticulocyte count": "PURPLE — EDTA",
    "red cell count": "PURPLE — EDTA",
    "red cell distribution width": "PURPLE — EDTA",
    "mean corpuscular volume": "PURPLE — EDTA",
    "mean corpuscular haemoglobin": "PURPLE — EDTA",
    "mean corpuscular haemoglobin concentration": "PURPLE — EDTA",
    "absolute neutrophil count": "PURPLE — EDTA",
    "absolute lymphocyte count": "PURPLE — EDTA",
    "neutrophils": "PURPLE — EDTA",
    "lymphocytes": "PURPLE — EDTA",
    "monocytes": "PURPLE — EDTA",
    "eosinophils": "PURPLE — EDTA",
    "basophils": "PURPLE — EDTA",
    "glycated haemoglobin": "PURPLE — EDTA",
    "malaria parasite (blood film)": "PURPLE — EDTA",
    "human leukocyte antigen": "PURPLE — EDTA",

    # ---- BLUE — Sodium Citrate (coagulation) ---------------------------- #
    "prothrombin time": "BLUE — Sodium Citrate",
    "international normalized ratio": "BLUE — Sodium Citrate",
    "activated partial thromboplastin time": "BLUE — Sodium Citrate",
    "thrombin time": "BLUE — Sodium Citrate",
    "d-dimer": "BLUE — Sodium Citrate",
    "fibrinogen": "BLUE — Sodium Citrate",
    "fibrin degradation products": "BLUE — Sodium Citrate",

    # ---- GREY — Fluoride Oxalate (glucose) ------------------------------ #
    "glucose": "GREY — Fluoride Oxalate",
    "fasting blood glucose": "GREY — Fluoride Oxalate",
    "oral glucose tolerance test": "GREY — Fluoride Oxalate",

    # ---- YELLOW — Serum (SST / clot activator) -------------------------- #
    # The large majority of routine chemistry, endocrine, immunology,
    # serology, and tumour-marker tests.
    "c-reactive protein": "YELLOW — Serum",
    "urea": "YELLOW — Serum",
    "urea and electrolytes": "YELLOW — Serum",
    "creatinine": "YELLOW — Serum",
    "estimated glomerular filtration rate": "YELLOW — Serum",
    "sodium": "YELLOW — Serum",
    "potassium": "YELLOW — Serum",
    "chloride": "YELLOW — Serum",
    "bicarbonate": "YELLOW — Serum",
    "anion gap": "YELLOW — Serum",
    "magnesium": "YELLOW — Serum",
    "phosphate": "YELLOW — Serum",
    "liver function tests": "YELLOW — Serum",
    "alanine transaminase": "YELLOW — Serum",
    "aspartate transaminase": "YELLOW — Serum",
    "alkaline phosphatase": "YELLOW — Serum",
    "gamma-glutamyl transferase": "YELLOW — Serum",
    "total protein": "YELLOW — Serum",
    "albumin": "YELLOW — Serum",
    "lactate dehydrogenase": "YELLOW — Serum",
    "thyroid function tests": "YELLOW — Serum",
    "thyroid stimulating hormone": "YELLOW — Serum",
    "free t3": "YELLOW — Serum",
    "free t4": "YELLOW — Serum",
    "troponin": "YELLOW — Serum",
    "creatine kinase": "YELLOW — Serum",
    "creatine kinase mb": "YELLOW — Serum",
    "b-type natriuretic peptide": "YELLOW — Serum",
    "n-terminal pro b-type natriuretic peptide": "YELLOW — Serum",
    "prostate-specific antigen": "YELLOW — Serum",
    "carcinoembryonic antigen": "YELLOW — Serum",
    "alpha-fetoprotein": "YELLOW — Serum",
    "cancer antigen 125": "YELLOW — Serum",
    "cancer antigen 15-3": "YELLOW — Serum",
    "cancer antigen 19-9": "YELLOW — Serum",
    "human chorionic gonadotropin": "YELLOW — Serum",
    "antinuclear antibody": "YELLOW — Serum",
    "rheumatoid factor": "YELLOW — Serum",
    "antineutrophil cytoplasmic antibody": "YELLOW — Serum",
    "complement c3": "YELLOW — Serum",
    "complement c4": "YELLOW — Serum",
    "ferritin": "YELLOW — Serum",
    "iron": "YELLOW — Serum",
    "total iron binding capacity": "YELLOW — Serum",
    "transferrin": "YELLOW — Serum",
    "transferrin saturation": "YELLOW — Serum",
    "vitamin b12": "YELLOW — Serum",
    "folate": "YELLOW — Serum",
    "vitamin d": "YELLOW — Serum",
    "parathyroid hormone": "YELLOW — Serum",
    "cortisol": "YELLOW — Serum",
    "adrenocorticotropic hormone": "YELLOW — Serum",
    "follicle stimulating hormone": "YELLOW — Serum",
    "luteinizing hormone": "YELLOW — Serum",
    "prolactin": "YELLOW — Serum",
    "growth hormone": "YELLOW — Serum",
    "insulin-like growth factor 1": "YELLOW — Serum",
    "aldosterone": "YELLOW — Serum",
    "plasma renin activity": "YELLOW — Serum",
    "cholesterol": "YELLOW — Serum",
    "triglycerides": "YELLOW — Serum",
    "hdl cholesterol": "YELLOW — Serum",
    "ldl cholesterol": "YELLOW — Serum",
    "hepatitis b surface antigen": "YELLOW — Serum",
    "hepatitis b surface antibody": "YELLOW — Serum",
    "hepatitis c virus antibody": "YELLOW — Serum",
    "rapid plasma reagin": "YELLOW — Serum",
    "venereal disease research laboratory test": "YELLOW — Serum",
    "treponema pallidum haemagglutination assay": "YELLOW — Serum",
    "cytomegalovirus": "YELLOW — Serum",
    "epstein-barr virus": "YELLOW — Serum",
    "varicella zoster virus": "YELLOW — Serum",
    "herpes simplex virus": "YELLOW — Serum",
    "cryptococcal antigen": "YELLOW — Serum",
    "osmolality": "YELLOW — Serum",
    "ammonia": "YELLOW — Serum",
    "amylase": "YELLOW — Serum",
    "lipase": "YELLOW — Serum",
    "haptoglobin": "YELLOW — Serum",
    "copper": "YELLOW — Serum",
    "zinc": "YELLOW — Serum",
    "angiotensin-converting enzyme": "YELLOW — Serum",
    "glucose-6-phosphate dehydrogenase": "YELLOW — Serum",

    # ---- Blood gas — not a coloured venous tube ------------------------- #
    "arterial blood gas": "BLOOD GAS SYRINGE — Heparinized",
    "venous blood gas": "BLOOD GAS SYRINGE — Heparinized",
    "ph": "BLOOD GAS SYRINGE — Heparinized",

    # ---- Resolved ambiguous-abbreviation candidates ---------------------- #
    # terminology_normalize.AMBIGUOUS_ABBREVIATIONS entries that genuinely
    # ARE orderable lab tests with a real, confident specimen requirement
    # (e.g. "TB" resolved to "Total Bilirubin", not "Tuberculosis") — so a
    # doctor resolving one of these inline no longer lands in "Unmapped"
    # for no reason. The candidates that are diagnoses/symptoms/treatments/
    # devices (e.g. "Cancer", "Chest Pain", "Discontinue") are deliberately
    # NOT added here — they aren't lab tests at all, so there's no
    # specimen requirement to show, honestly, not a gap to fill.
    "total bilirubin": "YELLOW — Serum",
    "uric acid": "YELLOW — Serum",
    "calcium": "YELLOW — Serum",
    "blood sugar": "GREY — Fluoride Oxalate",
    "c-peptide": "YELLOW — Serum",
    "complement fixation": "YELLOW — Serum",
    "western blot": "YELLOW — Serum",

    # ---- Urine — not a coloured venous tube ------------------------------ #
    "urinalysis": "URINE CONTAINER — Sterile",
    "protein creatinine ratio": "URINE CONTAINER — Sterile",
    "phencyclidine": "URINE CONTAINER — Sterile",  # toxicology screen
    # First-catch urine NAAT is the standard recommended specimen for
    # these two (per CDC/WHO STI testing guidance) — confident enough to
    # map, unlike e.g. "Tuberculosis" bare, where the right specimen
    # genuinely depends on which TB test is meant (sputum vs blood IGRA
    # vs skin test), so that one stays Unmapped rather than guessed.
    "chlamydia trachomatis": "URINE CONTAINER — Sterile",
    "gonococcus (neisseria gonorrhoeae)": "URINE CONTAINER — Sterile",

    # ---- Blood glucose monitoring — same handling as Glucose ------------- #
    "blood glucose monitoring": "GREY — Fluoride Oxalate",

    # ---- Bone marrow — not a coloured venous tube ------------------------ #
    "bone marrow": "BONE MARROW — Aspirate/Biopsy Kit",
}


def _key(name: str) -> str:
    return normalize_text(name)


def map_tests_to_specimens(tests_required: list[dict]) -> list[dict]:
    """Groups the CONFIRMED items of tests_required (status == "confirmed"
    only — ambiguous/unrecognized items are excluded, never guessed at)
    by their specimen/tube requirement. Returns a list of
    {"label": str, "tests": [str, ...]} groups, in first-seen order, with
    an "Unmapped — verify specimen requirements" group for any confirmed
    test this curated dictionary doesn't (yet) cover — never silently
    dropped, never guessed. Reads only `tests_required`; never touches or
    reads any other field."""
    order: list[str] = []
    grouped: dict[str, list[str]] = {}

    for item in tests_required or []:
        if item.get("status") != "confirmed":
            continue
        name = item.get("normalized")
        if not name:
            continue
        label = SPECIMEN_GROUPS.get(_key(name), UNMAPPED_LABEL)
        if label not in grouped:
            grouped[label] = []
            order.append(label)
        if name not in grouped[label]:  # a test requested twice isn't listed twice
            grouped[label].append(name)

    return [{"label": label, "tests": grouped[label]} for label in order]
