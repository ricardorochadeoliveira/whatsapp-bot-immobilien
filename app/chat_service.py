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

from dataclasses import dataclass, field
from typing import Callable, Optional

from app.intent_extraction import (
    IntentExtractionConfigError,
    extract_intent,
    extract_listing,
)
from app.matching import MatchingEngine, suchprofil_to_criteria
from app.models import Immobilie, Kunde, Lead, SearchCriteria, Suchprofil
from app.notifications import NotificationDispatcher
from app.rate_limiter import RateLimiter, build_default_rate_limiter
from app.repository import (
    FirmaRepository,
    ImmobilienRepository,
    KundenRepository,
    LeadRepository,
    SuchprofilRepository,
)

JA_WOERTER = {"ja", "j", "yes", "y", "klar", "gerne", "genau"}
NEIN_WOERTER = {"nein", "n", "no", "nope"}
VERMIETER_WOERTER = {"vermieter", "vermieterin", "anbieter", "vermieten", "inserieren"}
MIETER_WOERTER = {"mieter", "mieterin", "suche", "suchend", "wohnungssuche"}
FIRMA_WOERTER = {"firma", "unternehmen", "company", "geschaeft"}
PRIVAT_WOERTER = {"privat", "privatperson", "person", "einzelperson"}
RESET_WOERTER = {"reset", "neustart", "neu anfangen", "von vorne"}

# Kostenschutz fuer die Claude-API (siehe app/rate_limiter.py und
# docs/launch-checkliste.md): Nachrichten werden nicht unbegrenzt lang oder
# unbegrenzt oft an Claude weitergereicht.
MAX_MESSAGE_LENGTH = 800
MAX_CONVERSATION_MESSAGES = 20
RATE_LIMIT_MESSAGE = (
    "⏳ Du hast gerade viele Nachrichten geschickt. Bitte warte kurz, bevor "
    "du weiterschreibst."
)


@dataclass
class PendingLead:
    immobilie_id: str
    firma_id: str
    suchprofil_id: str


@dataclass
class Session:
    telefonnummer: str
    role: Optional[str] = None  # "vermieter" | "mieter"
    role_frage_gestellt: bool = False
    vermieter_typ: Optional[str] = None  # "firma" | "privatperson"
    vermieter_firma_id: Optional[str] = None
    claude_messages: list[dict] = field(default_factory=list)  # Mieter-Suche
    listing_messages: list[dict] = field(default_factory=list)  # Vermieter-Inserat
    display_messages: list[dict] = field(default_factory=list)
    pending_criteria: Optional[SearchCriteria] = None
    pending_lead: Optional[PendingLead] = None

    def add_display(self, role: str, text: str) -> None:
        self.display_messages.append({"role": role, "text": text})

    def reset(self) -> None:
        telefonnummer = self.telefonnummer
        display_messages = self.display_messages
        self.__init__(telefonnummer=telefonnummer)  # type: ignore[misc]
        self.display_messages = display_messages


def _format_treffer(immobilien: list[Immobilie]) -> str:
    if not immobilien:
        return "Aktuell habe ich leider keine passenden Inserate gefunden."
    zeilen = [f"Ich habe {len(immobilien)} passende Inserate gefunden:"]
    for i in immobilien:
        zeilen.append(
            f"- {i.titel} | {i.ort}, {i.kanton} | {i.zimmer} Zimmer | "
            f"CHF {i.preis}.- | {i.link}"
        )
    return "\n".join(zeilen)


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
    ):
        self._matching_engine = matching_engine
        self._immobilien_repo = immobilien_repo
        self._firma_repo = firma_repo
        self._kunden_repo = kunden_repo
        self._suchprofil_repo = suchprofil_repo
        self._dispatcher = dispatcher
        self._lead_repo = lead_repo
        self._rate_limiter = rate_limiter or build_default_rate_limiter()
        # Fuer proaktive Nachrichten (Match-Benachrichtigung, Freigabe-
        # Bestaetigung), die nicht als direkte Antwort auf eine eingehende
        # Nachricht entstehen - z.B. ein Aufruf von send_text_message aus
        # app/meta_whatsapp.py, wenn echtes WhatsApp angebunden ist. Im
        # simulierten Web-Chat bleibt das None (Frontend pollt die Historie).
        self._outbound_sender = outbound_sender
        self._sessions: dict[str, Session] = {}
        self._dispatcher.register(self._on_match)

    def get_session(self, telefonnummer: str) -> Session:
        if telefonnummer not in self._sessions:
            self._sessions[telefonnummer] = Session(telefonnummer=telefonnummer)
        return self._sessions[telefonnummer]

    def _send_proactive(self, session: Session, text: str) -> None:
        session.add_display("bot", text)
        if self._outbound_sender is not None:
            self._outbound_sender(session.telefonnummer, text)

    def _on_match(self, kunde: Kunde, suchprofil: Suchprofil, immobilie: Immobilie) -> None:
        # get_session() statt Dict-Lookup: eine Session existiert vielleicht
        # nicht mehr im Prozess-Speicher (z.B. nach einem Server-Neustart),
        # aber der Kunde/das Suchabo in der DB sind trotzdem noch aktiv - die
        # Benachrichtigung darf dadurch nicht stillschweigend verloren gehen.
        session = self.get_session(kunde.telefonnummer)
        text = (
            f"🔔 Neues Inserat fuer dein Suchabo ({_format_criteria(suchprofil_to_criteria(suchprofil))}): "
            f"{immobilie.titel} | {immobilie.ort}, {immobilie.kanton} | "
            f"{immobilie.zimmer} Zimmer | CHF {immobilie.preis}.- | {immobilie.link}"
        )
        self._send_proactive(session, text)

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
        session = self.get_session(telefonnummer)

        if len(text) > MAX_MESSAGE_LENGTH:
            antwort = (
                f"Deine Nachricht ist zu lang (max. {MAX_MESSAGE_LENGTH} Zeichen). "
                "Bitte fass dich kuerzer."
            )
            session.add_display("user", text[:MAX_MESSAGE_LENGTH] + "…")
            session.add_display("bot", antwort)
            return [antwort]

        session.add_display("user", text)

        if text.strip().lower() in RESET_WOERTER:
            session.reset()
            antwort = "Alles zurueckgesetzt. " + self._ask_role(session)
            session.add_display("bot", antwort)
            return [antwort]

        if session.pending_lead is not None:
            antwort = self._handle_pending_lead(session, text)
            session.add_display("bot", antwort)
            return [antwort]

        if session.role is None:
            antwort = self._handle_role_selection(session, text)
            session.add_display("bot", antwort)
            return [antwort]

        if session.role == "vermieter":
            return self._handle_vermieter(session, text)

        return self._handle_mieter(session, text)

    def _ask_role(self, session: Session) -> str:
        session.role_frage_gestellt = True
        return (
            "Willkommen! Bist du Vermieter (Inserat aufgeben) oder Mieter "
            "(Wohnung suchen)? Antworte mit 'Vermieter' oder 'Mieter'."
        )

    def _handle_role_selection(self, session: Session, text: str) -> str:
        if not session.role_frage_gestellt:
            return self._ask_role(session)

        antwort = text.strip().lower()
        if antwort in VERMIETER_WOERTER:
            session.role = "vermieter"
            return "Alles klar! Bist du eine Firma oder Privatperson? Antworte mit 'Firma' oder 'Privatperson'."
        if antwort in MIETER_WOERTER:
            session.role = "mieter"
            return "Alles klar! Beschreib mir einfach, was du suchst (z.B. '2.5-Zimmer-Wohnung in Zug, max 2000.-')."
        return "Bitte antworte mit 'Vermieter' oder 'Mieter'."

    # -- Vermieter -----------------------------------------------------

    def _handle_vermieter(self, session: Session, text: str) -> list[str]:
        if session.vermieter_typ is None:
            antwort = self._handle_vermieter_typ(session, text)
        elif session.vermieter_firma_id is None:
            antwort = self._handle_vermieter_name(session, text)
        else:
            return self._handle_listing_extraction(session, text)

        session.add_display("bot", antwort)
        return [antwort]

    def _handle_vermieter_typ(self, session: Session, text: str) -> str:
        antwort = text.strip().lower()
        if antwort in FIRMA_WOERTER:
            session.vermieter_typ = "firma"
            return "Wie heisst deine Firma?"
        if antwort in PRIVAT_WOERTER:
            session.vermieter_typ = "privatperson"
            return "Wie ist dein Name?"
        return "Bitte antworte mit 'Firma' oder 'Privatperson'."

    def _handle_vermieter_name(self, session: Session, text: str) -> str:
        name = text.strip()
        firma = self._firma_repo.get_or_create_by_phone(
            session.telefonnummer, name, session.vermieter_typ
        )
        session.vermieter_firma_id = firma.id
        return (
            "Danke! Jetzt beschreib mir dein Inserat in einem Satz "
            "(Titel, Zimmer, Ort, Kanton, Preis, Miete oder Kauf, Flaeche in m2)."
        )

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
            bild_url="https://picsum.photos/400/300",
            link="https://example.com/inserate/neu",
        )
        self._immobilien_repo.add(immobilie)
        session.listing_messages = []

        antwort = (
            f"✅ Danke! Dein Inserat \"{listing.title}\" wurde eingereicht und wird geprueft. "
            "Sobald es freigeschaltet ist, ist es fuer Mieter sichtbar. Du kannst gleich ein "
            "weiteres Inserat beschreiben, wenn du moechtest."
        )
        session.add_display("bot", antwort)
        return [antwort]

    # -- Mieter ----------------------------------------------------------

    def _handle_mieter(self, session: Session, text: str) -> list[str]:
        if session.pending_criteria is not None:
            antwort = self._handle_pending_confirmation(session, text)
            session.add_display("bot", antwort)
            return [antwort]

        session.claude_messages.append({"role": "user", "content": text})
        if len(session.claude_messages) > MAX_CONVERSATION_MESSAGES:
            session.claude_messages = session.claude_messages[-MAX_CONVERSATION_MESSAGES:]

        if not self._rate_limiter.allow(session.telefonnummer):
            session.add_display("bot", RATE_LIMIT_MESSAGE)
            return [RATE_LIMIT_MESSAGE]

        try:
            result = extract_intent(session.claude_messages)
        except IntentExtractionConfigError as exc:
            antwort = f"⚠️ {exc}"
            session.add_display("bot", antwort)
            return [antwort]

        if not result.is_complete:
            session.claude_messages.append(
                {"role": "assistant", "content": result.clarifying_question}
            )
            session.add_display("bot", result.clarifying_question)
            return [result.clarifying_question]

        criteria = result.criteria
        treffer = self._matching_engine.search(criteria)
        treffer_text = _format_treffer(treffer)
        rueckfrage = (
            f"Moechtest du fuer '{_format_criteria(criteria)}' ein Suchabo anlegen? "
            "Dann melde ich mich automatisch, sobald ein neues passendes Inserat "
            "reinkommt - egal von welchem Anbieter. (ja/nein)"
        )
        session.claude_messages.append(
            {"role": "assistant", "content": f"{treffer_text}\n{rueckfrage}"}
        )
        session.pending_criteria = criteria

        for nachricht in (treffer_text, rueckfrage):
            session.add_display("bot", nachricht)
        return [treffer_text, rueckfrage]

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
        return "Bitte antworte mit 'ja' oder 'nein' - moechtest du das Suchabo anlegen?"

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
        return "Bitte antworte mit 'ja' oder 'nein' - hast du Interesse an diesem Inserat?"
