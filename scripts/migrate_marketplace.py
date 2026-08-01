"""Migration fuer den Marktplatz-Pivot (siehe docs/produkt-abgleich.md,
Abschnitt "Marktplatz-Pivot"): Firma kann jetzt auch eine Privatperson sein
(email optional, telefonnummer + typ neu).

Laeuft ueber die postgres-Superuser-Verbindung (DATABASE_URL). Idempotent.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv(override=True)

from app.db import get_engine

DDL = """
ALTER TABLE firma ALTER COLUMN email DROP NOT NULL;
ALTER TABLE firma ADD COLUMN IF NOT EXISTS telefonnummer VARCHAR(32);
ALTER TABLE firma ADD COLUMN IF NOT EXISTS typ VARCHAR(16) NOT NULL DEFAULT 'firma';
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_constraint WHERE conname = 'firma_telefonnummer_key'
    ) THEN
        ALTER TABLE firma ADD CONSTRAINT firma_telefonnummer_key UNIQUE (telefonnummer);
    END IF;
END
$do$;
"""


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(DDL)
    print("Migration abgeschlossen: firma.email optional, telefonnummer + typ ergaenzt.")


if __name__ == "__main__":
    main()
