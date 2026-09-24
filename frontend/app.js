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
    showingRaw = false;
    updateTranscriptView();

    const structured = data.structured || {};
    const entryId = currentNormalizedText
      ? addHistoryEntry(currentNormalizedText, currentRawText)
      : null;
    if (entryId) {
      renderTests(entryId, structured.tests_required || []);
    }

    setStatus(
      `Done — detected ${data.language}, ${data.duration.toFixed(1)}s of audio.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Transcription failed: " + err.message);
  }
}

function updateTranscriptView() {
  transcriptEl.textContent =
    (showingRaw ? currentRawText : currentNormalizedText) || "(no speech detected)";
  toggleRawBtn.textContent = showingRaw
    ? "View normalized transcript"
    : "View raw transcript";
}

function resetTranscriptView() {
  currentNormalizedText = "";
  currentRawText = "";
  showingRaw = false;
  transcriptEl.textContent = "—";
  toggleRawBtn.textContent = "View raw transcript";
}

toggleRawBtn.addEventListener("click", () => {
  showingRaw = !showingRaw;
  updateTranscriptView();
});

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
      // bar (curated abbreviation, exact match, or strict fuzzy cutoff)
      // in terminology_normalize.py, so all confirmed items start checked.
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
      label.textContent = "Ambiguous medical abbreviation — which meaning did you intend?";
      body.appendChild(label);

      const group = document.createElement("div");
      group.className = "ambiguous-candidates";

      item.candidates.forEach((cand, candIndex) => {
        const optionLabel = document.createElement("label");
        optionLabel.className = "ambiguous-candidate";

        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = `ambiguous-${index}`;
        radio.dataset.testIndex = String(index);
        radio.value = String(candIndex);
        // Never pre-select a candidate — the doctor must choose.
        radio.checked = false;

        const textWrap = document.createElement("span");
        textWrap.className = "ambiguous-candidate-label";

        const nameSpan = document.createElement("span");
        nameSpan.textContent = cand.canonical_name;
        textWrap.appendChild(nameSpan);

        const domainSpan = document.createElement("span");
        domainSpan.className = "ambiguous-candidate-domain";
        domainSpan.textContent = `${cand.domain.replace(/_/g, " ")} — ${cand.reason}`;
        textWrap.appendChild(domainSpan);

        optionLabel.appendChild(radio);
        optionLabel.appendChild(textWrap);
        group.appendChild(optionLabel);
      });

      const noneLabel = document.createElement("label");
      noneLabel.className = "ambiguous-candidate";
      const noneRadio = document.createElement("input");
      noneRadio.type = "radio";
      noneRadio.name = `ambiguous-${index}`;
      noneRadio.dataset.testIndex = String(index);
      noneRadio.value = "none";
      noneRadio.checked = false;
      const noneText = document.createElement("span");
      noneText.textContent = "None of these / keep original";
      noneLabel.appendChild(noneRadio);
      noneLabel.appendChild(noneText);
      group.appendChild(noneLabel);

      body.appendChild(group);
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
    if (item.status === "confirmed") {
      const checkbox = testsList.querySelector(
        `input[type="checkbox"][data-test-index="${index}"]`
      );
      if (checkbox && checkbox.checked) {
        accepted.push(item);
      }
      return;
    }

    if (item.status === "ambiguous") {
      const selected = testsList.querySelector(`input[name="ambiguous-${index}"]:checked`);
      if (!selected || selected.value === "none") {
        return; // Doctor didn't pick a meaning — excluded, same as an unrecognized item.
      }
      const candidate = item.candidates[Number(selected.value)];
      accepted.push({
        raw: item.raw,
        normalized: candidate.canonical_name,
        source: candidate.domain,
        terminology_system: candidate.terminology_system,
        code: candidate.code,
        match_type: "ambiguous_abbreviation",
        confidence: null,
        status: "confirmed",
        confirmation_status: "clinician_confirmed",
        candidates: null,
        reason: null,
      });

      // Patch the reconstructed transcript: swap the "[ambiguous — please
      // confirm]" marker for this item with the doctor's chosen expansion,
      // using the same bracket convention as an auto-expanded abbreviation.
      const marker = `${item.raw} [ambiguous — please confirm]`;
      const replacement = `${candidate.canonical_name} (${item.raw})`;
      currentNormalizedText = currentNormalizedText.split(marker).join(replacement);
    }
    // status "unrecognized": never included.
  });

  updateTranscriptView();
  confirmHistoryTests(entryId, accepted, currentNormalizedText);
  hideTests();
  setStatus(`Saved ${accepted.length} confirmed test(s) to history.`);
});

skipTestsBtn.addEventListener("click", () => {
  hideTests();
});
