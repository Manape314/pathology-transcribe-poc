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
