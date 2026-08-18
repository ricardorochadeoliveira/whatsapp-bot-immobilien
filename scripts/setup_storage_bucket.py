"""Einmalig auszufuehren: legt den oeffentlichen Supabase-Storage-Bucket fuer
Inserat-Bilder an (siehe app/image_storage.py, docs/produkt-abgleich.md).
Idempotent - kann gefahrlos mehrfach laufen.
"""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

BUCKET = "immobilien-bilder"


def main() -> None:
    url = os.environ["SUPABASE_URL"]
    service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    resp = httpx.post(
        f"{url}/storage/v1/bucket",
        headers={"Authorization": f"Bearer {service_role_key}"},
        json={"id": BUCKET, "name": BUCKET, "public": True},
        timeout=15,
    )
    if resp.status_code < 300:
        print(f"Bucket '{BUCKET}' angelegt.")
    elif "already exists" in resp.text.lower() or "duplicate" in resp.text.lower():
        print(f"Bucket '{BUCKET}' existiert bereits - nichts zu tun.")
    else:
        resp.raise_for_status()

    # Serverseitiger Backstop fuer den direkten Browser-Upload des neuen
    # Hostpoint-Portals (dort validiert nur noch Client-JS Typ/Groesse,
    # das ist umgehbar - siehe docs/produkt-abgleich.md).
    update_resp = httpx.put(
        f"{url}/storage/v1/bucket/{BUCKET}",
        headers={"Authorization": f"Bearer {service_role_key}"},
        json={
            "public": True,
            "file_size_limit": "5MB",
            "allowed_mime_types": ["image/jpeg", "image/png", "image/webp"],
        },
        timeout=15,
    )
    update_resp.raise_for_status()
    print(f"Bucket '{BUCKET}': file_size_limit=5MB, allowed_mime_types gesetzt.")


if __name__ == "__main__":
    main()
