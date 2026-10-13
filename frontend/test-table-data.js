// Curated from the NHLS-style pathology request form you attached —
// transcribed cell by cell, then VERIFIED by actually running every probe
// below through the real matching pipeline
// (backend/terminology_normalize.py's normalize_test_item()) and checking
// the RESULT, not just whether it "confirmed" — an earlier pass only
// checked confirmation status and shipped several silently wrong matches
// (e.g. "Thyroid function" fuzzy-matching "Glasgow coma scale", "HIV ELISA"
// matching a diabetes antibody test) that LOOKED confirmed but pointed at
// the wrong test entirely. Never trust confirmation status alone when
// building a static mapping like this one — always check WHAT it resolved to.
//
// Each cell: {label, probe, canonical, note?}
//   - `label` is the text shown on the checkbox — kept matching the form's
//     own printed wording, even where that differs from `probe`.
//   - `probe` is what gets sent to /normalize-test-item when the doctor ticks
//     an empty box — the SAME endpoint any manually-typed "+ Add test" entry
//     already goes through, never a second matching system.
//   - `canonical` is the EXACT normalized name that probe is verified to
//     resolve to right now — this, not `probe`, is what a confirmed
//     tests_required item is matched against to decide if this box is
//     ticked (see test-table-render.js). Keeping these separate is what the
//     bug above was: comparing an abbreviation probe like "U&E" directly
//     against item.normalized ("Urea and Electrolytes") can never match.
//   - `probe`/`canonical` both null — no confident catalogue match was found
//     for this cell (checked directly, not assumed). The checkbox still
//     renders — nothing on the form is silently dropped — and ticking it
//     still goes through the normal resolution endpoint like any other
//     manually-typed test; it just won't auto-tick from dictation. `note`,
//     when present, is a plain (non-alarming) hover tooltip explaining why.
//
// A category with `nonTest: true` (Microbiology's "Specimen Type") isn't a
// set of lab tests at all — it's what sample/container is being sent. Those
// checkboxes are rendered for visual completeness only: never resolved
// against tests_required, never saved anywhere, always non-interactive —
// see test-table-render.js. `specify: true` adds a free-text input next to
// the checkbox (also never persisted) for a cell where the form implies the
// doctor writes in a detail (e.g. which fluid).
const TEST_TABLE_CATEGORIES = [
  {
    category: "Chemical Pathology — General",
    tests: [
      { label: "Blood gases", probe: "Blood gas", canonical: "Blood gas" },
      { label: "U+E", probe: "U&E", canonical: "Urea and Electrolytes" },
      { label: "LFT", probe: "LFT", canonical: "Liver Function Tests" },
      { label: "CMP", probe: null, canonical: null, note: "No catalogue match found under this abbreviation." },
      { label: "UA", probe: "Uric acid (serum)", canonical: "Uric acid (serum)" },
      { label: "Sodium", probe: "Sodium (serum)", canonical: "Sodium (serum)" },
      { label: "Potassium", probe: "Potassium (serum)", canonical: "Potassium (serum)" },
      { label: "Chloride", probe: "Chloride (serum)", canonical: "Chloride (serum)" },
      { label: "Urea", probe: "Urea", canonical: "Urea" },
      { label: "Creatinine", probe: "Creatinine (serum)", canonical: "Creatinine (serum)" },
      { label: "Calcium", probe: "Calcium (total)", canonical: "Calcium (total)" },
      { label: "Magnesium", probe: "Magnesium (serum)", canonical: "Magnesium (serum)" },
      { label: "Inorganic phosphate", probe: "Phosphate (serum)", canonical: "Phosphate (serum)" },
      { label: "Total protein", probe: "Protein (total) (serum)", canonical: "Protein (total) (serum)" },
      { label: "Albumin", probe: "Albumin (serum)", canonical: "Albumin (serum)" },
      { label: "Total bilirubin", probe: "Total bilirubin", canonical: "Bilirubin (total)" },
      { label: "Conjugated bilirubin", probe: "Conjugated bilirubin", canonical: "Bilirubin (conjugated/direct)" },
      { label: "ALP", probe: "ALP", canonical: "Alkaline Phosphatase" },
      { label: "GGT", probe: "GGT", canonical: "Gamma-Glutamyl Transferase" },
      { label: "ALT", probe: "ALT", canonical: "Alanine Transaminase" },
      { label: "AST", probe: "AST", canonical: "Aspartate Transaminase" },
      { label: "LDH", probe: "LDH", canonical: "Lactate Dehydrogenase" },
      { label: "Amylase", probe: "Amylase (serum)", canonical: "Amylase (serum)" },
      { label: "Lipase", probe: "Lipase", canonical: "Lipase" },
      { label: "Pseudocholinesterase", probe: "Cholinesterase (pseudo) (serum)", canonical: "Cholinesterase (pseudo) (serum)" },
      { label: "Red cell cholinesterase", probe: "Cholinesterase (red cell)", canonical: "Cholinesterase (red cell)" },
    ],
  },
  {
    category: "Chemical Pathology — Cardiac/Muscle",
    tests: [
      { label: "Aldolase", probe: "Aldolase", canonical: "Aldolase" },
      { label: "Creatine kinase", probe: "Creatine kinase", canonical: "Creatine kinase" },
      { label: "CK-MB fraction", probe: "Creatine kinase MB (CK-MB)", canonical: "Creatine kinase MB (CK-MB)" },
      { label: "Troponin", probe: "Troponin", canonical: "Troponin I", note: "Catalogue has separate Troponin I / Troponin T entries — this ticks the more commonly ordered Troponin I; add the other by name via \"+ Add test\" if specifically needed." },
    ],
  },
  {
    category: "Chemical Pathology — Diabetes/Metabolic",
    tests: [
      { label: "Osmolality", probe: "Osmolality (serum)", canonical: "Osmolality (serum)" },
      { label: "Glucose fasting", probe: "Glucose (plasma)", canonical: "Glucose (plasma)", note: "Catalogue doesn't distinguish fasting/random as separate tests — both are the same plasma glucose assay, ordered at a different time." },
      { label: "Glucose random", probe: "Glucose (plasma)", canonical: "Glucose (plasma)", note: "Catalogue doesn't distinguish fasting/random as separate tests — both are the same plasma glucose assay, ordered at a different time." },
      { label: "HbA1c", probe: "HbA1c", canonical: "Glycated Haemoglobin" },
      { label: "Lactate", probe: "Lactate (plasma)", canonical: "Lactate (plasma)" },
    ],
  },
  {
    category: "Chemical Pathology — Lipids",
    tests: [
      { label: "Lipogram", probe: "Lipogram", canonical: "Lipogram (HDL, LDL, total cholesterol & triglycerides)" },
      { label: "Triglycerides", probe: "Triglycerides", canonical: "Lipogram (HDL, LDL, total cholesterol & triglycerides)" },
      { label: "Total cholesterol", probe: "Total cholesterol", canonical: "Cholesterol (serum) (total)" },
      { label: "HDL cholesterol", probe: "HDL cholesterol", canonical: "Lipogram (HDL, LDL, total cholesterol & triglycerides)" },
      { label: "LDL cholesterol", probe: "LDL cholesterol", canonical: "Lipogram (HDL, LDL, total cholesterol & triglycerides)" },
    ],
  },
  {
    category: "Chemical Pathology — Anaemia",
    tests: [
      { label: "Iron studies", probe: "Iron studies", canonical: "Iron" },
      { label: "Iron", probe: "Iron", canonical: "Iron" },
      { label: "Transferrin", probe: "Transferrin", canonical: "Transferrin" },
      { label: "Ferritin", probe: "Ferritin", canonical: "Ferritin" },
      { label: "Vitamin B12", probe: "Vitamin B12", canonical: "Vitamin B12" },
      { label: "Haptoglobin", probe: "Haptoglobin", canonical: "Haptoglobin" },
      { label: "Folate (red cell)", probe: null, canonical: null, note: "Catalogue only has serum folate on record, a clinically different test from red cell folate." },
    ],
  },
  {
    category: "Chemical Pathology — Drugs/Toxic screen",
    tests: [
      { label: "Paracetamol", probe: "Paracetamol", canonical: "Acetaminophen (Paracetamol)" },
      { label: "Lithium", probe: "Lithium", canonical: "Lithium" },
      { label: "Tricyclic antidepressants", probe: "Tricyclic antidepressants", canonical: "Tricyclic antidepressants (serum)" },
      { label: "Phenytoin", probe: "Phenytoin", canonical: "Phenytoin (Epanutin)" },
      { label: "Phenobarbitone", probe: "Phenobarbital", canonical: "Phenobarbital (Gardenal)", note: "Catalogue uses the US spelling (Phenobarbital) for the same drug." },
      { label: "Valproate", probe: "Valproate", canonical: "Valproate, sodium (Convulex or Epilim)" },
      { label: "Carbamazepine", probe: "Carbamazepine", canonical: "Carbamazepine (Tegretol)" },
      { label: "Digoxin", probe: "Digoxin", canonical: "Digoxin (Lanoxin)" },
      { label: "Theophylline", probe: "Theophylline", canonical: "Theophylline" },
      { label: "Urine cannabis", probe: null, canonical: null, note: "No cannabis/THC screen found in the current catalogue." },
      { label: "Urine mandrax", probe: "Mandrax", canonical: "Mandrax (methaqualone)" },
      { label: "Salicylates", probe: "Salicylates", canonical: "Salicylates [Mass/volume] in Serum or Plasma" },
      { label: "Toxic screen", probe: null, canonical: null, note: "Generic on the form itself (which drug panel isn't specified) — add the specific drug via \"+ Add test\" instead." },
      { label: "Vancomycin", probe: "Vancomycin", canonical: "Vancomycin" },
    ],
  },
  {
    category: "Tumor Markers",
    tests: [
      { label: "Alpha feto protein", probe: "Alpha feto protein", canonical: "Alpha-1-Fetoprotein [Mass/volume] in Body fluid" },
      { label: "PSA", probe: "PSA", canonical: "Prostate-Specific Antigen" },
      { label: "CEA", probe: "CEA", canonical: "Carcinoembryonic Antigen" },
    ],
  },
  {
    category: "Chemical Pathology — Endocrinology",
    tests: [
      { label: "Thyroid function", probe: "Thyroid function tests", canonical: "Thyroid Function Tests" },
      { label: "TSH", probe: "TSH", canonical: "Thyroid Stimulating Hormone" },
      { label: "Free T4", probe: "Free T4", canonical: "Free T4 (Thyroxine)" },
      { label: "Free T3", probe: "Free T3", canonical: "Free T3 (Tri-iodothyronine)" },
      { label: "Beta HCG qual", probe: null, canonical: null, note: "No hCG test found in the current catalogue under this name." },
      { label: "Beta HCG quant", probe: null, canonical: null, note: "No hCG test found in the current catalogue under this name." },
      { label: "FSH", probe: "FSH", canonical: "Follicle Stimulating Hormone" },
      { label: "Estradiol", probe: "Oestradiol (E2)", canonical: "Oestradiol (E2)", note: "Catalogue uses the UK spelling (Oestradiol)." },
      { label: "LH", probe: "LH", canonical: "Luteinizing Hormone" },
      { label: "Progesterone", probe: "Progesterone", canonical: "Progesterone" },
      { label: "Prolactin", probe: "Prolactin", canonical: "Prolactin" },
      { label: "Testosterone", probe: "Testosterone", canonical: "Testosterone" },
      { label: "SHBG", probe: "SHBG", canonical: "Sex hormone binding globulin (SHBG)" },
      { label: "PTH", probe: "PTH", canonical: "Parathyroid Hormone" },
      { label: "Cortisol", probe: "Cortisol (serum)", canonical: "Cortisol (serum)" },
      { label: "Insulin", probe: "Insulin", canonical: "Insulin" },
    ],
  },
  {
    category: "Chemical Pathology — Urine",
    tests: [
      { label: "Na", probe: "Sodium (urine)", canonical: "Sodium (urine)" },
      { label: "K", probe: "Potassium (urine)", canonical: "Potassium (urine)" },
      { label: "Cl", probe: "Chloride (urine)", canonical: "Chloride (urine)" },
      { label: "Urea", probe: "Urea (urine)", canonical: "Urea (urine)" },
      { label: "Creat", probe: "Creatinine (urine)", canonical: "Creatinine (urine)" },
      { label: "Protein", probe: "Urine protein", canonical: "Protein (total) (urine)" },
      { label: "BJP", probe: "Bence Jones protein", canonical: "Protein electrophoresis (Bence Jones protein) (urine)" },
      { label: "Microalbumin", probe: "Microalbumin", canonical: "Microalbumin [Mass/time] in 24 hour Urine by Detection limit <= 3.0 mg/L" },
      { label: "Creat clearance", probe: "Creatinine clearance", canonical: "Creatinine clearance" },
      { label: "Dipstick urinalysis", probe: "Dipstick urinalysis", canonical: "Urinalysis (dipsticks)" },
      { label: "VMA", probe: "VMA", canonical: "Vanillyl mandelic acid (VMA) (quantitative)" },
      { label: "HVA", probe: "HVA", canonical: "Homovanillic acid (HVA)" },
      { label: "Osmolality", probe: "Osmolality (urine)", canonical: "Osmolality (urine)" },
      { label: "Prot / creat ratio", probe: null, canonical: null, note: "No protein/creatinine ratio test found in the current catalogue under this name." },
    ],
  },
  {
    category: "Cerebrospinal Fluid",
    tests: [
      { label: "CSF chemistry", probe: "CSF chemistry", canonical: "Fructose/Creatinine [Molar ratio] in Urine" },
      { label: "CSF ADA", probe: "Adenosine deaminase (CSF)", canonical: "Adenosine deaminase (CSF)" },
    ],
  },
  {
    category: "Haematology — General",
    tests: [
      { label: "FBC+Platelets", probe: "FBC", canonical: "Full Blood Count", aliases: ["Full Blood Examination"] },
      { label: "Differential count", probe: "Differential count", canonical: "Differential white cell count" },
      { label: "Morphology", probe: "Morphology", canonical: "Morphology" },
      { label: "Haemoglobin", probe: "Haemoglobin", canonical: "Haemoglobin" },
      { label: "White cell count", probe: "White cell count", canonical: "White cell count and white cell differential" },
      { label: "Platelet count", probe: "Platelet count", canonical: "Platelet count" },
      { label: "Reticulocytes", probe: "Reticulocyte count", canonical: "Reticulocyte count" },
      { label: "ESR", probe: "ESR", canonical: "Erythrocyte Sedimentation Rate" },
      { label: "CD4 count", probe: "Immunophenotyping (Flow cytometry): CD4", canonical: "Immunophenotyping (Flow cytometry): CD4" },
    ],
  },
  {
    category: "Haematology — Coagulation",
    tests: [
      { label: "INR", probe: "INR", canonical: "International Normalized Ratio" },
      { label: "PTT", probe: "PTT", canonical: "Activated Partial Thromboplastin Time" },
      { label: "Fibrinogen", probe: "Fibrinogen", canonical: "Fibrinogen" },
      { label: "D Dimers", probe: "D Dimers", canonical: "Fibrin D-dimer [Presence] in Platelet poor plasma by Latex agglutination" },
      { label: "Anti thrombin", probe: "Anti thrombin", canonical: "Anti - Thrombin" },
      { label: "Protein C", probe: "Protein C", canonical: "Protein C" },
      { label: "Protein S", probe: "Protein S", canonical: "Protein S" },
      { label: "Lupus anticoagulant", probe: "Lupus anticoagulant", canonical: "Lupus Anticoagulant" },
      { label: "Thrombin time", probe: "Thrombin time", canonical: "Thrombin Time" },
      { label: "DIC screen", probe: null, canonical: null, note: "Generic on the form itself — the catalogue has no single matching entry for a DIC workup as one test. The component coagulation tests above can be added individually." },
    ],
  },
  {
    category: "Haematology — Other",
    tests: [
      { label: "Coombs", probe: "Coombs (Direct - screening)", canonical: "Coombs (Direct - screening)", note: "Catalogue has separate Direct-screening / Direct-typing / Indirect Coombs entries — this ticks the routine screening variant." },
      { label: "ABO", probe: "ABO", canonical: "Blood Grouping: ABO Blood" },
      { label: "RH", probe: "Rhesus (Rh typing)", canonical: "Blood Grouping: Rhesus (Rh typing)" },
    ],
  },
  {
    category: "Immunology — Inflammation",
    tests: [
      { label: "CRP", probe: "CRP", canonical: "C-reactive protein" },
      { label: "IgG", probe: "Immunoglobulin G", canonical: "Immunoglobulin G (IgG)" },
      { label: "IgA", probe: "Immunoglobulin A", canonical: "Immunoglobulin A (IgA)" },
      { label: "IgM", probe: "Immunoglobulin M", canonical: "Immunoglobulin M (IgM)" },
      { label: "SPEP", probe: "Protein electrophoresis (serum)", canonical: "Protein electrophoresis (serum)" },
      { label: "C3, C4", probe: "Complement C3", canonical: "Complement C3", note: "Ticks Complement C3 — add Complement C4 separately via \"+ Add test\" if both are needed; the catalogue has no single combined C3+C4 entry." },
    ],
  },
  {
    category: "Immunology — Allergology",
    tests: [
      { label: "Total IgE", probe: "Total IgE", canonical: "Immunoglobulin E (IgE Total)" },
      { label: "IgE RAST", probe: null, canonical: null, note: "Generic on the form itself (the specific allergen isn't specified) — the catalogue only has named allergen-specific RAST panels. Add the specific allergen via \"+ Add test\" instead." },
    ],
  },
  {
    category: "Immunology — Auto immune",
    tests: [
      { label: "RF", probe: "RF", canonical: "Rheumatoid Factor" },
      { label: "ANA", probe: "ANA", canonical: "Antinuclear Antibody" },
      { label: "ENA", probe: "ENA", canonical: "Antigen (ENA) Antibodies" },
      { label: "Anti double stranded DNA", probe: "Anti double stranded DNA", canonical: "Anti - double stranded DNA" },
      { label: "Anti cardiolipin", probe: "Anti cardiolipin", canonical: "Anti - Cardiolipin (ACLA)" },
      { label: "Anti mitochondria", probe: "Anti mitochondria", canonical: "Anti - Mitochondrial" },
      { label: "Anti smooth muscle", probe: "Anti smooth muscle", canonical: "Anti - Smooth Muscle" },
    ],
  },
  {
    category: "Microbiology — Specimen Type",
    nonTest: true,
    tests: [
      { label: "Blood" },
      { label: "CSF" },
      { label: "Pus" },
      { label: "BAL" },
      { label: "Md stream urine" },
      { label: "Catheter urine" },
      { label: "SPU", specify: true },
      { label: "Aspirate" },
      { label: "Stool" },
      { label: "Rectal swab" },
      { label: "Throat swab" },
      { label: "Swab" },
      { label: "Fluid (specify)", specify: true },
      { label: "Catheter tip (IV, shunt)", specify: true },
    ],
  },
  {
    category: "Microbiology — Investigation Required",
    tests: [
      { label: "Parasites", probe: "Parasites", canonical: "Ova and parasites identified in Stool by Baermann concentration" },
      { label: "Malaria", probe: "Malaria", canonical: "Malaria rapid screen" },
      { label: "Microscopy/Culture/Sensitivity", probe: null, canonical: null, note: "Generic on the form itself (applies differently per specimen type) — add the specific specimen's M/C/S via \"+ Add test\" instead." },
      { label: "Anaerobic culture", probe: "Anaerobic culture", canonical: "Microorganism preliminary growth [Presence] in Blood by Anaerobic culture with antimicrobial neutralizer" },
      { label: "TB microscopy", probe: null, canonical: null, note: "No confident catalogue match for this generic phrasing (it depends on specimen type)." },
      { label: "NTM", probe: null, canonical: null, note: "Non-tuberculous mycobacteria — no match in the current catalogue under this abbreviation." },
      { label: "TB PCR direct", probe: null, canonical: null, note: "No confident match — 'PCR' alone matches an unrelated generic catalogue entry, not a TB-specific one." },
      { label: "TB culture", probe: "TB culture", canonical: "TB (Tuberculosis): Culture & sensitivity" },
      { label: "BCG", probe: null, canonical: null, note: "'BCG' only matches an unrelated lab dye-binding method in the current catalogue (a coincidental acronym collision), not a TB-related test." },
      { label: "TB sensitivity", probe: "TB sensitivity", canonical: "TB (Tuberculosis): Culture & sensitivity" },
      { label: "Fungal microscopy", probe: null, canonical: null, note: "Catalogue only has specimen-site-specific fungal test entries (e.g. nose, genital), no generic one matching this form's generic checkbox." },
      { label: "Fungal culture", probe: null, canonical: null, note: "Catalogue only has specimen-site-specific fungal test entries (e.g. nose, genital), no generic one matching this form's generic checkbox." },
    ],
  },
  {
    category: "Bacteriology / Parasitology / Fungi / Serology",
    tests: [
      { label: "Chlamydia PCR (urine/swab)", probe: "Chlamydia polyvalent", canonical: "Chlamydia polyvalent", note: "Catalogue's Chlamydia entry isn't PCR-specific by name, but is the one Chlamydia test on record." },
      { label: "MIF (serum)", probe: null, canonical: null, note: "Micro-immunofluorescence (Chlamydia serology) — no safe match in the current catalogue under this abbreviation." },
      { label: "Clostridium difficile toxin ELISA", probe: "Clostridium difficile toxin", canonical: "Clostridioides difficile Ab [Units/volume] in Serum" },
      { label: "ASOT", probe: "ASOT", canonical: "Streptolysin O Ab [Mass/volume] in Serum" },
      { label: "Anti-DNAse B", probe: "Anti-DNase B titre", canonical: "Anti - DNase B titre" },
      { label: "Anti-hyaluronidase", probe: null, canonical: null, note: "No safe match in the current catalogue." },
      { label: "RPR", probe: "RPR", canonical: "Rapid Plasma Reagin" },
      { label: "FTA", probe: "FTA", canonical: "Syphilis: Fluorescent treponemal antibody (FTA)" },
      { label: "TPHA", probe: "TPHA", canonical: "Treponema Pallidum Haemagglutination Assay" },
      { label: "VDRL", probe: "VDRL", canonical: "Venereal Disease Research Laboratory Test" },
      { label: "Bilharzia (IFA/ELISA)", probe: "Bilharzia (Schistosoma)", canonical: "Bilharzia (Schistosoma)", note: "IFA and ELISA are methods, not separate catalogue tests — ticking this selects the one Bilharzia serology test on record." },
      { label: "ELISA (specify)", probe: null, canonical: null, note: "Generic on the form itself (the specific antigen/antibody isn't specified) — add the specific test via \"+ Add test\" instead." },
      { label: "Amoebic IHA", probe: "Entamoeba histolytica IgG", canonical: "Entamoeba histolytica IgG" },
      { label: "Hydatid IHA", probe: "Echinococcus Antibodies", canonical: "Echinococcus Antibodies", note: "Hydatid disease is caused by Echinococcus — catalogue lists the test under that name." },
      { label: "Cysticercosis IgG", probe: "Cysticercosis IgG", canonical: "Taenia solium larva Ab [Units/volume] in Serum by Hemagglutination" },
      { label: "Crypto antigen", probe: null, canonical: null, note: "Ambiguous on the form itself (Cryptococcal vs Cryptosporidium antigen) — no confident catalogue match for either under this abbreviation." },
      { label: "Pneumocystis IFA", probe: "Pneumocystis IFA", canonical: "Pneumocystis jiroveci (PCP) antigen detection (IFA)" },
      { label: "Mycoplasma ELISA", probe: "Mycoplasma IgG & IgM", canonical: "Mycoplasma IgG & IgM" },
      { label: "Rickettsia IFA", probe: "Rickettsia IFA", canonical: "Rickettsia conorii (tick-bite fever) Antibodies (IFA)" },
    ],
  },
  {
    category: "Viral Serology — Hepatitis testing",
    tests: [
      { label: "Clinical hepatitis A", probe: "Hepatitis A IgM", canonical: "Hepatitis A virus (HAV) IgM" },
      { label: "Clinical hepatitis B", probe: "Hepatitis B surface antigen", canonical: "Hepatitis B virus (HBV) surface Antigen (HBsAg)" },
      { label: "Clinical hepatitis C", probe: "Hepatitis C antibody", canonical: "Hepatitis C Total Antibody" },
      { label: "Hepatitis B Ab", probe: "anti-HBs", canonical: "Hepatitis B virus (HBV) surface Antibodies (anti-HBs)" },
      { label: "Hepatitis B Ag", probe: "Hepatitis B surface antigen", canonical: "Hepatitis B virus (HBV) surface Antigen (HBsAg)" },
    ],
  },
  {
    category: "Viral Serology — HIV testing",
    tests: [
      { label: "HIV ELISA", probe: "HIV EIA", canonical: "HIV EIA (3rd generation)" },
      { label: "HIV rapid", probe: "HIV rapid (4th generation)", canonical: "HIV rapid (4th generation)" },
      { label: "HIV Viral load", probe: "HIV-1 viral load", canonical: "HIV-1 viral load" },
      { label: "HIV PCR", probe: null, canonical: null, note: "No confident match — 'PCR' alone matches an unrelated generic catalogue entry, not an HIV-specific one." },
    ],
  },
  {
    category: "Viral Serology — TORCH screen",
    tests: [
      { label: "Toxo IgG", probe: "Toxoplasma IgG", canonical: "Toxoplasma gondii IgG" },
      { label: "Toxo IgM", probe: "Toxoplasma IgM", canonical: "Toxoplasma gondii IgM" },
      { label: "Rubella IgG", probe: "Rubella IgG", canonical: "Rubella virus IgG Antibodies" },
      { label: "Rubella IgM", probe: "Rubella IgM", canonical: "Rubella virus IgM Antibodies" },
      { label: "CMV IgG", probe: "CMV IgG", canonical: "Cytomegalovirus (CMV) IgG" },
      { label: "CMV IgM", probe: "CMV IgM", canonical: "Cytomegalovirus (CMV) IgM" },
      { label: "Herpes IgG", probe: "Herpes IgG", canonical: "Herpes simplex virus (HSV) types 1 & 2 IgG antibodies" },
      { label: "Herpes IgM", probe: "Herpes IgM", canonical: "Herpes simplex virus (HSV) types 1 & 2 IgM antibodies" },
    ],
  },
  {
    category: "Viral Serology — Other Serology",
    tests: [
      { label: "EBV IgG", probe: "EBV IgG", canonical: "Epstein-Barr virus (EBV) IgG" },
      { label: "EBV IgM", probe: "EBV IgM", canonical: "Epstein-Barr virus (EBV) IgM" },
      { label: "VZV IgG", probe: "VZV IgG", canonical: "Varicella zoster virus IgG Ab index [Units/volume] in Serum and CSF" },
      { label: "VZV IgM", probe: "VZV IgM", canonical: "Varicella zoster virus IgM Ab [Units/volume] in Serum by Immunoassay" },
      { label: "Measles IgG", probe: "Measles IgG", canonical: "Measles virus IgG Antibodies" },
      { label: "Measles IgM", probe: "Measles IgM", canonical: "Measles virus IgM Antibodies for measles surveillance" },
      { label: "Mumps IgG", probe: "Mumps IgG", canonical: "Mumps virus IgG Antibodies" },
      { label: "Mumps IgM", probe: "Mumps IgM", canonical: "Mumps virus IgM Antibodies" },
      { label: "CMV pp65 IgG", probe: null, canonical: null, note: "CMV pp65 is an antigen test, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen test itself is still available via \"+ Add test\" (\"CMV pp65\")." },
      { label: "CMV pp65 IgM", probe: null, canonical: null, note: "CMV pp65 is an antigen test, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen test itself is still available via \"+ Add test\" (\"CMV pp65\")." },
      { label: "RSV IgG", probe: null, canonical: null, note: "RSV rapid testing is an antigen test, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen test itself is still available via \"+ Add test\" (\"Rapid RSV\")." },
      { label: "RSV IgM", probe: null, canonical: null, note: "RSV rapid testing is an antigen test, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen test itself is still available via \"+ Add test\" (\"Rapid RSV\")." },
      { label: "Rota/Adeno IgG", probe: null, canonical: null, note: "Rota/Adeno testing is an antigen panel, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen panel itself is still available via \"+ Add test\" (\"Rota/Adeno\")." },
      { label: "Rota/Adeno IgM", probe: null, canonical: null, note: "Rota/Adeno testing is an antigen panel, not an antibody test — no distinct IgG/IgM catalogue entry exists. The antigen panel itself is still available via \"+ Add test\" (\"Rota/Adeno\")." },
    ],
  },
  {
    category: "Viral Serology — Viral Isolation",
    tests: [
      { label: "Culture (specify)", probe: null, canonical: null, note: "Generic on the form itself (the doctor specifies what to culture) — not a single catalogue test. Add the specific test via \"+ Add test\" instead." },
      { label: "PCR (specify)", probe: null, canonical: null, note: "Generic on the form itself (the doctor specifies what to test for) — not a single catalogue test. Add the specific test via \"+ Add test\" instead." },
    ],
  },
];
