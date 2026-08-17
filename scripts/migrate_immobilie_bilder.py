"""Migration fuer Bild-Upload: ersetzt das einzelne Pflichtfeld
`immobilie.bild_url` durch eine Liste `immobilie.bilder` (JSONB-Array), damit
Vermieter mehrere Fotos pro Inserat hochladen koennen (siehe
docs/produkt-abgleich.md).

Laeuft ueber die postgres-Superuser-Verbindung (DATABASE_URL). Idempotent.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv(override=True)

from app.db import get_engine

DDL = """
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS bilder JSONB NOT NULL DEFAULT '[]';
UPDATE immobilie SET bilder = jsonb_build_array(bild_url)
    WHERE bild_url IS NOT NULL AND bilder = '[]';
ALTER TABLE immobilie DROP COLUMN IF EXISTS bild_url;
"""


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(DDL)
    print("Migration abgeschlossen: immobilie.bild_url -> immobilie.bilder (JSONB-Array).")


if __name__ == "__main__":
    main()
