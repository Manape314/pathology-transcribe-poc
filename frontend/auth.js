// ===========================================================================
// Doctor login / registration — backed by the server (backend/doctors.py)
// so the SAME HPCSA number and password work from any device, not just
// the one that registered. Passwords never touch localStorage; only a
// session token does, and the server is the only thing that ever checks
// a password. See doctors.py's docstring for the hashing/session design.
// ===========================================================================

const SESSION_KEY = "pathdictate_session"; // stores the session TOKEN, not the hpcsa

// Screens
const loginScreen = document.getElementById("loginScreen");
const registerScreen = document.getElementById("registerScreen");
const appScreen = document.getElementById("appScreen");
const profileScreen = document.getElementById("profileScreen");
const historyScreen = document.getElementById("historyScreen");
const ALL_SCREENS = [loginScreen, registerScreen, appScreen, profileScreen, historyScreen];

// Login form
const loginForm = document.getElementById("loginForm");
const loginHpcsa = document.getElementById("loginHpcsa");
const loginPassword = document.getElementById("loginPassword");
const loginError = document.getElementById("loginError");
const showRegisterLink = document.getElementById("showRegister");

// Register form
const registerForm = document.getElementById("registerForm");
const regName = document.getElementById("regName");
const regHpcsa = document.getElementById("regHpcsa");
const regCell = document.getElementById("regCell");
const regEmail = document.getElementById("regEmail");
const regPassword = document.getElementById("regPassword");
const regPassword2 = document.getElementById("regPassword2");
const registerError = document.getElementById("registerError");
const showLoginLink = document.getElementById("showLogin");

// App header / nav / logout
const doctorGreeting = document.getElementById("doctorGreeting");
const logoutBtn = document.getElementById("logoutBtn");
const profileNavBtn = document.getElementById("profileNavBtn");
const historyNavBtn = document.getElementById("historyNavBtn");

// Profile form
const profileForm = document.getElementById("profileForm");
const profileName = document.getElementById("profileName");
const profileHpcsa = document.getElementById("profileHpcsa");
const profileCell = document.getElementById("profileCell");
const profileEmail = document.getElementById("profileEmail");
const profileError = document.getElementById("profileError");
const profileSuccess = document.getElementById("profileSuccess");
const profileBackBtn = document.getElementById("profileBackBtn");

// Change-password form
const passwordForm = document.getElementById("passwordForm");
const currentPasswordInput = document.getElementById("currentPassword");
const newPasswordInput = document.getElementById("newPassword");
const newPassword2Input = document.getElementById("newPassword2");
const passwordError = document.getElementById("passwordError");
const passwordSuccess = document.getElementById("passwordSuccess");

// History screen
const historyBackBtn = document.getElementById("historyBackBtn");
const historySearchInput = document.getElementById("historySearchInput");
const historyList = document.getElementById("historyList");
const historyEmpty = document.getElementById("historyEmpty");

// The doctor profile returned by the last successful /login, /register,
// /me, or /profile call — kept in memory only (never localStorage), so
// doctorGreeting/the profile form always reflect the latest server state
// without a network round trip on every single read. Always re-fetched
// (via /me) on page load, so a stale value here never outlives the tab.
let currentDoctor = null;

function setSession(token) {
  localStorage.setItem(SESSION_KEY, token);
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  currentDoctor = null;
}

function getSession() {
  return localStorage.getItem(SESSION_KEY);
}

function authHeaders() {
  const token = getSession();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function showScreen(screen) {
  ALL_SCREENS.forEach((s) => s.classList.toggle("hidden", s !== screen));
}

function clearAuthErrors() {
  loginError.textContent = "";
  registerError.textContent = "";
}

function enterApp(doctor) {
  currentDoctor = doctor;
  doctorGreeting.textContent = doctor.name;
  showScreen(appScreen);
  maybeOfferInstall();
}

// --- Navigation between login / register -----------------------------------

showRegisterLink.addEventListener("click", (e) => {
  e.preventDefault();
  clearAuthErrors();
  registerForm.reset();
  showScreen(registerScreen);
});

showLoginLink.addEventListener("click", (e) => {
  e.preventDefault();
  clearAuthErrors();
  loginForm.reset();
  showScreen(loginScreen);
});

// --- Login -------------------------------------------------------------

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearAuthErrors();

  const hpcsa_number = loginHpcsa.value;
  const password = loginPassword.value;

  try {
    const res = await backendFetch(`/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hpcsa_number, password }),
    });
    if (!res.ok) {
      // Same generic message regardless of which field was wrong,
      // whatever the server's actual detail text says.
      loginError.textContent = "Incorrect HPCSA number or password.";
      return;
    }
    const data = await res.json();
    setSession(data.token);
    loginForm.reset();
    enterApp(data.doctor);
  } catch (err) {
    loginError.textContent = "Could not reach the server: " + err.message;
  }
});

// --- Registration --------------------------------------------------------

registerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearAuthErrors();

  const name = regName.value.trim();
  const hpcsa_number = regHpcsa.value.trim();
  const cell = regCell.value.trim();
  const email = regEmail.value.trim();
  const password = regPassword.value;
  const password2 = regPassword2.value;

  if (!name || !hpcsa_number || !cell || !email || !password) {
    registerError.textContent = "Please fill in all fields.";
    return;
  }

  if (password !== password2) {
    registerError.textContent = "Passwords do not match.";
    return;
  }

  try {
    const res = await backendFetch(`/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Dr. " + name, hpcsa_number, cell, email, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      registerError.textContent =
        res.status === 409
          ? "This HPCSA number is already registered. Please log in instead."
          : data.detail || "Registration failed.";
      return;
    }
    const data = await res.json();
    setSession(data.token);
    registerForm.reset();
    enterApp(data.doctor);
  } catch (err) {
    registerError.textContent = "Could not reach the server: " + err.message;
  }
});

// --- Logout ----------------------------------------------------------------

logoutBtn.addEventListener("click", () => {
  clearSession();
  clearAuthErrors();
  loginForm.reset();
  hideInstallBanner();
  showScreen(loginScreen);
});

// --- "Add to home screen" prompt --------------------------------------------
// Shown once, right after a successful login/registration, so it doesn't get
// in the way of the login screen itself.

const installBanner = document.getElementById("installPrompt");
const installText = document.getElementById("installText");
const installBtn = document.getElementById("installBtn");
const installDismissBtn = document.getElementById("installDismiss");

let deferredInstallPrompt = null;
const INSTALL_DISMISSED_KEY = "pathdictate_install_dismissed";

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
});

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

function isIos() {
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

function hideInstallBanner() {
  installBanner.classList.add("hidden");
}

function maybeOfferInstall() {
  if (isStandalone() || localStorage.getItem(INSTALL_DISMISSED_KEY)) {
    hideInstallBanner();
    return;
  }

  if (deferredInstallPrompt) {
    installText.textContent = "Install this app for quick, offline-ready access.";
    installBtn.classList.remove("hidden");
    installBanner.classList.remove("hidden");
  } else if (isIos()) {
    installText.textContent =
      "Install this app: tap Share, then \"Add to Home Screen\".";
    installBtn.classList.add("hidden");
    installBanner.classList.remove("hidden");
  } else {
    hideInstallBanner();
  }
}

installBtn.addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  hideInstallBanner();
});

installDismissBtn.addEventListener("click", () => {
  localStorage.setItem(INSTALL_DISMISSED_KEY, "1");
  hideInstallBanner();
});

// --- Profile editing ---------------------------------------------------------

function openProfileScreen() {
  if (!currentDoctor) return;

  profileError.textContent = "";
  profileSuccess.textContent = "";
  passwordError.textContent = "";
  passwordSuccess.textContent = "";
  passwordForm.reset();

  profileName.value = currentDoctor.name.replace(/^Dr\.\s*/, "");
  profileHpcsa.value = currentDoctor.hpcsa_number;
  profileCell.value = currentDoctor.cell;
  profileEmail.value = currentDoctor.email;

  showScreen(profileScreen);
}

profileNavBtn.addEventListener("click", openProfileScreen);
profileBackBtn.addEventListener("click", () => showScreen(appScreen));

profileForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  profileError.textContent = "";
  profileSuccess.textContent = "";

  const name = profileName.value.trim();
  const cell = profileCell.value.trim();
  const email = profileEmail.value.trim();

  if (!name || !cell || !email) {
    profileError.textContent = "Please fill in all fields.";
    return;
  }

  try {
    const res = await backendFetch(`/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ name: "Dr. " + name, cell, email }),
    });
    if (!res.ok) {
      profileError.textContent = "Could not update profile.";
      return;
    }
    const data = await res.json();
    currentDoctor = data.doctor;
    doctorGreeting.textContent = currentDoctor.name;
    profileSuccess.textContent = "Profile updated.";
  } catch (err) {
    profileError.textContent = "Could not reach the server: " + err.message;
  }
});

passwordForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  passwordError.textContent = "";
  passwordSuccess.textContent = "";

  const currentPassword = currentPasswordInput.value;
  const newPassword = newPasswordInput.value;
  const newPassword2 = newPassword2Input.value;

  if (newPassword !== newPassword2) {
    passwordError.textContent = "New passwords do not match.";
    return;
  }

  try {
    const res = await backendFetch(`/password`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (!res.ok) {
      passwordError.textContent = "Current password is incorrect.";
      return;
    }
    passwordForm.reset();
    passwordSuccess.textContent = "Password updated.";
  } catch (err) {
    passwordError.textContent = "Could not reach the server: " + err.message;
  }
});

// ---------------------------------------------------------------------------
// In-session bookkeeping for the CURRENT, still-being-reviewed dictation —
// NOT History anymore (History is server-backed, finalized-requests-only;
// see renderHistory() below). These exist purely so app.js keeps a stable,
// non-null marker once a transcription exists (currentEntryId gates the
// six-block view and the "Done" button), without persisting a draft
// anywhere — a draft was never meant to sync across devices, only a
// finalized, printed request is (see the History section below).
// ---------------------------------------------------------------------------

function addHistoryEntry(text) {
  if (!text) return null;
  return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
}

function confirmHistoryTests() {
  // No-op: nothing local to update. Confirmed tests are already part of
  // currentStructured in app.js, and reach the server as part of
  // /print-label's body when the doctor presses "Done — Print label".
}

function updateHistoryText() {
  // No-op — see the module comment above.
}

function attachLabelImage() {
  // No-op — the label image is already saved server-side by /print-label
  // itself (print_records.save_record); History reads it back from
  // there (see renderHistory() below), nothing to mirror locally.
}

// ---------------------------------------------------------------------------
// History — a doctor's own FINALIZED (printed) requests, fetched from the
// server (GET /requests) so the same list shows up on any device logged
// into the same account. Deliberately does not include in-progress or
// abandoned dictations — see backend/print_records.py's
// get_records_for_doctor() docstring.
// ---------------------------------------------------------------------------

let lastFetchedRequests = [];

function formatTimestamp(iso) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function renderHistoryItem(record) {
  const item = document.createElement("li");
  item.className = "history-item";

  const meta = document.createElement("div");
  meta.className = "history-meta";

  const timestamp = document.createElement("span");
  timestamp.className = "history-timestamp";
  timestamp.textContent = formatTimestamp(record.created_at);
  meta.appendChild(timestamp);

  const text = document.createElement("p");
  text.className = "history-text";
  text.textContent = record.normalized_text;

  item.appendChild(meta);
  item.appendChild(text);

  if (record.label_image) {
    const label = document.createElement("img");
    label.className = "label-image";
    label.alt = "Printed barcode label";
    label.src = record.label_image;
    item.appendChild(label);
  }

  const testsRequired = (record.structured && record.structured.tests_required) || [];
  const confirmedTests = testsRequired.filter((t) => t.status === "confirmed");
  if (confirmedTests.length) {
    const testsList = document.createElement("ul");
    testsList.className = "history-matches";

    confirmedTests.forEach((test) => {
      const testItem = document.createElement("li");
      testItem.className = "history-match-item";

      const badge = document.createElement("span");
      let badgeText;
      if (test.match_type === "ambiguous_abbreviation") {
        badgeText = "CONFIRMED";
      } else if (test.match_type === "known_abbreviation") {
        badgeText = "ABBREV";
      } else if (test.terminology_system) {
        badgeText = test.terminology_system;
      } else {
        badgeText = test.source === "loinc" ? "LOINC" : "NHLS";
      }
      badge.className = "source-badge" + (badgeText === "LOINC" ? " loinc" : "");
      badge.textContent = badgeText;

      const label = document.createElement("span");
      label.textContent = test.normalized;

      testItem.appendChild(badge);
      testItem.appendChild(label);
      testsList.appendChild(testItem);
    });

    item.appendChild(testsList);
  }

  return item;
}

function renderFilteredHistory() {
  const query = historySearchInput.value.trim().toLowerCase();
  const filtered = query
    ? lastFetchedRequests.filter((r) => r.normalized_text.toLowerCase().includes(query))
    : lastFetchedRequests;

  historyList.innerHTML = "";
  historyEmpty.classList.toggle("hidden", filtered.length > 0);
  filtered.forEach((record) => historyList.appendChild(renderHistoryItem(record)));
}

async function renderHistory() {
  historyList.innerHTML = "";
  historyEmpty.classList.add("hidden");
  try {
    const res = await backendFetch(`/requests`, { headers: authHeaders() });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    lastFetchedRequests = data.requests || [];
    renderFilteredHistory();
  } catch (err) {
    console.error("Failed to load history:", err);
    historyEmpty.textContent = "Could not load history: " + err.message;
    historyEmpty.classList.remove("hidden");
  }
}

historySearchInput.addEventListener("input", renderFilteredHistory);

function openHistoryScreen() {
  historySearchInput.value = "";
  renderHistory();
  showScreen(historyScreen);
}

historyNavBtn.addEventListener("click", openHistoryScreen);
historyBackBtn.addEventListener("click", () => showScreen(appScreen));

// --- Resume session on load --------------------------------------------------

(async function init() {
  const token = getSession();
  if (token) {
    try {
      const res = await backendFetch(`/me`, { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        enterApp(data.doctor);
        return;
      }
    } catch (err) {
      console.error("Failed to resume session:", err);
    }
    clearSession();
  }
  showScreen(loginScreen);
})();
