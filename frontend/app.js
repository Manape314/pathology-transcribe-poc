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

const matchesSection = document.getElementById("matchesSection");
const matchesList = document.getElementById("matchesList");
const matchesEmpty = document.getElementById("matchesEmpty");
const confirmMatchesBtn = document.getElementById("confirmMatchesBtn");
const skipMatchesBtn = document.getElementById("skipMatchesBtn");

let lastMatches = []; // the matches currently rendered in matchesSection

// A match is checked by default only above this stricter threshold — the
// MIN_MATCH_SCORE cutoff on the backend just decides what's worth showing
// at all; this decides what's worth pre-accepting.
const AUTO_ACCEPT_SCORE = 90;

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
  hideMatches();

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
    transcriptEl.textContent = data.text || "(no speech detected)";

    const entryId = data.text ? addHistoryEntry(data.text) : null;
    if (entryId) {
      renderMatches(entryId, data.matches || []);
    }

    setStatus(
      `Done — detected ${data.language}, ${data.duration.toFixed(1)}s of audio.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Transcription failed: " + err.message);
  }
}

function hideMatches() {
  matchesSection.classList.add("hidden");
  matchesList.innerHTML = "";
  lastMatches = [];
}

function renderMatches(entryId, matches) {
  lastMatches = matches;
  matchesSection.dataset.entryId = entryId;
  matchesList.innerHTML = "";
  matchesEmpty.classList.toggle("hidden", matches.length > 0);

  matches.forEach((match) => {
    const item = document.createElement("li");
    item.className = "match-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.matchId = match.match_id;
    checkbox.checked = match.score >= AUTO_ACCEPT_SCORE;

    const body = document.createElement("div");
    body.className = "match-body";

    const candidate = document.createElement("p");
    candidate.className = "match-candidate";
    candidate.textContent = `Heard: "${match.candidate_phrase}"`;

    const name = document.createElement("p");
    name.className = "match-name";
    name.textContent =
      match.source === "loinc" ? match.long_common_name : match.test_name;

    const meta = document.createElement("div");
    meta.className = "match-meta";

    const badge = document.createElement("span");
    badge.className = "source-badge" + (match.source === "loinc" ? " loinc" : "");
    badge.textContent = match.source === "loinc" ? "LOINC" : "NHLS";

    const detail = document.createElement("span");
    detail.textContent =
      match.source === "loinc" ? match.loinc_num : match.specimen_type;

    const score = document.createElement("span");
    score.className = "match-score";
    score.textContent = `${Math.round(match.score)}% match`;

    meta.appendChild(badge);
    meta.appendChild(detail);
    meta.appendChild(score);

    body.appendChild(candidate);
    body.appendChild(name);
    body.appendChild(meta);

    item.appendChild(checkbox);
    item.appendChild(body);
    matchesList.appendChild(item);
  });

  matchesSection.classList.remove("hidden");
}

confirmMatchesBtn.addEventListener("click", () => {
  const entryId = matchesSection.dataset.entryId;
  const checkedIds = new Set(
    Array.from(matchesList.querySelectorAll("input[type=checkbox]:checked")).map(
      (cb) => cb.dataset.matchId
    )
  );
  const accepted = lastMatches.filter((m) => checkedIds.has(m.match_id));

  confirmHistoryMatches(entryId, accepted);
  hideMatches();
  setStatus(`Saved ${accepted.length} confirmed match(es) to history.`);
});

skipMatchesBtn.addEventListener("click", () => {
  hideMatches();
});
