"""Testet die Konversationslogik ohne echten Claude-API-Aufruf.

extract_intent/extract_listing werden gemockt, damit der komplette Ablauf
(Rollenwahl, Mieter-Suche, Vermieter-Inserat) deterministisch und ohne
API-Key durchgespielt werden kann.
"""
from unittest.mock import MagicMock, patch

from app import chat_service as chat_service_module
from app import firma_service as firma_service_module
from app.chat_service import ChatService
from app.firma_service import FirmaService
from app.intent_extraction import IntentExtractionResult, ListingExtractionResult
from app.matching import MatchingEngine
from app.models import ListingSubmission, SearchCriteria
from app.notifications import NotificationDispatcher
from app.rate_limiter import RateLimiter
from app.repository import (
    InMemoryChatKontaktRepository,
    InMemoryFehlerLogRepository,
    InMemoryFirmaRepository,
    InMemoryImmobilienRepository,
    InMemoryKundenRepository,
    InMemoryLeadRepository,
    InMemorySuchprofilRepository,
)
from app.seed_data import build_seed_immobilien


def make_service(
    rate_limiter=None,
    image_sender=None,
    firma_service=None,
    firma_repo=None,
    chatkontakt_repo=None,
    fehlerlog_repo=None,
):
    immobilien_repo = InMemoryImmobilienRepository(seed=build_seed_immobilien())
    firma_repo = firma_repo if firma_repo is not None else InMemoryFirmaRepository()
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
        image_sender=image_sender,
        firma_service=firma_service,
        chatkontakt_repo=chatkontakt_repo,
        fehlerlog_repo=fehlerlog_repo,
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
    service.handle_message(phone, "chat")  # Webseite-oder-Chat-Frage: im Chat bleiben
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


def test_rollenwechsel_mitten_im_gespraech():
    service, *_ = make_service()
    phone = "+41790000098"
    _als_mieter(service, phone)

    antwort = service.handle_message(phone, "Vermieter")
    assert "Webseite" in antwort[0]

    session = service.get_session(phone)
    assert session.role == "vermieter"

    antwort = service.handle_message(phone, "chat")
    assert "Firma" in antwort[0] and "Privatperson" in antwort[0]

    zurueck = service.handle_message(phone, "Mieter")
    assert "beschreib" in zurueck[0].lower()
    assert service.get_session(phone).role == "mieter"


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


def test_suchtreffer_mit_bild_wird_als_foto_versendet():
    image_sender = MagicMock()
    service, *_ = make_service(image_sender=image_sender)
    _als_mieter(service, "+41790000020")
    criteria = SearchCriteria(rooms=2.5, canton="Zug", max_price=2200, property_type="Wohnung")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message("+41790000020", "2.5-Zimmer-Wohnung in Zug, max 2200.-")

    image_sender.assert_called_once_with(
        "+41790000020",
        "https://picsum.photos/seed/1/800/600",
        "Helle 2.5-Zimmer-Wohnung mit Balkon",
    )


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


def _bis_email_frage(service, phone, typ="firma", name="Testfirma AG"):
    service.handle_message(phone, "hallo")
    service.handle_message(phone, "Vermieter")
    service.handle_message(phone, "chat")
    service.handle_message(phone, typ)
    return service.handle_message(phone, name)


def test_vermieter_mit_konfiguriertem_firma_service_wird_nach_email_gefragt():
    firma_service = FirmaService(InMemoryFirmaRepository())
    service, *_ = make_service(firma_service=firma_service)
    antwort = _bis_email_frage(service, "+41790000040")
    assert "E-Mail" in antwort[0]
    assert service.get_session("+41790000040").vermieter_step == "email"


def test_vermieter_email_wird_validiert():
    firma_service = FirmaService(InMemoryFirmaRepository())
    service, *_ = make_service(firma_service=firma_service)
    phone = "+41790000041"
    _bis_email_frage(service, phone)

    antwort = service.handle_message(phone, "keine-email")
    assert "gueltige" in antwort[0].lower()
    assert service.get_session(phone).vermieter_step == "email"

    antwort = service.handle_message(phone, "vermieter@example.com")
    assert "Passwort" in antwort[0]
    assert service.get_session(phone).vermieter_step == "password"


def test_vermieter_konto_wird_erstellt_und_telefonnummer_verknuepft():
    firma_repo = InMemoryFirmaRepository()
    firma_service = FirmaService(firma_repo)
    service, *_ = make_service(firma_service=firma_service, firma_repo=firma_repo)
    phone = "+41790000042"
    _bis_email_frage(service, phone)
    service.handle_message(phone, "vermieter@example.com")

    with patch.object(
        firma_service_module, "sign_up", return_value={"id": "auth-user-123"}
    ):
        antworten = service.handle_message(phone, "SicheresPasswort1")

    assert any("Konto erstellt" in a for a in antworten)
    firma = firma_repo.get_by_phone(phone)
    assert firma is not None
    assert firma.auth_user_id == "auth-user-123"
    assert firma.email == "vermieter@example.com"
    assert firma.telefonnummer == phone
    assert service.get_session(phone).vermieter_step == "done"


def test_passwort_landet_nie_im_klartext_im_chatverlauf():
    firma_service = FirmaService(InMemoryFirmaRepository())
    service, *_ = make_service(firma_service=firma_service)
    phone = "+41790000043"
    _bis_email_frage(service, phone)
    service.handle_message(phone, "vermieter@example.com")

    with patch.object(firma_service_module, "sign_up", return_value={"id": "auth-user-456"}):
        service.handle_message(phone, "GeheimesPasswort1")

    verlauf = [m["text"] for m in service.get_session(phone).display_messages]
    assert "GeheimesPasswort1" not in verlauf
    assert "••••••••" in verlauf


def test_firma_auth_error_fuehrt_zurueck_zur_email_frage():
    firma_service = FirmaService(InMemoryFirmaRepository())
    service, *_ = make_service(firma_service=firma_service)
    phone = "+41790000044"
    _bis_email_frage(service, phone)
    service.handle_message(phone, "vermieter@example.com")

    from app.supabase_auth import SupabaseAuthError

    with patch.object(firma_service_module, "sign_up", side_effect=SupabaseAuthError("E-Mail bereits vergeben")):
        antworten = service.handle_message(phone, "SicheresPasswort1")

    assert any("bereits vergeben" in a for a in antworten)
    assert service.get_session(phone).vermieter_step == "email"


def test_wiederkehrender_vermieter_mit_konto_wird_direkt_erkannt():
    firma_repo = InMemoryFirmaRepository()
    firma_service = FirmaService(firma_repo)
    service, *_ = make_service(firma_service=firma_service, firma_repo=firma_repo)
    phone = "+41790000045"
    _bis_email_frage(service, phone)
    service.handle_message(phone, "vermieter@example.com")
    with patch.object(firma_service_module, "sign_up", return_value={"id": "auth-user-789"}):
        service.handle_message(phone, "SicheresPasswort1")

    # Neue Session simulieren (z.B. Server-Neustart oder neuer Chat-Kontext).
    service.get_session(phone).reset()

    antwort = service.handle_message(phone, "hallo")
    antwort = service.handle_message(phone, "Vermieter")
    assert "Willkommen zurueck" in antwort[0]
    assert "Testfirma AG" in antwort[0]
    assert service.get_session(phone).vermieter_step == "done"


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


def test_role_frage_bietet_vermieter_mieter_buttons():
    service, *_ = make_service()
    service.handle_message("+41790000030", "hallo")

    prompt = service.get_session("+41790000030").pending_interactive
    assert prompt.kind == "button"
    assert prompt.options == [("vermieter", "Vermieter"), ("mieter", "Mieter")]


def test_vermieter_wird_zuerst_zur_webseite_gefragt():
    service, *_ = make_service()
    phone = "+41790000036"
    service.handle_message(phone, "hallo")
    antwort = service.handle_message(phone, "Vermieter")

    assert "webseite.ch" not in antwort[0]  # kein Tippfehler im Linktext
    assert "wohnchat.ch/firma.html" in antwort[0]
    prompt = service.get_session(phone).pending_interactive
    assert prompt.kind == "button"
    assert prompt.options == [("webseite", "Webseite"), ("chat", "Hier im Chat")]


def test_vermieter_webseite_hinweis_blockiert_chat_nicht():
    service, *_ = make_service()
    phone = "+41790000037"
    service.handle_message(phone, "hallo")
    service.handle_message(phone, "Vermieter")

    antworten = service.handle_message(phone, "lieber die Webseite")
    assert any("wohnchat.ch/firma.html" in a for a in antworten)
    assert any("Firma" in a and "Privatperson" in a for a in antworten)
    assert service.get_session(phone).vermieter_step == "typ"


def test_vermieter_typ_frage_bietet_firma_privatperson_buttons():
    service, *_ = make_service()
    phone = "+41790000031"
    service.handle_message(phone, "hallo")
    service.handle_message(phone, "Vermieter")
    service.handle_message(phone, "chat")

    prompt = service.get_session(phone).pending_interactive
    assert prompt.kind == "button"
    assert prompt.options == [("firma", "Firma"), ("privatperson", "Privatperson")]


def test_suchabo_bestaetigung_bietet_ja_nein_buttons():
    service, *_ = make_service()
    phone = "+41790000032"
    _als_mieter(service, phone)
    criteria = SearchCriteria(rooms=2.5, canton="Zug", max_price=2200, property_type="Wohnung")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message(phone, "2.5-Zimmer-Wohnung in Zug, max 2200.-")

    prompt = service.get_session(phone).pending_interactive
    assert prompt.kind == "button"
    assert prompt.options == [("ja", "Ja"), ("nein", "Nein")]


def test_gefuehrte_suche_fragt_zimmer_dann_objekttyp_dann_preis():
    service, *_ = make_service()
    phone = "+41790000033"
    _als_mieter(service, phone)
    # Nur Kanton bekannt - Zimmer/Objekttyp/Preis fehlen noch.
    criteria = SearchCriteria(canton="Zug")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        antworten = service.handle_message(phone, "Ich suche etwas in Zug")

    assert "Zimmer" in antworten[0]
    prompt = service.get_session(phone).pending_interactive
    assert prompt.kind == "list"
    assert ("egal", "Egal") in prompt.options
    assert service.get_session(phone).pending_search_slot == "rooms"

    antworten = service.handle_message(phone, "3")
    assert "Objekt" in antworten[0]
    assert service.get_session(phone).pending_search_slot == "property_type"
    assert service.get_session(phone).partial_criteria.rooms == 3.0

    antworten = service.handle_message(phone, "egal")
    assert "Preis" in antworten[0]
    assert service.get_session(phone).pending_search_slot == "max_price"
    assert service.get_session(phone).partial_criteria.property_type is None

    antworten = service.handle_message(phone, "2500")
    assert any("Suchabo anlegen" in a for a in antworten)
    assert service.get_session(phone).pending_search_slot is None
    assert service.get_session(phone).pending_criteria.max_price == 2500


def test_gefuehrte_suche_erkennt_getippte_option_wie_bei_button_tap():
    """Ein Tap auf eine Listen-Zeile liefert denselben Text wie freies
    Tippen (siehe app/meta_whatsapp.py: parse_incoming_messages gibt den
    title zurueck) - dieser Test simuliert genau das."""
    service, *_ = make_service()
    phone = "+41790000034"
    _als_mieter(service, phone)
    criteria = SearchCriteria(canton="Bern")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message(phone, "Ich suche etwas in Bern")

    service.handle_message(phone, "5+")  # simuliert Tap auf die "5+"-Zeile
    assert service.get_session(phone).partial_criteria.rooms == 5.0

    service.handle_message(phone, "Wohnung")  # simuliert Tap auf "Wohnung"
    assert service.get_session(phone).partial_criteria.property_type == "Wohnung"


def test_gefuehrte_suche_unbekannte_antwort_fragt_erneut():
    service, *_ = make_service()
    phone = "+41790000035"
    _als_mieter(service, phone)
    criteria = SearchCriteria(canton="Zug")

    with patch.object(
        chat_service_module, "extract_intent", return_value=IntentExtractionResult(criteria=criteria)
    ):
        service.handle_message(phone, "Ich suche etwas in Zug")

    antworten = service.handle_message(phone, "blablabla nichts spezielles")
    assert "nicht verstanden" in antworten[0].lower()
    assert service.get_session(phone).pending_search_slot == "rooms"


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


# -- Superadmin-Hooks: ChatKontaktRepository/FehlerLogRepository -----------


def test_echte_nachricht_wird_als_chatkontakt_aktivitaet_erfasst():
    kontakt_repo = InMemoryChatKontaktRepository()
    service, *_ = make_service(chatkontakt_repo=kontakt_repo)
    phone = "+41790000050"

    service.handle_message(phone, "hallo")
    assert kontakt_repo.count_all() == 1

    service.handle_message(phone, "Mieter")
    assert kontakt_repo.count_all() == 1  # dieselbe Nummer, kein neuer Kontakt


def test_claude_api_fehler_landet_im_fehlerprotokoll():
    from app.intent_extraction import IntentExtractionConfigError

    fehlerlog_repo = InMemoryFehlerLogRepository()
    service, *_ = make_service(fehlerlog_repo=fehlerlog_repo)
    phone = "+41790000051"
    _als_mieter(service, phone)

    with patch.object(
        chat_service_module, "extract_intent", side_effect=IntentExtractionConfigError("API nicht erreichbar")
    ):
        service.handle_message(phone, "Ich suche etwas in Zug")

    eintraege = fehlerlog_repo.get_recent()
    assert len(eintraege) == 1
    assert eintraege[0].quelle == "claude_api"
    assert eintraege[0].telefonnummer == phone


def test_vermieter_signup_fehler_landet_im_fehlerprotokoll():
    from app.supabase_auth import SupabaseAuthError

    fehlerlog_repo = InMemoryFehlerLogRepository()
    firma_service = FirmaService(InMemoryFirmaRepository())
    service, *_ = make_service(firma_service=firma_service, fehlerlog_repo=fehlerlog_repo)
    phone = "+41790000052"
    _bis_email_frage(service, phone)
    service.handle_message(phone, "vermieter@example.com")

    with patch.object(firma_service_module, "sign_up", side_effect=SupabaseAuthError("E-Mail bereits vergeben")):
        service.handle_message(phone, "SicheresPasswort1")

    eintraege = fehlerlog_repo.get_recent()
    assert len(eintraege) == 1
    assert eintraege[0].quelle == "vermieter_signup"
