from unittest.mock import patch

import pytest

from app import superadmin_auth
from app.superadmin_auth import SuperadminAuthError
from app.supabase_auth import SupabaseAuthError


def test_login_rejects_email_not_on_allowlist(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    with pytest.raises(SuperadminAuthError):
        superadmin_auth.login("jemand-anderes@example.com", "irgendeinPasswort1")


def test_login_success_returns_access_token(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    fake_response = {
        "access_token": "token-abc",
        "user": {"id": "user-1", "email": "admin@wohnchat.ch", "factors": []},
    }
    with patch.object(superadmin_auth, "sign_in", return_value=fake_response):
        result = superadmin_auth.login("admin@wohnchat.ch", "richtigesPasswort1")

    assert result.mfa_required is False
    assert result.access_token == "token-abc"
    assert result.email == "admin@wohnchat.ch"


def test_login_wraps_supabase_error(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    with patch.object(superadmin_auth, "sign_in", side_effect=SupabaseAuthError("Falsches Passwort")):
        with pytest.raises(SuperadminAuthError):
            superadmin_auth.login("admin@wohnchat.ch", "falschesPasswort")


def test_login_returns_mfa_required_when_totp_verified(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    fake_response = {
        "access_token": "pending-token",
        "user": {
            "id": "user-1",
            "email": "admin@wohnchat.ch",
            "factors": [{"factor_type": "totp", "status": "verified", "id": "factor-1"}],
        },
    }
    with patch.object(superadmin_auth, "sign_in", return_value=fake_response):
        result = superadmin_auth.login("admin@wohnchat.ch", "richtigesPasswort1")

    assert result.mfa_required is True
    assert result.factor_id == "factor-1"
    assert result.pending_token == "pending-token"


def test_verify_session_rejects_email_not_on_allowlist(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    with patch.object(superadmin_auth, "get_user", return_value={"email": "jemand-anderes@example.com"}):
        with pytest.raises(SuperadminAuthError):
            superadmin_auth.verify_session("irgendein-token")


def test_verify_session_returns_email_for_allowlisted_user(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    with patch.object(superadmin_auth, "get_user", return_value={"email": "admin@wohnchat.ch"}):
        assert superadmin_auth.verify_session("gueltiger-token") == "admin@wohnchat.ch"


def test_verify_login_mfa_success(monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "admin@wohnchat.ch")
    fake_response = {"access_token": "aal2-token", "user": {"email": "admin@wohnchat.ch"}}
    with patch.object(superadmin_auth, "challenge_and_verify_totp", return_value=fake_response):
        result = superadmin_auth.verify_login_mfa("pending-token", "factor-1", "123456")

    assert result.access_token == "aal2-token"
    assert result.email == "admin@wohnchat.ch"
