"""Datenmodell, abgeglichen mit der Chef-Spezifikation "meinwohntraum.ai"
(siehe docs/produkt-abgleich.md).

Firma und Lead sind neu gegenueber dem urspruenglichen Platzhalter-Modell.
Immobilie und Suchprofil sind um Felder aus der Spezifikation erweitert
(firma_id, typ, hat_garten, status, zusatzfilter) - mit Defaults, damit
bestehender Code (Repository, Matching, Chat-Service) unveraendert weiter
funktioniert, bis diese Felder tatsaechlich verdrahtet werden.

Mandantentrennung (Multi-Tenancy): der Bot soll NICHT firmenuebergreifend
arbeiten - jede Firma sieht nur ihre eigenen Inserate und nur die Kunden,
die sich bei ihr gemeldet haben. Deshalb tragen Kunde, Suchprofil und
MatchLog zusaetzlich zu Immobilie/Lead ein (vorerst optionales) firma_id-
Feld. Das wird verpflichtend, sobald Firmen-Login existiert (siehe
docs/produkt-abgleich.md, Abschnitt RLS/Mandantentrennung).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Kunde(BaseModel):
    """WhatsApp-Endnutzer, mandantengetrennt pro Firma: dieselbe Telefonnummer
    kann pro Firma einen eigenen Kunde-Datensatz haben, damit eine Firma nur
    "ihre" Kunden sieht. firma_id ist vorerst optional (kein Firmen-Login),
    wird aber verpflichtend, sobald Firmen-Login/RLS aktiv ist."""

    id: str = Field(default_factory=_new_id)
    firma_id: Optional[str] = None
    telefonnummer: str
    opt_in: bool = True
    erstellt_am: datetime = Field(default_factory=_now)


class Firma(BaseModel):
    """Anbieter eines Inserats - Firma ODER Privatperson (siehe
    docs/produkt-abgleich.md, Marktplatz-Pivot). Zwei Wege, wie ein Datensatz
    entsteht:
    - Firmen-Portal-Signup (Supabase Auth): email + auth_user_id gesetzt,
      telefonnummer bleibt leer.
    - WhatsApp-Vermieter-Flow (kein Login): telefonnummer gesetzt, email/
      auth_user_id bleiben leer. Identifikation rein ueber die Telefonnummer,
      die WhatsApp bereits verifiziert."""

    id: str = Field(default_factory=_new_id)
    name: str
    typ: str = "firma"  # "firma" | "privatperson"
    email: Optional[str] = None
    telefonnummer: Optional[str] = None
    auth_user_id: Optional[str] = None
    gruendungsmitglied: bool = False
    erstellt_am: datetime = Field(default_factory=_now)


class Suchprofil(BaseModel):
    id: str = Field(default_factory=_new_id)
    kunde_id: str
    firma_id: Optional[str] = None  # denormalisiert, fuer RLS (siehe Kunde)
    zimmer: Optional[float] = None
    kanton: str
    ort: Optional[str] = None
    preis_max: Optional[int] = None
    objekttyp: Optional[str] = None
    typ: Optional[str] = None  # "miete" | "kauf"
    zusatzfilter: Optional[dict] = None
    aktiv: bool = True
    erstellt_am: datetime = Field(default_factory=_now)


class Immobilie(BaseModel):
    id: str = Field(default_factory=_new_id)
    firma_id: Optional[str] = None
    titel: str
    beschreibung: Optional[str] = None
    typ: str = "miete"  # "miete" | "kauf"
    zimmer: float
    kanton: str
    ort: str
    preis: int
    objekttyp: str
    flaeche_m2: float
    hat_garten: bool = False
    status: str = "aktiv"  # "aktiv" | "in_pruefung" | "deaktiviert"
    bilder: list[str] = Field(default_factory=list)
    link: str
    inseriert_am: datetime = Field(default_factory=_now)


class Lead(BaseModel):
    """Ausgeloest, wenn ein Kunde im WhatsApp-Chat auf ein Match positiv
    antwortet (Spezifikation 4.7, Schritt 5)."""

    id: str = Field(default_factory=_new_id)
    immobilie_id: str
    firma_id: str
    suchprofil_id: Optional[str] = None
    status: str = "neu"  # "neu" | "kontaktiert" | "abgeschlossen"
    erstellt_am: datetime = Field(default_factory=_now)


class MatchLog(BaseModel):
    id: str = Field(default_factory=_new_id)
    suchprofil_id: str
    immobilie_id: str
    firma_id: Optional[str] = None  # denormalisiert, fuer RLS (siehe Kunde)
    benachrichtigt_am: datetime = Field(default_factory=_now)


class SearchCriteria(BaseModel):
    """Ergebnis der Intent-Extraktion (Punkt 3) - Eingabe fuer die Matching-Engine (Punkt 4)."""

    rooms: Optional[float] = None
    canton: str
    city: Optional[str] = None
    max_price: Optional[int] = None
    property_type: Optional[str] = None


class ListingSubmission(BaseModel):
    """Ergebnis der Freitext-Extraktion, wenn ein Vermieter im WhatsApp-Chat
    ein Inserat beschreibt (Pendant zu SearchCriteria auf der Mieter-Seite)."""

    title: str
    rooms: float
    canton: str
    city: str
    price: int
    property_type: str
    living_space_m2: float
    listing_type: str  # "miete" | "kauf"
    has_garden: bool = False
    description: Optional[str] = None
