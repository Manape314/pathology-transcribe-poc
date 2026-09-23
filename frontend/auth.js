// ===========================================================================
// Doctor login / registration — all on-device, no backend involved.
//
// Profiles are stored in localStorage, keyed by HPCSA registration number.
// Passwords are never stored: only a salted SHA-256 hash of the password.
// ===========================================================================

const DOCTOR_KEY_PREFIX = "pathdictate_doctor_";
const HISTORY_KEY_PREFIX = "pathdictate_history_";
const SESSION_KEY = "pathdictate_session";

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
const historyList = document.getElementById("historyList");
const historyEmpty = document.getElementById("historyEmpty");

function normalizeHpcsa(value) {
  return value.trim().toUpperCase();
}

function doctorStorageKey(hpcsa) {
  return DOCTOR_KEY_PREFIX + normalizeHpcsa(hpcsa);
}

function getDoctor(hpcsa) {
  const raw = localStorage.getItem(doctorStorageKey(hpcsa));
  return raw ? JSON.parse(raw) : null;
}

function saveDoctor(doctor) {
  localStorage.setItem(doctorStorageKey(doctor.hpcsa), JSON.stringify(doctor));
}

function bufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function randomSaltHex() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return bufferToHex(bytes.buffer);
}

async function hashPassword(password, saltHex) {
  const data = new TextEncoder().encode(saltHex + password);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return bufferToHex(digest);
}

function setSession(hpcsa) {
  localStorage.setItem(SESSION_KEY, normalizeHpcsa(hpcsa));
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}

function getSession() {
  return localStorage.getItem(SESSION_KEY);
}

function showScreen(screen) {
  ALL_SCREENS.forEach((s) => s.classList.toggle("hidden", s !== screen));
}

function clearAuthErrors() {
  loginError.textContent = "";
  registerError.textContent = "";
}

function enterApp(doctor) {
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

  const hpcsa = loginHpcsa.value;
  const password = loginPassword.value;
  const doctor = getDoctor(hpcsa);

  // Generic error either way — never reveal which field was wrong.
  const fail = () => {
    loginError.textContent = "Incorrect HPCSA number or password.";
  };

  if (!doctor) {
    fail();
    return;
  }

  const enteredHash = await hashPassword(password, doctor.salt);
  if (enteredHash !== doctor.passwordHash) {
    fail();
    return;
  }

  setSession(doctor.hpcsa);
  loginForm.reset();
  enterApp(doctor);
});

// --- Registration --------------------------------------------------------

registerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearAuthErrors();

  const name = regName.value.trim();
  const hpcsa = regHpcsa.value.trim();
  const cell = regCell.value.trim();
  const email = regEmail.value.trim();
  const password = regPassword.value;
  const password2 = regPassword2.value;

  if (!name || !hpcsa || !cell || !email || !password) {
    registerError.textContent = "Please fill in all fields.";
    return;
  }

  if (password !== password2) {
    registerError.textContent = "Passwords do not match.";
    return;
  }

  if (getDoctor(hpcsa)) {
    registerError.textContent =
      "This HPCSA number is already registered on this device. Please log in instead.";
    return;
  }

  const salt = randomSaltHex();
  const passwordHash = await hashPassword(password, salt);

  const doctor = {
    name: "Dr. " + name,
    hpcsa,
    cell,
    email,
    salt,
    passwordHash,
  };

  saveDoctor(doctor);
  setSession(doctor.hpcsa);
  registerForm.reset();
  enterApp(doctor);
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
  const doctor = getDoctor(getSession());
  if (!doctor) return;

  profileError.textContent = "";
  profileSuccess.textContent = "";
  passwordError.textContent = "";
  passwordSuccess.textContent = "";
  passwordForm.reset();

  profileName.value = doctor.name.replace(/^Dr\.\s*/, "");
  profileHpcsa.value = doctor.hpcsa;
  profileCell.value = doctor.cell;
  profileEmail.value = doctor.email;

  showScreen(profileScreen);
}

profileNavBtn.addEventListener("click", openProfileScreen);
profileBackBtn.addEventListener("click", () => showScreen(appScreen));

profileForm.addEventListener("submit", (e) => {
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

  const doctor = getDoctor(getSession());
  if (!doctor) return;

  doctor.name = "Dr. " + name;
  doctor.cell = cell;
  doctor.email = email;
  saveDoctor(doctor);

  doctorGreeting.textContent = doctor.name;
  profileSuccess.textContent = "Profile updated.";
});

passwordForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  passwordError.textContent = "";
  passwordSuccess.textContent = "";

  const doctor = getDoctor(getSession());
  if (!doctor) return;

  const currentPassword = currentPasswordInput.value;
  const newPassword = newPasswordInput.value;
  const newPassword2 = newPassword2Input.value;

  const currentHash = await hashPassword(currentPassword, doctor.salt);
  if (currentHash !== doctor.passwordHash) {
    passwordError.textContent = "Current password is incorrect.";
    return;
  }

  if (newPassword !== newPassword2) {
    passwordError.textContent = "New passwords do not match.";
    return;
  }

  const salt = randomSaltHex();
  doctor.salt = salt;
  doctor.passwordHash = await hashPassword(newPassword, salt);
  saveDoctor(doctor);

  passwordForm.reset();
  passwordSuccess.textContent = "Password updated.";
});

// --- Transcription history ----------------------------------------------------

function historyStorageKey(hpcsa) {
  return HISTORY_KEY_PREFIX + normalizeHpcsa(hpcsa);
}

function getHistory(hpcsa) {
  const raw = localStorage.getItem(historyStorageKey(hpcsa));
  return raw ? JSON.parse(raw) : [];
}

function saveHistory(hpcsa, entries) {
  localStorage.setItem(historyStorageKey(hpcsa), JSON.stringify(entries));
}

// Called by app.js after a successful transcription. `text` is the
// clinician-facing normalized transcript (what's displayed by default and
// what's shown in History); `rawText` is the untouched Whisper output,
// kept for traceability. Returns the new entry's id so app.js can later
// attach confirmed tests to this exact entry, rather than assuming "the
// most recent one."
function addHistoryEntry(text, rawText) {
  const hpcsa = getSession();
  if (!hpcsa || !text) return null;

  const id = crypto.randomUUID
    ? crypto.randomUUID()
    : bufferToHex(crypto.getRandomValues(new Uint8Array(8)).buffer);

  const entries = getHistory(hpcsa);
  entries.unshift({
    id,
    text,
    rawText: rawText || "",
    timestamp: new Date().toISOString(),
  });
  saveHistory(hpcsa, entries);
  return id;
}

// Called by app.js once the doctor confirms which required tests to keep.
// Old entries (and entries where confirmation was skipped) simply have no
// "testsRequired" key — see renderHistory's guard below.
function confirmHistoryTests(entryId, tests) {
  const hpcsa = getSession();
  if (!hpcsa || !entryId) return;

  const entries = getHistory(hpcsa);
  const entry = entries.find((e) => e.id === entryId);
  if (!entry) return;

  entry.testsRequired = tests;
  entry.testsConfirmed = true;
  saveHistory(hpcsa, entries);
}

function deleteHistoryEntry(id) {
  const hpcsa = getSession();
  if (!hpcsa) return;
  saveHistory(hpcsa, getHistory(hpcsa).filter((entry) => entry.id !== id));
  renderHistory();
}

function formatTimestamp(iso) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function renderHistory() {
  const hpcsa = getSession();
  const entries = hpcsa ? getHistory(hpcsa) : [];

  historyList.innerHTML = "";
  historyEmpty.classList.toggle("hidden", entries.length > 0);

  entries.forEach((entry) => {
    const item = document.createElement("li");
    item.className = "history-item";

    const meta = document.createElement("div");
    meta.className = "history-meta";

    const timestamp = document.createElement("span");
    timestamp.className = "history-timestamp";
    timestamp.textContent = formatTimestamp(entry.timestamp);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "history-delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => {
      if (confirm("Delete this transcription from your history?")) {
        deleteHistoryEntry(entry.id);
      }
    });

    meta.appendChild(timestamp);
    meta.appendChild(deleteBtn);

    const text = document.createElement("p");
    text.className = "history-text";
    text.textContent = entry.text;

    item.appendChild(meta);
    item.appendChild(text);

    // Old entries (and entries where confirmation was skipped) simply
    // don't have a "testsRequired" key — this guard is the entire
    // backward-compat mechanism, no migration needed.
    if (entry.testsRequired && entry.testsRequired.length) {
      const testsList = document.createElement("ul");
      testsList.className = "history-matches";

      entry.testsRequired.forEach((test) => {
        const testItem = document.createElement("li");
        testItem.className = "history-match-item";

        const badge = document.createElement("span");
        badge.className = "source-badge" + (test.source === "loinc" ? " loinc" : "");
        badge.textContent =
          test.source === "abbreviation" ? "ABBREV" : test.source === "loinc" ? "LOINC" : "NHLS";

        const label = document.createElement("span");
        label.textContent = test.normalized;

        testItem.appendChild(badge);
        testItem.appendChild(label);
        testsList.appendChild(testItem);
      });

      item.appendChild(testsList);
    }

    historyList.appendChild(item);
  });
}

function openHistoryScreen() {
  renderHistory();
  showScreen(historyScreen);
}

historyNavBtn.addEventListener("click", openHistoryScreen);
historyBackBtn.addEventListener("click", () => showScreen(appScreen));

// --- Resume session on load --------------------------------------------------

(function init() {
  const sessionHpcsa = getSession();
  if (sessionHpcsa) {
    const doctor = getDoctor(sessionHpcsa);
    if (doctor) {
      showScreen(appScreen);
      doctorGreeting.textContent = doctor.name;
      return;
    }
    clearSession();
  }
  showScreen(loginScreen);
})();
