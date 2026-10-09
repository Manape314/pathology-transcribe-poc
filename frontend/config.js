// The ONE place the backend's address is configured — loaded before
// app.js, auth.js, and lookup.js, which all call backendFetch() below
// instead of the bare fetch(). Change this single line to switch between
// local development and a hosted backend; never hardcode BACKEND_URL
// separately in any other file.
const BACKEND_URL = "https://retouch-kick-strike.ngrok-free.dev";

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
