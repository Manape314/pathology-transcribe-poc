// ===========================================================================
// Doctor login / registration — all on-device, no backend involved.
//
// Profiles are stored in localStorage, keyed by HPCSA registration number.
// Passwords are never stored: only a salted SHA-256 hash of the password.
// ===========================================================================

const DOCTOR_KEY_PREFIX = "pathdictate_doctor_";
const SESSION_KEY = "pathdictate_session";

// Screens
const loginScreen = document.getElementById("loginScreen");
const registerScreen = document.getElementById("registerScreen");
const appScreen = document.getElementById("appScreen");

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

// App header / logout
const doctorGreeting = document.getElementById("doctorGreeting");
const logoutBtn = document.getElementById("logoutBtn");

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
  [loginScreen, registerScreen, appScreen].forEach((s) =>
    s.classList.toggle("hidden", s !== screen)
  );
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
