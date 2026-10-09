// BACKEND_URL comes from config.js (loaded before this file — see
// index.html's <script> order) so it's configured in exactly one place
// for the whole frontend, not duplicated per page.

const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const toggleRawBtn = document.getElementById("toggleRawBtn");

const testsSection = document.getElementById("testsSection");
const testsList = document.getElementById("testsList");
const testsEmpty = document.getElementById("testsEmpty");
const confirmTestsBtn = document.getElementById("confirmTestsBtn");
const skipTestsBtn = document.getElementById("skipTestsBtn");
const addTestInput = document.getElementById("addTestInput");
const addTestBtn = document.getElementById("addTestBtn");

const specimenSection = document.getElementById("specimenSection");
const specimenGroups = document.getElementById("specimenGroups");
const specimenConfirmCheckbox = document.getElementById("specimenConfirmCheckbox");

const incompleteBanner = document.getElementById("incompleteBanner");
const incompleteList = document.getElementById("incompleteList");
const blocksContainer = document.getElementById("blocksContainer");
const blockSpecimen = document.getElementById("blockSpecimen");
const blockHospital = document.getElementById("blockHospital");
const blockPatient = document.getElementById("blockPatient");
const blockClinical = document.getElementById("blockClinical");
const blockPriority = document.getElementById("blockPriority");

const printSection = document.getElementById("printSection");
const doneBtn = document.getElementById("doneBtn");
const retryPrintBtn = document.getElementById("retryPrintBtn");
const printStatus = document.getElementById("printStatus");
const printLabelImage = document.getElementById("printLabelImage");

// ---------------------------------------------------------------------------
// Six-block review layout — purely a different RENDERING of the same
// structured result /transcribe already returns from the single dictation
// (see backend/field_extraction.py's _LABEL_DEFS/_DISPLAY_FIELDS, which
// this mirrors). "kind" picks how each field renders:
//   "plain"    -> {raw, value, status} passthrough, escaped text
//   "date"     -> confirmed value, or raw + "[unconfirmed]" if ambiguous
//   "time"     -> same as date, but offers the AM/PM quick-pick when the
//                 raw text looks like it's just missing am/pm
//   "clinical" -> {raw, value, status, resolved_terms} — value rendered
//                 with inline ambiguous-term highlighting (same mechanism
//                 as before, just scoped to one field instead of the
//                 whole transcript)
// ---------------------------------------------------------------------------
const BLOCK_DEFS = [
  [blockSpecimen, [
    ["specimen_type", "Specimen type", "plain"],
    ["specimen_site", "Specimen site", "plain"],
    ["date_collected", "Date collected", "date"],
    ["time_collected", "Time collected", "time"],
    ["reason_for_request", "Reason for request", "plain"],
  ]],
  [blockHospital, [
    ["hospital", "Hospital", "plain"],
    ["ward", "Ward", "plain"],
  ]],
  // Patient hospital number ONLY — no patient name, DOB, phone, or
  // address in this block (explicit requirement; never add one back in
  // without being asked).
  [blockPatient, [
    ["patient_id", "Patient hospital number", "plain"],
  ]],
  // Clinical History + Provisional Diagnosis ONLY — no medication in
  // this block (explicit requirement; never add it back in without
  // being asked).
  [blockClinical, [
    ["clinical_history", "Clinical history", "clinical"],
    ["provisional_diagnosis", "Provisional diagnosis", "clinical"],
  ]],
  // Priority ONLY — no date/time requested in this block (explicit
  // requirement; never add them back in without being asked).
  [blockPriority, [
    ["priority", "Priority", "plain"],
  ]],
];

// The dictation proforma's required fields (backend/README's template) —
// tests_required is checked separately below (needs a non-empty-array
// check, not a {value} check). date_of_birth/patient_name/medication/etc.
// exist but aren't required — dictating without them never blocks Done.
const REQUIRED_PLAIN_FIELDS = [
  ["specimen_type", "Specimen type"],
  ["specimen_site", "Specimen site"],
  ["reason_for_request", "Reason for request"],
  ["hospital", "Hospital"],
  ["ward", "Ward"],
  ["patient_id", "Patient hospital number"],
  ["priority", "Priority"],
];
const REQUIRED_CLINICAL_FIELDS = [
  ["clinical_history", "Clinical history"],
  ["provisional_diagnosis", "Provisional diagnosis"],
];
const REQUIRED_DATE_TIME_FIELDS = [
  ["date_collected", "Date collected"],
  ["time_collected", "Time collected"],
];

let printStatusPollTimer = null; // see pollPrintStatus() — cleared on a new submission/recording

let currentRequestId = null; // set once /print-label succeeds; used by polling + Retry Print
let lastTestsRequired = []; // the tests_required items currently rendered
let currentNormalizedText = "";
let currentRawText = "";
let showingRaw = false;
let currentStructured = {}; // the full structured object from the last transcription
let currentEntryId = null; // the History entry id the current transcript is saved under

let mediaRecorder = null; // the active MediaRecorder, when recording
let chunks = []; // audio Blob pieces collected during a recording
let isRecording = false;

function setStatus(msg) {
  statusEl.textContent = msg;
}

// Toggle handler: first press starts recording, second press stops it.
recordBtn.addEventListener("click", async () => {
  if (!isRecording) {
    await startRecording();
  } else {
    stopRecording();
  }
});

async function startRecording() {
  hideTests();
  hideSpecimenGroups();
  hidePrintSection();
  closeAmbiguousPopup();
  resetTranscriptView();

  try {
    // Ask for mic access. This prompts the user the first time. It will REJECT
    // on an insecure origin (plain http:// that isn't localhost) — that's the
    // usual cause of "recording won't start" on a phone. Use HTTPS there.
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    // MediaRecorder captures the mic. Browsers pick their own container:
    // Chrome/Firefox -> audio/webm (Opus), iOS Safari -> audio/mp4 (AAC).
    // We don't force a type; the backend decodes whatever comes in via FFmpeg.
    mediaRecorder = new MediaRecorder(stream);
    chunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      // Stop the mic hardware (removes the browser's "recording" indicator).
      stream.getTracks().forEach((t) => t.stop());
      // Reassemble the pieces into one Blob and send it.
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
      await sendForTranscription(blob);
    };

    mediaRecorder.start();
    isRecording = true;
    recordBtn.textContent = "Stop";
    recordBtn.classList.add("recording");
    setStatus("Recording… press Stop when done.");
  } catch (err) {
    console.error(err);
    setStatus("Microphone error: " + err.message);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop(); // triggers onstop above
  }
  isRecording = false;
  recordBtn.textContent = "Record";
  recordBtn.classList.remove("recording");
  setStatus("Uploading & transcribing…");
}

async function sendForTranscription(blob) {
  // Choose a filename extension that matches the recorded type, so the server's
  // decoder can sniff the format correctly.
  const ext = blob.type.includes("mp4") ? "mp4" : "webm";

  // multipart/form-data with a field named "file" — matches the FastAPI
  // endpoint signature: transcribe(file: UploadFile = File(...)).
  const form = new FormData();
  form.append("file", blob, `recording.${ext}`);

  try {
    const res = await backendFetch(`/transcribe`, {
      method: "POST",
      body: form,
    });

    if (!res.ok) {
      throw new Error(`Server responded ${res.status}`);
    }

    const data = await res.json();

    currentNormalizedText = data.normalized_text || data.raw_text || "";
    currentRawText = data.raw_text || "";
    currentStructured = data.structured || {};
    showingRaw = false;
    specimenConfirmCheckbox.checked = false; // a new request — any prior confirmation doesn't apply
    updateTranscriptView(); // renders the six blocks + validation banner

    currentEntryId = currentNormalizedText
      ? addHistoryEntry(currentNormalizedText, currentRawText)
      : null;
    if (currentEntryId) {
      renderTests(currentEntryId, currentStructured.tests_required || []);
      renderSpecimenGroups(currentStructured.specimen_requirements || []);
      showPrintSection();
    }
    refreshValidation();

    setStatus(
      `Done — detected ${data.language}, ${data.duration.toFixed(1)}s of audio.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Transcription failed: " + err.message);
  }
}

// Every ambiguous abbreviation in a field actually SHOWN in a block
// (clinical_history, provisional_diagnosis, tests_required — not
// medication, which isn't displayed anywhere) gets ONE consistent
// treatment: a small clickable "resolve" chip next to its field, opening
// the same candidate-choice popup (see renderAmbiguousChip/
// openAmbiguousPopup below).
function collectAmbiguousItems() {
  const items = [];

  function pushEntry(field, raw, candidates, item) {
    items.push({ field, raw, candidates, item });
  }

  ["clinical_history", "provisional_diagnosis"].forEach((field) => {
    const resolvedTerms = (currentStructured[field] || {}).resolved_terms || [];
    resolvedTerms.forEach((item) => {
      if (item.status === "ambiguous") pushEntry(field, item.raw_phrase, item.candidates, item);
    });
  });

  (currentStructured.tests_required || []).forEach((item) => {
    if (item.status === "ambiguous") pushEntry("tests_required", item.raw, item.candidates, item);
  });

  // medication is intentionally not collected here — it's never shown in
  // any block (see BLOCK_DEFS), so there'd be nowhere to render its chip.

  return items;
}

// A small clickable chip for one ambiguous entry — opens the same
// candidate-choice popup tests_required's own ambiguous items already use.
function renderAmbiguousChip(entry) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ambiguous-resolve-btn";
  btn.textContent = `"${entry.raw}" — choose what you meant`;
  btn.addEventListener("click", () => openAmbiguousPopup(btn, entry));
  return btn;
}

// Builds the two AM/PM quick-pick buttons for a time field that was
// dictated without am/pm (datetime_normalize.normalize_time() already
// refuses to guess — see backend/field_extraction.py). Clicking one
// calls POST /normalize-time (via saveDateOrTimeField) with the SAME raw
// text + the chosen suffix, so "what counts as a valid time" has exactly
// one definition, never duplicated in JavaScript.
function renderAmPmQuickPick(fieldKey, raw) {
  const wrap = document.createElement("span");
  wrap.className = "ampm-prompt";
  wrap.appendChild(document.createTextNode(`"${raw}" — `));

  const amBtn = document.createElement("button");
  amBtn.type = "button";
  amBtn.className = "ampm-btn";
  amBtn.textContent = `${raw} AM`;
  amBtn.addEventListener("click", () => saveDateOrTimeField(fieldKey, "time", `${raw} AM`));
  wrap.appendChild(amBtn);

  wrap.appendChild(document.createTextNode(" "));

  const pmBtn = document.createElement("button");
  pmBtn.type = "button";
  pmBtn.className = "ampm-btn";
  pmBtn.textContent = `${raw} PM`;
  pmBtn.addEventListener("click", () => saveDateOrTimeField(fieldKey, "time", `${raw} PM`));
  wrap.appendChild(pmBtn);

  return wrap;
}

function appendFieldRow(dl, label, valueNode) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.appendChild(valueNode);
  dl.appendChild(dt);
  dl.appendChild(dd);
  return dd;
}

// A single-line editable field, live in its block — never a separate
// free-text re-parse step. Saves on focusout (or Enter), and only if the
// value actually changed, so clicking in and back out of an untouched
// field never fires a network call.
function createFieldInput(initialValue, placeholder, onSave) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "field-input";
  input.value = initialValue || "";
  input.placeholder = placeholder;
  input.addEventListener("focusout", () => {
    if (input.value === initialValue) return;
    onSave(input.value);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur(); // triggers the focusout handler above
    }
  });
  return input;
}

function createFieldTextarea(initialValue, onSave) {
  const textarea = document.createElement("textarea");
  textarea.className = "field-textarea";
  textarea.value = initialValue || "";
  textarea.placeholder = "Not dictated — click to add";
  textarea.addEventListener("focusout", () => {
    if (textarea.value === initialValue) return;
    onSave(textarea.value);
  });
  return textarea;
}

// Pure client-side update — specimen_type/specimen_site/reason_for_request/
// hospital/ward/patient_id/priority/patient_name are raw passthrough with
// no server-side normalization at all, so there's nothing to call out to.
async function savePlainField(key, newValue) {
  const trimmed = newValue.trim();
  currentStructured[key] = trimmed ? { raw: trimmed, value: trimmed, status: "extracted" } : null;
  await rebuildNormalizedText();
  updateTranscriptView();
  updateHistoryText(currentEntryId, currentNormalizedText);
}

// Shared by every date_*/time_* field, the inline input's save handler AND
// the AM/PM quick-pick buttons — "what counts as a valid date/time" has
// exactly one definition each (datetime_normalize.normalize_date()/
// normalize_time()), never duplicated in JavaScript.
async function saveDateOrTimeField(key, kind, newRawText) {
  const raw = newRawText.trim();
  if (!raw) {
    currentStructured[key] = null;
    currentStructured[`${key}_status`] = "ambiguous";
    currentStructured[`${key}_raw`] = "";
    await rebuildNormalizedText();
    updateTranscriptView();
    updateHistoryText(currentEntryId, currentNormalizedText);
    return;
  }
  const endpoint = kind === "time" ? "/normalize-time" : "/normalize-date";
  try {
    const res = await backendFetch(`${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    currentStructured[key] = data.value;
    currentStructured[`${key}_status`] = data.status;
    currentStructured[`${key}_raw`] = raw;
    await rebuildNormalizedText();
    updateTranscriptView();
    updateHistoryText(currentEntryId, currentNormalizedText);
    setStatus(
      data.status === "confirmed"
        ? `${kind === "time" ? "Time" : "Date"} resolved → ${data.value}.`
        : `Still ambiguous: "${raw}" — try again with the full date/time.`
    );
  } catch (err) {
    console.error(err);
    setStatus(`Failed to resolve ${key}: ` + err.message);
  }
}

// Shared by clinical_history/provisional_diagnosis/medication's inline
// textarea — re-resolves ONLY this one field (abbreviation expansion +
// ambiguous-term flagging) from its own raw text, via the exact same
// clinical_terminology.resolve_field_text() field_extraction.py already
// calls internally, never a whole-transcript re-parse.
async function saveClinicalField(key, newRawText) {
  try {
    const res = await backendFetch(`/resolve-clinical-field`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field_key: key, raw_text: newRawText, structured: currentStructured }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    currentStructured[key] = data.field;
    await rebuildNormalizedText();
    updateTranscriptView();
    updateHistoryText(currentEntryId, currentNormalizedText);
  } catch (err) {
    console.error(err);
    setStatus(`Failed to update ${key}: ` + err.message);
  }
}

function renderBlockField(dl, key, label, kind) {
  if (kind === "plain") {
    const field = currentStructured[key];
    const value = field && field.value ? field.value : "";
    const input = createFieldInput(value, "Not dictated — click to add", (newValue) =>
      savePlainField(key, newValue)
    );
    appendFieldRow(dl, label, input);
    return;
  }

  if (kind === "date" || kind === "time") {
    const raw = currentStructured[`${key}_raw`] || "";
    const status = currentStructured[`${key}_status`];
    const confirmedValue = currentStructured[key];
    // Once resolved, show the clean resolved value (e.g. "2026-10-06" /
    // "07:55"), not the raw dictated phrase — only an unresolved field
    // shows its raw text, as the thing still needing to be fixed.
    const displayValue = status === "confirmed" && confirmedValue ? confirmedValue : raw;
    const input = createFieldInput(displayValue, "Not dictated — click to add", (newValue) =>
      saveDateOrTimeField(key, kind, newValue)
    );
    if (raw && status !== "confirmed") input.classList.add("field-input-unconfirmed");
    const dd = appendFieldRow(dl, label, input);

    // One-click shortcut alongside the input, only when am/pm is plausibly
    // the ONLY thing missing — anything stranger ("around three", "this
    // morning") still just needs the full corrected text typed in.
    if (kind === "time" && status !== "confirmed" && raw && !/[ap]\.?\s*m\.?/i.test(raw)) {
      dd.appendChild(renderAmPmQuickPick(key, raw));
    }
    return;
  }

  if (kind === "clinical") {
    const field = currentStructured[key];
    const raw = (field && field.raw) || "";
    const textarea = createFieldTextarea(raw, (newValue) => saveClinicalField(key, newValue));
    const dd = appendFieldRow(dl, label, textarea);

    const items = collectAmbiguousItems().filter((entry) => entry.field === key);
    if (items.length) {
      const chipList = document.createElement("div");
      chipList.className = "ambiguous-chip-list";
      items.forEach((entry) => chipList.appendChild(renderAmbiguousChip(entry)));
      dd.appendChild(chipList);
    }
  }
}

function renderAllBlocks() {
  BLOCK_DEFS.forEach(([dl, fields]) => {
    dl.innerHTML = "";
    fields.forEach(([key, label, kind]) => renderBlockField(dl, key, label, kind));
  });
}

// Everything the doctor must still do before "Done" is allowed — see
// README's required-field list (mirrors the dictation proforma). Returns
// human-readable labels, not field keys, so the banner can show them
// directly.
function getIncompleteFields(structured) {
  const missing = [];

  REQUIRED_PLAIN_FIELDS.forEach(([key, label]) => {
    const field = structured[key];
    if (!field || !field.value || !field.value.trim()) missing.push(label);
  });

  REQUIRED_CLINICAL_FIELDS.forEach(([key, label]) => {
    const field = structured[key];
    if (!field || !field.value || !field.value.trim()) missing.push(label);
  });

  REQUIRED_DATE_TIME_FIELDS.forEach(([key, label]) => {
    if (structured[`${key}_status`] !== "confirmed") missing.push(label);
  });

  const tests = structured.tests_required || [];
  if (tests.length === 0) missing.push("Tests required");

  const hasUnresolvedAmbiguous =
    tests.some((t) => t.status === "ambiguous") ||
    // medication is intentionally not checked here — it's never shown in
    // any block (see BLOCK_DEFS), so there'd be no chip to resolve it
    // with; an ambiguous term there must never block Done.
    ["clinical_history", "provisional_diagnosis"].some((key) => {
      const field = structured[key];
      return field && (field.resolved_terms || []).some((t) => t.status === "ambiguous");
    });
  if (hasUnresolvedAmbiguous) {
    missing.push("Unresolved ambiguous term(s) — tap \"choose what you meant\" to resolve");
  }

  return missing;
}

// The single place that decides whether "Done" is allowed and what the
// incomplete-request banner shows — called after every transcription,
// resolve action, edit, and checkbox change, so it's never possible for
// the button to drift out of sync with the actual data. The banner itself
// only makes sense next to the structured blocks, so it stays hidden
// while viewing the raw transcript.
function refreshValidation() {
  const incomplete = currentEntryId ? getIncompleteFields(currentStructured) : [];

  if (showingRaw || incomplete.length === 0) {
    incompleteBanner.classList.add("hidden");
    incompleteList.innerHTML = "";
  } else {
    incompleteList.innerHTML = "";
    incomplete.forEach((label) => {
      const li = document.createElement("li");
      li.textContent = label;
      incompleteList.appendChild(li);
    });
    incompleteBanner.classList.remove("hidden");
  }

  doneBtn.disabled =
    !currentEntryId || incomplete.length > 0 || !specimenConfirmCheckbox.checked;
}

specimenConfirmCheckbox.addEventListener("change", refreshValidation);

// Regenerates currentNormalizedText (the flat string that's actually
// saved/printed/looked-up) from the CURRENT currentStructured, via the
// same build_normalized_text() reconstruction the backend already uses
// everywhere else — never hand-patched in JavaScript. Called after any
// action that mutates a structured field directly (ambiguous-term
// resolve, AM/PM resolve) rather than through the free-text Edit box.
async function rebuildNormalizedText() {
  try {
    const res = await backendFetch(`/rebuild-transcript`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ structured: currentStructured }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    currentNormalizedText = data.normalized_text || currentNormalizedText;
  } catch (err) {
    console.error("Failed to rebuild transcript text:", err);
    // The live block view (read straight from currentStructured) is
    // still correct even if this particular resync failed — only the
    // flat string used for History/submission would lag until the next
    // successful resolve.
  }
}

function updateTranscriptView() {
  if (showingRaw) {
    transcriptEl.textContent = currentRawText || "(no speech detected)";
    transcriptEl.classList.remove("hidden");
    blocksContainer.classList.add("hidden");
  } else {
    transcriptEl.classList.add("hidden");
    blocksContainer.classList.remove("hidden");
    renderAllBlocks();
  }
  toggleRawBtn.textContent = showingRaw
    ? "View normalized transcript"
    : "View raw transcript";
  refreshValidation();
}

function resetTranscriptView() {
  currentNormalizedText = "";
  currentRawText = "";
  currentStructured = {};
  currentEntryId = null;
  showingRaw = false;
  specimenConfirmCheckbox.checked = false;
  updateTranscriptView();
}

toggleRawBtn.addEventListener("click", () => {
  showingRaw = !showingRaw;
  updateTranscriptView();
});

// ---------------------------------------------------------------------------
// Inline ambiguous-term click-to-choose popup
// ---------------------------------------------------------------------------

let activePopup = null;
let dismissPopupListener = null;

function closeAmbiguousPopup() {
  if (activePopup) {
    activePopup.remove();
    activePopup = null;
  }
  if (dismissPopupListener) {
    document.removeEventListener("click", dismissPopupListener, true);
    dismissPopupListener = null;
  }
}

// Opened directly by whichever chip/button triggered it (renderAmbiguousChip
// for clinical/medication fields, the equivalent button in renderTests() for
// tests_required) — each already closes over its own `entry`, no delegated
// listener or DOM lookup needed.

function openAmbiguousPopup(anchorEl, entry) {
  closeAmbiguousPopup();

  const popup = document.createElement("div");
  popup.className = "ambiguous-popup";

  const title = document.createElement("p");
  title.className = "ambiguous-popup-title";
  title.textContent = `"${entry.raw}" — which did you mean?`;
  popup.appendChild(title);

  const list = document.createElement("div");
  list.className = "ambiguous-popup-list";

  entry.candidates.forEach((cand) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ambiguous-popup-option";

    const name = document.createElement("span");
    name.className = "option-name";
    name.textContent = cand.canonical_name;
    btn.appendChild(name);

    const detail = document.createElement("span");
    detail.className = "option-detail";
    detail.textContent = `${cand.domain.replace(/_/g, " ")} — ${cand.reason}`;
    btn.appendChild(detail);

    btn.addEventListener("click", () => resolveAmbiguousItem(entry, cand));
    list.appendChild(btn);
  });

  const noneBtn = document.createElement("button");
  noneBtn.type = "button";
  noneBtn.className = "ambiguous-popup-option none-option";
  const noneName = document.createElement("span");
  noneName.className = "option-name";
  noneName.textContent = "None of these / keep original";
  noneBtn.appendChild(noneName);
  noneBtn.addEventListener("click", closeAmbiguousPopup);
  list.appendChild(noneBtn);

  popup.appendChild(list);
  document.body.appendChild(popup);

  const rect = anchorEl.getBoundingClientRect();
  const maxLeft = window.innerWidth - popup.offsetWidth - 16;
  popup.style.top = `${rect.bottom + 6}px`;
  popup.style.left = `${Math.max(16, Math.min(rect.left, maxLeft))}px`;

  activePopup = popup;
  // Deferred so the click that opened the popup doesn't immediately close it.
  setTimeout(() => {
    dismissPopupListener = (e) => {
      if (!popup.contains(e.target)) closeAmbiguousPopup();
    };
    document.addEventListener("click", dismissPopupListener, true);
  }, 0);
}

async function resolveAmbiguousItem(entry, candidate) {
  // Mutate the underlying item in place so re-rendering (blocks AND the
  // Tests required panel) reflects the resolution and doesn't re-highlight it.
  Object.assign(entry.item, {
    status: "confirmed",
    confirmation_status: "clinician_confirmed",
    candidates: null,
    reason: null,
  });
  if (entry.field === "tests_required") {
    entry.item.normalized = candidate.canonical_name;
    entry.item.source = candidate.domain;
    entry.item.terminology_system = candidate.terminology_system;
    entry.item.code = candidate.code;
    entry.item.match_type = "ambiguous_abbreviation";
  } else {
    entry.item.normalized_term = candidate.canonical_name;
    entry.item.terminology_system = candidate.terminology_system;
    entry.item.code = candidate.code;
    entry.item.match_type = "ambiguous_abbreviation";
  }

  closeAmbiguousPopup();
  // Regenerate the flat normalized_text from the now-updated
  // currentStructured (build_normalized_text() server-side), rather than
  // hand-splicing the marker string — one reconstruction path, not two.
  await rebuildNormalizedText();
  updateTranscriptView();
  if (entry.field === "tests_required") {
    renderTests(currentEntryId, currentStructured.tests_required || []);
    await refreshSpecimenRequirements();
  }
  updateHistoryText(currentEntryId, currentNormalizedText);
  setStatus(`Resolved "${entry.raw}" → ${candidate.canonical_name}.`);
}

// Specimen groups are shown from structured.specimen_requirements, which
// the backend computes once from tests_required at transcription time
// (field_extraction.py). Resolving an inline ambiguous test changes
// tests_required client-side afterwards, so that snapshot goes stale —
// this asks the SAME curated mapping (specimen_mapping.py, via
// POST /specimen-requirements) to recompute it from the current
// tests_required, rather than duplicating that dictionary in JS where it
// could drift out of sync with the Python one. Never blocks/breaks the
// rest of the resolution flow if the request fails — the specimen
// section just keeps showing its last-known groups.
async function refreshSpecimenRequirements() {
  try {
    const res = await backendFetch(`/specimen-requirements`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tests_required: currentStructured.tests_required || [] }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    currentStructured.specimen_requirements = data.specimen_requirements;
    renderSpecimenGroups(currentStructured.specimen_requirements);
    // A stale confirmation must never silently survive a change to what
    // it's confirming — force the doctor to re-check it.
    specimenConfirmCheckbox.checked = false;
    refreshValidation();
  } catch (err) {
    console.error("Failed to refresh specimen requirements:", err);
  }
}

// ---------------------------------------------------------------------------
// Tests required — checkbox confirm/exclude for confirmed items. Ambiguous
// items resolve inline in the transcript above (see openAmbiguousPopup); this
// panel just shows a pointer back there until that happens.
// ---------------------------------------------------------------------------

function hideTests() {
  testsSection.classList.add("hidden");
  testsList.innerHTML = "";
  lastTestsRequired = [];
}

// Purely additive/display-only: renders backend/specimen_mapping.py's
// grouping of the CONFIRMED tests_required collection. Never edits the
// Tests required section above, never recomputed client-side (e.g.
// resolving an ambiguous test inline does NOT update this — it reflects
// exactly what the server computed from tests_required at transcription
// time), and hides entirely rather than showing an empty section when
// there's nothing to group (e.g. no tests were dictated at all).
function renderSpecimenGroups(groups) {
  renderSpecimenGroupsInto(specimenGroups, specimenSection, groups);
}

function hideSpecimenGroups() {
  specimenGroups.innerHTML = "";
  specimenSection.classList.add("hidden");
}

function renderTests(entryId, testsRequired) {
  lastTestsRequired = testsRequired;
  testsSection.dataset.entryId = entryId;
  testsList.innerHTML = "";
  testsEmpty.classList.toggle("hidden", testsRequired.length > 0);

  testsRequired.forEach((item, index) => {
    const el = document.createElement("li");
    el.className = "match-item" + (item.status !== "confirmed" ? " " + item.status : "");

    if (item.status === "confirmed") {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.testIndex = String(index);
      // Every item that reaches here already cleared a real confidence
      // bar (curated abbreviation, exact match, strict fuzzy cutoff, or a
      // clinician's own inline disambiguation choice), so it starts checked.
      checkbox.checked = true;
      el.appendChild(checkbox);
    }

    const body = document.createElement("div");
    body.className = "match-body";

    const candidate = document.createElement("p");
    candidate.className = "match-candidate";
    candidate.textContent = `Heard: "${item.raw}"`;
    body.appendChild(candidate);

    if (item.status === "confirmed") {
      const name = document.createElement("p");
      name.className = "match-name";
      name.textContent = item.normalized;
      body.appendChild(name);

      const meta = document.createElement("div");
      meta.className = "match-meta";

      const badge = document.createElement("span");
      badge.className = "source-badge" + (item.source === "loinc" ? " loinc" : "");
      badge.textContent =
        item.source === "abbreviation" ? "ABBREV" : item.source === "loinc" ? "LOINC" : "NHLS";
      meta.appendChild(badge);

      if (typeof item.score === "number") {
        const score = document.createElement("span");
        score.className = "match-score";
        score.textContent = `${Math.round(item.score)}% match`;
        meta.appendChild(score);
      }

      body.appendChild(meta);
    } else if (item.status === "ambiguous") {
      const label = document.createElement("p");
      label.className = "match-name";
      label.textContent = "Ambiguous medical abbreviation";
      body.appendChild(label);

      const resolveBtn = document.createElement("button");
      resolveBtn.type = "button";
      resolveBtn.className = "ambiguous-resolve-btn";
      resolveBtn.textContent = `"${item.raw}" — choose what you meant`;
      resolveBtn.addEventListener("click", () => {
        openAmbiguousPopup(resolveBtn, { field: "tests_required", raw: item.raw, candidates: item.candidates, item });
      });
      body.appendChild(resolveBtn);
    } else {
      const warning = document.createElement("p");
      warning.className = "match-name";
      warning.textContent = "Unrecognized — fix it directly below";
      body.appendChild(warning);

      const fixRow = document.createElement("div");
      fixRow.className = "unrecognized-fix-row";
      const fixInput = createFieldInput(item.raw, "Correct test name", (newRaw) =>
        saveTestItemFix(index, newRaw)
      );
      fixRow.appendChild(fixInput);
      body.appendChild(fixRow);
    }

    el.appendChild(body);

    // Removing a test is always available, regardless of status — covers
    // a garbled/unwanted item (e.g. a stray "priority agent" phrase
    // misheard as a test name) as well as a confirmed test the doctor
    // simply doesn't want, without needing to re-dictate anything.
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-test-btn";
    removeBtn.setAttribute("aria-label", `Remove "${item.raw}"`);
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => removeTestItem(index));
    el.appendChild(removeBtn);

    testsList.appendChild(el);
  });

  testsSection.classList.remove("hidden");
}

// Removing a test never calls the backend — nothing to re-normalize,
// just drop the one entry and resync everything downstream of the list
// (tube groupings, the specimen-confirm checkbox, Done gating).
async function removeTestItem(index) {
  const [removed] = currentStructured.tests_required.splice(index, 1);
  await rebuildNormalizedText();
  renderTests(currentEntryId, currentStructured.tests_required);
  await refreshSpecimenRequirements(); // also re-validates (see its own docstring)
  updateHistoryText(currentEntryId, currentNormalizedText);
  setStatus(removed ? `Removed "${removed.raw}".` : "Test removed.");
}

// Shared by an unrecognized item's inline fix AND "+ Add test" below —
// both resolve exactly ONE test name via the exact same
// terminology_normalize.normalize_tests_required() the initial dictation
// itself goes through, never touching any other item in the list.
async function resolveOneTestName(raw) {
  const res = await backendFetch(`/normalize-test-item`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw, structured: currentStructured }),
  });
  if (!res.ok) throw new Error(`Server responded ${res.status}`);
  const data = await res.json();
  return data.item;
}

async function saveTestItemFix(index, newRaw) {
  const trimmed = newRaw.trim();
  if (!trimmed) return;
  try {
    const item = await resolveOneTestName(trimmed);
    currentStructured.tests_required[index] = item;
    await rebuildNormalizedText();
    renderTests(currentEntryId, currentStructured.tests_required);
    await refreshSpecimenRequirements(); // also re-validates (see its own docstring)
    updateHistoryText(currentEntryId, currentNormalizedText);
    setStatus(
      item.status === "confirmed"
        ? `Resolved "${trimmed}" → ${item.normalized}.`
        : `"${trimmed}" is still not recognized — check the spelling.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Failed to update test: " + err.message);
  }
}

async function addTest() {
  const trimmed = addTestInput.value.trim();
  if (!trimmed) return;
  try {
    const item = await resolveOneTestName(trimmed);
    currentStructured.tests_required = currentStructured.tests_required || [];
    currentStructured.tests_required.push(item);
    addTestInput.value = "";
    await rebuildNormalizedText();
    renderTests(currentEntryId, currentStructured.tests_required);
    await refreshSpecimenRequirements(); // also re-validates (see its own docstring)
    updateHistoryText(currentEntryId, currentNormalizedText);
    setStatus(
      item.status === "confirmed"
        ? `Added "${trimmed}" → ${item.normalized}.`
        : `Added "${trimmed}" — not recognized, check the spelling.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Failed to add test: " + err.message);
  }
}

addTestBtn.addEventListener("click", addTest);
addTestInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addTest();
  }
});

confirmTestsBtn.addEventListener("click", () => {
  const entryId = testsSection.dataset.entryId;
  const accepted = [];

  lastTestsRequired.forEach((item, index) => {
    if (item.status !== "confirmed") return; // still ambiguous/unrecognized: excluded.
    const checkbox = testsList.querySelector(
      `input[type="checkbox"][data-test-index="${index}"]`
    );
    if (checkbox && checkbox.checked) {
      accepted.push(item);
    }
  });

  confirmHistoryTests(entryId, accepted, currentNormalizedText);
  hideTests();
  setStatus(`Saved ${accepted.length} confirmed test(s) to history.`);
});

skipTestsBtn.addEventListener("click", () => {
  hideTests();
});

// ---------------------------------------------------------------------------
// Finalize request: print the barcode label (server-side, via NIIMBOT —
// see backend/label_printing.py) and keep a digital copy lab staff can
// look up by scanning it (backend/print_records.py). This is a distinct,
// explicit final step, separate from the Tests required panel's own
// confirm/save — pressing it doesn't depend on that panel being used.
// ---------------------------------------------------------------------------

function stopPrintStatusPolling() {
  if (printStatusPollTimer) {
    clearTimeout(printStatusPollTimer);
    printStatusPollTimer = null;
  }
}

function showPrintSection() {
  printStatus.textContent = "";
  printLabelImage.classList.add("hidden");
  printLabelImage.removeAttribute("src");
  retryPrintBtn.classList.add("hidden");
  stopPrintStatusPolling();
  printSection.classList.remove("hidden");
}

function hidePrintSection() {
  printSection.classList.add("hidden");
  printStatus.textContent = "";
  printLabelImage.classList.add("hidden");
  printLabelImage.removeAttribute("src");
  retryPrintBtn.classList.add("hidden");
  stopPrintStatusPolling();
}

// Printing is asynchronous: /print-label only queues a print job (see
// backend/print_jobs.py) and returns immediately — a separate local
// print agent (backend/print_agent.py) claims it and does the actual
// printing, possibly seconds later, possibly on a machine far from
// wherever this browser is. This polls GET /print-status/{request_id}
// every 3s until that agent reports a final printed/failed outcome.
// Closing the browser just stops this polling loop — the queued job
// keeps processing server-side regardless (see README's "Local print
// agent" section).
async function pollPrintStatus(requestId) {
  if (requestId !== currentRequestId) return; // superseded by a newer submission
  try {
    const res = await backendFetch(`/print-status/${encodeURIComponent(requestId)}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();

    if (data.print_status === "printed") {
      printStatus.textContent = `Printed — Request ID: ${requestId}`;
      retryPrintBtn.classList.add("hidden");
      return;
    }
    if (data.print_status === "failed") {
      // A printer failure never looks like data loss: the digital copy
      // is already saved server-side regardless of print_status.
      printStatus.textContent =
        `Digital copy saved (Request ID: ${requestId}) — ` +
        `printer error: ${data.print_error}. Check the print station.`;
      retryPrintBtn.classList.remove("hidden");
      return;
    }

    printStatus.textContent = `Request submitted — Request ID: ${requestId}. Waiting for printer…`;
    printStatusPollTimer = setTimeout(() => pollPrintStatus(requestId), 3000);
  } catch (err) {
    console.error(err);
    printStatus.textContent = "Failed to reach the server: " + err.message;
    printStatusPollTimer = setTimeout(() => pollPrintStatus(requestId), 3000);
  }
}

doneBtn.addEventListener("click", async () => {
  if (!currentDoctor) {
    printStatus.textContent = "Not logged in — please log in again.";
    return;
  }

  doneBtn.disabled = true;
  retryPrintBtn.classList.add("hidden");
  printStatus.textContent = "Submitting…";

  try {
    const res = await backendFetch(`/print-label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doctor_name: currentDoctor.name,
        doctor_phone: currentDoctor.cell,
        hpcsa_number: currentDoctor.hpcsa_number,
        raw_text: currentRawText,
        normalized_text: currentNormalizedText,
        structured: currentStructured,
      }),
    });

    if (!res.ok) {
      throw new Error(`Server responded ${res.status}`);
    }

    const data = await res.json();
    currentRequestId = data.request_id;
    printStatus.textContent = `Request submitted — Request ID: ${data.request_id}. Waiting for printer…`;

    // Two identical barcodes for this request: the one a print agent
    // will eventually send to the physical printer, and this one (the
    // exact same image, rendered once here) shown immediately and saved
    // onto the History entry — never re-rendered, so they can't drift.
    if (data.label_image) {
      printLabelImage.src = data.label_image;
      printLabelImage.classList.remove("hidden");
      if (currentEntryId) {
        attachLabelImage(currentEntryId, data.label_image);
      }
    }

    stopPrintStatusPolling();
    printStatusPollTimer = setTimeout(() => pollPrintStatus(data.request_id), 3000);
  } catch (err) {
    console.error(err);
    printStatus.textContent = "Failed to reach the server: " + err.message;
  } finally {
    // Re-enable only if the request is still actually complete — a
    // failed submit must not leave Done clickable past validation.
    refreshValidation();
  }
});

retryPrintBtn.addEventListener("click", async () => {
  if (!currentRequestId) return;
  retryPrintBtn.disabled = true;
  printStatus.textContent = "Retrying…";

  try {
    const res = await backendFetch(
      `/print-jobs/${encodeURIComponent(currentRequestId)}/retry`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    retryPrintBtn.classList.add("hidden");
    stopPrintStatusPolling();
    printStatusPollTimer = setTimeout(() => pollPrintStatus(currentRequestId), 500);
  } catch (err) {
    console.error(err);
    printStatus.textContent = "Failed to reach the server: " + err.message;
  } finally {
    retryPrintBtn.disabled = false;
  }
});
