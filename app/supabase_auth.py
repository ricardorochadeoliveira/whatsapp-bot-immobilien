"""Firmen-Login ueber Supabase Auth (statt selbstgebautem Passwort-Hashing -
sicherer, siehe docs/produkt-abgleich.md).

Token-Pruefung laeuft bewusst ueber GET /auth/v1/user gegen Supabase selbst,
statt den JWT lokal zu decodieren/verifizieren - dadurch brauchen wir keinen
JWT-Secret zu verwalten und uebernehmen keine eigene Verantwortung fuer
Algorithmus-/Expiry-/Signatur-Pruefung. Kostet einen zusaetzlichen
Netzwerk-Roundtrip pro Anfrage, das ist fuer unseren Umfang unkritisch.
"""
from __future__ import annotations

import base64
import json
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
    """Verifiziert den Access-Token bei Supabase und gibt den Nutzer zurueck
    (inkl. `factors` fuer den MFA-Status). Wirft SupabaseAuthError, wenn der
    Token ungueltig/abgelaufen ist."""
    url, anon_key = _config()
    resp = httpx.get(
        f"{url}/auth/v1/user",
        headers={"apikey": anon_key, "Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError("Token ungueltig oder abgelaufen.")
    return resp.json()


def sign_out(access_token: str) -> None:
    """Invalidiert den Token serverseitig bei Supabase (Logout) - damit ist
    er nicht nur lokal vergessen, sondern wirklich nicht mehr benutzbar."""
    url, anon_key = _config()
    try:
        httpx.post(
            f"{url}/auth/v1/logout",
            headers={"apikey": anon_key, "Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except httpx.HTTPError:
        pass  # Logout darf nie fehlschlagen - der Cookie wird so oder so geloescht.


def recover_password(email: str, redirect_to: str | None = None) -> None:
    """Stoesst den Supabase-Passwort-Reset an - verschickt eine E-Mail mit
    einem Recovery-Link. Gibt bewusst nichts zurueck und wirft nur bei
    echten Betriebsfehlern (z.B. Rate-Limit) - ob die E-Mail-Adresse
    ueberhaupt existiert, verraet Supabase hier absichtlich nicht (schuetzt
    vor Account-Enumeration), das reicht bis zum Aufrufer durch."""
    url, anon_key = _config()
    endpoint = f"{url}/auth/v1/recover"
    resp = httpx.post(
        endpoint,
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        params={"redirect_to": redirect_to} if redirect_to else None,
        json={"email": email},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.json().get("msg") or resp.text)


def update_password_with_recovery_token(recovery_token: str, new_password: str) -> None:
    """Setzt ein neues Passwort - der recovery_token (aus dem Link in der
    Reset-E-Mail) dient hier selbst als Bearer-Auth, genau wie bei Supabase
    vorgesehen."""
    url, anon_key = _config()
    resp = httpx.put(
        f"{url}/auth/v1/user",
        headers={"apikey": anon_key, "Authorization": f"Bearer {recovery_token}"},
        json={"password": new_password},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError(
            resp.json().get("msg") or "Link ungueltig oder abgelaufen - bitte neu anfordern."
        )


def _decode_jwt_aal(access_token: str) -> str | None:
    """Liest den `aal`-Claim (Authentication Assurance Level, "aal1"/"aal2")
    direkt aus dem JWT-Payload. Keine eigene Signaturpruefung noetig: dieser
    Token wurde im selben Aufruf bereits ueber get_user() bei Supabase
    verifiziert - ein manipulierter Claim haette die Signatur ungueltig
    gemacht und Supabase haette den Request schon abgelehnt."""
    try:
        payload_b64 = access_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return payload.get("aal")
    except (IndexError, ValueError, TypeError):
        return None


def enroll_totp(access_token: str, friendly_name: str = "Authenticator App") -> dict:
    """Startet die 2FA-Einrichtung. Antwort enthaelt `id` (factor_id) und
    `totp.qr_code` (fertiger data:image/svg+xml-URI, direkt in <img src>
    einsetzbar) + `totp.secret` (fuer manuelle Eingabe)."""
    url, anon_key = _config()
    resp = httpx.post(
        f"{url}/auth/v1/factors",
        headers={"apikey": anon_key, "Authorization": f"Bearer {access_token}"},
        json={"factor_type": "totp", "friendly_name": friendly_name},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.json().get("msg") or resp.text)
    return resp.json()


def challenge_and_verify_totp(access_token: str, factor_id: str, code: str) -> dict:
    """Fuehrt Challenge + Verify in einem Aufruf durch (Aufrufer muss nur
    factor_id + Code kennen). Bei Erfolg enthaelt die Antwort eine neue
    Session (access_token/refresh_token) mit aal2."""
    url, anon_key = _config()
    headers = {"apikey": anon_key, "Authorization": f"Bearer {access_token}"}

    challenge_resp = httpx.post(
        f"{url}/auth/v1/factors/{factor_id}/challenge", headers=headers, timeout=10
    )
    if challenge_resp.status_code >= 400:
        raise SupabaseAuthError(challenge_resp.json().get("msg") or challenge_resp.text)
    challenge_id = challenge_resp.json().get("id")

    verify_resp = httpx.post(
        f"{url}/auth/v1/factors/{factor_id}/verify",
        headers=headers,
        json={"challenge_id": challenge_id, "code": code},
        timeout=10,
    )
    if verify_resp.status_code >= 400:
        raise SupabaseAuthError(verify_resp.json().get("msg") or "Code ungueltig oder abgelaufen.")
    return verify_resp.json()
