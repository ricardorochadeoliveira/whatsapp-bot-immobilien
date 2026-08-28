"""SQLAlchemy-ORM-Tabellen, die das Datenmodell aus app/models.py 1:1 in
Supabase-Postgres abbilden - inkl. Mandantentrennung (firma_id) fuer RLS,
siehe docs/produkt-abgleich.md."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FirmaORM(Base):
    __tablename__ = "firma"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    typ: Mapped[str] = mapped_column(String(16), default="firma", nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    telefonnummer: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    auth_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    gruendungsmitglied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KundeORM(Base):
    __tablename__ = "kunde"
    __table_args__ = (UniqueConstraint("telefonnummer", "firma_id", name="uq_kunde_phone_firma"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    firma_id: Mapped[str | None] = mapped_column(ForeignKey("firma.id"), nullable=True)
    telefonnummer: Mapped[str] = mapped_column(String(32), nullable=False)
    opt_in: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SuchprofilORM(Base):
    __tablename__ = "suchprofil"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kunde_id: Mapped[str] = mapped_column(ForeignKey("kunde.id"), nullable=False)
    firma_id: Mapped[str | None] = mapped_column(ForeignKey("firma.id"), nullable=True)
    zimmer: Mapped[float | None] = mapped_column(Float, nullable=True)
    kanton: Mapped[str] = mapped_column(String(64), nullable=False)
    ort: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preis_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    objekttyp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    typ: Mapped[str | None] = mapped_column(String(16), nullable=True)
    zusatzfilter: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImmobilieORM(Base):
    __tablename__ = "immobilie"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    firma_id: Mapped[str | None] = mapped_column(ForeignKey("firma.id"), nullable=True)
    titel: Mapped[str] = mapped_column(String(255), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(String, nullable=True)
    typ: Mapped[str] = mapped_column(String(16), default="miete", nullable=False)
    zimmer: Mapped[float] = mapped_column(Float, nullable=False)
    kanton: Mapped[str] = mapped_column(String(64), nullable=False)
    ort: Mapped[str] = mapped_column(String(128), nullable=False)
    preis: Mapped[int] = mapped_column(Integer, nullable=False)
    objekttyp: Mapped[str] = mapped_column(String(64), nullable=False)
    flaeche_m2: Mapped[float] = mapped_column(Float, nullable=False)
    hat_garten: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="aktiv", nullable=False)
    bilder: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    link: Mapped[str] = mapped_column(String(512), nullable=False)
    inseriert_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadORM(Base):
    __tablename__ = "lead"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    immobilie_id: Mapped[str] = mapped_column(ForeignKey("immobilie.id"), nullable=False)
    firma_id: Mapped[str] = mapped_column(ForeignKey("firma.id"), nullable=False)
    suchprofil_id: Mapped[str | None] = mapped_column(ForeignKey("suchprofil.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="neu", nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatKontaktORM(Base):
    __tablename__ = "chat_kontakt"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    telefonnummer: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    letzte_aktivitaet_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FehlerLogORM(Base):
    __tablename__ = "fehler_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    quelle: Mapped[str] = mapped_column(String(64), nullable=False)
    meldung: Mapped[str] = mapped_column(String, nullable=False)
    telefonnummer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MatchLogORM(Base):
    __tablename__ = "match_log"
    __table_args__ = (UniqueConstraint("suchprofil_id", "immobilie_id", name="uq_match_log_pair"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    suchprofil_id: Mapped[str] = mapped_column(ForeignKey("suchprofil.id"), nullable=False)
    immobilie_id: Mapped[str] = mapped_column(ForeignKey("immobilie.id"), nullable=False)
    firma_id: Mapped[str | None] = mapped_column(ForeignKey("firma.id"), nullable=True)
    benachrichtigt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
