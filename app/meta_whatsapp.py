"""Anbindung an die Meta WhatsApp Cloud API (direkt, kein BSP).

Abweichung von der urspruenglichen Chef-Spezifikation (Abschnitt 4.7 sah
einen Business Solution Provider wie Twilio/360dialog vor) - siehe
docs/produkt-abgleich.md fuer die Begruendung (Meta Business Account war
bereits vorhanden, direkte Anbindung ist guenstiger und nicht wesentlich
aufwaendiger).

Drei Bausteine:
- send_text_message(): ausgehende Nachrichten senden.
- verify_webhook_signature(): prueft, dass ein Webhook-Aufruf wirklich von
  Meta kommt (HMAC-SHA256 mit dem App-Secret) - ohne das koennte jeder
  gefaelschte "eingehende Nachrichten" an unseren Webhook schicken.
- parse_incoming_messages(): extrahiert (telefonnummer, text)-Paare aus
  einem Meta-Webhook-Payload.
"""
from __future__ import annotations

import hashlib
import hmac
import os

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


def verify_webhook_signature(payload: bytes, signature_header: str) -> bool:
    app_secret = os.environ.get("META_APP_SECRET")
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def parse_incoming_messages(payload: dict) -> list[tuple[str, str]]:
    """Ignoriert Statusupdates (delivered/read) und Nicht-Text-Nachrichten
    (Bilder, Standort etc. - vorerst nicht unterstuetzt)."""
    results = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                if message.get("type") == "text":
                    telefonnummer = "+" + message["from"].lstrip("+")
                    text = message.get("text", {}).get("body", "")
                    if text:
                        results.append((telefonnummer, text))
    return results
