import pytest

from datetime_normalize import normalize_date, normalize_time


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("25 minutes to 3 p.m.", "14:35"),
        ("20 minutes to 3 p.m.", "14:40"),
        ("quarter to three p.m.", "14:45"),
        ("half past two p.m.", "14:30"),
        ("quarter past two p.m.", "14:15"),
        ("three p.m.", "15:00"),
        ("9 a.m.", "09:00"),
        ("twelve p.m.", "12:00"),
        ("twelve a.m.", "00:00"),
        ("2:30 pm", "14:30"),
    ],
)
def test_normalize_time_confirmed(raw, expected):
    value, status = normalize_time(raw)
    assert value == expected
    assert status == "confirmed"


@pytest.mark.parametrize(
    "raw",
    [
        "three o'clock",  # no am/pm — direction ambiguous
        "around three",
        "this morning",
        "later",
        "yesterday",
        "10 minutes to 3",  # no am/pm — must NOT be guessed
        "5 minutes past 3",  # no am/pm — must NOT be guessed
        "",
    ],
)
def test_normalize_time_ambiguous(raw):
    value, status = normalize_time(raw)
    assert value is None
    assert status == "ambiguous"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("22 September 2026", "2026-09-22"),
        ("22nd September 2026", "2026-09-22"),
        ("September 22nd 2026", "2026-09-22"),
        ("22nd of September 2026", "2026-09-22"),
        ("2026-22nd September", "2026-09-22"),
        ("2026-09-22", "2026-09-22"),
        ("17 January 2026", "2026-01-17"),
    ],
)
def test_normalize_date_confirmed(raw, expected):
    value, status = normalize_date(raw)
    assert value == expected
    assert status == "confirmed"


def test_normalize_date_malformed_multiple_months_is_ambiguous():
    value, status = normalize_date("1998-8-August 14th, 6 May")
    assert value is None
    assert status == "ambiguous"


@pytest.mark.parametrize(
    "raw",
    [
        "September 2026",  # no day
        "22 2026",  # no month
        "31 February 2026",  # invalid calendar date
        "2026-02-30",  # invalid calendar date (ISO form)
        "",
    ],
)
def test_normalize_date_ambiguous(raw):
    value, status = normalize_date(raw)
    assert value is None
    assert status == "ambiguous"
