import hashlib
import hmac

import httpx
import pytest

from app.meta_whatsapp import (
    MetaWhatsAppSendError,
    parse_incoming_messages,
    send_button_message,
    send_image_message,
    send_list_message,
    verify_webhook_signature,
)


def test_verify_webhook_signature_accepts_correct_signature(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    payload = b'{"hello": "world"}'
    signature = "sha256=" + hmac.new(b"test-secret", payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, signature) is True


def test_verify_webhook_signature_rejects_wrong_signature(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    payload = b'{"hello": "world"}'

    assert verify_webhook_signature(payload, "sha256=deadbeef") is False


def test_verify_webhook_signature_rejects_when_secret_missing(monkeypatch):
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    payload = b'{"hello": "world"}'
    signature = "sha256=" + hmac.new(b"whatever", payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, signature) is False


def test_verify_webhook_signature_rejects_malformed_header(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    assert verify_webhook_signature(b"x", "") is False
    assert verify_webhook_signature(b"x", "not-sha256-prefixed") is False


def test_parse_incoming_messages_extracts_text_messages():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "41791234567",
                                    "type": "text",
                                    "text": {"body": "Hallo"},
                                    "id": "wamid.abc123",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    result = parse_incoming_messages(payload)
    assert result == [("+41791234567", "Hallo", "wamid.abc123")]


def test_parse_incoming_messages_ignores_non_text_and_status_updates():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [{"id": "abc", "status": "delivered"}],
                            "messages": [
                                {"from": "41791234567", "type": "image", "image": {"id": "x"}}
                            ],
                        }
                    }
                ]
            }
        ]
    }
    assert parse_incoming_messages(payload) == []


def test_parse_incoming_messages_handles_empty_payload():
    assert parse_incoming_messages({}) == []


def test_parse_incoming_messages_extracts_button_reply_title():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "41791234567",
                                    "type": "interactive",
                                    "id": "wamid.btn1",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "vermieter", "title": "Vermieter"},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    result = parse_incoming_messages(payload)
    assert result == [("+41791234567", "Vermieter", "wamid.btn1")]


def test_parse_incoming_messages_extracts_list_reply_title():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "41791234567",
                                    "type": "interactive",
                                    "id": "wamid.list1",
                                    "interactive": {
                                        "type": "list_reply",
                                        "list_reply": {"id": "egal", "title": "Egal"},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    result = parse_incoming_messages(payload)
    assert result == [("+41791234567", "Egal", "wamid.list1")]


def test_send_image_message_posts_correct_payload(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"messages": [{"id": "wamid.abc"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    send_image_message("+41791234567", "https://example.com/bild.jpg", "Schoene Wohnung")

    assert "12345" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["to"] == "41791234567"
    assert captured["json"]["type"] == "image"
    assert captured["json"]["image"] == {"link": "https://example.com/bild.jpg", "caption": "Schoene Wohnung"}


def test_send_image_message_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(400, json={"error": "bad"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(MetaWhatsAppSendError):
        send_image_message("+41791234567", "https://example.com/bild.jpg")


def test_send_button_message_posts_correct_payload(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"messages": [{"id": "wamid.abc"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    send_button_message(
        "+41791234567", "Bist du Vermieter oder Mieter?", [("vermieter", "Vermieter"), ("mieter", "Mieter")]
    )

    assert "12345" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["to"] == "41791234567"
    assert captured["json"]["type"] == "interactive"
    assert captured["json"]["interactive"]["type"] == "button"
    assert captured["json"]["interactive"]["body"] == {"text": "Bist du Vermieter oder Mieter?"}
    assert captured["json"]["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "vermieter", "title": "Vermieter"}},
        {"type": "reply", "reply": {"id": "mieter", "title": "Mieter"}},
    ]


def test_send_button_message_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(400, json={"error": "bad"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(MetaWhatsAppSendError):
        send_button_message("+41791234567", "Frage?", [("ja", "Ja"), ("nein", "Nein")])


def test_send_list_message_posts_correct_payload(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"messages": [{"id": "wamid.abc"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    send_list_message(
        "+41791234567",
        "Wie viele Zimmer suchst du mindestens?",
        "Zimmer waehlen",
        [("1", "1"), ("2", "2"), ("egal", "Egal")],
    )

    assert "12345" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["to"] == "41791234567"
    assert captured["json"]["type"] == "interactive"
    assert captured["json"]["interactive"]["type"] == "list"
    assert captured["json"]["interactive"]["body"] == {"text": "Wie viele Zimmer suchst du mindestens?"}
    assert captured["json"]["interactive"]["action"]["button"] == "Zimmer waehlen"
    assert captured["json"]["interactive"]["action"]["sections"] == [
        {"rows": [{"id": "1", "title": "1"}, {"id": "2", "title": "2"}, {"id": "egal", "title": "Egal"}]}
    ]


def test_send_list_message_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "12345")

    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(400, json={"error": "bad"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(MetaWhatsAppSendError):
        send_list_message("+41791234567", "Frage?", "Auswaehlen", [("a", "A")])
