"""Supabase/Postgres-Implementierungen der Repository-Interfaces (Punkt 1).

Erfuellen exakt dieselben Interfaces wie die In-Memory-Repositories in
app/repository.py - Matching-Engine (Punkt 4) und Matching-Job (Punkt 5)
merken nicht, welche Implementierung dahintersteckt.

FirmaRepository ist der einzige mandantengebundene Teil hier: add() und
get_by_auth_user_id() laufen ueber die eingeschraenkte app_runtime-Rolle
+ RLS (siehe app.db.tenant_session), get_by_id() bewusst ueber die
Superuser-Verbindung, weil das ein interner Systemlookup ist (z.B. fuer den
Matching-Job), kein tenant-gebundener Zugriff einer eingeloggten Firma.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import tenant_session
from app.models import ChatKontakt, FehlerLog, Firma, Immobilie, Kunde, Lead, MatchLog, Suchprofil
from app.models_orm import (
    ChatKontaktORM,
    FehlerLogORM,
    FirmaORM,
    ImmobilieORM,
    KundeORM,
    LeadORM,
    MatchLogORM,
    SuchprofilORM,
)
from app.repository import (
    ChatKontaktRepository,
    FehlerLogRepository,
    FirmaRepository,
    ImmobilienRepository,
    KundenRepository,
    LeadRepository,
    MatchLogRepository,
    SuchprofilRepository,
)


def _firma_from_orm(row: FirmaORM) -> Firma:
    return Firma(
        id=row.id,
        name=row.name,
        typ=row.typ,
        email=row.email,
        telefonnummer=row.telefonnummer,
        auth_user_id=row.auth_user_id,
        gruendungsmitglied=row.gruendungsmitglied,
        erstellt_am=row.erstellt_am,
    )


def _kunde_from_orm(row: KundeORM) -> Kunde:
    return Kunde(
        id=row.id,
        firma_id=row.firma_id,
        telefonnummer=row.telefonnummer,
        opt_in=row.opt_in,
        erstellt_am=row.erstellt_am,
    )


def _suchprofil_from_orm(row: SuchprofilORM) -> Suchprofil:
    return Suchprofil(
        id=row.id,
        kunde_id=row.kunde_id,
        firma_id=row.firma_id,
        zimmer=row.zimmer,
        kanton=row.kanton,
        ort=row.ort,
        preis_max=row.preis_max,
        objekttyp=row.objekttyp,
        typ=row.typ,
        zusatzfilter=row.zusatzfilter,
        aktiv=row.aktiv,
        erstellt_am=row.erstellt_am,
    )


def _immobilie_from_orm(row: ImmobilieORM) -> Immobilie:
    return Immobilie(
        id=row.id,
        firma_id=row.firma_id,
        titel=row.titel,
        beschreibung=row.beschreibung,
        typ=row.typ,
        zimmer=row.zimmer,
        kanton=row.kanton,
        ort=row.ort,
        preis=row.preis,
        objekttyp=row.objekttyp,
        flaeche_m2=row.flaeche_m2,
        hat_garten=row.hat_garten,
        status=row.status,
        bilder=row.bilder or [],
        link=row.link,
        inseriert_am=row.inseriert_am,
    )


def _matchlog_from_orm(row: MatchLogORM) -> MatchLog:
    return MatchLog(
        id=row.id,
        suchprofil_id=row.suchprofil_id,
        immobilie_id=row.immobilie_id,
        firma_id=row.firma_id,
        benachrichtigt_am=row.benachrichtigt_am,
    )


def _lead_from_orm(row: LeadORM) -> Lead:
    return Lead(
        id=row.id,
        immobilie_id=row.immobilie_id,
        firma_id=row.firma_id,
        suchprofil_id=row.suchprofil_id,
        status=row.status,
        erstellt_am=row.erstellt_am,
    )


class SupabaseFirmaRepository(FirmaRepository):
    def __init__(self, admin_session_factory: sessionmaker):
        self._admin_session_factory = admin_session_factory

    def add(self, firma: Firma) -> Firma:
        with tenant_session(auth_user_id=firma.auth_user_id) as session:
            row = FirmaORM(**firma.model_dump())
            session.add(row)
        return firma

    def get_by_auth_user_id(self, auth_user_id: str) -> Optional[Firma]:
        with tenant_session(auth_user_id=auth_user_id) as session:
            row = session.scalar(select(FirmaORM).where(FirmaORM.auth_user_id == auth_user_id))
            return _firma_from_orm(row) if row else None

    def get_by_id(self, firma_id: str) -> Optional[Firma]:
        with self._admin_session_factory() as session:
            row = session.get(FirmaORM, firma_id)
            return _firma_from_orm(row) if row else None

    def get_by_email(self, email: str) -> Optional[Firma]:
        with self._admin_session_factory() as session:
            row = session.scalar(select(FirmaORM).where(FirmaORM.email == email))
            return _firma_from_orm(row) if row else None

    def get_or_create_by_phone(self, telefonnummer: str, name: str, typ: str) -> Firma:
        """WhatsApp-Vermieter-Flow, kein Login/RLS-Kontext - laeuft ueber die
        Superuser-Verbindung wie get_by_id/get_by_email/get_all."""
        with self._admin_session_factory() as session:
            row = session.scalar(select(FirmaORM).where(FirmaORM.telefonnummer == telefonnummer))
            if row is not None:
                return _firma_from_orm(row)

            firma = Firma(name=name, typ=typ, telefonnummer=telefonnummer)
            row = FirmaORM(**firma.model_dump())
            session.add(row)
            session.commit()
            return firma

    def get_by_phone(self, telefonnummer: str) -> Optional[Firma]:
        with self._admin_session_factory() as session:
            row = session.scalar(select(FirmaORM).where(FirmaORM.telefonnummer == telefonnummer))
            return _firma_from_orm(row) if row else None

    def link_phone(self, firma_id: str, telefonnummer: str) -> None:
        """Stempelt telefonnummer auf eine per Chat-Signup neu erstellte Firma
        (hat bis dahin nur auth_user_id/email) - laeuft wie get_or_create_by_phone
        ueber die Superuser-Verbindung, kein RLS-Kontext im WhatsApp-Flow."""
        with self._admin_session_factory() as session:
            row = session.get(FirmaORM, firma_id)
            if row is not None:
                row.telefonnummer = telefonnummer
                session.commit()

    def get_all(self) -> list[Firma]:
        with self._admin_session_factory() as session:
            rows = session.scalars(select(FirmaORM)).all()
            return [_firma_from_orm(r) for r in rows]


class SupabaseImmobilienRepository(ImmobilienRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def add(self, immobilie: Immobilie) -> Immobilie:
        with self._session_factory() as session:
            row = ImmobilieORM(**immobilie.model_dump())
            session.add(row)
            session.commit()
        return immobilie

    def get_all(self) -> list[Immobilie]:
        with self._session_factory() as session:
            rows = session.scalars(select(ImmobilieORM)).all()
            return [_immobilie_from_orm(r) for r in rows]

    def get_by_id(self, immobilie_id: str) -> Optional[Immobilie]:
        with self._session_factory() as session:
            row = session.get(ImmobilieORM, immobilie_id)
            return _immobilie_from_orm(row) if row else None

    def get_by_firma(self, firma_id: str) -> list[Immobilie]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ImmobilieORM).where(ImmobilieORM.firma_id == firma_id)
            ).all()
            return [_immobilie_from_orm(r) for r in rows]

    def get_by_status(self, status: str) -> list[Immobilie]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ImmobilieORM).where(ImmobilieORM.status == status)
            ).all()
            return [_immobilie_from_orm(r) for r in rows]

    def set_status(self, immobilie_id: str, status: str) -> None:
        with self._session_factory() as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is not None:
                row.status = status
                session.commit()

    def set_bilder(self, immobilie_id: str, bilder: list[str]) -> None:
        with self._session_factory() as session:
            row = session.get(ImmobilieORM, immobilie_id)
            if row is not None:
                row.bilder = bilder
                session.commit()


class SupabaseKundenRepository(KundenRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def get_or_create_by_phone(self, telefonnummer: str, firma_id: Optional[str] = None) -> Kunde:
        with self._session_factory() as session:
            row = session.scalar(
                select(KundeORM).where(
                    KundeORM.telefonnummer == telefonnummer, KundeORM.firma_id == firma_id
                )
            )
            if row is not None:
                return _kunde_from_orm(row)

            kunde = Kunde(telefonnummer=telefonnummer, firma_id=firma_id)
            row = KundeORM(**kunde.model_dump())
            session.add(row)
            session.commit()
            return kunde

    def get_by_id(self, kunde_id: str) -> Optional[Kunde]:
        with self._session_factory() as session:
            row = session.get(KundeORM, kunde_id)
            return _kunde_from_orm(row) if row else None


class SupabaseSuchprofilRepository(SuchprofilRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def add(self, suchprofil: Suchprofil) -> Suchprofil:
        with self._session_factory() as session:
            row = SuchprofilORM(**suchprofil.model_dump())
            session.add(row)
            session.commit()
        return suchprofil

    def get_all_active(self) -> list[Suchprofil]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SuchprofilORM).where(SuchprofilORM.aktiv.is_(True))
            ).all()
            return [_suchprofil_from_orm(r) for r in rows]

    def get_by_kunde(self, kunde_id: str) -> list[Suchprofil]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SuchprofilORM).where(SuchprofilORM.kunde_id == kunde_id)
            ).all()
            return [_suchprofil_from_orm(r) for r in rows]


class SupabaseMatchLogRepository(MatchLogRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def exists(self, suchprofil_id: str, immobilie_id: str) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(MatchLogORM).where(
                    MatchLogORM.suchprofil_id == suchprofil_id,
                    MatchLogORM.immobilie_id == immobilie_id,
                )
            )
            return row is not None

    def add(self, match_log: MatchLog) -> MatchLog:
        with self._session_factory() as session:
            row = MatchLogORM(**match_log.model_dump())
            session.add(row)
            session.commit()
        return match_log

    def get_all(self) -> list[MatchLog]:
        with self._session_factory() as session:
            rows = session.scalars(select(MatchLogORM)).all()
            return [_matchlog_from_orm(r) for r in rows]


class SupabaseLeadRepository(LeadRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def add(self, lead: Lead) -> Lead:
        with self._session_factory() as session:
            row = LeadORM(**lead.model_dump())
            session.add(row)
            session.commit()
        return lead

    def get_by_firma(self, firma_id: str) -> list[Lead]:
        with self._session_factory() as session:
            rows = session.scalars(select(LeadORM).where(LeadORM.firma_id == firma_id)).all()
            return [_lead_from_orm(r) for r in rows]


class SupabaseChatKontaktRepository(ChatKontaktRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def record_activity(self, telefonnummer: str) -> None:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.scalar(
                select(ChatKontaktORM).where(ChatKontaktORM.telefonnummer == telefonnummer)
            )
            if row is None:
                kontakt = ChatKontakt(telefonnummer=telefonnummer)
                session.add(ChatKontaktORM(**kontakt.model_dump()))
            else:
                row.letzte_aktivitaet_am = now
            session.commit()

    def count_all(self) -> int:
        with self._session_factory() as session:
            return len(session.scalars(select(ChatKontaktORM)).all())

    def count_active_since(self, seit: datetime) -> int:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ChatKontaktORM).where(ChatKontaktORM.letzte_aktivitaet_am >= seit)
            ).all()
            return len(rows)


class SupabaseFehlerLogRepository(FehlerLogRepository):
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def add(self, quelle: str, meldung: str, telefonnummer: Optional[str] = None) -> FehlerLog:
        eintrag = FehlerLog(quelle=quelle, meldung=meldung, telefonnummer=telefonnummer)
        with self._session_factory() as session:
            session.add(FehlerLogORM(**eintrag.model_dump()))
            session.commit()
        return eintrag

    def get_recent(self, limit: int = 200) -> list[FehlerLog]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(FehlerLogORM).order_by(FehlerLogORM.erstellt_am.desc()).limit(limit)
            ).all()
            return [
                FehlerLog(
                    id=r.id,
                    quelle=r.quelle,
                    meldung=r.meldung,
                    telefonnummer=r.telefonnummer,
                    erstellt_am=r.erstellt_am,
                )
                for r in rows
            ]
