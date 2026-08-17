"""Bild-Upload fuer Inserate ueber Supabase Storage (siehe
docs/produkt-abgleich.md). Nutzt bewusst nur die REST-API via httpx statt
eines eigenen Supabase-Python-Pakets - gleiches Muster wie
app/supabase_auth.py, keine neue Abhaengigkeit noetig.

Der Bucket `immobilien-bilder` muss vorher einmalig existieren, siehe
scripts/setup_storage_bucket.py.
"""
from __future__ import annotations

import os
import uuid

import httpx

BUCKET = "immobilien-bilder"
MAX_BYTES = 5 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ImageUploadError(RuntimeError):
    """Bild-Upload ist fehlgeschlagen (ungueltiger Typ, zu gross, oder
    Supabase-Storage-Fehler)."""


def _config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not service_role_key:
        raise ImageUploadError(
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY sind nicht gesetzt (siehe .env.example)."
        )
    return url, service_role_key


def _safe_filename(filename: str) -> str:
    base = os.path.basename(filename or "bild")
    return "".join(c for c in base if c.isalnum() or c in ".-_") or "bild"


def upload_image(immobilie_id: str, filename: str, content: bytes, content_type: str) -> str:
    """Laedt ein Bild hoch und gibt die oeffentliche URL zurueck."""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ImageUploadError(
            f"Ungueltiger Bildtyp '{content_type}'. Erlaubt: JPEG, PNG, WebP."
        )
    if len(content) > MAX_BYTES:
        raise ImageUploadError("Bild ist zu gross (max. 5 MB).")

    url, service_role_key = _config()
    path = f"{immobilie_id}/{uuid.uuid4().hex}_{_safe_filename(filename)}"

    try:
        resp = httpx.post(
            f"{url}/storage/v1/object/{BUCKET}/{path}",
            headers={
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": content_type,
            },
            content=content,
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ImageUploadError(
            f"Supabase Storage lehnte den Upload ab ({exc.response.status_code}): {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ImageUploadError(f"Supabase Storage nicht erreichbar: {exc}") from exc

    return f"{url}/storage/v1/object/public/{BUCKET}/{path}"
