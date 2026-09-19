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
    if (data.text) {
      addHistoryEntry(data.text);
    }
    setStatus(
      `Done — detected ${data.language}, ${data.duration.toFixed(1)}s of audio.`
    );
  } catch (err) {
    console.error(err);
    setStatus("Transcription failed: " + err.message);
  }
}
