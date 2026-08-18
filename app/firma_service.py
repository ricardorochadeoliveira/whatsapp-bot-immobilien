"""Firmen-Login (Supabase Auth) + Inserate-Verwaltung, mandantengetrennt.

create_inserat/list_inserate/set_inserat_status laufen bewusst NICHT ueber
ImmobilienRepository (das ist fuer den WhatsApp-/Matching-Pfad gedacht und
laeuft dort ueber die Superuser-Verbindung), sondern direkt ueber
app.db.tenant_session - damit greift RLS tatsaechlich (eingeschraenkte
app_runtime-Rolle + gesetzter Mandanten-Kontext), nicht nur auf dem Papier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.db import tenant_session
from app.models import Firma, Immobilie, Lead
from app.models_orm import FirmaORM, ImmobilieORM, LeadORM
from app.password_policy import WeakPasswordError, validate_password_strength
from app.repository import FirmaRepository
from app.repository_supabase import _firma_from_orm, _immobilie_from_orm, _lead_from_orm
from app.supabase_auth import (
    SupabaseAuthError,
    _decode_jwt_aal,
    challenge_and_verify_totp,
    enroll_totp,
    get_user,
    recover_password,
    sign_in,
    sign_up,
    update_password_with_recovery_token,
)


class FirmaAuthError(RuntimeError):
    """Signup/Login/Autorisierung fehlgeschlagen - als 401/400 an die API durchreichen."""


EDITABLE_INSERAT_FIELDS = {
    "titel", "beschreibung", "typ", "zimmer", "kanton", "ort",
    "preis", "objekttyp", "flaeche_m2", "hat_garten",
}


def _verified_totp_factor(user: dict) -> Optional[dict]:
    for factor in user.get("factors") or []:
        if factor.get("factor_type") == "totp" and factor.get("status") == "verified":
            return factor
    return None


@dataclass
class LoginResult:
    """Entweder direkt eingeloggt (firma/access_token gesetzt) oder die
    Firma hat 2FA aktiviert und muss zuerst den Code bestaetigen
    (mfa_required=True, pending_token ist nur fuer diesen naechsten Schritt
    nutzbar - authenticate() verweigert ihm sonst den Zugriff, da aal1)."""

    firma: Optional[Firma] = None
    access_token: Optional[str] = None
    mfa_required: bool = False
    factor_id: Optional[str] = None
    pending_token: Optional[str] = None


class FirmaService:
    def __init__(self, firma_repo: FirmaRepository):
        self._firma_repo = firma_repo

    def signup(self, name: str, email: str, password: str) -> Firma:
        try:
            validate_password_strength(password)
        except WeakPasswordError as exc:
            raise FirmaAuthError(str(exc)) from exc

        try:
            data = sign_up(email, password)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

        auth_user_id = data.get("id") or (data.get("user") or {}).get("id")
        if not auth_user_id:
            raise FirmaAuthError("Supabase hat keine Nutzer-ID zurueckgegeben.")

        return self._firma_repo.add(Firma(name=name, email=email, auth_user_id=auth_user_id))

    def login(self, email: str, password: str) -> LoginResult:
        try:
            data = sign_in(email, password)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

        access_token = data.get("access_token")
        user = data.get("user") or {}
        auth_user_id = user.get("id")
        if not access_token or not auth_user_id:
            raise FirmaAuthError("Unerwartete Antwort von Supabase Auth.")

        firma = self._firma_repo.get_by_auth_user_id(auth_user_id)
        if firma is None:
            raise FirmaAuthError("Kein Firmenprofil zu diesem Login gefunden.")

        factor = _verified_totp_factor(user)
        if factor is not None:
            return LoginResult(mfa_required=True, factor_id=factor["id"], pending_token=access_token)

        return LoginResult(firma=firma, access_token=access_token)

    def verify_login_mfa(self, pending_token: str, factor_id: str, code: str) -> LoginResult:
        """Zweiter Schritt nach login() mit mfa_required=True - prueft den
        TOTP-Code und liefert bei Erfolg die aal2-Session."""
        try:
            data = challenge_and_verify_totp(pending_token, factor_id, code)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

        access_token = data.get("access_token")
        auth_user_id = (data.get("user") or {}).get("id")
        if not access_token or not auth_user_id:
            raise FirmaAuthError("Unerwartete Antwort von Supabase Auth.")

        firma = self._firma_repo.get_by_auth_user_id(auth_user_id)
        if firma is None:
            raise FirmaAuthError("Kein Firmenprofil zu diesem Login gefunden.")
        return LoginResult(firma=firma, access_token=access_token)

    def authenticate(self, access_token: str) -> Firma:
        """Verifiziert den Token bei Supabase und liefert die zugehoerige
        Firma. Von FastAPI-Endpunkten als Dependency genutzt. Firmen mit
        aktivierter 2FA muessen aal2 vorweisen (siehe verify_login_mfa) -
        ein blosser aal1-Token (z.B. der pending_token aus login()) wird
        sonst verweigert."""
        try:
            user = get_user(access_token)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

        if _verified_totp_factor(user) is not None and _decode_jwt_aal(access_token) != "aal2":
            raise FirmaAuthError("Zwei-Faktor-Bestaetigung erforderlich.")

        firma = self._firma_repo.get_by_auth_user_id(user["id"])
        if firma is None:
            raise FirmaAuthError("Kein Firmenprofil zu diesem Login gefunden.")
        return firma

    def enroll_mfa(self, access_token: str) -> dict:
        try:
            return enroll_totp(access_token)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

    def activate_mfa(self, access_token: str, factor_id: str, code: str) -> str:
        """Aktiviert den Faktor (Challenge+Verify) und gibt den neuen
        aal2-Access-Token zurueck - die aktuelle Session wird dadurch
        elevated, es ist kein erneuter Login noetig."""
        try:
            data = challenge_and_verify_totp(access_token, factor_id, code)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc
        new_token = data.get("access_token")
        if not new_token:
            raise FirmaAuthError("Unerwartete Antwort von Supabase Auth.")
        return new_token

    def request_password_reset(self, email: str, redirect_to: Optional[str] = None) -> None:
        """Stoesst den Reset an. Verrat absichtlich nie, ob die E-Mail
        ueberhaupt zu einer Firma gehoert (Aufrufer soll immer dieselbe
        generische Meldung anzeigen) - nur echte Betriebsfehler (z.B.
        Supabase-Rate-Limit) werden durchgereicht."""
        try:
            recover_password(email, redirect_to=redirect_to)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

    def reset_password(self, recovery_token: str, new_password: str) -> None:
        try:
            validate_password_strength(new_password)
        except WeakPasswordError as exc:
            raise FirmaAuthError(str(exc)) from exc

        try:
            update_password_with_recovery_token(recovery_token, new_password)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

    def create_inserat(self, firma: Firma, immobilie: Immobilie) -> Immobilie:
        immobilie = immobilie.model_copy(update={"firma_id": firma.id})
        with tenant_session(firma_id=firma.id) as session:
            session.add(ImmobilieORM(**immobilie.model_dump()))
        return immobilie

    def list_inserate(self, firma: Firma) -> list[Immobilie]:
        with tenant_session(firma_id=firma.id) as session:
            rows = session.scalars(
                select(ImmobilieORM).where(ImmobilieORM.firma_id == firma.id)
            ).all()
            return [_immobilie_from_orm(r) for r in rows]

    def set_inserat_status(self, firma: Firma, immobilie_id: str, status: str) -> None:
        with tenant_session(firma_id=firma.id) as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is None or row.firma_id != firma.id:
                raise FirmaAuthError("Inserat nicht gefunden oder gehoert nicht dieser Firma.")
            row.status = status

    def add_bilder(self, firma: Firma, immobilie_id: str, urls: list[str]) -> Immobilie:
        with tenant_session(firma_id=firma.id) as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is None or row.firma_id != firma.id:
                raise FirmaAuthError("Inserat nicht gefunden oder gehoert nicht dieser Firma.")
            row.bilder = [*(row.bilder or []), *urls]
            session.flush()
            return _immobilie_from_orm(row)

    def remove_bild(self, firma: Firma, immobilie_id: str, url: str) -> Immobilie:
        with tenant_session(firma_id=firma.id) as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is None or row.firma_id != firma.id:
                raise FirmaAuthError("Inserat nicht gefunden oder gehoert nicht dieser Firma.")
            row.bilder = [b for b in (row.bilder or []) if b != url]
            session.flush()
            return _immobilie_from_orm(row)

    def update_inserat(self, firma: Firma, immobilie_id: str, updates: dict) -> Immobilie:
        with tenant_session(firma_id=firma.id) as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is None or row.firma_id != firma.id:
                raise FirmaAuthError("Inserat nicht gefunden oder gehoert nicht dieser Firma.")
            for field, value in updates.items():
                if field in EDITABLE_INSERAT_FIELDS:
                    setattr(row, field, value)
            session.flush()
            return _immobilie_from_orm(row)

    def update_profile(self, firma: Firma, name: str) -> Firma:
        # firma-Tabelle hat eine eigene RLS-Policy ueber auth_user_id (nicht
        # firma_id) - siehe scripts/migrate_multi_tenancy.py, Policy
        # "self_only". tenant_session() deshalb mit auth_user_id aufrufen,
        # sonst findet session.get() die Zeile mangels gesetztem
        # app.current_auth_user_id gar nicht erst.
        with tenant_session(auth_user_id=firma.auth_user_id) as session:
            row = session.get(FirmaORM, firma.id)
            if row is None:
                raise FirmaAuthError("Firma nicht gefunden.")
            row.name = name.strip()
            session.flush()
            return _firma_from_orm(row)

    def list_leads(self, firma: Firma) -> list[Lead]:
        with tenant_session(firma_id=firma.id) as session:
            rows = session.scalars(select(LeadORM).where(LeadORM.firma_id == firma.id)).all()
            return [_lead_from_orm(r) for r in rows]
