from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

import print_jobs
import print_records

FAKE_LABEL_IMAGE = "data:image/png;base64,FAKEBASE64DATA=="


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_print_jobs.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    print_jobs.init_db()
    # Both STALE_JOB_TIMEOUT_SECONDS and MAX_PRINT_ATTEMPTS are read at
    # import time as module constants — tests that need different values
    # monkeypatch these directly rather than re-importing the module.


def _make_request(request_id="PR260101-AAAA"):
    print_records.save_record(
        request_id=request_id,
        doctor_name="Dr. Test",
        hpcsa_number="111111",
        raw_text="raw",
        normalized_text="norm",
        structured={},
        print_status="pending",
        label_image_base64=FAKE_LABEL_IMAGE,
    )
    return request_id


def test_create_job_is_pending():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    job = print_jobs.latest_job_for_request(request_id)
    assert job["print_job_id"] == job_id
    assert job["status"] == "pending"
    assert job["attempt_count"] == 0


def test_claim_returns_minimum_payload_only():
    request_id = _make_request()
    print_jobs.create_job(request_id)

    claimed = print_jobs.claim_next_job("POC_PRINTER_01")
    assert claimed is not None
    assert claimed["request_id"] == request_id
    assert claimed["doctor_name"] == "Dr. Test"
    assert claimed["printer_id"] == "POC_PRINTER_01"
    assert claimed["qr_payload"].endswith(f"/lookup.html?id={request_id}")
    # Exactly these five keys — no raw_text/structured/clinical content
    # ever reaches a print agent.
    assert set(claimed.keys()) == {
        "print_job_id", "request_id", "qr_payload", "doctor_name", "printer_id",
    }


def test_claim_with_nothing_pending_returns_none():
    assert print_jobs.claim_next_job("POC_PRINTER_01") is None


def test_second_claim_on_an_already_claimed_job_gets_nothing():
    # Simulates two agents racing: the job is claimed once, then a
    # second claim attempt (nothing else pending) must not re-claim the
    # same row — proves the WHERE status='pending' guard works.
    request_id = _make_request()
    print_jobs.create_job(request_id)

    first = print_jobs.claim_next_job("STATION_A")
    assert first is not None

    second = print_jobs.claim_next_job("STATION_B")
    assert second is None


def test_stale_printing_job_becomes_claimable_again():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("STATION_A")  # -> status="printing"

    # Simulate the claiming agent crashing: back-date claimed_at past
    # the staleness window directly in the DB (no code path does this
    # normally — it's standing in for "a long time passed").
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=print_jobs.STALE_JOB_TIMEOUT_SECONDS + 10)
    with print_records._engine.begin() as conn:
        conn.execute(
            print_jobs.print_jobs.update()
            .where(print_jobs.print_jobs.c.print_job_id == job_id)
            .values(claimed_at=stale_time)
        )

    reclaimed = print_jobs.claim_next_job("STATION_B")
    assert reclaimed is not None
    assert reclaimed["print_job_id"] == job_id
    job = print_jobs.latest_job_for_request(request_id)
    assert job["attempt_count"] == 2  # claimed twice now
    assert job["station_id"] == "STATION_B"


def test_job_exceeding_max_attempts_is_auto_failed_not_reclaimed():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("STATION_A")

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=print_jobs.STALE_JOB_TIMEOUT_SECONDS + 10)
    with print_records._engine.begin() as conn:
        conn.execute(
            print_jobs.print_jobs.update()
            .where(print_jobs.print_jobs.c.print_job_id == job_id)
            .values(claimed_at=stale_time, attempt_count=print_jobs.MAX_PRINT_ATTEMPTS)
        )

    assert print_jobs.claim_next_job("STATION_B") is None
    job = print_jobs.latest_job_for_request(request_id)
    assert job["status"] == "failed"
    assert job["last_error"] == "Exceeded max print attempts"


def test_mark_printed_updates_job_and_denormalized_print_record():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("POC_PRINTER_01")

    assert print_jobs.mark_printed(job_id, "POC_PRINTER_01") is True
    job = print_jobs.latest_job_for_request(request_id)
    assert job["status"] == "printed"
    assert job["printed_at"] is not None

    record = print_records.get_record(request_id)
    assert record["print_status"] == "printed"


def test_mark_printed_rejects_wrong_station_id():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("POC_PRINTER_01")

    assert print_jobs.mark_printed(job_id, "SOME_OTHER_STATION") is False
    job = print_jobs.latest_job_for_request(request_id)
    assert job["status"] == "printing"  # unchanged


def test_mark_failed_updates_job_and_denormalized_print_record():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("POC_PRINTER_01")

    assert print_jobs.mark_failed(job_id, "POC_PRINTER_01", "printer not connected") is True
    job = print_jobs.latest_job_for_request(request_id)
    assert job["status"] == "failed"
    assert job["last_error"] == "printer not connected"

    record = print_records.get_record(request_id)
    assert record["print_status"] == "print_failed"
    assert record["print_error"] == "printer not connected"


def test_retry_only_works_on_a_failed_job():
    request_id = _make_request()
    job_id = print_jobs.create_job(request_id)

    # Still pending — retry must refuse, not silently requeue.
    assert print_jobs.retry_job(request_id) is None

    print_jobs.claim_next_job("POC_PRINTER_01")
    print_jobs.mark_failed(job_id, "POC_PRINTER_01", "printer not connected")

    retried = print_jobs.retry_job(request_id)
    assert retried is not None
    assert retried["print_job_id"] == job_id  # SAME row, not a new one
    assert retried["status"] == "pending"
    assert retried["station_id"] is None
    # The stale error from the attempt being retried must not linger —
    # it's a continued attempt, not a display of the last failure.
    assert retried["last_error"] is None


def test_reprint_creates_a_new_job_leaving_the_original_untouched():
    request_id = _make_request()
    first_job_id = print_jobs.create_job(request_id)
    print_jobs.claim_next_job("POC_PRINTER_01")
    print_jobs.mark_printed(first_job_id, "POC_PRINTER_01")

    second_job_id = print_jobs.reprint_job(request_id)
    assert second_job_id != first_job_id

    latest = print_jobs.latest_job_for_request(request_id)
    assert latest["print_job_id"] == second_job_id
    assert latest["status"] == "pending"
    assert latest["attempt_count"] == 0

    # The original printed job's row is untouched, not overwritten.
    with print_records._engine.connect() as conn:
        original = conn.execute(
            text("SELECT status FROM print_jobs WHERE print_job_id = :id"),
            {"id": first_job_id},
        ).scalar()
    assert original == "printed"
