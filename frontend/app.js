// BACKEND_URL comes from config.js (loaded before this file — see
// index.html's <script> order) so it's configured in exactly one place
// for the whole frontend, not duplicated per page.

const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const toggleRawBtn = document.getElementById("toggleRawBtn");
const editTranscriptBtn = document.getElementById("editTranscriptBtn");
const transcriptEditArea = document.getElementById("transcriptEditArea");
const transcriptEditActions = document.getElementById("transcriptEditActions");
const saveTranscriptEditBtn = document.getElementById("saveTranscriptEditBtn");
const cancelTranscriptEditBtn = document.getElementById("cancelTranscriptEditBtn");

const testsSection = document.getElementById("testsSection");
const testsList = document.getElementById("testsList");
const testsEmpty = document.getElementById("testsEmpty");
const confirmTestsBtn = document.getElementById("confirmTestsBtn");
const skipTestsBtn = document.getElementById("skipTestsBtn");

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
  [blockPatient, [
    ["patient_name", "Patient name", "plain"],
    ["date_of_birth", "Date of birth", "date"],
    ["patient_id", "Patient hospital number", "plain"],
  ]],
  [blockClinical, [
    ["clinical_history", "Clinical history", "clinical"],
    ["provisional_diagnosis", "Provisional diagnosis", "clinical"],
    ["medication", "Relevant medication", "clinical"],
  ]],
  [blockPriority, [
    ["priority", "Priority", "plain"],
    ["date_requested", "Date requested", "date"],
    ["time_requested", "Time requested", "time"],
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
let editingTranscript = false; // see enterEditMode()/saveTranscriptEdit()/cancelTranscriptEdit()
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
    const res = await fetch(`${BACKEND_URL}/transcribe`, {
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

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// Every ambiguous abbreviation anywhere in the current structured result —
// regardless of which field it came from (clinical_history,
// provisional_diagnosis, tests_required, medication) — gets ONE consistent
// treatment: it's highlighted directly in the transcript text, and clicking
// it pops up its candidate list right there. This replaces separate
// below-transcript panels for each field with a single, discoverable
// interaction.
//
// Order matters here: it MUST match the field order build_normalized_text
// (backend/field_extraction.py's _DISPLAY_FIELDS) actually writes the
// transcript in — clinical_history, provisional_diagnosis, tests_required,
// medication — so that if the SAME abbreviation is ambiguous in two
// different fields (e.g. "TB" in both Provisional diagnosis and Tests
// required), the two identical marker strings in the text get paired up
// with the right underlying item, not swapped.
function collectAmbiguousItems() {
  const items = [];
  // Tracks, per distinct marker STRING, how many times we've seen it so
  // far in this pass — gives each entry an `occurrenceIndex` (its 0-based
  // position among identical markers) which both rendering and resolution
  // use to target the correct occurrence, never a blanket replace-all.
  const occurrenceCounters = new Map();

  function pushEntry(field, raw, candidates, item) {
    const marker = `${raw} [ambiguous — please confirm]`;
    // Keyed per (field, marker) — highlightAmbiguousMarkers() now searches
    // within one field's own isolated text at a time (one block each),
    // not the single whole-transcript string this was originally written
    // for, so the occurrence count must restart per field.
    const counterKey = `${field}::${marker}`;
    const occurrenceIndex = occurrenceCounters.get(counterKey) || 0;
    occurrenceCounters.set(counterKey, occurrenceIndex + 1);
    items.push({ field, raw, candidates, item, marker, occurrenceIndex });
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

  const medicationTerms = (currentStructured.medication || {}).resolved_terms || [];
  medicationTerms.forEach((item) => {
    if (item.status === "ambiguous") pushEntry("medication", item.raw_phrase, item.candidates, item);
  });

  return items;
}

// Finds the start index of the (n+1)th occurrence of `needle` in
// `haystack` (0-based `n`), or -1 if there aren't that many.
function nthIndexOf(haystack, needle, n) {
  let from = 0;
  for (let i = 0; i < n; i++) {
    const pos = haystack.indexOf(needle, from);
    if (pos === -1) return -1;
    from = pos + needle.length;
  }
  return haystack.indexOf(needle, from);
}

const ambiguousItemsById = new Map();

// Wraps every marker in `items` (a pre-filtered subset of
// collectAmbiguousItems(), one field's worth) that's found inside `text`
// in a clickable <span>. This is the SAME algorithm the old whole-
// transcript renderer used, just scoped to one field's own text instead
// of the full reconstructed paragraph — called once per clinical block
// field (Clinical history / Provisional diagnosis / Relevant medication),
// so IDs accumulate across a render pass; callers clear
// ambiguousItemsById ONCE before the first call, not per field.
function highlightAmbiguousMarkers(text, items) {
  const escaped = escapeHtml(text);

  const insertions = [];
  items.forEach((entry) => {
    const escapedMarker = escapeHtml(entry.marker);
    const pos = nthIndexOf(escaped, escapedMarker, entry.occurrenceIndex);
    if (pos === -1) return; // not present in this field's text — nothing to highlight
    const id = `amb-${ambiguousItemsById.size}`;
    ambiguousItemsById.set(id, entry);
    insertions.push({ start: pos, end: pos + escapedMarker.length, id, html: escapedMarker });
  });

  insertions.sort((a, b) => a.start - b.start);

  let html = "";
  let cursor = 0;
  insertions.forEach(({ start, end, id, html: markerHtml }) => {
    html += escaped.slice(cursor, start);
    html += `<span class="ambiguous-highlight" data-amb-id="${id}" tabindex="0" role="button">${markerHtml}</span>`;
    cursor = end;
  });
  html += escaped.slice(cursor);
  return html;
}

// Builds the two AM/PM quick-pick buttons for a time field that was
// dictated without am/pm (datetime_normalize.normalize_time() already
// refuses to guess — see backend/field_extraction.py). Clicking one
// calls POST /normalize-time with the SAME raw text + the chosen suffix,
// so "what counts as a valid time" has exactly one definition, never
// duplicated in JavaScript.
function renderAmPmQuickPick(fieldKey, raw) {
  const escapedRaw = escapeHtml(raw);
  return (
    `<span class="ampm-prompt">"${escapedRaw}" — ` +
    `<button type="button" class="ampm-btn" data-field-key="${fieldKey}" data-raw="${escapedRaw}" data-ampm="AM">${escapedRaw} AM</button> ` +
    `<button type="button" class="ampm-btn" data-field-key="${fieldKey}" data-raw="${escapedRaw}" data-ampm="PM">${escapedRaw} PM</button>` +
    `</span>`
  );
}

function appendFieldRow(dl, label, valueHtml) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.innerHTML = valueHtml;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function renderBlockField(dl, key, label, kind) {
  if (kind === "plain") {
    const field = currentStructured[key];
    if (!field || !field.value) return; // skip empty — same convention as build_normalized_text
    appendFieldRow(dl, label, escapeHtml(field.value));
    return;
  }

  if (kind === "date" || kind === "time") {
    if (!(key in currentStructured)) return;
    const value = currentStructured[key];
    const status = currentStructured[`${key}_status`];
    if (status === "confirmed" && value) {
      appendFieldRow(dl, label, escapeHtml(value));
      return;
    }
    const raw = currentStructured[`${key}_raw`] || "";
    if (!raw) return; // nothing was dictated for this field at all
    // Only offer the quick-pick when am/pm is plausibly the ONLY thing
    // missing — anything stranger ("around three", "this morning") still
    // falls back to the plain "[unconfirmed]" text + Edit transcript.
    if (kind === "time" && !/[ap]\.?\s*m\.?/i.test(raw)) {
      appendFieldRow(dl, label, renderAmPmQuickPick(key, raw));
    } else {
      appendFieldRow(
        dl, label,
        `<span class="field-unconfirmed">${escapeHtml(raw)} [unconfirmed — please verify]</span>`
      );
    }
    return;
  }

  if (kind === "clinical") {
    const field = currentStructured[key];
    if (!field || !field.value) return;
    const items = collectAmbiguousItems().filter((entry) => entry.field === key);
    appendFieldRow(dl, label, highlightAmbiguousMarkers(field.value, items));
  }
}

function renderAllBlocks() {
  ambiguousItemsById.clear(); // one shared id sequence across the whole render pass
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
    ["clinical_history", "provisional_diagnosis", "medication"].some((key) => {
      const field = structured[key];
      return field && (field.resolved_terms || []).some((t) => t.status === "ambiguous");
    });
  if (hasUnresolvedAmbiguous) {
    missing.push("Unresolved ambiguous term(s) — tap the highlighted text to resolve");
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
    !currentEntryId || editingTranscript || incomplete.length > 0 || !specimenConfirmCheckbox.checked;
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
    const res = await fetch(`${BACKEND_URL}/rebuild-transcript`, {
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

async function resolveTimeAmPm(fieldKey, raw, ampm) {
  try {
    const res = await fetch(`${BACKEND_URL}/normalize-time`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw: `${raw} ${ampm}` }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();

    currentStructured[fieldKey] = data.value;
    currentStructured[`${fieldKey}_status`] = data.status;
    currentStructured[`${fieldKey}_raw`] = `${raw} ${ampm}`;

    await rebuildNormalizedText();
    updateTranscriptView();
    updateHistoryText(currentEntryId, currentNormalizedText);
    setStatus(`Resolved time → ${data.value} (${ampm}).`);
  } catch (err) {
    console.error(err);
    setStatus("Failed to resolve time: " + err.message);
  }
}

// Single delegated listener for both interaction types that live inside
// the six blocks: ambiguous-term highlights (clinical_history/
// provisional_diagnosis/medication) and the AM/PM quick-pick buttons.
blocksContainer.addEventListener("click", (e) => {
  const ampmBtn = e.target.closest(".ampm-btn");
  if (ampmBtn) {
    e.stopPropagation();
    resolveTimeAmPm(ampmBtn.dataset.fieldKey, ampmBtn.dataset.raw, ampmBtn.dataset.ampm);
    return;
  }
  const span = e.target.closest(".ambiguous-highlight");
  if (span) {
    e.stopPropagation();
    const entry = ambiguousItemsById.get(span.dataset.ambId);
    if (entry) openAmbiguousPopup(span, entry);
  }
});

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
  exitEditMode();
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
// Transcript edit mode — free-text correction (e.g. a Whisper spelling
// mistake, or typing in a field the doctor forgot to dictate) before
// confirming. Saving RE-PARSES the edited text through the same
// field_extraction.py used by /transcribe (POST /extract-fields), so the
// six blocks and the completeness check never go stale relative to a
// hand-typed correction — this is also why "least disruptive mechanism
// already supported" doubles as the fix for a missing field: typing
// "Priority: Routine" into this box and saving makes Priority show up
// resolved in its block, exactly as if it had been dictated.
// ---------------------------------------------------------------------------

function enterEditMode() {
  if (!currentNormalizedText && !currentRawText) return; // nothing to edit yet
  showingRaw = false;
  editingTranscript = true;
  transcriptEditArea.value = currentNormalizedText;
  transcriptEl.classList.add("hidden");
  blocksContainer.classList.add("hidden");
  incompleteBanner.classList.add("hidden");
  toggleRawBtn.classList.add("hidden");
  editTranscriptBtn.classList.add("hidden");
  transcriptEditArea.classList.remove("hidden");
  transcriptEditActions.classList.remove("hidden");
  doneBtn.disabled = true; // avoid submitting a stale pre-edit version mid-edit
  transcriptEditArea.focus();
}

function exitEditMode() {
  editingTranscript = false;
  transcriptEditArea.value = "";
  transcriptEditArea.classList.add("hidden");
  transcriptEditActions.classList.add("hidden");
  toggleRawBtn.classList.remove("hidden");
  editTranscriptBtn.classList.remove("hidden");
  updateTranscriptView(); // re-shows raw text or blocks (whichever showingRaw says) + re-validates
}

async function saveTranscriptEdit() {
  const editedText = transcriptEditArea.value;
  saveTranscriptEditBtn.disabled = true;
  try {
    const res = await fetch(`${BACKEND_URL}/extract-fields`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: editedText }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();

    currentStructured = data.structured || {};
    currentNormalizedText = data.normalized_text || editedText;
    exitEditMode();
    if (currentEntryId) {
      renderTests(currentEntryId, currentStructured.tests_required || []);
      renderSpecimenGroups(currentStructured.specimen_requirements || []);
      updateHistoryText(currentEntryId, currentNormalizedText);
    }
    setStatus("Transcript updated.");
  } catch (err) {
    console.error(err);
    setStatus("Failed to save edits: " + err.message);
  } finally {
    saveTranscriptEditBtn.disabled = false;
  }
}

function cancelTranscriptEdit() {
  exitEditMode();
  setStatus("Edit cancelled.");
}

editTranscriptBtn.addEventListener("click", enterEditMode);
saveTranscriptEditBtn.addEventListener("click", saveTranscriptEdit);
cancelTranscriptEditBtn.addEventListener("click", cancelTranscriptEdit);

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

// Ambiguous-term clicks are handled by the single delegated listener on
// blocksContainer (see above, near highlightAmbiguousMarkers) — that's
// where these spans actually live now; transcriptEl is raw-text-only.

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
    const res = await fetch(`${BACKEND_URL}/specimen-requirements`, {
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
      warning.textContent = "Unrecognized — clinician confirmation required";
      body.appendChild(warning);
    }

    el.appendChild(body);
    testsList.appendChild(el);
  });

  testsSection.classList.remove("hidden");
}

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
    const res = await fetch(`${BACKEND_URL}/print-status/${encodeURIComponent(requestId)}`);
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
  const hpcsa = getSession();
  const doctor = hpcsa ? getDoctor(hpcsa) : null;
  if (!doctor) {
    printStatus.textContent = "Not logged in — please log in again.";
    return;
  }

  doneBtn.disabled = true;
  retryPrintBtn.classList.add("hidden");
  printStatus.textContent = "Submitting…";

  try {
    const res = await fetch(`${BACKEND_URL}/print-label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doctor_name: doctor.name,
        doctor_phone: doctor.cell,
        hpcsa_number: doctor.hpcsa,
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
    const res = await fetch(
      `${BACKEND_URL}/print-jobs/${encodeURIComponent(currentRequestId)}/retry`,
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
