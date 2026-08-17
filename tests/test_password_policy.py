import pytest

from app.password_policy import WeakPasswordError, validate_password_strength


def test_rejects_too_short_password():
    with pytest.raises(WeakPasswordError):
        validate_password_strength("abc123")


def test_rejects_password_without_digit():
    with pytest.raises(WeakPasswordError):
        validate_password_strength("nurbuchstaben")


def test_rejects_password_without_letter():
    with pytest.raises(WeakPasswordError):
        validate_password_strength("12345678")


def test_accepts_valid_password():
    validate_password_strength("Sicher123")  # darf keine Exception werfen
