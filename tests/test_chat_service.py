"""Testet die Konversationslogik ohne echten Claude-API-Aufruf.

extract_intent/extract_listing werden gemockt, damit der komplette Ablauf
(Rollenwahl, Mieter-Suche, Vermieter-Inserat) deterministisch und ohne
API-Key durchgespielt werden kann.
"""
from unittest.mock import patch

from app import chat_service as chat_service_module
from app.chat_service import ChatService
from app.intent_extraction import IntentExtractionResult, ListingExtractionResult
from app.matching import MatchingEngine
from app.models import ListingSubmission, SearchCriteria
from app.notifications import NotificationDispatcher
from app.rate_limiter import RateLimiter
from app.repository import (
    InMemoryFirmaRepository,
    InMemoryImmobilienRepository,
    InMemoryKundenRepository,
    InMemoryLeadRepository,
    InMemorySuchprofilRepository,
)
from app.seed_data import build_seed_immobilien


def make_service(rate_limiter=None):
    immobilien_repo = InMemoryImmobilienRepository(seed=build_seed_immobilien())
    firma_repo = InMemoryFirmaRepository()
    kunden_repo = InMemoryKundenRepository()
    suchprofil_repo = InMemorySuchprofilRepository()
    lead_repo = InMemoryLeadRepository()
    dispatcher = NotificationDispatcher()
    service = ChatService(
        matching_engine=MatchingEngine(immobilien_repo),
        immobilien_repo=immobilien_repo,
        firma_repo=firma_repo,
        rate_limiter=rate_limiter,
        kunden_repo=kunden_repo,
        suchprofil_repo=suchprofil_repo,
        dispatcher=dispatcher,
        lead_repo=lead_repo,
    )
    return service, suchprofil_repo, immobilien_repo, firma_repo, lead_repo


def _als_mieter(service, phone):
    """Fuehrt eine neue Session durch die Rollenwahl bis zum Suchprompt."""
    erste = service.handle_message(phone, "hallo")
    assert "Vermieter" in erste[0] and "Mieter" in erste[0]
    zweite = service.handle_message(phone, "Mieter")
    assert "beschreib" in zweite[0].lower()


def _als_vermieter(service, phone, typ="firma", name="Testfirma AG"):
    service.handle_message(phone, "hallo")
    service.handle_message(phone, "Vermieter")
    service.handle_message(phone, typ)
    antwort = service.handle_message(phone, name)
    return antwort


def test_role_frage_kommt_zuerst():
    service, *_ = make_service()
    antworten = service.handle_message("+41790000099", "Ich suche eine Wohnung in Zug")
    assert antworten == [
        "Willkommen! Bist du Vermieter (Inserat aufgeben) oder Mieter "
        "(Wohnung suchen)? Antworte mit 'Vermieter' oder 'Mieter'."
    ]


def test_clarifying_question_when_intent_incomplete():
    service, *_ = make_service()
    _als_mieter(service, "+41790000000")
    with patch.object(
        chat_service_module,
        "extract_intent",
        return_value=IntentExtractionResult(clarifying_question="Welchen Kanton meinst du?"),
    ):
        antworten = service.handle_message("+41790000000", "Ich suche eine Wohnung")

    assert antworten == ["Welchen Kanton meinst du?"]


def test_full_flow_creates_suchprofil_on_yes():
    service, suchprofil_repo, *_ = make_service()
    _als_mieter(service, "+41790000001")
    criteria = SearchCriteria(rooms=2.5, canton="Zug", max_price=2200, property_type="Wohnung")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message("+41790000001", "2.5-Zimmer-Wohnung in Zug, max 2200.-")

    antworten = service.handle_message("+41790000001", "ja")

    assert any("Suchabo angelegt" in a for a in antworten)
    profile = suchprofil_repo.get_by_kunde(
        suchprofil_repo.get_all_active()[0].kunde_id
    )
    assert len(profile) == 1
    assert profile[0].kanton == "Zug"


def test_full_flow_no_suchprofil_on_no():
    service, suchprofil_repo, *_ = make_service()
    _als_mieter(service, "+41790000002")
    criteria = SearchCriteria(rooms=2.5, canton="Zug", max_price=2200, property_type="Wohnung")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message("+41790000002", "2.5-Zimmer-Wohnung in Zug, max 2200.-")

    service.handle_message("+41790000002", "nein")

    assert suchprofil_repo.get_all_active() == []


def test_vermieter_flow_creates_listing_in_pruefung():
    service, _, immobilien_repo, firma_repo, _ = make_service()
    _als_vermieter(service, "+41790000003", typ="firma", name="Testfirma AG")

    listing = ListingSubmission(
        title="Schoene 3-Zimmer-Wohnung",
        rooms=3,
        canton="Bern",
        city="Bern",
        price=1800,
        property_type="Wohnung",
        living_space_m2=75,
        listing_type="miete",
    )
    with patch.object(
        chat_service_module, "extract_listing", return_value=ListingExtractionResult(listing=listing)
    ):
        antworten = service.handle_message("+41790000003", "3-Zimmer-Wohnung in Bern fuer 1800.-")

    assert any("eingereicht" in a for a in antworten)
    neue = [i for i in immobilien_repo.get_all() if i.titel == "Schoene 3-Zimmer-Wohnung"]
    assert len(neue) == 1
    assert neue[0].status == "in_pruefung"

    firma = firma_repo.get_by_id(neue[0].firma_id)
    assert firma.name == "Testfirma AG"
    assert firma.typ == "firma"
    assert firma.telefonnummer == "+41790000003"


def test_vermieter_privatperson_flow():
    service, _, immobilien_repo, firma_repo, _ = make_service()
    _als_vermieter(service, "+41790000004", typ="Privatperson", name="Max Muster")

    listing = ListingSubmission(
        title="Gemuetliches Studio",
        rooms=1.5,
        canton="Luzern",
        city="Luzern",
        price=1200,
        property_type="Studio",
        living_space_m2=35,
        listing_type="miete",
    )
    with patch.object(
        chat_service_module, "extract_listing", return_value=ListingExtractionResult(listing=listing)
    ):
        service.handle_message("+41790000004", "Studio in Luzern fuer 1200.-")

    neue = [i for i in immobilien_repo.get_all() if i.titel == "Gemuetliches Studio"]
    firma = firma_repo.get_by_id(neue[0].firma_id)
    assert firma.typ == "privatperson"
    assert firma.name == "Max Muster"


def test_pending_in_pruefung_listing_not_matchable_until_approved():
    """Sicherstellen, dass ein frisch eingereichtes (nicht freigegebenes)
    Inserat noch nicht in der Mieter-Suche auftaucht."""
    service, _, immobilien_repo, _, _ = make_service()
    _als_vermieter(service, "+41790000005")

    listing = ListingSubmission(
        title="Verstecktes Inserat",
        rooms=2,
        canton="Zug",
        city="Zug",
        price=2000,
        property_type="Wohnung",
        living_space_m2=60,
        listing_type="miete",
    )
    with patch.object(
        chat_service_module, "extract_listing", return_value=ListingExtractionResult(listing=listing)
    ):
        service.handle_message("+41790000005", "2-Zimmer-Wohnung in Zug fuer 2000.-")

    from app.matching import matches

    neue = [i for i in immobilien_repo.get_all() if i.titel == "Verstecktes Inserat"][0]
    criteria = SearchCriteria(rooms=2, canton="Zug", max_price=2000, property_type="Wohnung")
    assert matches(neue, criteria) is False  # status == "in_pruefung"


def test_message_too_long_is_rejected_without_calling_claude():
    service, *_ = make_service()
    _als_mieter(service, "+41790000010")

    with patch.object(chat_service_module, "extract_intent") as mock_extract:
        antworten = service.handle_message("+41790000010", "x" * 2000)

    mock_extract.assert_not_called()
    assert "zu lang" in antworten[0].lower()


def test_rate_limit_blocks_further_claude_calls():
    # Beide Nachrichten muessen extract_intent erreichen (keine pending_criteria
    # dazwischen) - dafuer bleibt die Extraktion beide Male unvollstaendig.
    tight_limiter = RateLimiter(per_phone_per_minute=1, per_phone_per_day=100, global_per_minute=100)
    service, *_ = make_service(rate_limiter=tight_limiter)
    _als_mieter(service, "+41790000011")

    with patch.object(
        chat_service_module,
        "extract_intent",
        return_value=IntentExtractionResult(clarifying_question="Welchen Kanton meinst du?"),
    ) as mock_extract:
        service.handle_message("+41790000011", "Ich suche eine Wohnung")
        assert mock_extract.call_count == 1

        antworten = service.handle_message("+41790000011", "Zug")
        assert mock_extract.call_count == 1  # kein zweiter Aufruf, Limit erreicht
        assert "warte" in antworten[0].lower()
