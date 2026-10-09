import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

import doctors
import print_records


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    # Same disposable-file-per-test pattern test_print_records.py already
    # uses — doctors.py reads print_records._engine dynamically (see its
    # own _engine() docstring), so swapping it here is enough for both
    # modules to share the same temp database.
    db_path = tmp_path / "test_doctors.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(print_records, "_engine", engine)
    print_records.init_db()
    doctors.init_db()


def test_create_and_verify_password_roundtrip():
    doctor = doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    assert doctor == {
        "hpcsa_number": "808080",
        "name": "Dr. Jane Doe",
        "cell": "0821234567",
        "email": "jane@example.com",
    }

    verified = doctors.verify_password("808080", "hunter22")
    assert verified == doctor

    # HPCSA lookup is case/whitespace-insensitive, matching the frontend's
    # own normalizeHpcsa() convention.
    assert doctors.verify_password(" 808080 ", "hunter22") is not None
    assert doctors.verify_password("808080", "wrongpassword") is None
    assert doctors.verify_password("999999", "hunter22") is None


def test_duplicate_hpcsa_raises_integrity_error():
    doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    with pytest.raises(IntegrityError):
        doctors.create_doctor("Dr. Someone Else", "808080", "0829999999", "x@example.com", "otherpass")


def test_password_is_never_stored_in_plaintext():
    doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    row = doctors._get_row("808080")
    assert row["password_hash"] != "hunter22"
    assert "hunter22" not in row["password_hash"]
    # A fresh random salt each time — not a fixed/shared one.
    doctors.create_doctor("Dr. Second Doc", "808081", "0821234568", "x2@example.com", "hunter22")
    row2 = doctors._get_row("808081")
    assert row["password_salt"] != row2["password_salt"]
    assert row["password_hash"] != row2["password_hash"]


def test_issuing_two_session_tokens_does_not_invalidate_either():
    # The whole point of moving auth server-side: the same account logged
    # in on two devices (PC + phone) at once must not kick either one out.
    doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    token_a = doctors.issue_session_token("808080")
    token_b = doctors.issue_session_token("808080")
    assert token_a != token_b
    assert doctors.get_doctor_by_token(token_a) is not None
    assert doctors.get_doctor_by_token(token_b) is not None


def test_get_doctor_by_invalid_token_returns_none():
    assert doctors.get_doctor_by_token("not-a-real-token") is None
    assert doctors.get_doctor_by_token("") is None


def test_update_profile():
    doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    updated = doctors.update_profile("808080", "Dr. Jane Updated", "0829999999", "new@example.com")
    assert updated["name"] == "Dr. Jane Updated"
    assert updated["cell"] == "0829999999"
    assert updated["email"] == "new@example.com"
    assert doctors.verify_password("808080", "hunter22") == updated


def test_update_password_requires_correct_current_password():
    doctors.create_doctor("Dr. Jane Doe", "808080", "0821234567", "jane@example.com", "hunter22")
    assert doctors.update_password("808080", "wrongpassword", "newpass123") is False
    assert doctors.verify_password("808080", "hunter22") is not None  # unchanged

    assert doctors.update_password("808080", "hunter22", "newpass123") is True
    assert doctors.verify_password("808080", "hunter22") is None  # old password now rejected
    assert doctors.verify_password("808080", "newpass123") is not None
