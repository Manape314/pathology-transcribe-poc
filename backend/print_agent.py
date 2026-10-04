"""
Standalone local print agent — runs on whichever Windows PC actually has
the NIIMBOT B21 plugged in, nowhere near the hosted backend.

This is a DELIBERATELY small, separate process. It does NOT contain the
pathology application: no Whisper, no transcription, no clinical
terminology normalization, no NHLS/specimen mapping, no pathology
request database, no pathologist lookup UI, no doctor authentication.
It imports exactly one module from this project — label_printing.py
(pure label rendering + the niimprint hardware adapter) — plus the
stdlib and `requests`. It knows three things about a job: its
print_job_id, the request_id and doctor_name to put on the label, and
the qr_payload URL to encode — nothing about clinical content.

Flow (simple HTTPS polling outbound to the hosted backend — no inbound
connections, no public server, nothing for the hosted backend to reach
INTO this machine for):

    loop:
        POST /print-jobs/claim {station_id} with Authorization: Bearer <token>
        no job (204)   -> sleep POLL_INTERVAL_SECONDS, loop again
        job (200)      -> render the label, print it, report the result,
                           loop again immediately (don't wait — there may
                           be more queued)

Run it with:
    python print_agent.py

(left running in a terminal on the printing machine; see README's
"Local print agent" section for env vars and optional Task Scheduler
setup to survive reboots — not built into this script).
"""

import os
import time

import requests

import label_printing

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")
STATION_ID = os.getenv("STATION_ID", "POC_PRINTER_01")
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5"))

_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}"}


def claim_next_job() -> dict | None:
    resp = requests.post(
        f"{BACKEND_URL}/print-jobs/claim",
        json={"station_id": STATION_ID},
        headers=_HEADERS,
        timeout=10,
    )
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def report_complete(print_job_id: str) -> None:
    requests.post(
        f"{BACKEND_URL}/print-jobs/{print_job_id}/complete",
        json={"station_id": STATION_ID},
        headers=_HEADERS,
        timeout=10,
    ).raise_for_status()


def report_fail(print_job_id: str, error: str) -> None:
    requests.post(
        f"{BACKEND_URL}/print-jobs/{print_job_id}/fail",
        json={"station_id": STATION_ID, "error": error},
        headers=_HEADERS,
        timeout=10,
    ).raise_for_status()


def process_job(job: dict) -> None:
    print(f"Claimed job {job['print_job_id']} for request {job['request_id']}")
    try:
        image = label_printing.render_label_image(
            job["doctor_name"], job["request_id"], qr_payload=job["qr_payload"]
        )
        label_printing.print_label_image(image)
    except label_printing.PrinterConnectionError as exc:
        print(f"Print failed: {exc}")
        report_fail(job["print_job_id"], str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — never let a rendering bug crash the loop
        print(f"Unexpected error rendering/printing: {exc}")
        report_fail(job["print_job_id"], f"Unexpected agent error: {exc}")
        return

    print(f"Printed request {job['request_id']}")
    report_complete(job["print_job_id"])


def main() -> None:
    if not AGENT_TOKEN:
        raise SystemExit("AGENT_TOKEN is not set — refusing to start (the backend will reject every request anyway).")

    print(f"Print agent '{STATION_ID}' polling {BACKEND_URL} every {POLL_INTERVAL_SECONDS}s. Ctrl+C to stop.")
    while True:
        try:
            job = claim_next_job()
        except requests.RequestException as exc:
            print(f"Could not reach the backend ({exc}) — retrying next poll.")
            job = None

        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        process_job(job)


if __name__ == "__main__":
    main()
