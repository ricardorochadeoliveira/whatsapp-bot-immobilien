"""Anbindung an die Meta WhatsApp Cloud API (direkt, kein BSP).

Abweichung von der urspruenglichen Chef-Spezifikation (Abschnitt 4.7 sah
einen Business Solution Provider wie Twilio/360dialog vor) - siehe
docs/produkt-abgleich.md fuer die Begruendung (Meta Business Account war
bereits vorhanden, direkte Anbindung ist guenstiger und nicht wesentlich
aufwaendiger).

Bausteine:
- send_text_message()/send_image_message()/send_button_message()/
  send_list_message(): ausgehende Nachrichten senden (Text, Bild, Reply-
  Buttons, List Message).
- verify_webhook_signature(): prueft, dass ein Webhook-Aufruf wirklich von
  Meta kommt (HMAC-SHA256 mit dem App-Secret) - ohne das koennte jeder
  gefaelschte "eingehende Nachrichten" an unseren Webhook schicken.
- parse_incoming_messages(): extrahiert (telefonnummer, text, message_id)-
  Tupel aus einem Meta-Webhook-Payload (Text- und Interactive-Antworten).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

import httpx

GRAPH_API_VERSION = "v21.0"


class MetaWhatsAppConfigError(RuntimeError):
    """META_ACCESS_TOKEN/META_PHONE_NUMBER_ID sind nicht gesetzt."""


class MetaWhatsAppSendError(RuntimeError):
    """Senden ueber die Meta-API ist fehlgeschlagen (z.B. 24h-Fenster
    abgelaufen und kein genehmigtes Template verwendet - siehe
    docs/launch-checkliste.md)."""


def _config() -> tuple[str, str]:
    token = os.environ.get("META_ACCESS_TOKEN")
    phone_number_id = os.environ.get("META_PHONE_NUMBER_ID")
    if not token or not phone_number_id:
        raise MetaWhatsAppConfigError(
            "META_ACCESS_TOKEN/META_PHONE_NUMBER_ID sind nicht gesetzt."
        )
    return token, phone_number_id


def is_configured() -> bool:
    return bool(os.environ.get("META_ACCESS_TOKEN") and os.environ.get("META_PHONE_NUMBER_ID"))


def send_text_message(to: str, text: str) -> None:
    """to: Telefonnummer inkl. '+' (unser internes Format) - wird fuer die
    Meta-API automatisch ohne '+' gesendet."""
    token, phone_number_id = _config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "text",
                "text": {"body": text},
            },
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MetaWhatsAppSendError(
            f"Meta-API lehnte Nachricht ab ({exc.response.status_code}): {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise MetaWhatsAppSendError(f"Meta-API nicht erreichbar: {exc}") from exc


def send_image_message(to: str, image_url: str, caption: Optional[str] = None) -> None:
    """Sendet ein Bild per oeffentlicher URL - Meta laedt es selbst herunter,
    kein separater Media-Upload zu Meta noetig, da unsere Bilder bereits
    oeffentlich auf Supabase Storage liegen."""
    token, phone_number_id = _config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    image_payload: dict = {"link": image_url}
    if caption:
        image_payload["caption"] = caption
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "image",
                "image": image_payload,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MetaWhatsAppSendError(
            f"Meta-API lehnte Bild ab ({exc.response.status_code}): {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise MetaWhatsAppSendError(f"Meta-API nicht erreichbar: {exc}") from exc


def send_button_message(to: str, body_text: str, options: list[tuple[str, str]]) -> None:
    """Sendet eine WhatsApp Reply-Buttons-Nachricht. options: Liste von
    (id, title) - max. 3 (Meta-Limit fuer Reply Buttons)."""
    token, phone_number_id = _config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    buttons = [
        {"type": "reply", "reply": {"id": option_id, "title": title}}
        for option_id, title in options
    ]
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text},
                    "action": {"buttons": buttons},
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MetaWhatsAppSendError(
            f"Meta-API lehnte Nachricht ab ({exc.response.status_code}): {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise MetaWhatsAppSendError(f"Meta-API nicht erreichbar: {exc}") from exc


def send_list_message(to: str, body_text: str, button_label: str, options: list[tuple[str, str]]) -> None:
    """Sendet eine WhatsApp List-Message. options: Liste von (id, title) -
    max. 10 Zeilen (Meta-Limit fuer List Messages)."""
    token, phone_number_id = _config()
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    rows = [{"id": option_id, "title": title} for option_id, title in options]
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": body_text},
                    "action": {"button": button_label, "sections": [{"rows": rows}]},
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MetaWhatsAppSendError(
            f"Meta-API lehnte Nachricht ab ({exc.response.status_code}): {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise MetaWhatsAppSendError(f"Meta-API nicht erreichbar: {exc}") from exc


def verify_webhook_signature(payload: bytes, signature_header: str) -> bool:
    app_secret = os.environ.get("META_APP_SECRET")
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def parse_incoming_messages(payload: dict) -> list[tuple[str, str, str]]:
    """Ignoriert Statusupdates (delivered/read) und Nicht-Text/-Interactive-
    Nachrichten (Bilder, Standort etc. - vorerst nicht unterstuetzt).

    Ein Tap auf einen Button/eine Listen-Zeile (type == "interactive") wird
    wie eine getippte Freitext-Antwort behandelt: der Titel der angetippten
    Option wird als Text zurueckgegeben (nicht die id), damit
    ChatService.handle_message keinen Unterschied zwischen Tippen und
    Antippen macht (z.B. "Egal" antippen == "Egal" tippen).

    Gibt zusaetzlich Metas Nachrichten-ID (wamid) mit aus, damit der Aufrufer
    Wiederholungszustellungen erkennen kann (siehe app/webhook_dedup.py)."""
    results = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_id = message.get("id", "")
                if message.get("type") == "text":
                    telefonnummer = "+" + message["from"].lstrip("+")
                    text = message.get("text", {}).get("body", "")
                    if text:
                        results.append((telefonnummer, text, message_id))
                elif message.get("type") == "interactive":
                    interactive = message.get("interactive", {})
                    reply = interactive.get("button_reply") or interactive.get("list_reply")
                    title = reply.get("title") if reply else None
                    if title:
                        telefonnummer = "+" + message["from"].lstrip("+")
                        results.append((telefonnummer, title, message_id))
    return results
