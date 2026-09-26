// ===========================================================================
// CONFIG — point this at your backend.
// ---------------------------------------------------------------------------
// Local testing on the same machine: "http://localhost:8000".
// From a PHONE, localhost means the phone itself, so use the laptop's LAN IP,
// e.g. "http://192.168.1.20:8000". See the README for the HTTPS caveat: the mic
// only works on https:// or on http://localhost.
const BACKEND_URL = "http://localhost:8000";
// ===========================================================================

const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const toggleRawBtn = document.getElementById("toggleRawBtn");

const testsSection = document.getElementById("testsSection");
const testsList = document.getElementById("testsList");
const testsEmpty = document.getElementById("testsEmpty");
const confirmTestsBtn = document.getElementById("confirmTestsBtn");
const skipTestsBtn = document.getElementById("skipTestsBtn");

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
    updateTranscriptView();

    currentEntryId = currentNormalizedText
      ? addHistoryEntry(currentNormalizedText, currentRawText)
      : null;
    if (currentEntryId) {
      renderTests(currentEntryId, currentStructured.tests_required || []);
    }

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
    const occurrenceIndex = occurrenceCounters.get(marker) || 0;
    occurrenceCounters.set(marker, occurrenceIndex + 1);
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

function renderNormalizedHtml(text) {
  const escaped = escapeHtml(text);
  const ambiguousItems = collectAmbiguousItems();
  ambiguousItemsById.clear();

  const insertions = [];
  ambiguousItems.forEach((entry, index) => {
    const escapedMarker = escapeHtml(entry.marker);
    const pos = nthIndexOf(escaped, escapedMarker, entry.occurrenceIndex);
    if (pos === -1) return; // not present in the current text — nothing to highlight
    const id = `amb-${index}`;
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

function updateTranscriptView() {
  if (showingRaw) {
    transcriptEl.textContent = currentRawText || "(no speech detected)";
  } else if (currentNormalizedText) {
    transcriptEl.innerHTML = renderNormalizedHtml(currentNormalizedText);
  } else {
    transcriptEl.textContent = "(no speech detected)";
  }
  toggleRawBtn.textContent = showingRaw
    ? "View normalized transcript"
    : "View raw transcript";
}

function resetTranscriptView() {
  currentNormalizedText = "";
  currentRawText = "";
  currentStructured = {};
  currentEntryId = null;
  showingRaw = false;
  transcriptEl.textContent = "—";
  toggleRawBtn.textContent = "View raw transcript";
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

transcriptEl.addEventListener("click", (e) => {
  const span = e.target.closest(".ambiguous-highlight");
  if (!span) return;
  e.stopPropagation();
  const entry = ambiguousItemsById.get(span.dataset.ambId);
  if (entry) {
    openAmbiguousPopup(span, entry);
  }
});

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

function resolveAmbiguousItem(entry, candidate) {
  const replacement = `${candidate.canonical_name} (${entry.raw})`;
  const pos = nthIndexOf(currentNormalizedText, entry.marker, entry.occurrenceIndex);
  if (pos !== -1) {
    currentNormalizedText =
      currentNormalizedText.slice(0, pos) +
      replacement +
      currentNormalizedText.slice(pos + entry.marker.length);
  }

  // Mutate the underlying item in place so re-rendering (transcript AND the
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
  updateTranscriptView();
  if (entry.field === "tests_required") {
    renderTests(currentEntryId, currentStructured.tests_required || []);
  }
  updateHistoryText(currentEntryId, currentNormalizedText);
  setStatus(`Resolved "${entry.raw}" → ${candidate.canonical_name}.`);
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

      const hint = document.createElement("p");
      hint.className = "match-hint";
      hint.textContent = "Tap the highlighted term in the transcript above to choose what you meant.";
      body.appendChild(hint);
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
