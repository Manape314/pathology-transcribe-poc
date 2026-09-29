// Same backend address as app.js — see that file's CONFIG comment for the
// HTTPS/LAN-IP caveat when this page is opened from a different device.
const BACKEND_URL = "http://localhost:8000";

const lookupForm = document.getElementById("lookupForm");
const requestIdInput = document.getElementById("requestIdInput");
const lookupStatus = document.getElementById("lookupStatus");
const resultSection = document.getElementById("resultSection");
const resultMeta = document.getElementById("resultMeta");
const resultText = document.getElementById("resultText");
const resultLabelImage = document.getElementById("resultLabelImage");

requestIdInput.focus();

lookupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const requestId = requestIdInput.value.trim();
  if (!requestId) return;

  resultSection.classList.add("hidden");
  lookupStatus.textContent = "Looking up…";

  try {
    const res = await fetch(`${BACKEND_URL}/print-lookup/${encodeURIComponent(requestId)}`);

    if (res.status === 404) {
      lookupStatus.textContent = `No request found with ID "${requestId}".`;
      return;
    }
    if (!res.ok) {
      throw new Error(`Server responded ${res.status}`);
    }

    const record = await res.json();
    lookupStatus.textContent = "";
    resultMeta.textContent =
      `${record.doctor_name} (HPCSA ${record.hpcsa_number}) — ` +
      `printed ${new Date(record.printed_at).toLocaleString()}`;
    resultText.textContent = record.normalized_text;
    // The exact same barcode that was physically printed — useful if lab
    // staff need to confirm it or reprint a damaged label.
    resultLabelImage.src = record.label_image || "";
    resultSection.classList.remove("hidden");
  } catch (err) {
    console.error(err);
    lookupStatus.textContent = "Failed to reach the server: " + err.message;
  } finally {
    requestIdInput.select();
  }
});
