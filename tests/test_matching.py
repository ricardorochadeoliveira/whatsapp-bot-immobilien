"""Regressionstest: Kanton/Ort werden je nach Quelle mal mit Umlaut
("Zürich"), mal in ASCII-Transliteration ("Zuerich") erfasst - ohne
Umlaut-Faltung in matching._normalize() galt das faelschlich als
unterschiedlicher Kanton, und ein real existierendes, exakt passendes
Inserat wurde dem Kunden nicht angezeigt (siehe app/matching.py)."""
from app.matching import matches
from app.models import Immobilie, SearchCriteria


def _immobilie(**overrides):
    defaults = dict(
        titel="Testwohnung",
        zimmer=2.5,
        kanton="Zürich",
        ort="Kloten",
        preis=2090,
        objekttyp="Wohnung",
        flaeche_m2=60,
        link="https://example.com/x",
    )
    defaults.update(overrides)
    return Immobilie(**defaults)


def test_umlaut_kanton_matcht_ascii_transliterierten_suchkanton():
    immobilie = _immobilie(kanton="Zürich")
    criteria = SearchCriteria(canton="Zuerich", rooms=2.5, max_price=3000, property_type="Wohnung")

    assert matches(immobilie, criteria) is True


def test_ascii_kanton_matcht_umlaut_suchkanton():
    immobilie = _immobilie(kanton="Zuerich")
    criteria = SearchCriteria(canton="Zürich", rooms=2.5, max_price=3000, property_type="Wohnung")

    assert matches(immobilie, criteria) is True


def test_umlaut_ort_matcht_ascii_transliterierten_suchort():
    immobilie = _immobilie(kanton="Zürich", ort="Zürich")
    criteria = SearchCriteria(canton="Zuerich", city="Zuerich")

    assert matches(immobilie, criteria) is True


def test_unterschiedlicher_kanton_matcht_weiterhin_nicht():
    immobilie = _immobilie(kanton="Zürich")
    criteria = SearchCriteria(canton="Bern")

    assert matches(immobilie, criteria) is False
