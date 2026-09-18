"""Konversationslogik fuer den einen gemeinsamen WhatsApp-Bot (Marktplatz-
Pivot, siehe docs/produkt-abgleich.md). Kein Login, keine Firmen-Auswahl -
jede Konversation beginnt mit der Rollenwahl Vermieter/Mieter.

Mieter-Ablauf:
1. Freitext -> Intent-Extraktion. Bei fehlenden Angaben: Rueckfrage.
2. Vollstaendige Kriterien -> Matching-Engine (firmenuebergreifend) -> Treffer.
3. Optional: Suchabo anlegen -> automatische Benachrichtigung bei neuen
   passenden Inseraten (Matching-Job), egal von welchem Anbieter.
4. Bei Interesse an einem Match: Lead an den Anbieter weiterleiten.

Vermieter-Ablauf (Firma ODER Privatperson, kein Login):
1. Firma oder Privatperson? -> Name/Firmenname -> per Telefonnummer als
   Anbieter (wieder-)erkannt (app.repository.FirmaRepository).
2. Inserat per Freitext beschreiben -> Extraktion (Listing-Tool). Bei
   fehlenden Angaben: Rueckfrage.
3. Inserat wird mit Status "in_pruefung" angelegt - taucht erst nach
   Freigabe im Admin-Panel in der Mieter-Suche auf (Schutz gegen
   Fake-Inserate, da es keinen Login/Verifizierungsschritt gibt).
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.firma_service import FirmaAuthError, FirmaService
from app.intent_extraction import (
    IntentExtractionConfigError,
    extract_intent,
    extract_listing,
)
from app.matching import MatchingEngine, suchprofil_to_criteria
from app.models import Firma, Immobilie, Kunde, Lead, SearchCriteria, Suchprofil
from app.notifications import NotificationDispatcher
from app.rate_limiter import RateLimiter, build_default_rate_limiter
from app.repository import (
    ChatKontaktRepository,
    ChatverlaufRepository,
    FehlerLogRepository,
    FirmaRepository,
    ImmobilienRepository,
    KundenRepository,
    LeadRepository,
    SuchprofilRepository,
)

logger = logging.getLogger("immo_bot.chat_service")

JA_WOERTER = {"ja", "j", "yes", "y", "klar", "gerne", "genau"}
NEIN_WOERTER = {"nein", "n", "no", "nope"}
VERMIETER_WOERTER = {"vermieter", "vermieterin", "anbieter", "vermieten", "inserieren"}
MIETER_WOERTER = {"mieter", "mieterin", "suche", "suchend", "wohnungssuche"}
FIRMA_WOERTER = {"firma", "unternehmen", "company", "geschaeft"}
PRIVAT_WOERTER = {"privat", "privatperson", "person", "einzelperson"}
RESET_WOERTER = {"reset", "neustart", "neu anfangen", "von vorne"}

# Grosszuegiger als die exakten Keyword-Sets oben (Teilstring- statt
# Exakt-Match): hier sind Falsch-Positive praktisch ausgeschlossen (eine
# Zimmerzahl/ein Preis enthaelt nie zufaellig "egal"), und genau das
# Nicht-Erkennen von "Egal" war der gemeldete Bug.
EGAL_PHRASES = {
    "egal", "keine praeferenz", "keine ahnung", "weiss nicht",
    "spielt keine rolle", "keine vorgabe", "unwichtig",
}

# Feste Auswahl fuer die gefuehrte Zimmer-/Objekttyp-Rueckfrage (siehe
# _ask_search_slot) - Titel sind absichtlich das, was auch als Freitext
# eingegeben werden koennte, da ein Tap auf eine Option denselben Text wie
# eine getippte Antwort erzeugt (app/meta_whatsapp.py: parse_incoming_messages).
ROOMS_OPTIONS = [
    ("1", "1"), ("1.5", "1.5"), ("2", "2"), ("2.5", "2.5"), ("3", "3"),
    ("3.5", "3.5"), ("4", "4"), ("4.5", "4.5"), ("5+", "5+"), ("egal", "Egal"),
]
PROPERTY_TYPE_OPTIONS = [
    ("wohnung", "Wohnung"), ("haus", "Haus"), ("loft", "Loft"),
    ("studio", "Studio"), ("egal", "Egal"),
]
PROPERTY_TYPES = {"wohnung", "haus", "loft", "studio"}
ROOMS_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)")

# Buttons fuer die bestehenden Ja/Nein- bzw. Zwei-Wege-Entscheidungen (ids
# entsprechen bewusst den bereits vorhandenen Keyword-Sets oben - Tap und
# Freitext landen dadurch in derselben, unveraenderten Matching-Logik).
ROLE_OPTIONS = [("vermieter", "Vermieter"), ("mieter", "Mieter")]
VERMIETER_TYP_OPTIONS = [("firma", "Firma"), ("privatperson", "Privatperson")]
JA_NEIN_OPTIONS = [("ja", "Ja"), ("nein", "Nein")]
WEBSITE_OR_CHAT_OPTIONS = [("webseite", "Webseite"), ("chat", "Hier im Chat")]

# Teilstring-Abgleich wie bei EGAL_PHRASES - erkennt auch Freitext wie
# "lieber auf der webseite", nicht nur den exakten Button-Titel.
WEBSITE_WOERTER = {"webseite", "website", "homepage", "seite"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_egal(text: str) -> bool:
    normalized = text.strip().lower()
    return any(phrase in normalized for phrase in EGAL_PHRASES)


def _parse_rooms_answer(text: str) -> tuple[bool, Optional[float]]:
    """(True, wert) bei erkannter Antwort (wert=None fuer "egal"), sonst
    (False, None) - der Aufrufer fragt in dem Fall erneut nach."""
    if _is_egal(text):
        return True, None
    match = ROOMS_PATTERN.search(text.replace(",", "."))
    if match:
        return True, float(match.group(1))
    return False, None


def _parse_property_type_answer(text: str) -> tuple[bool, Optional[str]]:
    if _is_egal(text):
        return True, None
    normalized = text.strip().lower()
    for option in PROPERTY_TYPES:
        if option in normalized:
            return True, option.capitalize()
    return False, None


def _parse_price_answer(text: str) -> tuple[bool, Optional[int]]:
    if _is_egal(text):
        return True, None
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        return True, int(digits)
    return False, None

# Kostenschutz fuer die Claude-API (siehe app/rate_limiter.py und
# docs/launch-checkliste.md): Nachrichten werden nicht unbegrenzt lang oder
# unbegrenzt oft an Claude weitergereicht.
MAX_MESSAGE_LENGTH = 800
MAX_CONVERSATION_MESSAGES = 20
RATE_LIMIT_MESSAGE = (
    "⏳ Du hast gerade viele Nachrichten geschickt. Bitte warte kurz, bevor "
    "du weiterschreibst."
)

# Wird bei jedem neu erfassten Inserat als Default gesetzt, solange keine
# echte Detailseite existiert (siehe _handle_listing_extraction/web/main.py) -
# beim Anzeigen an Mieter bewusst ausgeblendet (siehe _format_treffer), statt
# ihnen einen kaputten Platzhalter-Link zu zeigen.
PLACEHOLDER_LINK = "https://example.com/inserate/neu"

# Verzoegerung fuer die Suchabo-Rueckfrage nach einem Treffer: die
# Kontaktdaten/das Bild werden ueber einen separaten proaktiven WhatsApp-Call
# verschickt, dessen Zustellreihenfolge sich nicht garantieren laesst - ohne
# Verzoegerung kam die Rueckfrage teils VOR dem Bild an, was verwirrend wirkt.
SUCHABO_FRAGE_DELAY_SECONDS = 6

# Wie viele Bilder eines Inserats bei einem Treffer maximal verschickt
# werden - mehr als eins, damit sich der Kunde ein besseres Bild machen
# kann, aber begrenzt, um den Chat nicht mit Bildern zu ueberladen.
MAX_BILDER_PRO_TREFFER = 3


@dataclass
class PendingLead:
    immobilie_id: str
    firma_id: str
    suchprofil_id: str


@dataclass
class InteractivePrompt:
    """Zeigt an, dass die zuletzt zurueckgegebene Bot-Antwort als WhatsApp
    Button-/List-Message statt als reiner Text verschickt werden soll (siehe
    web/main.py: _process_message). kind: "button" | "list"."""

    kind: str
    options: list[tuple[str, str]]
    list_button_label: Optional[str] = None


@dataclass
class Session:
    telefonnummer: str
    role: Optional[str] = None  # "vermieter" | "mieter"
    role_frage_gestellt: bool = False
    vermieter_typ: Optional[str] = None  # "firma" | "privatperson"
    vermieter_firma_id: Optional[str] = None
    # Schrittkette fuer den Vermieter-Flow (siehe _handle_vermieter):
    # "website_or_chat" | "typ" | "name" | "email" | "password" | "done".
    # None nur ganz am Anfang, bevor _start_vermieter_flow ueberhaupt lief.
    vermieter_step: Optional[str] = None
    vermieter_name: Optional[str] = None
    vermieter_email: Optional[str] = None
    claude_messages: list[dict] = field(default_factory=list)  # Mieter-Suche
    listing_messages: list[dict] = field(default_factory=list)  # Vermieter-Inserat
    display_messages: list[dict] = field(default_factory=list)
    pending_criteria: Optional[SearchCriteria] = None
    pending_lead: Optional[PendingLead] = None
    pending_interactive: Optional[InteractivePrompt] = None
    pending_search_slot: Optional[str] = None  # "rooms" | "property_type" | "max_price"
    partial_criteria: Optional[SearchCriteria] = None
    # Wartet auf eine Nummer-Auswahl unter mehreren Treffern (siehe
    # _finish_search/_handle_listing_choice) - Liste der Immobilie-IDs in
    # der Reihenfolge, wie sie dem Kunden angezeigt wurden.
    pending_listing_choice: Optional[list[str]] = None
    pending_listing_criteria: Optional[SearchCriteria] = None
    # Felder, die bereits gefragt UND beantwortet wurden - auch wenn die
    # Antwort "Egal" war (dann bleibt das Feld auf SearchCriteria weiterhin
    # None). Ohne diese Liste liesse sich "noch nicht gefragt" nicht von
    # "gefragt, Antwort war Egal" unterscheiden und die Frage wuerde sich
    # wiederholen (siehe _next_missing_search_slot).
    resolved_search_slots: set = field(default_factory=set)
    # Optionaler Persistenz-Hook, von ChatService.get_session() gesetzt
    # (siehe app/repository.py: ChatverlaufRepository) - kein dataclass-
    # Vergleich/-Repr, da es eine gebundene Funktion ist. add_display() ist
    # der EINE Ort, an dem wirklich jede Nachricht (Kunde/Bot, synchron UND
    # proaktiv/verzoegert) durchlaeuft - genau deshalb wird hier persistiert
    # statt an den vielen einzelnen Aufrufstellen.
    on_message: Optional[Callable[[str, str], None]] = field(default=None, repr=False, compare=False)

    def add_display(self, role: str, text: str) -> None:
        self.display_messages.append({"role": role, "text": text})
        if self.on_message is not None:
            self.on_message(role, text)

    def reset(self) -> None:
        telefonnummer = self.telefonnummer
        display_messages = self.display_messages
        on_message = self.on_message
        self.__init__(telefonnummer=telefonnummer)  # type: ignore[misc]
        self.display_messages = display_messages
        self.on_message = on_message


def _format_treffer(immobilien: list[Immobilie]) -> str:
    if not immobilien:
        return "Aktuell habe ich leider keine passenden Inserate gefunden."
    mehrere = len(immobilien) > 1
    kopf = (
        f"Ich habe {len(immobilien)} passende Inserate gefunden:"
        if mehrere
        else "Ich habe folgendes passendes Inserat gefunden:"
    )
    zeilen = [kopf]
    for index, i in enumerate(immobilien, start=1):
        praefix = f"{index}. " if mehrere else "- "
        zeile = f"{praefix}{i.titel} | {i.ort}, {i.kanton} | {i.zimmer} Zimmer | CHF {i.preis}.-"
        if i.link and i.link != PLACEHOLDER_LINK:
            zeile += f" | {i.link}"
        zeilen.append(zeile)
    return "\n".join(zeilen)


def _format_kontakt(immobilie: Immobilie, firma: Optional[Firma]) -> str:
    """Kontaktperson des Inserats (siehe Immobilie.kontakt_*) - faellt auf
    die Konto-Daten der Firma/Person zurueck, falls das Inserat selbst keine
    eigene Kontaktperson hinterlegt hat (z.B. aeltere Inserate von vor
    diesem Feature)."""
    name = immobilie.kontakt_name or (firma.name if firma else None)
    telefon = immobilie.kontakt_telefon or (firma.telefonnummer if firma else None)
    email = immobilie.kontakt_email or (firma.email if firma else None)
    if not any((name, telefon, email)):
        return "Kontaktdaten sind für dieses Inserat leider nicht hinterlegt."
    teile = [f"Kontakt: {name}" if name else "Kontakt:"]
    if telefon:
        teile.append(f"Tel. {telefon}")
    if email:
        teile.append(email)
    return " | ".join(teile)


def _format_criteria(criteria: SearchCriteria) -> str:
    teile = [f"{criteria.rooms} Zimmer" if criteria.rooms else None, criteria.property_type]
    ort = criteria.city or criteria.canton
    teile = [t for t in teile if t]
    beschreibung = " ".join(teile) if teile else "Objekt"
    preis = f", max. CHF {criteria.max_price}.-" if criteria.max_price else ""
    return f"{beschreibung} in {ort}{preis}"


class ChatService:
    def __init__(
        self,
        matching_engine: MatchingEngine,
        immobilien_repo: ImmobilienRepository,
        firma_repo: FirmaRepository,
        kunden_repo: KundenRepository,
        suchprofil_repo: SuchprofilRepository,
        dispatcher: NotificationDispatcher,
        lead_repo: Optional[LeadRepository] = None,
        rate_limiter: Optional[RateLimiter] = None,
        outbound_sender: Optional[Callable[[str, str], None]] = None,
        image_sender: Optional[Callable[[str, str, Optional[str]], None]] = None,
        button_sender: Optional[Callable[[str, str, list[tuple[str, str]]], None]] = None,
        firma_service: Optional[FirmaService] = None,
        chatkontakt_repo: Optional[ChatKontaktRepository] = None,
        fehlerlog_repo: Optional[FehlerLogRepository] = None,
        chatverlauf_repo: Optional[ChatverlaufRepository] = None,
        schedule_delay: Optional[Callable[[float, Callable[[], None]], None]] = None,
    ):
        self._matching_engine = matching_engine
        self._immobilien_repo = immobilien_repo
        self._firma_repo = firma_repo
        self._kunden_repo = kunden_repo
        self._suchprofil_repo = suchprofil_repo
        self._dispatcher = dispatcher
        self._lead_repo = lead_repo
        self._rate_limiter = rate_limiter or build_default_rate_limiter()
        # Fuer echte Konto-Erstellung (E-Mail/Passwort) direkt im WhatsApp-
        # Vermieter-Flow (siehe _handle_vermieter) - None, wenn nicht
        # konfiguriert (z.B. DATABASE_URL_RUNTIME fehlt lokal); der Flow
        # faellt dann auf das reine Telefonnummer-Verfahren von frueher zurueck.
        self._firma_service = firma_service
        # Fuer proaktive Nachrichten (Match-Benachrichtigung, Freigabe-
        # Bestaetigung), die nicht als direkte Antwort auf eine eingehende
        # Nachricht entstehen - z.B. ein Aufruf von send_text_message aus
        # app/meta_whatsapp.py, wenn echtes WhatsApp angebunden ist. Im
        # simulierten Web-Chat bleibt das None (Frontend pollt die Historie).
        self._outbound_sender = outbound_sender
        # Fuer echte Foto-Nachrichten via WhatsApp (Match-Benachrichtigung,
        # bester Suchtreffer) - analog outbound_sender, None im simulierten
        # Web-Chat.
        self._image_sender = image_sender
        # Fuer die verzoegerte Suchabo-Rueckfrage mit Ja/Nein-Buttons (siehe
        # _schedule_suchabo_frage) - analog outbound_sender, faellt aber auf
        # outbound_sender (reiner Text) zurueck, falls nicht konfiguriert.
        self._button_sender = button_sender
        # Fuer den Superadmin-Bereich (Statistiken/Fehler-Protokoll) - None,
        # wenn nicht konfiguriert (z.B. in Tests), dann bleiben die
        # entsprechenden Hooks unten einfach No-Ops.
        self._chatkontakt_repo = chatkontakt_repo
        self._fehlerlog_repo = fehlerlog_repo
        # Fuer den Chat-Einblick im Superadmin-Bereich - persistiert jede
        # Nachricht (siehe Session.on_message/add_display), damit sie einen
        # Neustart/Redeploy ueberlebt. None (z.B. in Tests) = keine
        # Persistenz, der In-Memory-Verlauf funktioniert unveraendert weiter.
        self._chatverlauf_repo = chatverlauf_repo
        # Testbarer Ersatz fuer "nach N Sekunden ausfuehren" (siehe
        # _schedule_suchabo_frage) - Default nutzt einen echten Timer/Thread;
        # Tests koennen hier synchron ausfuehren lassen, um nicht wirklich
        # warten zu muessen.
        self._schedule_delay = schedule_delay or self._default_schedule_delay
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._dispatcher.register(self._on_match)

    def get_session(self, telefonnummer: str) -> Session:
        if telefonnummer not in self._sessions:
            session = Session(telefonnummer=telefonnummer)
            if self._chatverlauf_repo is not None:
                session.on_message = lambda role, text, tel=telefonnummer: self._persist_message(tel, role, text)
            self._sessions[telefonnummer] = session
        return self._sessions[telefonnummer]

    def _persist_message(self, telefonnummer: str, rolle: str, text: str) -> None:
        try:
            self._chatverlauf_repo.add(telefonnummer, rolle, text)
        except Exception as exc:  # Persistenz darf den Chat nie zum Absturz bringen
            logger.exception("Chatverlauf-Persistenz fuer %s fehlgeschlagen", telefonnummer)
            if self._fehlerlog_repo is not None:
                self._fehlerlog_repo.add("chatverlauf_persist", str(exc), telefonnummer=telefonnummer)

    def _send_proactive(self, session: Session, text: str) -> None:
        session.add_display("bot", text)
        if self._outbound_sender is not None:
            self._outbound_sender(session.telefonnummer, text)

    def _send_proactive_image(self, session: Session, image_url: str, caption: str) -> None:
        # Web-Chat-Simulator ist reiner Text - Bild-URL als anklickbare Zeile.
        session.add_display("bot", f"[Bild] {caption} {image_url}")
        if self._image_sender is not None:
            self._image_sender(session.telefonnummer, image_url, caption)

    def _send_proactive_images(self, session: Session, immobilie: Immobilie) -> None:
        """Verschickt bis zu MAX_BILDER_PRO_TREFFER Bilder eines Inserats -
        jedes als eigene proaktive Nachricht, gleiche Bildunterschrift wie
        bisher bei einem einzelnen Bild."""
        for bild in immobilie.bilder[:MAX_BILDER_PRO_TREFFER]:
            self._send_proactive_image(session, bild, immobilie.titel)

    def _send_proactive_button(self, session: Session, text: str, options: list[tuple[str, str]]) -> None:
        # pending_interactive wird ERST hier (beim tatsaechlichen Versand)
        # gesetzt, nicht schon beim Planen des verzoegerten Versands - sonst
        # wuerde web/main.py:_process_message es faelschlich auf eine
        # bereits zuvor zurueckgegebene, unabhaengige Antwort anwenden (siehe
        # _schedule_suchabo_frage).
        session.pending_interactive = InteractivePrompt("button", options)
        session.add_display("bot", text)
        if self._button_sender is not None:
            self._button_sender(session.telefonnummer, text, options)
        elif self._outbound_sender is not None:
            self._outbound_sender(session.telefonnummer, text)

    @staticmethod
    def _default_schedule_delay(delay_seconds: float, callback: Callable[[], None]) -> None:
        threading.Timer(delay_seconds, callback).start()

    def _on_match(self, kunde: Kunde, suchprofil: Suchprofil, immobilie: Immobilie) -> None:
        # get_session() statt Dict-Lookup: eine Session existiert vielleicht
        # nicht mehr im Prozess-Speicher (z.B. nach einem Server-Neustart),
        # aber der Kunde/das Suchabo in der DB sind trotzdem noch aktiv - die
        # Benachrichtigung darf dadurch nicht stillschweigend verloren gehen.
        session = self.get_session(kunde.telefonnummer)
        text = (
            f"🔔 Neues Inserat für dein Suchabo ({_format_criteria(suchprofil_to_criteria(suchprofil))}): "
            f"{immobilie.titel} | {immobilie.ort}, {immobilie.kanton} | "
            f"{immobilie.zimmer} Zimmer | CHF {immobilie.preis}.- | {immobilie.link}"
        )
        self._send_proactive(session, text)
        self._send_proactive_images(session, immobilie)

        if self._lead_repo is not None and immobilie.firma_id is not None:
            self._send_proactive(session, "Hast du Interesse an diesem Inserat? (ja/nein)")
            session.pending_lead = PendingLead(
                immobilie_id=immobilie.id,
                firma_id=immobilie.firma_id,
                suchprofil_id=suchprofil.id,
            )

    def notify_listing_approved(self, immobilie: Immobilie) -> None:
        """Vom Admin-Freigabe-Endpunkt aufgerufen, damit der Vermieter im Chat
        Bescheid bekommt, sobald sein Inserat live ist."""
        if immobilie.firma_id is None:
            return
        firma = self._firma_repo.get_by_id(immobilie.firma_id)
        if firma is None or not firma.telefonnummer:
            return
        session = self.get_session(firma.telefonnummer)
        self._send_proactive(
            session, f"✅ Dein Inserat \"{immobilie.titel}\" wurde freigegeben und ist jetzt sichtbar."
        )

    def handle_message(self, telefonnummer: str, text: str) -> list[str]:
        # Grobkoerniger, globaler Lock statt Pro-Telefonnummer-Granularitaet:
        # stellt das bisherige (durch den Single-Event-Loop erzwungene)
        # Verhalten wieder her, seit die Verarbeitung ueber FastAPIs
        # Background-Tasks in einem Threadpool laeuft und dadurch echt
        # parallel auf self._sessions/den Rate-Limiter zugreifen koennte.
        with self._lock:
            return self._handle_message_locked(telefonnummer, text)

    def _handle_message_locked(self, telefonnummer: str, text: str) -> list[str]:
        session = self.get_session(telefonnummer)
        session.pending_interactive = None

        if len(text) > MAX_MESSAGE_LENGTH:
            antwort = (
                f"Deine Nachricht ist zu lang (max. {MAX_MESSAGE_LENGTH} Zeichen). "
                "Kannst du sie etwas kürzer fassen?"
            )
            session.add_display("user", text[:MAX_MESSAGE_LENGTH] + "…")
            session.add_display("bot", antwort)
            return [antwort]

        # Waehrend des Passwort-Schritts NIE den Klartext im (admin-
        # einsehbaren) Chatverlauf speichern - siehe _handle_vermieter.
        session.add_display("user", "••••••••" if session.vermieter_step == "password" else text)
        if self._chatkontakt_repo is not None:
            self._chatkontakt_repo.record_activity(telefonnummer)

        if text.strip().lower() in RESET_WOERTER:
            session.reset()
            antwort = "Alles klar, wir fangen nochmal von vorne an. " + self._ask_role(session)
            session.add_display("bot", antwort)
            return [antwort]

        if session.role is not None:
            antwort = text.strip().lower()
            neue_rolle = None
            if antwort in VERMIETER_WOERTER and session.role != "vermieter":
                neue_rolle = "vermieter"
            elif antwort in MIETER_WOERTER and session.role != "mieter":
                neue_rolle = "mieter"
            if neue_rolle is not None:
                session.reset()
                session.role = neue_rolle
                session.role_frage_gestellt = True
                if neue_rolle == "vermieter":
                    return self._start_vermieter_flow(session)
                antwort_text = (
                    "Alles klar, du bist jetzt als Mieter unterwegs! Beschreib "
                    "mir einfach, was du suchst (z.B. '2.5-Zimmer-Wohnung in "
                    "Zug, max 2000.-')."
                )
                session.add_display("bot", antwort_text)
                return [antwort_text]

        if session.pending_lead is not None:
            antwort = self._handle_pending_lead(session, text)
            session.add_display("bot", antwort)
            return [antwort]

        if session.role is None:
            return self._handle_role_selection(session, text)

        if session.role == "vermieter":
            return self._handle_vermieter(session, text)

        return self._handle_mieter(session, text)

    def _ask_role(self, session: Session) -> str:
        session.role_frage_gestellt = True
        session.pending_interactive = InteractivePrompt("button", ROLE_OPTIONS)
        return (
            "Willkommen! Bist du Vermieter (Inserat aufgeben) oder Mieter "
            "(Wohnung suchen)? Antworte mit 'Vermieter' oder 'Mieter'."
        )

    def _handle_role_selection(self, session: Session, text: str) -> list[str]:
        if not session.role_frage_gestellt:
            antwort = self._ask_role(session)
            session.add_display("bot", antwort)
            return [antwort]

        antwort = text.strip().lower()
        if antwort in VERMIETER_WOERTER:
            session.role = "vermieter"
            return self._start_vermieter_flow(session)
        if antwort in MIETER_WOERTER:
            session.role = "mieter"
            antwort_text = "Alles klar! Beschreib mir einfach, was du suchst (z.B. '2.5-Zimmer-Wohnung in Zug, max 2000.-')."
            session.add_display("bot", antwort_text)
            return [antwort_text]
        session.pending_interactive = InteractivePrompt("button", ROLE_OPTIONS)
        antwort_text = "Bitte antworte mit 'Vermieter' oder 'Mieter'."
        session.add_display("bot", antwort_text)
        return [antwort_text]

    # -- Vermieter -----------------------------------------------------

    LISTING_PROMPT = (
        "Jetzt beschreib mir dein Inserat in einem Satz "
        "(Titel, Zimmer, Ort, Kanton, Preis, Miete oder Kauf, Fläche in m²)."
    )

    def _start_vermieter_flow(self, session: Session) -> list[str]:
        """Einstiegspunkt, sobald jemand Vermieter wird - entweder direkt
        weiter (wiederkehrender, bereits per Konto verifizierter Vermieter)
        oder mit der Webseite-oder-Chat-Frage. Wird von beiden Stellen
        aufgerufen, die session.role = "vermieter" setzen."""
        bestehende_firma = None
        if self._firma_service is not None:
            bestehende_firma = self._firma_repo.get_by_phone(session.telefonnummer)

        if bestehende_firma is not None and bestehende_firma.auth_user_id is not None:
            session.vermieter_firma_id = bestehende_firma.id
            session.vermieter_typ = bestehende_firma.typ
            session.vermieter_step = "done"
            antwort = f"Willkommen zurück, {bestehende_firma.name}! {self.LISTING_PROMPT}"
            session.add_display("bot", antwort)
            return [antwort]

        session.vermieter_step = "website_or_chat"
        antwort = (
            "Möchtest du dein Inserat lieber direkt auf unserer Webseite "
            "erstellen? Das geht oft schneller: wohnchat.ch/firma.html. Oder "
            "ich führe dich hier im Chat durch die Erfassung. Dann richte "
            "ich dir dabei gleich ein Konto ein, mit dem du dich auch auf der "
            "Webseite einloggen kannst."
        )
        session.pending_interactive = InteractivePrompt("button", WEBSITE_OR_CHAT_OPTIONS)
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_vermieter(self, session: Session, text: str) -> list[str]:
        if session.vermieter_step == "website_or_chat":
            return self._handle_vermieter_website_choice(session, text)
        if session.vermieter_step == "typ":
            return self._handle_vermieter_typ_step(session, text)
        if session.vermieter_step == "name":
            return self._handle_vermieter_name_step(session, text)
        if session.vermieter_step == "email":
            return self._handle_vermieter_email_step(session, text)
        if session.vermieter_step == "password":
            return self._handle_vermieter_password_step(session, text)
        return self._handle_listing_extraction(session, text)

    def _handle_vermieter_website_choice(self, session: Session, text: str) -> list[str]:
        normalized = text.strip().lower()
        session.vermieter_step = "typ"
        frage = "Bist du eine Firma oder Privatperson? Antworte mit 'Firma' oder 'Privatperson'."
        session.pending_interactive = InteractivePrompt("button", VERMIETER_TYP_OPTIONS)
        if any(w in normalized for w in WEBSITE_WOERTER):
            hinweis = (
                "Klar, hier nochmal der Link: wohnchat.ch/firma.html. Dort kannst "
                "du dich registrieren und dein Inserat direkt erfassen. Falls du "
                "lieber hier weitermachst, kein Problem:"
            )
            session.add_display("bot", hinweis)
            session.add_display("bot", frage)
            return [hinweis, frage]
        session.add_display("bot", frage)
        return [frage]

    def _handle_vermieter_typ_step(self, session: Session, text: str) -> list[str]:
        antwort_lower = text.strip().lower()
        if antwort_lower in FIRMA_WOERTER:
            session.vermieter_typ = "firma"
            session.vermieter_step = "name"
            antwort = "Wie heisst deine Firma?"
        elif antwort_lower in PRIVAT_WOERTER:
            session.vermieter_typ = "privatperson"
            session.vermieter_step = "name"
            antwort = "Wie ist dein Name?"
        else:
            session.pending_interactive = InteractivePrompt("button", VERMIETER_TYP_OPTIONS)
            antwort = "Bitte antworte mit 'Firma' oder 'Privatperson'."
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_vermieter_name_step(self, session: Session, text: str) -> list[str]:
        session.vermieter_name = text.strip()

        if self._firma_service is None:
            # Konto-Erstellung nicht konfiguriert (z.B. DATABASE_URL_RUNTIME
            # fehlt lokal) - wie frueher: nur ueber die Telefonnummer merken,
            # kein echtes Konto.
            firma = self._firma_repo.get_or_create_by_phone(
                session.telefonnummer, session.vermieter_name, session.vermieter_typ
            )
            session.vermieter_firma_id = firma.id
            session.vermieter_step = "done"
            antwort = f"Danke! {self.LISTING_PROMPT}"
            session.add_display("bot", antwort)
            return [antwort]

        session.vermieter_step = "email"
        antwort = (
            "Danke! Damit du dein Inserat auch später verwalten und dich "
            "ebenfalls auf der Webseite einloggen kannst, richte ich dir "
            "gleich ein Konto ein. Wie lautet deine E-Mail-Adresse?"
        )
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_vermieter_email_step(self, session: Session, text: str) -> list[str]:
        email = text.strip()
        if not EMAIL_PATTERN.match(email):
            antwort = "Das sieht nicht nach einer gültigen E-Mail-Adresse aus. Wie lautet deine E-Mail-Adresse?"
            session.add_display("bot", antwort)
            return [antwort]
        session.vermieter_email = email
        session.vermieter_step = "password"
        antwort = "Und jetzt noch ein Passwort für dein Konto (mindestens 8 Zeichen, mit Buchstabe und Ziffer)."
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_vermieter_password_step(self, session: Session, text: str) -> list[str]:
        password = text.strip()
        try:
            firma = self._firma_service.signup(session.vermieter_name, session.vermieter_email, password)
        except FirmaAuthError as exc:
            if self._fehlerlog_repo is not None:
                self._fehlerlog_repo.add("vermieter_signup", str(exc), telefonnummer=session.telefonnummer)
            session.vermieter_step = "email"
            session.vermieter_email = None
            antwort = f"⚠️ {exc} Wie lautet deine E-Mail-Adresse?"
            session.add_display("bot", antwort)
            return [antwort]

        self._firma_repo.link_phone(firma.id, session.telefonnummer)
        session.vermieter_firma_id = firma.id
        session.vermieter_step = "done"
        antwort = (
            f"✅ Konto erstellt! Du kannst dich mit {session.vermieter_email} auch "
            f"auf wohnchat.ch/firma.html einloggen. {self.LISTING_PROMPT}"
        )
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_listing_extraction(self, session: Session, text: str) -> list[str]:
        session.listing_messages.append({"role": "user", "content": text})
        if len(session.listing_messages) > MAX_CONVERSATION_MESSAGES:
            session.listing_messages = session.listing_messages[-MAX_CONVERSATION_MESSAGES:]

        if not self._rate_limiter.allow(session.telefonnummer):
            session.add_display("bot", RATE_LIMIT_MESSAGE)
            return [RATE_LIMIT_MESSAGE]

        try:
            result = extract_listing(session.listing_messages)
        except IntentExtractionConfigError as exc:
            if self._fehlerlog_repo is not None:
                self._fehlerlog_repo.add("claude_api", str(exc), telefonnummer=session.telefonnummer)
            antwort = f"⚠️ {exc}"
            session.add_display("bot", antwort)
            return [antwort]

        if not result.is_complete:
            session.listing_messages.append(
                {"role": "assistant", "content": result.clarifying_question}
            )
            session.add_display("bot", result.clarifying_question)
            return [result.clarifying_question]

        listing = result.listing
        immobilie = Immobilie(
            firma_id=session.vermieter_firma_id,
            titel=listing.title,
            beschreibung=listing.description,
            typ=listing.listing_type,
            zimmer=listing.rooms,
            kanton=listing.canton,
            ort=listing.city,
            preis=listing.price,
            objekttyp=listing.property_type,
            flaeche_m2=listing.living_space_m2,
            hat_garten=listing.has_garden,
            status="in_pruefung",
            bilder=[],
            link=PLACEHOLDER_LINK,
            # Kontaktperson fuer Interessenten (siehe _format_kontakt) - im
            # WhatsApp-Vermieter-Flow ist das der Chattende selbst, dessen
            # Name/Nummer wir aus dem Flow bereits kennen, ohne extra danach
            # zu fragen.
            kontakt_name=session.vermieter_name,
            kontakt_telefon=session.telefonnummer,
        )
        self._immobilien_repo.add(immobilie)
        session.listing_messages = []

        antwort = (
            f"✅ Danke! Dein Inserat \"{listing.title}\" wurde eingereicht und wird geprüft. "
            "Sobald es freigeschaltet ist, ist es für Mieter sichtbar. Du kannst gleich ein "
            "weiteres Inserat beschreiben, wenn du möchtest."
        )
        session.add_display("bot", antwort)
        return [antwort]

    # -- Mieter ----------------------------------------------------------

    def _handle_mieter(self, session: Session, text: str) -> list[str]:
        if session.pending_listing_choice is not None:
            zahl = re.search(r"\d+", text)
            if zahl and 1 <= int(zahl.group()) <= len(session.pending_listing_choice):
                return self._handle_listing_choice(session, text)
            if zahl:
                antwort = f"Bitte antworte mit einer Zahl zwischen 1 und {len(session.pending_listing_choice)}."
                session.add_display("bot", antwort)
                return [antwort]
            # Keine Zahl erkennbar: vermutlich eine neue Suche statt einer
            # Auswahl unter den gezeigten Treffern - gleiche Ueberlegung wie
            # beim pending_criteria-Fallback unten.
            session.pending_listing_choice = None
            session.pending_listing_criteria = None

        if session.pending_search_slot is not None:
            return self._handle_pending_search_slot(session, text)

        if session.pending_criteria is not None:
            antwort_lower = text.strip().lower()
            if antwort_lower in JA_WOERTER or antwort_lower in NEIN_WOERTER:
                antwort = self._handle_pending_confirmation(session, text)
                session.add_display("bot", antwort)
                return [antwort]
            # Keine ja/nein-Antwort: der Kunde beschreibt vermutlich schon
            # die naechste Suche (z.B. eine weitere Wohnung/anderer Kanton),
            # statt auf die Suchabo-Frage einzugehen. Ohne dieses Loslassen
            # wuerde jede neue Suchanfrage hier einfach mit "bitte ja oder
            # nein" beantwortet und nie ausgefuehrt - genau der gemeldete
            # Bug, dass passende Inserate im Chat nicht auftauchen.
            session.pending_criteria = None
            session.pending_interactive = None

        session.claude_messages.append({"role": "user", "content": text})
        if len(session.claude_messages) > MAX_CONVERSATION_MESSAGES:
            session.claude_messages = session.claude_messages[-MAX_CONVERSATION_MESSAGES:]

        if not self._rate_limiter.allow(session.telefonnummer):
            session.add_display("bot", RATE_LIMIT_MESSAGE)
            return [RATE_LIMIT_MESSAGE]

        try:
            result = extract_intent(session.claude_messages)
        except IntentExtractionConfigError as exc:
            if self._fehlerlog_repo is not None:
                self._fehlerlog_repo.add("claude_api", str(exc), telefonnummer=session.telefonnummer)
            antwort = f"⚠️ {exc}"
            session.add_display("bot", antwort)
            return [antwort]

        if not result.is_complete:
            session.claude_messages.append(
                {"role": "assistant", "content": result.clarifying_question}
            )
            session.add_display("bot", result.clarifying_question)
            return [result.clarifying_question]

        session.partial_criteria = result.criteria
        session.resolved_search_slots = set()
        naechster_slot = self._next_missing_search_slot(session, result.criteria)
        if naechster_slot is not None:
            return self._ask_search_slot(session, naechster_slot)
        return self._finish_search(session, result.criteria)

    def _next_missing_search_slot(self, session: Session, criteria: SearchCriteria) -> Optional[str]:
        """Ein Feld gilt nur dann als "noch offen", wenn es sowohl leer ist
        ALS AUCH noch nicht beantwortet wurde - sonst liesse sich eine
        Egal-Antwort (Feld bleibt None) nicht von "noch nicht gefragt"
        unterscheiden, und die Frage wuerde sich endlos wiederholen."""
        for slot, value in (
            ("rooms", criteria.rooms),
            ("property_type", criteria.property_type),
            ("max_price", criteria.max_price),
        ):
            if value is None and slot not in session.resolved_search_slots:
                return slot
        return None

    def _search_slot_question(self, session: Session, slot: str) -> str:
        """Setzt session.pending_search_slot/pending_interactive und gibt
        den Fragetext zurueck, OHNE ihn anzuzeigen - der Aufrufer entscheidet,
        ob er ihn direkt oder mit einem Hinweis kombiniert anzeigt."""
        session.pending_search_slot = slot
        if slot == "rooms":
            session.pending_interactive = InteractivePrompt("list", ROOMS_OPTIONS, "Zimmer wählen")
            return "Wie viele Zimmer suchst du mindestens?"
        if slot == "property_type":
            session.pending_interactive = InteractivePrompt("list", PROPERTY_TYPE_OPTIONS, "Objekttyp wählen")
            return "Welche Art von Objekt suchst du?"
        session.pending_interactive = None
        return "Bis zu welchem Preis pro Monat (CHF)? Antworte mit einer Zahl oder 'egal'."

    def _ask_search_slot(self, session: Session, slot: str) -> list[str]:
        antwort = self._search_slot_question(session, slot)
        session.add_display("bot", antwort)
        return [antwort]

    def _handle_pending_search_slot(self, session: Session, text: str) -> list[str]:
        slot = session.pending_search_slot
        criteria = session.partial_criteria
        if criteria is None:
            # Sollte nicht vorkommen (z.B. Session-Verlust) - lieber robust
            # neu einsteigen statt abzustuerzen.
            session.pending_search_slot = None
            return self._handle_mieter(session, text)

        if slot == "rooms":
            ok, value = _parse_rooms_answer(text)
            if ok:
                criteria.rooms = value
        elif slot == "property_type":
            ok, value = _parse_property_type_answer(text)
            if ok:
                criteria.property_type = value
        else:
            ok, value = _parse_price_answer(text)
            if ok:
                criteria.max_price = value

        if not ok:
            frage = self._search_slot_question(session, slot)
            antwort = f"Das habe ich nicht verstanden. {frage}"
            session.add_display("bot", antwort)
            return [antwort]

        session.pending_search_slot = None
        session.resolved_search_slots.add(slot)
        naechster_slot = self._next_missing_search_slot(session, criteria)
        if naechster_slot is not None:
            return self._ask_search_slot(session, naechster_slot)
        return self._finish_search(session, criteria)

    def _finish_search(self, session: Session, criteria: SearchCriteria) -> list[str]:
        treffer = self._matching_engine.search(criteria)
        treffer_text = _format_treffer(treffer)
        # Suche ist abgeschlossen - Rohverlauf leeren, damit eine weitere
        # Suche im selben Chat (andere Wohnung/anderer Kanton) nicht durch
        # die bereits beantworteten alten Kriterien verfaelscht wird (Claude
        # sieht sonst weiterhin z.B. den alten Kanton in der Historie und
        # vermischt ihn mit der neuen Anfrage). Analog zu
        # session.listing_messages nach einem eingereichten Inserat.
        session.claude_messages = []

        if not treffer:
            rueckfrage = (
                f"Möchtest du für '{_format_criteria(criteria)}' ein Suchabo anlegen? "
                "Dann melde ich mich automatisch, sobald ein neues passendes Inserat "
                "reinkommt, egal von welchem Anbieter. (ja/nein)"
            )
            session.pending_criteria = criteria
            session.pending_interactive = InteractivePrompt("button", JA_NEIN_OPTIONS)
            for nachricht in (treffer_text, rueckfrage):
                session.add_display("bot", nachricht)
            return [treffer_text, rueckfrage]

        if len(treffer) == 1:
            kontakt_text = self._deliver_listing_contact(session, treffer[0])
            kombiniert = f"{treffer_text}\n{kontakt_text}"
            session.add_display("bot", kombiniert)
            self._schedule_suchabo_frage(session, criteria)
            return [kombiniert]

        # Mehrere Treffer: erst auswaehlen lassen, statt Kontaktdaten fuer
        # alle auf einmal preiszugeben (siehe _handle_listing_choice).
        session.pending_listing_choice = [i.id for i in treffer]
        session.pending_listing_criteria = criteria
        session.pending_interactive = None
        frage = f"Welches Inserat interessiert dich? Antworte mit der Nummer (1-{len(treffer)})."
        for nachricht in (treffer_text, frage):
            session.add_display("bot", nachricht)
        return [treffer_text, frage]

    def _deliver_listing_contact(self, session: Session, immobilie: Immobilie) -> str:
        """Gibt die Kontaktdaten-Zeile fuer ein einzelnes Inserat zurueck,
        verschickt dessen Bild (falls vorhanden) proaktiv und legt einen Lead
        an - der Kunde hat durch das Erreichen dieses Punktes (Treffer
        gesehen bzw. bewusst ausgewaehlt) sein Interesse bereits gezeigt."""
        firma = self._firma_repo.get_by_id(immobilie.firma_id) if immobilie.firma_id else None
        kontakt_text = _format_kontakt(immobilie, firma)
        if self._lead_repo is not None and immobilie.firma_id is not None:
            self._lead_repo.add(Lead(immobilie_id=immobilie.id, firma_id=immobilie.firma_id))
        self._send_proactive_images(session, immobilie)
        return kontakt_text

    def _handle_listing_choice(self, session: Session, text: str) -> list[str]:
        ids = session.pending_listing_choice
        criteria = session.pending_listing_criteria
        index = int(re.search(r"\d+", text).group()) - 1
        session.pending_listing_choice = None
        session.pending_listing_criteria = None

        immobilie = self._immobilien_repo.get_by_id(ids[index])
        if immobilie is None:
            antwort = "Dieses Inserat ist leider nicht mehr verfügbar. Sag mir einfach, wenn du nochmal suchen willst."
            session.add_display("bot", antwort)
            return [antwort]

        kontakt_text = self._deliver_listing_contact(session, immobilie)
        session.add_display("bot", kontakt_text)
        self._schedule_suchabo_frage(session, criteria)
        return [kontakt_text]

    def _schedule_suchabo_frage(self, session: Session, criteria: SearchCriteria) -> None:
        """Fragt (verzoegert, siehe SUCHABO_FRAGE_DELAY_SECONDS) an, ob fuer
        die Suche ein laufendes Suchabo angelegt werden soll. Der Ja/Nein-
        "Gate"-Zustand wird SOFORT gesetzt (ein schneller Folgetext des
        Kunden soll bereits richtig behandelt werden), nur der eigentliche
        Versand der Frage kommt verzoegert - sonst ueberholt sie regelmaessig
        das separat verschickte Bild/die Kontaktzeile."""
        session.pending_criteria = criteria
        text = (
            f"Möchtest du für '{_format_criteria(criteria)}' ausserdem ein Suchabo anlegen? "
            "Dann melde ich mich automatisch, sobald ein neues passendes Inserat reinkommt, "
            "egal von welchem Anbieter. (ja/nein)"
        )
        self._schedule_delay(
            SUCHABO_FRAGE_DELAY_SECONDS,
            lambda: self._send_proactive_button(session, text, JA_NEIN_OPTIONS),
        )

    def _handle_pending_confirmation(self, session: Session, text: str) -> str:
        antwort = text.strip().lower()
        if antwort in JA_WOERTER:
            criteria = session.pending_criteria
            session.pending_criteria = None
            kunde = self._kunden_repo.get_or_create_by_phone(session.telefonnummer)
            suchprofil = self._suchprofil_repo.add(
                Suchprofil(
                    kunde_id=kunde.id,
                    zimmer=criteria.rooms,
                    kanton=criteria.canton,
                    ort=criteria.city,
                    preis_max=criteria.max_price,
                    objekttyp=criteria.property_type,
                )
            )
            return (
                f"✅ Suchabo angelegt ({_format_criteria(criteria)}). "
                f"Ich benachrichtige dich hier, sobald ein neues Inserat passt. "
                f"[Suchprofil {suchprofil.id}]"
            )
        if antwort in NEIN_WOERTER:
            session.pending_criteria = None
            return "Alles klar, kein Suchabo angelegt. Sag mir einfach, wenn du eine neue Suche starten willst."
        session.pending_interactive = InteractivePrompt("button", JA_NEIN_OPTIONS)
        return "Möchtest du das Suchabo anlegen? Bitte antworte mit 'ja' oder 'nein'."

    def _handle_pending_lead(self, session: Session, text: str) -> str:
        antwort = text.strip().lower()
        if antwort in JA_WOERTER:
            pending = session.pending_lead
            session.pending_lead = None
            self._lead_repo.add(
                Lead(
                    immobilie_id=pending.immobilie_id,
                    firma_id=pending.firma_id,
                    suchprofil_id=pending.suchprofil_id,
                )
            )
            return "✅ Danke! Ich habe dein Interesse an den Anbieter weitergeleitet, er meldet sich bei dir."
        if antwort in NEIN_WOERTER:
            session.pending_lead = None
            return "Alles klar, kein Interesse vermerkt."
        session.pending_interactive = InteractivePrompt("button", JA_NEIN_OPTIONS)
        return "Hast du Interesse an diesem Inserat? Bitte antworte mit 'ja' oder 'nein'."
