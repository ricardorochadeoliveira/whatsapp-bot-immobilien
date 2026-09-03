"""Punkt 6: Simuliertes Chat-Frontend (Platzhalter fuer WhatsApp) + Admin-API
zum Ausloesen neuer (gemockter) Inserate, um den Matching-Job (Punkt 5) zu
demonstrieren.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from app.bootstrap import context  # noqa: E402  (nach load_dotenv)
from app import code_assistant  # noqa: E402
from app import superadmin_auth  # noqa: E402
from app.code_assistant import CodeAssistantConfigError, CodeAssistantConflictError  # noqa: E402
from app.firma_service import FirmaAuthError  # noqa: E402
from app.image_storage import ImageUploadError, upload_image  # noqa: E402
from app.meta_whatsapp import (  # noqa: E402
    parse_incoming_messages,
    send_button_message,
    send_list_message,
    send_text_message,
    verify_webhook_signature,
)
from app.models import Firma, Immobilie, Lead  # noqa: E402
from app.superadmin_auth import SuperadminAuthError  # noqa: E402
from app.supabase_auth import sign_out  # noqa: E402
from app.webhook_dedup import SeenMessageIds  # noqa: E402

_seen_message_ids = SeenMessageIds()

MAX_BILDER_PRO_INSERAT = 6


async def _upload_bilder(immobilie_id: str, files: list[UploadFile], bereits_vorhanden: int) -> list[str]:
    if bereits_vorhanden + len(files) > MAX_BILDER_PRO_INSERAT:
        raise HTTPException(
            status_code=400,
            detail=f"Maximal {MAX_BILDER_PRO_INSERAT} Bilder pro Inserat erlaubt.",
        )
    urls: list[str] = []
    for f in files:
        content = await f.read()
        try:
            urls.append(
                upload_image(
                    immobilie_id, f.filename or "bild", content, f.content_type or ""
                )
            )
        except ImageUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return urls

logger = logging.getLogger("immo_bot.webhook")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="WhatsApp Immobilien-Bot (Simulation)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def require_admin_key(x_admin_key: str = Header(default="")):
    """Schuetzt das interne Admin-/Chat-Simulator-Panel unter /admin. Ist
    ADMIN_API_KEY nicht gesetzt, bleibt der Bereich unprotected (lokales
    Entwickeln ohne Reibung) - siehe docs/launch-checkliste.md fuer den
    Hinweis, das vor einem echten Go-Live zu setzen. Das Firmen-Portal
    (/firma, /api/firma/*) ist davon unabhaengig - das laeuft ueber
    Supabase Auth."""
    expected = os.environ.get("ADMIN_API_KEY")
    if expected and x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Ungueltiger oder fehlender Admin-Key.")


ADMIN_PROTECTED = [Depends(require_admin_key)]


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/ueber-uns")
def ueber_uns():
    return FileResponse(str(STATIC_DIR / "ueber-uns.html"))


@app.get("/impressum")
def impressum():
    return FileResponse(str(STATIC_DIR / "impressum.html"))


@app.get("/agb")
def agb():
    return FileResponse(str(STATIC_DIR / "agb.html"))


@app.get("/firma")
def firma_portal():
    return FileResponse(str(STATIC_DIR / "firma.html"))


@app.get("/superadmin")
def superadmin_panel():
    # Unprotected wie /admin/firma - liefert nur HTML aus, der eigentliche
    # Schutz laeuft ueber die superadmin_session-Cookie-gesicherten API-Calls,
    # die die Seite selbst macht.
    return FileResponse(str(STATIC_DIR / "superadmin.html"))


@app.get("/admin")
def admin_panel():
    # Verschoben von "/" - die Startseite ist jetzt die oeffentliche
    # Landingpage. Die Seite selbst enthaelt keine Daten - Schutz greift
    # auf den API-Aufrufen, die chat.html macht (Browser-Navigation kann
    # keine Custom-Header mitschicken, daher kein Schutz direkt auf dieser
    # Route).
    return FileResponse(str(STATIC_DIR / "chat.html"))


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


def _process_message(telefonnummer: str, text: str) -> None:
    antworten = context.chat_service.handle_message(telefonnummer, text)
    # Wartet gerade eine Button-/Listen-Frage auf Antwort (siehe
    # app/chat_service.py: InteractivePrompt)? Dann geht die LETZTE
    # Antwort in der Liste als interaktive Nachricht raus statt als Text -
    # das ist bei jeder aktuellen Verwendung auch die eigentliche Frage.
    prompt = context.chat_service.get_session(telefonnummer).pending_interactive
    letzter_index = len(antworten) - 1
    for index, antwort in enumerate(antworten):
        try:
            if prompt is not None and index == letzter_index:
                if prompt.kind == "button":
                    send_button_message(telefonnummer, antwort, prompt.options)
                else:
                    send_list_message(
                        telefonnummer, antwort, prompt.list_button_label or "Auswaehlen", prompt.options
                    )
            else:
                send_text_message(telefonnummer, antwort)
        except Exception as exc:
            logger.exception("Antwort an %s konnte nicht gesendet werden", telefonnummer)
            context.fehlerlog_repo.add("whatsapp_send", str(exc), telefonnummer=telefonnummer)


@app.post("/webhook/whatsapp")
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(body, signature):
        raise HTTPException(status_code=401, detail="Ungueltige Signatur.")

    payload = await request.json()
    # Antwort so schnell wie moeglich bestaetigen (Verarbeitung laeuft als
    # Background-Task NACH dem Senden der Response) - sonst liefert Meta bei
    # einem langsamen Claude-/Sende-Aufruf denselben Webhook-Event erneut
    # aus. message_id-Dedup faengt zusaetzlich Zustellungen ab, die trotzdem
    # doppelt ankommen (z.B. Netzwerkprobleme auf Metas Seite).
    for telefonnummer, text, message_id in parse_incoming_messages(payload):
        if message_id and not _seen_message_ids.mark_seen(message_id):
            logger.info("Doppelte Webhook-Zustellung ignoriert: %s", message_id)
            continue
        background_tasks.add_task(_process_message, telefonnummer, text)

    return {"ok": True}


def require_firma_service():
    if context.firma_service is None:
        raise HTTPException(
            status_code=503,
            detail="Firmen-Login ist nicht konfiguriert (DATABASE_URL_RUNTIME/SUPABASE_* fehlen).",
        )
    return context.firma_service


FIRMA_COOKIE_NAME = "firma_session"


def _set_firma_cookie(response: Response, request: Request, access_token: str) -> None:
    # secure=True nur bei https - lokal (http://localhost) wuerde der Browser
    # einen Secure-Cookie sonst gar nicht erst setzen.
    response.set_cookie(
        FIRMA_COOKIE_NAME,
        access_token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def get_current_firma(
    firma_session: str = Cookie(default=""), service=Depends(require_firma_service)
) -> Firma:
    if not firma_session:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt.")
    try:
        return service.authenticate(firma_session)
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


@app.post("/api/admin/inserate/{immobilie_id}/bilder", dependencies=ADMIN_PROTECTED, response_model=Immobilie)
async def admin_upload_bilder(immobilie_id: str, files: list[UploadFile] = File(...)) -> Immobilie:
    immobilie = context.immobilien_repo.get_by_id(immobilie_id)
    if immobilie is None:
        raise HTTPException(status_code=404, detail="Inserat nicht gefunden.")

    urls = await _upload_bilder(immobilie_id, files, bereits_vorhanden=len(immobilie.bilder))
    neue_bilder = [*immobilie.bilder, *urls]
    context.immobilien_repo.set_bilder(immobilie_id, neue_bilder)
    return immobilie.model_copy(update={"bilder": neue_bilder})


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
    firma: Optional[Firma] = None
    mfa_required: bool = False
    factor_id: Optional[str] = None
    pending_token: Optional[str] = None


class FirmaMfaLoginRequest(BaseModel):
    pending_token: str
    factor_id: str
    code: str


class FirmaMfaActivateRequest(BaseModel):
    factor_id: str
    code: str


class FirmaPasswordForgotRequest(BaseModel):
    email: str


class FirmaPasswordResetRequest(BaseModel):
    recovery_token: str
    new_password: str


def _check_auth_rate_limit(email: str, request: Request) -> None:
    key = f"{email.strip().lower()}|{request.client.host if request.client else 'unknown'}"
    if not context.auth_rate_limiter.allow(key):
        raise HTTPException(
            status_code=429, detail="Zu viele Versuche. Bitte warte kurz und versuche es erneut."
        )


@app.post("/api/firma/signup", response_model=Firma)
def firma_signup(req: FirmaSignupRequest, request: Request):
    _check_auth_rate_limit(req.email, request)
    service = require_firma_service()
    try:
        return service.signup(name=req.name, email=req.email, password=req.password)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/firma/password/forgot")
def firma_password_forgot(req: FirmaPasswordForgotRequest, request: Request):
    _check_auth_rate_limit(req.email, request)
    service = require_firma_service()
    redirect_to = f"{request.url.scheme}://{request.url.netloc}/firma"
    try:
        service.request_password_reset(req.email, redirect_to=redirect_to)
    except FirmaAuthError as exc:
        # Betriebsfehler (z.B. Supabase-Rate-Limit) nicht an den Client
        # durchreichen (Anti-Enumeration) - aber fuers Fehler-Protokoll merken.
        context.fehlerlog_repo.add("firma_password_forgot", str(exc))
    # Immer dieselbe generische Antwort - egal ob die E-Mail existiert oder
    # der Versand geklappt hat (kein Account-Enumeration-Signal).
    return {"ok": True, "detail": "Falls ein Konto mit dieser E-Mail existiert, wurde eine E-Mail mit einem Link zum Zuruecksetzen verschickt."}


@app.post("/api/firma/password/reset")
def firma_password_reset(req: FirmaPasswordResetRequest):
    service = require_firma_service()
    try:
        service.reset_password(req.recovery_token, req.new_password)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/firma/login", response_model=FirmaLoginResponse)
def firma_login(req: FirmaLoginRequest, request: Request, response: Response):
    _check_auth_rate_limit(req.email, request)
    service = require_firma_service()
    try:
        result = service.login(email=req.email, password=req.password)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if result.mfa_required:
        return FirmaLoginResponse(
            mfa_required=True, factor_id=result.factor_id, pending_token=result.pending_token
        )

    _set_firma_cookie(response, request, result.access_token)
    return FirmaLoginResponse(firma=result.firma)


@app.post("/api/firma/login/mfa", response_model=FirmaLoginResponse)
def firma_login_mfa(req: FirmaMfaLoginRequest, request: Request, response: Response):
    service = require_firma_service()
    try:
        result = service.verify_login_mfa(req.pending_token, req.factor_id, req.code)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_firma_cookie(response, request, result.access_token)
    return FirmaLoginResponse(firma=result.firma)


@app.post("/api/firma/logout")
def firma_logout(response: Response, firma_session: str = Cookie(default="")):
    if firma_session:
        sign_out(firma_session)
    response.delete_cookie(FIRMA_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/firma/me", response_model=Firma)
def firma_me(firma: Firma = Depends(get_current_firma)):
    return firma


class FirmaUpdateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)


@app.patch("/api/firma/me", response_model=Firma)
def firma_update_me(req: FirmaUpdateRequest, firma: Firma = Depends(get_current_firma)):
    try:
        return context.firma_service.update_profile(firma, req.name)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/firma/mfa/enroll")
def firma_mfa_enroll(firma_session: str = Cookie(default=""), firma: Firma = Depends(get_current_firma)):
    service = require_firma_service()
    try:
        data = service.enroll_mfa(firma_session)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    totp = data.get("totp") or {}
    return {"factor_id": data.get("id"), "qr_code": totp.get("qr_code"), "secret": totp.get("secret")}


@app.post("/api/firma/mfa/activate")
def firma_mfa_activate(
    req: FirmaMfaActivateRequest,
    request: Request,
    response: Response,
    firma_session: str = Cookie(default=""),
    firma: Firma = Depends(get_current_firma),
):
    service = require_firma_service()
    try:
        new_token = service.activate_mfa(firma_session, req.factor_id, req.code)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_firma_cookie(response, request, new_token)
    return {"ok": True}


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


class InseratUpdateRequest(BaseModel):
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


@app.patch("/api/firma/inserate/{immobilie_id}", response_model=Immobilie)
def firma_update_inserat(
    immobilie_id: str, req: InseratUpdateRequest, firma: Firma = Depends(get_current_firma)
):
    try:
        return context.firma_service.update_inserat(firma, immobilie_id, req.model_dump())
    except FirmaAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/firma/inserate/{immobilie_id}/bilder", response_model=Immobilie)
async def firma_upload_bilder(
    immobilie_id: str,
    files: list[UploadFile] = File(...),
    firma: Firma = Depends(get_current_firma),
):
    eigene = context.firma_service.list_inserate(firma)
    inserat = next((i for i in eigene if i.id == immobilie_id), None)
    if inserat is None:
        raise HTTPException(status_code=404, detail="Inserat nicht gefunden oder gehoert nicht dieser Firma.")

    urls = await _upload_bilder(immobilie_id, files, bereits_vorhanden=len(inserat.bilder))
    try:
        return context.firma_service.add_bilder(firma, immobilie_id, urls)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/firma/inserate/{immobilie_id}/bilder")
def firma_remove_bild(immobilie_id: str, url: str, firma: Firma = Depends(get_current_firma)):
    try:
        return context.firma_service.remove_bild(firma, immobilie_id, url)
    except FirmaAuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/firma/leads", response_model=list[Lead])
def firma_list_leads(firma: Firma = Depends(get_current_firma)):
    return context.firma_service.list_leads(firma)


# ---------------------------------------------------------------------------
# Superadmin-Bereich: eigener Login (Supabase Auth + E-Mail-Allowlist, siehe
# app/superadmin_auth.py), Bot-Statistiken, Fehler-Protokoll, Entwickler-Chat
# (siehe app/code_assistant.py).
# ---------------------------------------------------------------------------

SUPERADMIN_COOKIE_NAME = "superadmin_session"


def _set_superadmin_cookie(response: Response, request: Request, access_token: str) -> None:
    response.set_cookie(
        SUPERADMIN_COOKIE_NAME,
        access_token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def get_current_superadmin(superadmin_session: str = Cookie(default="")) -> str:
    if not superadmin_session:
        raise HTTPException(status_code=401, detail="Nicht eingeloggt.")
    try:
        return superadmin_auth.verify_session(superadmin_session)
    except SuperadminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


SUPERADMIN_PROTECTED = [Depends(get_current_superadmin)]


class SuperadminLoginRequest(BaseModel):
    email: str
    password: str


class SuperadminLoginResponse(BaseModel):
    email: Optional[str] = None
    mfa_required: bool = False
    factor_id: Optional[str] = None
    pending_token: Optional[str] = None


class SuperadminMfaLoginRequest(BaseModel):
    pending_token: str
    factor_id: str
    code: str


class SuperadminMfaActivateRequest(BaseModel):
    factor_id: str
    code: str


def _check_superadmin_rate_limit(email: str, request: Request) -> None:
    key = f"{email.strip().lower()}|{request.client.host if request.client else 'unknown'}"
    if not context.superadmin_rate_limiter.allow(key):
        raise HTTPException(
            status_code=429, detail="Zu viele Versuche. Bitte warte kurz und versuche es erneut."
        )


@app.post("/api/superadmin/login", response_model=SuperadminLoginResponse)
def superadmin_login(req: SuperadminLoginRequest, request: Request, response: Response):
    _check_superadmin_rate_limit(req.email, request)
    try:
        result = superadmin_auth.login(req.email, req.password)
    except SuperadminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if result.mfa_required:
        return SuperadminLoginResponse(
            mfa_required=True, factor_id=result.factor_id, pending_token=result.pending_token
        )
    _set_superadmin_cookie(response, request, result.access_token)
    return SuperadminLoginResponse(email=result.email)


@app.post("/api/superadmin/login/mfa", response_model=SuperadminLoginResponse)
def superadmin_login_mfa(req: SuperadminMfaLoginRequest, request: Request, response: Response):
    try:
        result = superadmin_auth.verify_login_mfa(req.pending_token, req.factor_id, req.code)
    except SuperadminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_superadmin_cookie(response, request, result.access_token)
    return SuperadminLoginResponse(email=result.email)


@app.post("/api/superadmin/logout")
def superadmin_logout(response: Response, superadmin_session: str = Cookie(default="")):
    if superadmin_session:
        sign_out(superadmin_session)
    response.delete_cookie(SUPERADMIN_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/superadmin/me")
def superadmin_me(email: str = Depends(get_current_superadmin)):
    return {"email": email}


@app.post("/api/superadmin/mfa/enroll", dependencies=SUPERADMIN_PROTECTED)
def superadmin_mfa_enroll(superadmin_session: str = Cookie(default="")):
    try:
        data = superadmin_auth.enroll_mfa(superadmin_session)
    except SuperadminAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    totp = data.get("totp") or {}
    return {"factor_id": data.get("id"), "qr_code": totp.get("qr_code"), "secret": totp.get("secret")}


@app.post("/api/superadmin/mfa/activate")
def superadmin_mfa_activate(
    req: SuperadminMfaActivateRequest,
    request: Request,
    response: Response,
    superadmin_session: str = Cookie(default=""),
    email: str = Depends(get_current_superadmin),
):
    try:
        new_token = superadmin_auth.activate_mfa(superadmin_session, req.factor_id, req.code)
    except SuperadminAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_superadmin_cookie(response, request, new_token)
    return {"ok": True}


@app.get("/api/superadmin/stats", dependencies=SUPERADMIN_PROTECTED)
def superadmin_stats() -> dict:
    now = datetime.now(timezone.utc)
    fehler = context.fehlerlog_repo.get_recent(500)
    fehler_24h = sum(1 for f in fehler if f.erstellt_am >= now - timedelta(hours=24))
    return {
        "gesamt_kontakte": context.chatkontakt_repo.count_all(),
        "aktive_jetzt": context.chatkontakt_repo.count_active_since(now - timedelta(minutes=5)),
        "aktive_heute": context.chatkontakt_repo.count_active_since(now - timedelta(hours=24)),
        "gesamt_firmen": len(context.firma_repo.get_all()),
        "aktive_inserate": len(context.immobilien_repo.get_by_status("aktiv")),
        "fehler_24h": fehler_24h,
    }


@app.get("/api/superadmin/fehler", dependencies=SUPERADMIN_PROTECTED)
def superadmin_fehler(limit: int = 200) -> list[dict]:
    return [
        {
            "quelle": f.quelle,
            "meldung": f.meldung,
            "telefonnummer": f.telefonnummer,
            "erstellt_am": f.erstellt_am.isoformat(),
        }
        for f in context.fehlerlog_repo.get_recent(limit)
    ]


# ---------------------------------------------------------------------------
# Entwickler-Chat (siehe app/code_assistant.py) - EIN gemeinsamer, laufender
# Chat fuer alle Superadmins. Claude liest/schreibt Code selbst, Tests
# entscheiden ueber "push-bereit", Push bleibt ein eigener, expliziter Schritt.
# ---------------------------------------------------------------------------


class SuperadminChatSendRequest(BaseModel):
    message: str


class SuperadminChatStateResponse(BaseModel):
    display_messages: list[dict]
    diff: str
    files_changed: list[str]
    push_allowed: bool
    test_output: str


class SuperadminChatSendResponse(SuperadminChatStateResponse):
    reply: str


class SuperadminChatPushRequest(BaseModel):
    commit_message: str


@app.get(
    "/api/superadmin/chat/state",
    dependencies=SUPERADMIN_PROTECTED,
    response_model=SuperadminChatStateResponse,
)
def superadmin_chat_state() -> SuperadminChatStateResponse:
    return SuperadminChatStateResponse(**code_assistant.get_state())


@app.post(
    "/api/superadmin/chat/send",
    dependencies=SUPERADMIN_PROTECTED,
    response_model=SuperadminChatSendResponse,
)
def superadmin_chat_send(req: SuperadminChatSendRequest) -> SuperadminChatSendResponse:
    try:
        result = code_assistant.send_message(req.message)
    except CodeAssistantConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SuperadminChatSendResponse(**result)


@app.post("/api/superadmin/chat/push", dependencies=SUPERADMIN_PROTECTED)
def superadmin_chat_push(req: SuperadminChatPushRequest) -> dict:
    try:
        return code_assistant.push_current(req.commit_message)
    except CodeAssistantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodeAssistantConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/superadmin/chat/reset", dependencies=SUPERADMIN_PROTECTED)
def superadmin_chat_reset() -> dict:
    code_assistant.reset_chat()
    return {"ok": True}
