// BACKEND_URL comes from config.js (loaded before this file — see
// lookup.html's <script> order), the same single shared declaration
// app.js uses — never duplicated per page.

const lookupForm = document.getElementById("lookupForm");
const requestIdInput = document.getElementById("requestIdInput");
const lookupStatus = document.getElementById("lookupStatus");
const startScanBtn = document.getElementById("startScanBtn");
const cancelScanBtn = document.getElementById("cancelScanBtn");
const scannerArea = document.getElementById("scannerArea");
const scannerVideo = document.getElementById("scannerVideo");
const scannerCanvas = document.getElementById("scannerCanvas");
const resultSection = document.getElementById("resultSection");
const resultMeta = document.getElementById("resultMeta");
const resultText = document.getElementById("resultText");
const resultLabelImage = document.getElementById("resultLabelImage");

const resultSpecimenSection = document.getElementById("resultSpecimenSection");
const resultSpecimenGroups = document.getElementById("resultSpecimenGroups");

const doctorContactSection = document.getElementById("doctorContactSection");
const doctorContactName = document.getElementById("doctorContactName");
const doctorContactHpcsa = document.getElementById("doctorContactHpcsa");
const doctorContactPhoneText = document.getElementById("doctorContactPhoneText");
const doctorContactActions = document.getElementById("doctorContactActions");
const callLink = document.getElementById("callLink");
const messageLink = document.getElementById("messageLink");
const copyNumberBtn = document.getElementById("copyNumberBtn");
const copyStatus = document.getElementById("copyStatus");

requestIdInput.focus();

// The QR now encodes a lookup URL ("{FRONTEND_URL}/lookup.html?id=...",
// see backend/label_printing.py) rather than a bare ID, so scanning it
// navigates straight here with ?id= set (handled below) — this function
// only runs for whatever ends up in the manual text input: a plain ID
// typed/read off the label, a pasted full URL (e.g. if someone copies
// the QR's decoded text instead of letting it navigate), or — for labels
// printed by an earlier version of this feature — a legacy {id, ...}
// JSON payload. Contact details are never in any of these; always looked
// up server-side for privacy.
function extractRequestId(scannedValue) {
  const trimmed = scannedValue.trim();

  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed.id === "string") return parsed.id;
    } catch (err) {
      // Not valid JSON after all — fall through to the other formats.
    }
  }

  try {
    const url = new URL(trimmed);
    const idParam = url.searchParams.get("id");
    if (idParam) return idParam;
  } catch (err) {
    // Not a full URL either — fall through, treat it as a plain ID.
  }

  return trimmed;
}

// Converts a stored/displayed phone number (which may have spaces,
// hyphens, parentheses, etc. — however the doctor typed it at
// registration) into the digits-and-leading-"+"-only form the tel:/sms:
// URI schemes expect. Never invents or alters a missing number — callers
// check for a falsy/empty phone before calling this at all.
function toTelUri(displayPhone) {
  const trimmed = displayPhone.trim();
  const hasLeadingPlus = trimmed.startsWith("+");
  const digitsOnly = trimmed.replace(/[^0-9]/g, "");
  return (hasLeadingPlus ? "+" : "") + digitsOnly;
}

// Purely additive/display-only — same rendering as app.js's
// renderSpecimenGroups(), reading record.structured.specimen_requirements
// (computed once, server-side, by backend/specimen_mapping.py at
// transcription time — see field_extraction.py). Shown separately from
// the confirmed transcript, never merged into or replacing it.
function renderSpecimenGroups(groups) {
  renderSpecimenGroupsInto(resultSpecimenGroups, resultSpecimenSection, groups);
}

function renderDoctorContact(record) {
  doctorContactName.textContent = record.doctor_name || "";
  doctorContactHpcsa.textContent = record.hpcsa_number ? `HPCSA: ${record.hpcsa_number}` : "";

  const phone = (record.doctor_phone || "").trim();
  copyStatus.textContent = "";

  if (!phone) {
    // Never show broken Call/Message links for a number we don't have.
    doctorContactPhoneText.textContent = "Contact number unavailable.";
    doctorContactActions.classList.add("hidden");
    doctorContactSection.classList.remove("hidden");
    return;
  }

  doctorContactPhoneText.textContent = `Cell: ${phone}`;
  const telUri = toTelUri(phone);
  callLink.href = `tel:${telUri}`;
  // Pre-fills the SMS body with the request ID of whichever record is
  // CURRENTLY displayed (this function always runs fresh on every
  // lookup, never reused across records — see the lookupForm handler
  // below) so it can't accidentally reference a previous lookup's ID.
  // Never sent automatically — this only opens the OS's own composer;
  // the pathologist still has to review and send it themselves. No
  // patient name/history/diagnosis/results are ever included — just the
  // reference line.
  const body = `Regarding pathology request ${record.request_id}:`;
  messageLink.href = `sms:${telUri}?body=${encodeURIComponent(body)}`;
  doctorContactActions.classList.remove("hidden");
  doctorContactSection.classList.remove("hidden");
}

copyNumberBtn.addEventListener("click", async () => {
  const phone = doctorContactPhoneText.textContent.replace(/^Cell:\s*/, "");
  if (!phone) return;

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(phone);
      copyStatus.textContent = "Number copied.";
    } else {
      throw new Error("Clipboard API not available");
    }
  } catch (err) {
    console.error(err);
    // Graceful fallback: a legacy selection-based copy, and — if even
    // that fails (some browsers block it outside a real user gesture
    // context) — at minimum leave the number selected so it can still be
    // copied manually with Ctrl/Cmd+C.
    try {
      const temp = document.createElement("textarea");
      temp.value = phone;
      temp.style.position = "fixed";
      temp.style.opacity = "0";
      document.body.appendChild(temp);
      temp.focus();
      temp.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(temp);
      copyStatus.textContent = copied
        ? "Number copied."
        : "Couldn't copy automatically — the number is shown above, please copy it manually.";
    } catch (fallbackErr) {
      console.error(fallbackErr);
      copyStatus.textContent =
        "Couldn't copy automatically — the number is shown above, please copy it manually.";
    }
  }
});

// The one lookup code path — used by both manual "Find Request" submits
// and the automatic QR-scan lookup (new URLSearchParams(location.search)
// below), so there's exactly one place that talks to /print-lookup, not
// two that could drift apart.
async function runLookup(requestId) {
  if (!requestId) return;
  requestIdInput.value = requestId;

  resultSection.classList.add("hidden");
  resultSpecimenSection.classList.add("hidden");
  doctorContactSection.classList.add("hidden");
  lookupStatus.textContent = "Looking up…";

  try {
    const res = await backendFetch(`/print-lookup/${encodeURIComponent(requestId)}`);

    if (res.status === 400) {
      lookupStatus.textContent = `"${requestId}" doesn't look like a valid request ID.`;
      return;
    }
    if (res.status === 404) {
      lookupStatus.textContent = `No request found with ID "${requestId}".`;
      return;
    }
    if (res.status === 503) {
      lookupStatus.textContent = "Could not reach the request database — try again shortly.";
      return;
    }
    if (!res.ok) {
      throw new Error(`Server responded ${res.status}`);
    }

    const record = await res.json();
    lookupStatus.textContent = "";

    renderDoctorContact(record);

    resultMeta.textContent = `printed ${new Date(record.printed_at).toLocaleString()}`;
    resultText.textContent = record.normalized_text;
    // The exact same barcode that was physically printed — useful if lab
    // staff need to confirm it or reprint a damaged label.
    resultLabelImage.src = record.label_image || "";
    resultSection.classList.remove("hidden");

    const structured = record.structured || {};
    renderSpecimenGroups(structured.specimen_requirements || []);
  } catch (err) {
    console.error(err);
    lookupStatus.textContent = "Failed to reach the server: " + err.message;
  } finally {
    requestIdInput.select();
  }
}

lookupForm.addEventListener("submit", (e) => {
  e.preventDefault();
  runLookup(extractRequestId(requestIdInput.value));
});

// ---------------------------------------------------------------------------
// In-page QR scanning (additional to the native-camera-app path below,
// which already works today since the QR encodes a full lookup URL).
// Useful on a desktop/webcam, or for anyone who'd rather not leave this
// page for their phone's own camera app. Decoding is done entirely
// client-side via jsQR (loaded in lookup.html) — nothing is sent
// anywhere until a code is actually found, at which point this reuses
// the exact same runLookup()/extractRequestId() path as manual entry.
// ---------------------------------------------------------------------------

let scanStream = null;
let scanAnimationFrame = null;

function stopScan() {
  if (scanAnimationFrame !== null) {
    cancelAnimationFrame(scanAnimationFrame);
    scanAnimationFrame = null;
  }
  if (scanStream) {
    scanStream.getTracks().forEach((track) => track.stop());
    scanStream = null;
  }
  scannerArea.classList.add("hidden");
}

function tickScan() {
  if (!scanStream) return; // stopScan() ran while a frame was already queued

  const canvasContext = scannerCanvas.getContext("2d");
  if (scannerVideo.readyState === scannerVideo.HAVE_ENOUGH_DATA) {
    scannerCanvas.width = scannerVideo.videoWidth;
    scannerCanvas.height = scannerVideo.videoHeight;
    canvasContext.drawImage(scannerVideo, 0, 0, scannerCanvas.width, scannerCanvas.height);

    const frame = canvasContext.getImageData(0, 0, scannerCanvas.width, scannerCanvas.height);
    const decoded = jsQR(frame.data, frame.width, frame.height);
    if (decoded && decoded.data) {
      stopScan(); // release the camera immediately, before even looking anything up
      runLookup(extractRequestId(decoded.data));
      return;
    }
  }

  scanAnimationFrame = requestAnimationFrame(tickScan);
}

async function startScan() {
  lookupStatus.textContent = "";
  try {
    // Same secure-context requirement as microphone access (app.js's
    // startRecording()): REJECTS on plain http:// that isn't localhost.
    scanStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" },
    });
    scannerVideo.srcObject = scanStream;
    scannerArea.classList.remove("hidden");
    await scannerVideo.play();
    scanAnimationFrame = requestAnimationFrame(tickScan);
  } catch (err) {
    console.error(err);
    lookupStatus.textContent = "Camera error: " + err.message;
    stopScan();
  }
}

startScanBtn.addEventListener("click", startScan);
cancelScanBtn.addEventListener("click", stopScan);

// Scanning the printed QR opens this page at ?id=<request_id> (see
// backend/label_printing.py) — look it up immediately so scan-and-go
// works without also requiring the manual "Find Request" step. Manual
// entry (above) is untouched and still works exactly the same if this
// param is absent or scanning failed.
const _scannedId = new URLSearchParams(location.search).get("id");
if (_scannedId) {
  runLookup(extractRequestId(_scannedId));
}
