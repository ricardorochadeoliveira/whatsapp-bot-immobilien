"""Verdrahtet Repositories, Matching-Engine, Matching-Job und Chat-Service.

Ist DATABASE_URL gesetzt (Supabase-Postgres, Punkt 1), werden die
Supabase-Repositories verwendet, sonst faellt die App auf die In-Memory-
Repositories zurueck (z.B. fuer Tests oder lokales Arbeiten ohne DB).
Matching-Engine, Matching-Job und Chat-Service kennen den Unterschied nicht.
"""
from __future__ import annotations

import logging
from typing import Callable

from app.chat_service import ChatService
from app.code_assistant import cleanup_orphaned_tmpdirs
from app.db import get_database_url, get_engine, get_runtime_database_url, get_session_factory
from app.firma_service import FirmaService
from app.matching import MatchingEngine
from app.matching_job import MatchingJob
from app.meta_whatsapp import is_configured as meta_whatsapp_configured
from app.meta_whatsapp import send_image_message, send_text_message
from app.notifications import NotificationDispatcher
from app.rate_limiter import RateLimiter

logger = logging.getLogger("immo_bot.bootstrap")


def _build_outbound_sender(fehlerlog_repo) -> Callable[[str, str], None]:
    """Wrapper um send_text_message: Fehler (z.B. 24h-Fenster abgelaufen,
    Meta-API nicht erreichbar) duerfen proaktive Benachrichtigungen nicht
    zum Absturz bringen - nur loggen und weitermachen. Faengt auch im
    Fehler-Protokoll auf (siehe app/repository.py: FehlerLogRepository)."""

    def _send(to: str, text: str) -> None:
        try:
            send_text_message(to, text)
        except Exception as exc:
            logger.exception("WhatsApp-Versand an %s fehlgeschlagen", to)
            fehlerlog_repo.add("whatsapp_proactive_send", str(exc), telefonnummer=to)

    return _send


def _build_image_sender(fehlerlog_repo) -> Callable[[str, str, str | None], None]:
    """Wrapper um send_image_message - gleiches Fehlerverhalten wie
    _build_outbound_sender."""

    def _send(to: str, image_url: str, caption: str | None) -> None:
        try:
            send_image_message(to, image_url, caption)
        except Exception as exc:
            logger.exception("WhatsApp-Bildversand an %s fehlgeschlagen", to)
            fehlerlog_repo.add("whatsapp_proactive_send", str(exc), telefonnummer=to)

    return _send
from app.repository import (
    ChatKontaktRepository,
    FehlerLogRepository,
    FirmaRepository,
    ImmobilienRepository,
    InMemoryChatKontaktRepository,
    InMemoryFehlerLogRepository,
    InMemoryFirmaRepository,
    InMemoryImmobilienRepository,
    InMemoryKundenRepository,
    InMemoryLeadRepository,
    InMemoryMatchLogRepository,
    InMemorySuchprofilRepository,
    KundenRepository,
    LeadRepository,
    MatchLogRepository,
    SuchprofilRepository,
)
from app.seed_data import build_seed_immobilien


def _build_repos() -> tuple[
    ImmobilienRepository,
    KundenRepository,
    SuchprofilRepository,
    MatchLogRepository,
    LeadRepository,
    FirmaRepository,
    ChatKontaktRepository,
    FehlerLogRepository,
]:
    if not get_database_url():
        return (
            InMemoryImmobilienRepository(seed=build_seed_immobilien()),
            InMemoryKundenRepository(),
            InMemorySuchprofilRepository(),
            InMemoryMatchLogRepository(),
            InMemoryLeadRepository(),
            InMemoryFirmaRepository(),
            InMemoryChatKontaktRepository(),
            InMemoryFehlerLogRepository(),
        )

    from app.models_orm import Base
    from app.repository_supabase import (
        SupabaseChatKontaktRepository,
        SupabaseFehlerLogRepository,
        SupabaseFirmaRepository,
        SupabaseImmobilienRepository,
        SupabaseKundenRepository,
        SupabaseLeadRepository,
        SupabaseMatchLogRepository,
        SupabaseSuchprofilRepository,
    )

    Base.metadata.create_all(get_engine())
    session_factory = get_session_factory()

    immobilien_repo = SupabaseImmobilienRepository(session_factory)
    if not immobilien_repo.get_all():
        for immobilie in build_seed_immobilien():
            immobilien_repo.add(immobilie)

    # FirmaRepository braucht fuer die WhatsApp-Vermieter-Identifikation
    # (get_or_create_by_phone) nur die Superuser-Verbindung, keine RLS - im
    # Gegensatz zum Firmen-Portal-Login (FirmaService), das die
    # eingeschraenkte app_runtime-Rolle voraussetzt (siehe unten).
    firma_repo = SupabaseFirmaRepository(session_factory)
    _ensure_demo_firma(session_factory, firma_repo)

    return (
        immobilien_repo,
        SupabaseKundenRepository(session_factory),
        SupabaseSuchprofilRepository(session_factory),
        SupabaseMatchLogRepository(session_factory),
        SupabaseLeadRepository(session_factory),
        firma_repo,
        SupabaseChatKontaktRepository(session_factory),
        SupabaseFehlerLogRepository(session_factory),
    )


DEMO_FIRMA_EMAIL = "demo@meinwohntraum.local"


def _ensure_demo_firma(session_factory, firma_repo) -> None:
    """Sorgt dafuer, dass es eine 'Demo GmbH' gibt und die Seed-Inserate ihr
    gehoeren - sonst waeren sie unter dem neuen Mandantenmodell fuer niemanden
    ueber die Firma-gescopte Suche auffindbar. Nur fuer Demo-/Testzwecke, kein
    echter Firmen-Login (auth_user_id bleibt None), daher Direktzugriff ueber
    die Superuser-Session statt ueber FirmaRepository.add() (das RLS via
    auth_user_id voraussetzt)."""
    from app.models import Firma
    from app.models_orm import FirmaORM, ImmobilieORM

    demo = firma_repo.get_by_email(DEMO_FIRMA_EMAIL)
    if demo is None:
        demo_model = Firma(name="Demo GmbH", email=DEMO_FIRMA_EMAIL)
        with session_factory() as session:
            session.add(FirmaORM(**demo_model.model_dump()))
            session.commit()
        demo = demo_model

    with session_factory() as session:
        rows = session.query(ImmobilieORM).filter(ImmobilieORM.firma_id.is_(None)).all()
        for row in rows:
            row.firma_id = demo.id
        if rows:
            session.commit()


class AppContext:
    def __init__(self):
        # Verwaiste Tempordner eines fruehen Absturzes waehrend eines KI-
        # Assistenten-Laufs aufraeumen (siehe app/code_assistant.py) - vor
        # allem anderen, damit ein hartnaeckiger Ordner nie liegen bleibt.
        cleanup_orphaned_tmpdirs()
        (
            self.immobilien_repo,
            self.kunden_repo,
            self.suchprofil_repo,
            self.matchlog_repo,
            self.lead_repo,
            self.firma_repo,
            self.chatkontakt_repo,
            self.fehlerlog_repo,
        ) = _build_repos()

        # FirmaService (Login/Signup/Firmen-Portal) braucht die eingeschraenkte
        # app_runtime-Rolle fuer RLS - ohne DATABASE_URL_RUNTIME bleibt nur
        # dieses Feature deaktiviert, der WhatsApp-Vermieter-Pfad (firma_repo)
        # funktioniert unabhaengig davon.
        self.firma_service = FirmaService(self.firma_repo) if get_runtime_database_url() else None

        # Schutz gegen Brute-Force auf Login/Signup - eigene, strengere
        # Instanz als der Chat-Rate-Limiter (Key hier ist "email|ip" statt
        # Telefonnummer, die Klasse ist generisch genug dafuer).
        self.auth_rate_limiter = RateLimiter(
            per_phone_per_minute=5, per_phone_per_day=50, global_per_minute=30
        )
        # Eigene, ebenso strenge Instanz fuer den Superadmin-Login - getrennt
        # vom Firmenportal-Login, damit ein Brute-Force-Versuch auf das eine
        # nicht das Limit des anderen mitverbraucht.
        self.superadmin_rate_limiter = RateLimiter(
            per_phone_per_minute=5, per_phone_per_day=50, global_per_minute=30
        )

        self.matching_engine = MatchingEngine(self.immobilien_repo)
        self.dispatcher = NotificationDispatcher()
        self.matching_job = MatchingJob(
            matching_engine=self.matching_engine,
            suchprofil_repo=self.suchprofil_repo,
            kunden_repo=self.kunden_repo,
            matchlog_repo=self.matchlog_repo,
            dispatcher=self.dispatcher,
        )
        self.chat_service = ChatService(
            matching_engine=self.matching_engine,
            immobilien_repo=self.immobilien_repo,
            firma_repo=self.firma_repo,
            kunden_repo=self.kunden_repo,
            suchprofil_repo=self.suchprofil_repo,
            dispatcher=self.dispatcher,
            lead_repo=self.lead_repo,
            outbound_sender=_build_outbound_sender(self.fehlerlog_repo) if meta_whatsapp_configured() else None,
            image_sender=_build_image_sender(self.fehlerlog_repo) if meta_whatsapp_configured() else None,
            firma_service=self.firma_service,
            chatkontakt_repo=self.chatkontakt_repo,
            fehlerlog_repo=self.fehlerlog_repo,
        )


context = AppContext()
