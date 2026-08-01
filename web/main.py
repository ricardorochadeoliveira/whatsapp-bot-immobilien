"""Punkt 6: Simuliertes Chat-Frontend (Platzhalter fuer WhatsApp) + Admin-API
zum Ausloesen neuer (gemockter) Inserate, um den Matching-Job (Punkt 5) zu
demonstrieren.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from app.bootstrap import context  # noqa: E402  (nach load_dotenv)
from app.firma_service import FirmaAuthError  # noqa: E402
from app.meta_whatsapp import (  # noqa: E402
    parse_incoming_messages,
    send_text_message,
    verify_webhook_signature,
)
from app.models import Firma, Immobilie, Lead  # noqa: E402

logger = logging.getLogger("immo_bot.webhook")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="WhatsApp Immobilien-Bot (Simulation)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def require_admin_key(x_admin_key: str = Header(default="")):
    """Schuetzt das interne Admin-/Chat-Simulator-Panel. Ist ADMIN_API_KEY
    nicht gesetzt, bleibt der Bereich unprotected (lokales Entwickeln ohne
    Reibung) - siehe docs/launch-checkliste.md fuer den Hinweis, das vor
    einem echten Go-Live zu setzen. Das Firmen-Portal (/firma, /api/firma/*)
    ist davon unabhaengig - das laeuft ueber Supabase Auth."""
    expected = os.environ.get("ADMIN_API_KEY")
    if expected and x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Ungueltiger oder fehlender Admin-Key.")


ADMIN_PROTECTED = [Depends(require_admin_key)]


@app.get("/")
def index():
    # Die Seite selbst enthaelt keine Daten - Schutz greift auf den
    # API-Aufrufen, die chat.html macht (Browser-Navigation kann keine
    # Custom-Header mitschicken, daher kein Schutz direkt auf dieser Route).
    return FileResponse(str(STATIC_DIR / "chat.html"))


@app.get("/firma")
def firma_portal():
    return FileResponse(str(STATIC_DIR / "firma.html"))


# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API Webhook (siehe app/meta_whatsapp.py und
# docs/produkt-abgleich.md). Ersetzt langfristig den simulierten Chat oben.
# ---------------------------------------------------------------------------


@app.get("/webhook/whatsapp")
def verify_whatsapp_webhook(request: Request):
    """Meta ruft das beim Einrichten des Webhooks einmalig auf, um den von
    uns gewaehlten META_WEBHOOK_VERIFY_TOKEN zu bestaetigen."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    expected_token = os.environ.get("META_WEBHOOK_VERIFY_TOKEN")

    if mode == "subscribe" and expected_token and token == expected_token:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook-Verifikation fehlgeschlagen.")


@app.post("/webhook/whatsapp")
async def receive_whatsapp_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(body, signature):
        raise HTTPException(status_code=401, detail="Ungueltige Signatur.")

    payload = await request.json()
    for telefonnummer, text in parse_incoming_messages(payload):
        antworten = context.chat_service.handle_message(telefonnummer, text)
        for antwort in antworten:
            try:
                send_text_message(telefonnummer, antwort)
            except Exception:
                logger.exception("Antwort an %s konnte nicht gesendet werden", telefonnummer)

    return {"ok": True}


def require_firma_service():
    if context.firma_service is None:
        raise HTTPException(
            status_code=503,
            detail="Firmen-Login ist nicht konfiguriert (DATABASE_URL_RUNTIME/SUPABASE_* fehlen).",
        )
    return context.firma_service


def get_current_firma(authorization: str = Header(default="")) -> Firma:
    service = require_firma_service()
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Fehlender Authorization-Header.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return service.authenticate(token)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class ChatRequest(BaseModel):
    telefonnummer: str
    text: str


class ChatHistoryEntry(BaseModel):
    role: str
    text: str


@app.post("/api/chat", dependencies=ADMIN_PROTECTED)
def post_chat(req: ChatRequest) -> list[ChatHistoryEntry]:
    context.chat_service.handle_message(req.telefonnummer, req.text)
    session = context.chat_service.get_session(req.telefonnummer)
    return session.display_messages


@app.get("/api/chat/history", dependencies=ADMIN_PROTECTED)
def get_chat_history(telefonnummer: str) -> list[ChatHistoryEntry]:
    session = context.chat_service.get_session(telefonnummer)
    return session.display_messages


class NewListingRequest(BaseModel):
    titel: str
    zimmer: float
    kanton: str
    ort: str
    preis: int
    objekttyp: str
    flaeche_m2: float = 70
    bild_url: str = "https://picsum.photos/400/300"
    link: Optional[str] = None
    firma_id: Optional[str] = None


class SimulateListingResponse(BaseModel):
    immobilie: Immobilie
    neue_matches: int


@app.post("/api/admin/simulate-listing", dependencies=ADMIN_PROTECTED)
def simulate_listing(req: NewListingRequest) -> SimulateListingResponse:
    """Admin-Weg, ein bereits aktives Inserat direkt anzulegen (umgeht die
    Pruef-Queue bewusst - anders als der anonyme WhatsApp-Vermieter-Pfad)."""
    immobilie = Immobilie(
        titel=req.titel,
        firma_id=req.firma_id,
        zimmer=req.zimmer,
        kanton=req.kanton,
        ort=req.ort,
        preis=req.preis,
        objekttyp=req.objekttyp,
        flaeche_m2=req.flaeche_m2,
        bild_url=req.bild_url,
        link=req.link or "https://example.com/inserate/neu",
    )
    context.immobilien_repo.add(immobilie)
    neue_matches = context.matching_job.process_new_listing(immobilie)
    return SimulateListingResponse(immobilie=immobilie, neue_matches=len(neue_matches))


@app.get("/api/admin/immobilien", dependencies=ADMIN_PROTECTED)
def list_immobilien() -> list[Immobilie]:
    return context.immobilien_repo.get_all()


@app.get("/api/admin/firmen", dependencies=ADMIN_PROTECTED)
def list_firmen() -> list[dict]:
    return [
        {"id": f.id, "name": f.name, "typ": f.typ} for f in context.firma_repo.get_all()
    ]


@app.get("/api/admin/matchlog", dependencies=ADMIN_PROTECTED)
def list_matchlog() -> list[dict]:
    return [
        {
            "id": m.id,
            "suchprofil_id": m.suchprofil_id,
            "immobilie_id": m.immobilie_id,
            "benachrichtigt_am": m.benachrichtigt_am.isoformat(),
        }
        for m in context.matchlog_repo.get_all()
    ]


# ---------------------------------------------------------------------------
# Pruef-Queue: WhatsApp-Vermieter-Inserate (kein Login) muessen freigegeben
# werden, bevor sie in der Mieter-Suche sichtbar sind (Schutz gegen
# Fake-Inserate, siehe docs/produkt-abgleich.md, Marktplatz-Pivot).
# ---------------------------------------------------------------------------


class PendingInseratResponse(BaseModel):
    immobilie: Immobilie
    firma_name: Optional[str] = None
    firma_typ: Optional[str] = None
    firma_telefonnummer: Optional[str] = None
    name_konflikt: bool = False


@app.get("/api/admin/inserate/pruefung", dependencies=ADMIN_PROTECTED)
def list_pending_inserate() -> list[PendingInseratResponse]:
    """Reichert jedes zur Pruefung anstehende Inserat mit Anbieter-Infos an
    und markiert, ob der angegebene Firmenname bereits unter einer ANDEREN
    Telefonnummer existiert - moeglicher Hinweis auf Identitaets-
    Vortaeuschung (siehe docs/produkt-abgleich.md, Sicherheitsabschnitt)."""
    alle_firmen = context.firma_repo.get_all()
    pending = context.immobilien_repo.get_by_status("in_pruefung")

    result = []
    for immobilie in pending:
        firma = next((f for f in alle_firmen if f.id == immobilie.firma_id), None)
        konflikt = False
        if firma is not None:
            konflikt = any(
                f.id != firma.id and f.name.strip().lower() == firma.name.strip().lower()
                for f in alle_firmen
            )
        result.append(
            PendingInseratResponse(
                immobilie=immobilie,
                firma_name=firma.name if firma else None,
                firma_typ=firma.typ if firma else None,
                firma_telefonnummer=firma.telefonnummer if firma else None,
                name_konflikt=konflikt,
            )
        )
    return result


@app.post("/api/admin/inserate/{immobilie_id}/freigeben", dependencies=ADMIN_PROTECTED)
def approve_inserat(immobilie_id: str) -> dict:
    immobilie = context.immobilien_repo.get_by_id(immobilie_id)
    if immobilie is None:
        raise HTTPException(status_code=404, detail="Inserat nicht gefunden.")
    context.immobilien_repo.set_status(immobilie_id, "aktiv")
    immobilie = immobilie.model_copy(update={"status": "aktiv"})
    neue_matches = context.matching_job.process_new_listing(immobilie)
    context.chat_service.notify_listing_approved(immobilie)
    return {"ok": True, "neue_matches": len(neue_matches)}


@app.post("/api/admin/inserate/{immobilie_id}/ablehnen", dependencies=ADMIN_PROTECTED)
def reject_inserat(immobilie_id: str) -> dict:
    if context.immobilien_repo.get_by_id(immobilie_id) is None:
        raise HTTPException(status_code=404, detail="Inserat nicht gefunden.")
    context.immobilien_repo.set_status(immobilie_id, "abgelehnt")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Firmen-Login (Supabase Auth) + Inserate-Verwaltung, mandantengetrennt (RLS)
# ---------------------------------------------------------------------------


class FirmaSignupRequest(BaseModel):
    name: str
    email: str
    password: str


class FirmaLoginRequest(BaseModel):
    email: str
    password: str


class FirmaLoginResponse(BaseModel):
    access_token: str
    firma: Firma


@app.post("/api/firma/signup", response_model=Firma)
def firma_signup(req: FirmaSignupRequest):
    service = require_firma_service()
    try:
        return service.signup(name=req.name, email=req.email, password=req.password)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/firma/login", response_model=FirmaLoginResponse)
def firma_login(req: FirmaLoginRequest):
    service = require_firma_service()
    try:
        result = service.login(email=req.email, password=req.password)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return FirmaLoginResponse(access_token=result.access_token, firma=result.firma)


@app.get("/api/firma/me", response_model=Firma)
def firma_me(firma: Firma = Depends(get_current_firma)):
    return firma


class InseratRequest(BaseModel):
    titel: str
    beschreibung: Optional[str] = None
    typ: str = "miete"
    zimmer: float
    kanton: str
    ort: str
    preis: int
    objekttyp: str
    flaeche_m2: float
    hat_garten: bool = False
    bild_url: str = "https://picsum.photos/400/300"
    link: str = "https://example.com/inserate/neu"


@app.post("/api/firma/inserate", response_model=Immobilie)
def firma_create_inserat(req: InseratRequest, firma: Firma = Depends(get_current_firma)):
    immobilie = Immobilie(**req.model_dump())
    return context.firma_service.create_inserat(firma, immobilie)


@app.get("/api/firma/inserate", response_model=list[Immobilie])
def firma_list_inserate(firma: Firma = Depends(get_current_firma)):
    return context.firma_service.list_inserate(firma)


class InseratStatusRequest(BaseModel):
    status: str  # "aktiv" | "deaktiviert"


@app.patch("/api/firma/inserate/{immobilie_id}/status")
def firma_set_inserat_status(
    immobilie_id: str, req: InseratStatusRequest, firma: Firma = Depends(get_current_firma)
):
    try:
        context.firma_service.set_inserat_status(firma, immobilie_id, req.status)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/firma/leads", response_model=list[Lead])
def firma_list_leads(firma: Firma = Depends(get_current_firma)):
    return context.firma_service.list_leads(firma)
