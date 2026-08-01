"""SQLAlchemy-Setup fuer die Supabase-Postgres-Anbindung (Punkt 1).

DATABASE_URL kommt aus der .env (Supabase-Projekt: Settings -> Database ->
Connection string -> URI). Ist DATABASE_URL nicht gesetzt, bleibt die App auf
den In-Memory-Repositories (siehe app/bootstrap.py) - Supabase ist also rein
optional und schaltet sich automatisch zu.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _normalized_url(raw_url: str) -> str:
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw_url


class Base(DeclarativeBase):
    pass


def get_database_url() -> str | None:
    """Superuser-Verbindung: nur fuer Migrationen/Admin-Skripte/Bootstrap-
    Seeding, NICHT fuer Anfragen, die RLS respektieren sollen (Superuser
    umgeht RLS immer)."""
    raw_url = os.environ.get("DATABASE_URL")
    return _normalized_url(raw_url) if raw_url else None


def get_runtime_database_url() -> str | None:
    """Eingeschraenkte Rolle (app_runtime), respektiert RLS. Fuer alle
    mandantengebundenen Anfragen (Firmen-Login/-Dashboard, spaeter auch
    WhatsApp-Anfragen pro Firma) - siehe docs/produkt-abgleich.md."""
    raw_url = os.environ.get("DATABASE_URL_RUNTIME")
    return _normalized_url(raw_url) if raw_url else None


_engine = None
_SessionLocal = None
_runtime_engine = None
_RuntimeSessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        if not url:
            raise RuntimeError("DATABASE_URL ist nicht gesetzt.")
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_runtime_engine():
    global _runtime_engine
    if _runtime_engine is None:
        url = get_runtime_database_url()
        if not url:
            raise RuntimeError("DATABASE_URL_RUNTIME ist nicht gesetzt.")
        _runtime_engine = create_engine(url, pool_pre_ping=True)
    return _runtime_engine


def get_runtime_session_factory():
    global _RuntimeSessionLocal
    if _RuntimeSessionLocal is None:
        _RuntimeSessionLocal = sessionmaker(bind=get_runtime_engine(), expire_on_commit=False)
    return _RuntimeSessionLocal


@contextmanager
def tenant_session(*, auth_user_id: str | None = None, firma_id: str | None = None):
    """Oeffnet eine Transaktion auf der eingeschraenkten app_runtime-Rolle und
    setzt den Mandanten-Kontext per SET LOCAL (gilt nur fuer diese eine
    Transaktion - sicher auch bei Connection-Pooling, siehe RLS-Policies in
    scripts/migrate_multi_tenancy.py).

    auth_user_id wird gebraucht, bevor firma_id bekannt ist (z.B. beim ersten
    Login-Lookup); firma_id fuer alle folgenden mandantengebundenen Anfragen.
    """
    session: Session = get_runtime_session_factory()()
    try:
        with session.begin():
            if auth_user_id is not None:
                session.execute(
                    text("SET LOCAL app.current_auth_user_id = :v"), {"v": auth_user_id}
                )
            if firma_id is not None:
                session.execute(text("SET LOCAL app.current_firma_id = :v"), {"v": firma_id})
            yield session
    finally:
        session.close()
