"""Firmen-Login (Supabase Auth) + Inserate-Verwaltung, mandantengetrennt.

create_inserat/list_inserate/set_inserat_status laufen bewusst NICHT ueber
ImmobilienRepository (das ist fuer den WhatsApp-/Matching-Pfad gedacht und
laeuft dort ueber die Superuser-Verbindung), sondern direkt ueber
app.db.tenant_session - damit greift RLS tatsaechlich (eingeschraenkte
app_runtime-Rolle + gesetzter Mandanten-Kontext), nicht nur auf dem Papier.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db import tenant_session
from app.models import Firma, Immobilie, Lead
from app.models_orm import ImmobilieORM, LeadORM
from app.repository import FirmaRepository
from app.repository_supabase import _immobilie_from_orm, _lead_from_orm
from app.supabase_auth import SupabaseAuthError, get_user, sign_in, sign_up


class FirmaAuthError(RuntimeError):
    """Signup/Login/Autorisierung fehlgeschlagen - als 401/400 an die API durchreichen."""


@dataclass
class LoginResult:
    access_token: str
    firma: Firma


class FirmaService:
    def __init__(self, firma_repo: FirmaRepository):
        self._firma_repo = firma_repo

    def signup(self, name: str, email: str, password: str) -> Firma:
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
        auth_user_id = (data.get("user") or {}).get("id")
        if not access_token or not auth_user_id:
            raise FirmaAuthError("Unerwartete Antwort von Supabase Auth.")

        firma = self._firma_repo.get_by_auth_user_id(auth_user_id)
        if firma is None:
            raise FirmaAuthError("Kein Firmenprofil zu diesem Login gefunden.")

        return LoginResult(access_token=access_token, firma=firma)

    def authenticate(self, access_token: str) -> Firma:
        """Verifiziert den Bearer-Token bei Supabase und liefert die zugehoerige
        Firma. Von FastAPI-Endpunkten als Dependency genutzt."""
        try:
            user = get_user(access_token)
        except SupabaseAuthError as exc:
            raise FirmaAuthError(str(exc)) from exc

        firma = self._firma_repo.get_by_auth_user_id(user["id"])
        if firma is None:
            raise FirmaAuthError("Kein Firmenprofil zu diesem Login gefunden.")
        return firma

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

    def list_leads(self, firma: Firma) -> list[Lead]:
        with tenant_session(firma_id=firma.id) as session:
            rows = session.scalars(select(LeadORM).where(LeadORM.firma_id == firma.id)).all()
            return [_lead_from_orm(r) for r in rows]
