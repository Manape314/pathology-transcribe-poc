// The ONE place the backend's address is configured — loaded before
// app.js, auth.js, and lookup.js, which all call backendFetch() below
// instead of the bare fetch(). Empty string = same origin as this page
// itself, which is correct now that main.py serves this frontend directly
// (app.mount("/", StaticFiles(...))) — frontend and backend are the SAME
// origin wherever this is deployed, so a relative path already reaches the
// right place with no hardcoded host to keep updating. Only set this to an
// absolute URL (e.g. during local development with the frontend and backend
// run as two separate processes on different ports) if they're genuinely
// NOT the same origin.
const BACKEND_URL = "";

// Every call to the backend goes through this instead of bare fetch() —
// ngrok's free tier injects an HTML "you're about to visit..." warning
// page in front of every request unless this exact header is present,
// which would otherwise make every fetch() silently receive HTML instead
// of the JSON it expects. Harmless (and ignored) against localhost or any
// real hosted deployment, so it's always sent rather than only when
// BACKEND_URL happens to point at an ngrok tunnel.
function backendFetch(path, options = {}) {
  return fetch(`${BACKEND_URL}${path}`, {
    ...options,
    headers: { "ngrok-skip-browser-warning": "true", ...(options.headers || {}) },
  });
}

// Fetches the filled NHLS request-form PDF for a finalized request
// (GET /request-form/{id}, see backend/request_form.py) and opens it in a
// new tab for viewing (from there it can be printed or saved). Goes through
// backendFetch (not a plain <a href>) so the ngrok header is still sent.
// Shared by app.js (after "Done") and lookup.js. `statusEl` only ever shows
// text when something goes wrong; nothing extra appears on success.
async function openRequestForm(requestId, statusEl) {
  // The new tab is opened right away, inside the click, so the browser
  // doesn't block it as a pop-up; the PDF is loaded into it once fetched.
  const viewer = window.open("", "_blank");
  if (viewer) {
    viewer.opener = null;
    try {
      viewer.document.title = "Request form";
      viewer.document.body.textContent = "Preparing request form…";
    } catch (_) {
      // purely cosmetic placeholder text; ignore if the tab isn't ready yet
    }
  }
  if (statusEl && statusEl.dataset.hideWhenEmpty !== undefined) {
    statusEl.textContent = "";
    statusEl.classList.add("hidden");
  }
  try {
    const res = await backendFetch(`/request-form/${encodeURIComponent(requestId)}`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (viewer && !viewer.closed) {
      viewer.location.href = url;
    } else {
      // Pop-ups blocked: fall back to a normal download of the same PDF.
      const a = document.createElement("a");
      a.href = url;
      a.download = `request-form-${requestId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (err) {
    console.error(err);
    if (viewer && !viewer.closed) viewer.close();
    if (statusEl) {
      statusEl.textContent = "Couldn't open the request form: " + err.message;
      statusEl.classList.remove("hidden");
    }
  }
}
