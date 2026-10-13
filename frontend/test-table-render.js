// Shared between app.js (doctor, editable) and lookup.js (pathologist,
// read-only) — exactly like specimen-render.js already is for "Tubes
// Required" — one rendering definition, not two copies that could drift.
// Requires frontend/test-table-data.js to be loaded first.
//
// The table is purely a SECOND rendering of tests_required (the same
// array app.js's renderTests()/removeTestItem()/addTest() already own)
// — it has no state of its own. A cell's checked/unchecked state is
// recomputed from tests_required on every render, never tracked
// separately, so the table and the list below it can never drift apart.

function _ttKey(name) {
  return (name || "").trim().toLowerCase();
}

// The names a CONFIRMED tests_required item must match for this cell to
// show ticked. `canonical` (not `probe`!) — see test-table-data.js's
// header comment for why those two must never be conflated: `probe` is
// what gets RESOLVED when ticking an empty box, `canonical` is what that
// resolution is verified to come back as.
function _ttCellNames(cell) {
  return [cell.canonical, ...(cell.aliases || [])].filter(Boolean).map(_ttKey);
}

// The index into tests_required of the CONFIRMED item this cell
// represents, or -1 if none (box unticked). Matched by canonical name,
// never by raw dictated text, so phrasing differences don't matter.
function findConfirmedIndexForCell(testsRequired, cell) {
  const names = _ttCellNames(cell);
  if (names.length === 0) return -1;
  return (testsRequired || []).findIndex(
    (item) => item.status === "confirmed" && names.includes(_ttKey(item.normalized))
  );
}

// Used by app.js's renderTests() to skip showing a confirmed item as a
// duplicate line in the plain list when it's already a ticked box here.
function isCanonicalNameInTable(normalizedName) {
  const key = _ttKey(normalizedName);
  return TEST_TABLE_CATEGORIES.some(
    (cat) => !cat.nonTest && cat.tests.some((cell) => _ttCellNames(cell).includes(key))
  );
}

function _buildCard(category, tests, testsRequired, { readOnly, nonTest, onCheck, onUncheck }) {
  const card = document.createElement("div");
  card.className = "test-table-card";

  const heading = document.createElement("h4");
  heading.className = "test-table-card-title";
  heading.textContent = category;
  card.appendChild(heading);

  const list = document.createElement("ul");
  list.className = "test-table-cell-list";

  tests.forEach((cell) => {
    const li = document.createElement("li");
    li.className = "test-table-cell";
    // A plain, uncoloured hover tooltip when there's something worth
    // knowing about this cell (e.g. a spelling/specimen note) — never a
    // visual warning badge/colour; see test-table-data.js's header
    // comment.
    if (cell.note) li.title = cell.note;

    const labelEl = document.createElement("label");

    const input = document.createElement("input");
    input.type = "checkbox";

    if (nonTest) {
      // Visual reference only — never tied to tests_required or saved
      // anywhere (see test-table-data.js's header comment for why).
      input.disabled = true;
    } else {
      const index = findConfirmedIndexForCell(testsRequired, cell);
      input.checked = index !== -1;
      input.disabled = readOnly;
      if (cell.canonical) input.title = cell.canonical;

      if (!readOnly) {
        input.addEventListener("change", () => {
          if (input.checked) {
            onCheck && onCheck(cell);
          } else {
            onUncheck && onUncheck(cell, index);
          }
        });
      }
    }

    labelEl.appendChild(input);
    const span = document.createElement("span");
    span.textContent = cell.label;
    labelEl.appendChild(span);

    li.appendChild(labelEl);

    // A free-text note the doctor can write in alongside the checkbox
    // (e.g. which fluid) — never persisted, same visual-only status as
    // the rest of this nonTest category (see test-table-data.js).
    if (cell.specify) {
      const specifyInput = document.createElement("input");
      specifyInput.type = "text";
      specifyInput.className = "test-table-specify-input";
      specifyInput.placeholder = "specify…";
      specifyInput.disabled = readOnly;
      li.appendChild(specifyInput);
    }

    list.appendChild(li);
  });

  card.appendChild(list);
  return card;
}

// opts: {readOnly, onCheck(cell), onUncheck(cell, index)}. onCheck/
// onUncheck are ignored (and never wired) when readOnly, and never
// called for a nonTest category's checkboxes either way.
function renderTestTable(container, testsRequired, opts = {}) {
  const { readOnly = false, onCheck = null, onUncheck = null } = opts;
  container.innerHTML = "";

  // A dense multi-column grid (like the paper form), not a list of
  // full-width cards — scrolls sideways on a narrow screen rather than
  // collapsing to one column, by design (see .test-table-scroll/.grid
  // in styles.css).
  const scroll = document.createElement("div");
  scroll.className = "test-table-scroll";

  const grid = document.createElement("div");
  grid.className = "test-table-grid";

  TEST_TABLE_CATEGORIES.forEach((cat) => {
    grid.appendChild(
      _buildCard(cat.category, cat.tests, testsRequired, {
        readOnly,
        nonTest: !!cat.nonTest,
        onCheck,
        onUncheck,
      })
    );
  });

  scroll.appendChild(grid);
  container.appendChild(scroll);
}
