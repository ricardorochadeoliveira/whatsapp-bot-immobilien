import base64
import json

import httpx
import pytest

from app.supabase_auth import (
    SupabaseAuthError,
    _decode_jwt_aal,
    recover_password,
    update_password_with_recovery_token,
)


def _fake_jwt(payload: dict) -> str:
    """Baut ein JWT-foermiges Token mit beliebigem Payload (Header/Signatur
    sind fuer den Test irrelevant - _decode_jwt_aal verifiziert die Signatur
    bewusst nicht selbst, siehe Docstring dort)."""
    header_b64 = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.signature"


def test_decodes_aal2_claim():
    token = _fake_jwt({"aal": "aal2", "sub": "user-123"})
    assert _decode_jwt_aal(token) == "aal2"


def test_decodes_aal1_claim():
    token = _fake_jwt({"aal": "aal1"})
    assert _decode_jwt_aal(token) == "aal1"


def test_returns_none_for_missing_claim():
    token = _fake_jwt({"sub": "user-123"})
    assert _decode_jwt_aal(token) is None


def test_returns_none_for_malformed_token():
    assert _decode_jwt_aal("not-a-jwt") is None


def test_recover_password_posts_email_and_redirect(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    captured = {}

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    recover_password("firma@example.com", redirect_to="https://app.example.com/firma")

    assert captured["url"] == "https://example.supabase.co/auth/v1/recover"
    assert captured["json"] == {"email": "firma@example.com"}
    assert captured["params"] == {"redirect_to": "https://app.example.com/firma"}


def test_recover_password_raises_on_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        return httpx.Response(429, json={"msg": "rate limit exceeded"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(SupabaseAuthError):
        recover_password("firma@example.com")


def test_update_password_with_recovery_token_success(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    captured = {}

    def fake_put(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={}, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "put", fake_put)
    update_password_with_recovery_token("recovery-token-abc", "NeuesPasswort1")

    assert captured["headers"]["Authorization"] == "Bearer recovery-token-abc"
    assert captured["json"] == {"password": "NeuesPasswort1"}


def test_update_password_with_recovery_token_raises_on_invalid_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    def fake_put(url, headers=None, json=None, timeout=None):
        return httpx.Response(401, json={"msg": "invalid token"}, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "put", fake_put)
    with pytest.raises(SupabaseAuthError):
        update_password_with_recovery_token("expired-token", "NeuesPasswort1")
