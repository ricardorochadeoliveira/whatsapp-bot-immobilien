"""Punkt 4: Matching-Engine / Suchlogik.

Klassische strukturierte Filterlogik (kein RAG/Embeddings noetig). Arbeitet
ausschliesslich gegen ImmobilienRepository, sodass die Datenquelle spaeter
(Punkt 2) ausgetauscht werden kann, ohne diese Logik anzufassen.
"""
from __future__ import annotations

from typing import Optional

from app.models import Immobilie, SearchCriteria, Suchprofil
from app.repository import ImmobilienRepository


# Kanton/Ort werden mal mit Umlaut ("Zürich"), mal in ASCII-Transliteration
# ("Zuerich") erfasst - je nachdem, ob die Schreibweise vom Nutzer uebernommen
# oder von Claude "normalisiert" wurde (die Suchtool-Beschreibung in
# app/intent_extraction.py nennt explizit "Zuerich" als Beispiel). Ohne diese
# Umlaut-Faltung galt "Zürich" != "Zuerich" und ein real existierendes,
# exakt passendes Inserat wurde stillschweigend nicht gefunden.
_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def _normalize(value: str) -> str:
    return value.strip().translate(_UMLAUT_MAP).lower()


def matches(immobilie: Immobilie, criteria: SearchCriteria) -> bool:
    if immobilie.status != "aktiv":
        return False
    if _normalize(immobilie.kanton) != _normalize(criteria.canton):
        return False
    if criteria.city and _normalize(immobilie.ort) != _normalize(criteria.city):
        return False
    if criteria.rooms is not None and immobilie.zimmer < criteria.rooms:
        return False
    if criteria.max_price is not None and immobilie.preis > criteria.max_price:
        return False
    if criteria.property_type and _normalize(immobilie.objekttyp) != _normalize(
        criteria.property_type
    ):
        return False
    return True


def suchprofil_to_criteria(suchprofil: Suchprofil) -> SearchCriteria:
    return SearchCriteria(
        rooms=suchprofil.zimmer,
        canton=suchprofil.kanton,
        city=suchprofil.ort,
        max_price=suchprofil.preis_max,
        property_type=suchprofil.objekttyp,
    )


class MatchingEngine:
    def __init__(self, immobilien_repo: ImmobilienRepository):
        self._immobilien_repo = immobilien_repo

    def search(self, criteria: SearchCriteria, firma_id: Optional[str] = None) -> list[Immobilie]:
        pool = (
            self._immobilien_repo.get_by_firma(firma_id)
            if firma_id is not None
            else self._immobilien_repo.get_all()
        )
        return [i for i in pool if matches(i, criteria)]

    def matches_suchprofil(self, immobilie: Immobilie, suchprofil: Suchprofil) -> bool:
        return matches(immobilie, suchprofil_to_criteria(suchprofil))
