"""Firmen-Login ueber Supabase Auth (statt selbstgebautem Passwort-Hashing -
sicherer, siehe docs/produkt-abgleich.md).

Token-Pruefung laeuft bewusst ueber GET /auth/v1/user gegen Supabase selbst,
statt den JWT lokal zu decodieren/verifizieren - dadurch brauchen wir keinen
JWT-Secret zu verwalten und uebernehmen keine eigene Verantwortung fuer
Algorithmus-/Expiry-/Signatur-Pruefung. Kostet einen zusaetzlichen
Netzwerk-Roundtrip pro Anfrage, das ist fuer unseren Umfang unkritisch.
"""
from __future__ import annotations

import os

import httpx


class SupabaseAuthError(RuntimeError):
    """Signup/Login/Token-Pruefung ist fehlgeschlagen (inkl. falsche Zugangsdaten)."""


def _config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not anon_key:
        raise SupabaseAuthError(
            "SUPABASE_URL/SUPABASE_ANON_KEY sind nicht gesetzt (siehe .env.example)."
        )
    return url, anon_key


def sign_up(email: str, password: str) -> dict:
    url, anon_key = _config()
    resp = httpx.post(
        f"{url}/auth/v1/signup",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.json().get("msg") or resp.text)
    return resp.json()


def sign_in(email: str, password: str) -> dict:
    url, anon_key = _config()
    resp = httpx.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.json().get("error_description") or resp.text)
    return resp.json()


def get_user(access_token: str) -> dict:
    """Verifiziert den Access-Token bei Supabase und gibt den Nutzer zurueck.
    Wirft SupabaseAuthError, wenn der Token ungueltig/abgelaufen ist."""
    url, anon_key = _config()
    resp = httpx.get(
        f"{url}/auth/v1/user",
        headers={"apikey": anon_key, "Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError("Token ungueltig oder abgelaufen.")
    return resp.json()
