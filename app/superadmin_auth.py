"""Superadmin-Login - eigene Session/eigene Endpunkte, aber derselbe
Supabase-Auth-Nutzerpool wie das Firmenportal (app/firma_service.py). Kein
neues Krypto-/Hashing-Modul: Identitaet, Passwort und MFA liegen komplett bei
Supabase, genau wie beim Firmenportal (siehe app/supabase_auth.py-Docstring).

Der eigentliche Schutzmechanismus ist die E-Mail-Allowlist (SUPERADMIN_EMAILS):
jede Person mit einem Supabase-Auth-Konto (auch eine registrierte Firma) kann
sich technisch bei Supabase anmelden - erst der Abgleich gegen diese Allowlist
entscheidet, ob daraus eine Superadmin-Session wird."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from app.supabase_auth import (
    SupabaseAuthError,
    challenge_and_verify_totp,
    enroll_totp,
    get_user,
    sign_in,
)


class SuperadminAuthError(RuntimeError):
    """Login/Autorisierung fehlgeschlagen - als 401/400 an die API durchreichen."""


def _allowlist() -> set[str]:
    raw = os.environ.get("SUPERADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _verified_totp_factor(user: dict) -> Optional[dict]:
    for factor in user.get("factors") or []:
        if factor.get("factor_type") == "totp" and factor.get("status") == "verified":
            return factor
    return None


@dataclass
class LoginResult:
    email: Optional[str] = None
    access_token: Optional[str] = None
    mfa_required: bool = False
    factor_id: Optional[str] = None
    pending_token: Optional[str] = None


def login(email: str, password: str) -> LoginResult:
    if email.strip().lower() not in _allowlist():
        # Bewusst dieselbe Meldung wie bei falschem Passwort - kein Signal,
        # ob eine E-Mail ueberhaupt zur Allowlist gehoert.
        raise SuperadminAuthError("Ungueltige Zugangsdaten.")

    try:
        data = sign_in(email, password)
    except SupabaseAuthError:
        raise SuperadminAuthError("Ungueltige Zugangsdaten.") from None

    access_token = data.get("access_token")
    user = data.get("user") or {}
    if not access_token or not user.get("email"):
        raise SuperadminAuthError("Unerwartete Antwort von Supabase Auth.")

    factor = _verified_totp_factor(user)
    if factor is not None:
        return LoginResult(mfa_required=True, factor_id=factor["id"], pending_token=access_token)

    return LoginResult(email=user["email"], access_token=access_token)


def verify_login_mfa(pending_token: str, factor_id: str, code: str) -> LoginResult:
    try:
        data = challenge_and_verify_totp(pending_token, factor_id, code)
    except SupabaseAuthError as exc:
        raise SuperadminAuthError(str(exc)) from exc

    access_token = data.get("access_token")
    user = data.get("user") or {}
    if not access_token or not user.get("email"):
        raise SuperadminAuthError("Unerwartete Antwort von Supabase Auth.")
    if user["email"].strip().lower() not in _allowlist():
        raise SuperadminAuthError("Ungueltige Zugangsdaten.")
    return LoginResult(email=user["email"], access_token=access_token)


def verify_session(access_token: str) -> str:
    """Verifiziert den Token bei Supabase UND erneut gegen die Allowlist
    (Verteidigung in der Tiefe - falls die Allowlist sich seit dem Login
    geaendert hat). Gibt die E-Mail zurueck."""
    try:
        user = get_user(access_token)
    except SupabaseAuthError as exc:
        raise SuperadminAuthError(str(exc)) from exc

    email = (user.get("email") or "").strip().lower()
    if not email or email not in _allowlist():
        raise SuperadminAuthError("Ungueltige oder abgelaufene Session.")
    return email


def enroll_mfa(access_token: str) -> dict:
    try:
        return enroll_totp(access_token)
    except SupabaseAuthError as exc:
        raise SuperadminAuthError(str(exc)) from exc


def activate_mfa(access_token: str, factor_id: str, code: str) -> str:
    try:
        data = challenge_and_verify_totp(access_token, factor_id, code)
    except SupabaseAuthError as exc:
        raise SuperadminAuthError(str(exc)) from exc
    new_token = data.get("access_token")
    if not new_token:
        raise SuperadminAuthError("Unerwartete Antwort von Supabase Auth.")
    return new_token
