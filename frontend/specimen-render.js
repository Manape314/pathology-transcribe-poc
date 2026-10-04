// Shared between app.js (doctor side) and lookup.js (lab lookup page) so
// the "Tubes Required" rendering — including the colour-swatch
// mapping — has one definition, not two copies that could drift. The
// grouped data itself comes from backend/specimen_mapping.py via
// structured.specimen_requirements; this file only displays it.

const SPECIMEN_SWATCH_COLOURS = {
  PURPLE: "#7c3aed",
  YELLOW: "#eab308",
  BLUE: "#2563eb",
  GREY: "#6b7280",
};

// For groups backend/specimen_mapping.py deliberately doesn't give a tube
// colour (not a coloured venous draw at all, or genuinely unmapped) —
// every group gets SOME visual marker, not just the coloured ones, so the
// list doesn't look broken/inconsistent for these rows. Short plain-ASCII
// text badges — same shape as the existing ABBREV/NHLS/LOINC badges
// already used elsewhere in this app (.source-badge) — rather than an
// icon glyph or hand-drawn SVG path, neither of which can be visually
// verified to render correctly without a browser.
const SPECIMEN_SYMBOLS = {
  "BLOOD GAS SYRINGE": "GAS",
  "URINE CONTAINER": "URINE",
  "BONE MARROW": "BM",
  UNMAPPED: "!",
};
// Fallback for any future non-colour group this list doesn't know about
// yet — never nothing, even for a category not explicitly listed above.
const SPECIMEN_SYMBOL_FALLBACK = "•";

// Never colour/symbol-only: this swatch/badge is a supplementary visual
// aid for sighted users, but the label text ALWAYS names the colour or
// category in words too ("PURPLE — EDTA", "Unmapped — verify specimen
// requirements") — someone who can't distinguish the swatch's colour or
// the badge character still gets the same information from the text.
function swatchColourFor(label) {
  const prefix = label.split("—")[0].trim().toUpperCase();
  return SPECIMEN_SWATCH_COLOURS[prefix] || null;
}

function symbolFor(label) {
  const prefix = label.split("—")[0].trim().toUpperCase();
  return SPECIMEN_SYMBOLS[prefix] || SPECIMEN_SYMBOL_FALLBACK;
}

function renderSpecimenGroupsInto(groupsEl, sectionEl, groups) {
  groupsEl.innerHTML = "";

  if (!groups || groups.length === 0) {
    sectionEl.classList.add("hidden");
    return;
  }

  groups.forEach((group) => {
    const block = document.createElement("div");
    block.className = "specimen-group";

    const heading = document.createElement("h4");
    heading.className = "specimen-group-label";

    const colour = swatchColourFor(group.label);
    if (colour) {
      const swatch = document.createElement("span");
      swatch.className = "specimen-swatch";
      swatch.style.background = colour;
      swatch.setAttribute("aria-hidden", "true");
      heading.appendChild(swatch);
    } else {
      const badge = document.createElement("span");
      badge.className = "specimen-symbol";
      badge.textContent = symbolFor(group.label);
      badge.setAttribute("aria-hidden", "true");
      heading.appendChild(badge);
    }

    const labelText = document.createElement("span");
    labelText.textContent = group.label;
    heading.appendChild(labelText);

    block.appendChild(heading);

    const list = document.createElement("ul");
    list.className = "specimen-group-list";
    group.tests.forEach((testName) => {
      const item = document.createElement("li");
      item.textContent = testName;
      list.appendChild(item);
    });
    block.appendChild(list);

    groupsEl.appendChild(block);
  });

  sectionEl.classList.remove("hidden");
}
