// The ONE place the backend's address is configured — loaded before
// app.js and lookup.js, both of which just reference this global. Change
// this single line to switch between local development and a hosted
// backend; never hardcode BACKEND_URL separately in any other file.
const BACKEND_URL = "http://localhost:8000";
