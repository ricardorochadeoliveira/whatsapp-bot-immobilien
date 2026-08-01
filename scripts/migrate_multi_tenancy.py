"""Einmalige Migration: Multi-Tenancy (firma_id) + RLS auf Supabase einrichten.

Laeuft ueber die postgres-Superuser-Verbindung (DATABASE_URL) - notwendig,
um Rollen anzulegen und RLS zu aktivieren. Die App selbst soll spaeter NICHT
mehr ueber diese Superuser-Rolle laufen, sondern ueber die hier erzeugte
eingeschraenkte Rolle 'app_runtime' (siehe DATABASE_URL_RUNTIME in .env).

Idempotent: kann mehrfach ausgefuehrt werden, ohne Fehler bei bereits
vorhandenen Objekten.
"""
from __future__ import annotations

import secrets

from dotenv import load_dotenv, set_key

load_dotenv(override=True)

from app.db import get_database_url, get_engine

ENV_PATH = ".env"
RUNTIME_ROLE = "app_runtime"

DDL_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS firma (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    auth_user_id VARCHAR(64) UNIQUE,
    gruendungsmitglied BOOLEAN NOT NULL DEFAULT false,
    erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lead (
    id VARCHAR(32) PRIMARY KEY,
    immobilie_id VARCHAR(32) NOT NULL REFERENCES immobilie(id),
    firma_id VARCHAR(32) NOT NULL REFERENCES firma(id),
    suchprofil_id VARCHAR(32) REFERENCES suchprofil(id),
    status VARCHAR(16) NOT NULL DEFAULT 'neu',
    erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DDL_ALTER_EXISTING = """
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS firma_id VARCHAR(32) REFERENCES firma(id);
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS beschreibung TEXT;
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS typ VARCHAR(16) NOT NULL DEFAULT 'miete';
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS hat_garten BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE immobilie ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'aktiv';

ALTER TABLE kunde ADD COLUMN IF NOT EXISTS firma_id VARCHAR(32) REFERENCES firma(id);
ALTER TABLE kunde DROP CONSTRAINT IF EXISTS kunde_telefonnummer_key;
ALTER TABLE kunde DROP CONSTRAINT IF EXISTS uq_kunde_phone_firma;
ALTER TABLE kunde ADD CONSTRAINT uq_kunde_phone_firma UNIQUE (telefonnummer, firma_id);

ALTER TABLE suchprofil ADD COLUMN IF NOT EXISTS firma_id VARCHAR(32) REFERENCES firma(id);
ALTER TABLE suchprofil ADD COLUMN IF NOT EXISTS typ VARCHAR(16);
ALTER TABLE suchprofil ADD COLUMN IF NOT EXISTS zusatzfilter JSONB;

ALTER TABLE match_log ADD COLUMN IF NOT EXISTS firma_id VARCHAR(32) REFERENCES firma(id);
"""

TENANT_TABLES = ["immobilie", "kunde", "suchprofil", "match_log", "lead"]


def sql_role_and_grants(password: str) -> str:
    return f"""
DO $do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
        CREATE ROLE {RUNTIME_ROLE} LOGIN PASSWORD '{password}'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    ELSE
        ALTER ROLE {RUNTIME_ROLE} WITH PASSWORD '{password}';
    END IF;
END
$do$;

GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON firma, kunde, suchprofil, immobilie, match_log, lead
    TO {RUNTIME_ROLE};
"""


def sql_rls() -> str:
    statements = []
    for table in TENANT_TABLES + ["firma"]:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")

    for table in TENANT_TABLES:
        statements.append(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        statements.append(
            f"""CREATE POLICY tenant_isolation ON {table}
                USING (firma_id = current_setting('app.current_firma_id', true))
                WITH CHECK (firma_id = current_setting('app.current_firma_id', true));"""
        )

    # firma-Tabelle: eine Firma sieht/aendert nur ihre eigene Zeile. Ueber
    # auth_user_id (nicht firma_id!), weil firma_id beim allerersten Login-
    # Lookup (nach Supabase-Auth-Verifikation, vor dem Nachschlagen der
    # eigenen firma_id) noch nicht bekannt ist - auth_user_id dagegen schon.
    statements.append("DROP POLICY IF EXISTS self_only ON firma;")
    statements.append(
        """CREATE POLICY self_only ON firma
            USING (auth_user_id = current_setting('app.current_auth_user_id', true))
            WITH CHECK (auth_user_id = current_setting('app.current_auth_user_id', true));"""
    )
    return "\n".join(statements)


def build_runtime_url(superuser_url: str, password: str) -> str:
    """Leitet aus der postgres-Superuser-URL dieselbe Verbindung fuer die
    eingeschraenkte Rolle ab (gleicher Host/Pooler, anderer User/Passwort)."""
    scheme, rest = superuser_url.split("://", 1)
    userinfo, hostpart = rest.split("@", 1)
    user = userinfo.split(":", 1)[0]

    if "." in user:
        # Session-Pooler-Format: <rolle>.<project-ref>
        project_ref = user.split(".", 1)[1]
        new_user = f"{RUNTIME_ROLE}.{project_ref}"
    else:
        new_user = RUNTIME_ROLE

    return f"{scheme}://{new_user}:{password}@{hostpart}"


def main() -> None:
    engine = get_engine()
    password = secrets.token_urlsafe(24)

    with engine.begin() as conn:
        conn.exec_driver_sql(DDL_NEW_TABLES)
        conn.exec_driver_sql(DDL_ALTER_EXISTING)
        conn.exec_driver_sql(sql_role_and_grants(password))
        conn.exec_driver_sql(sql_rls())

    superuser_url = get_database_url()
    runtime_url = build_runtime_url(superuser_url, password).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    set_key(ENV_PATH, "DATABASE_URL_RUNTIME", runtime_url)

    print("Migration abgeschlossen.")
    print(f"Neue eingeschraenkte Rolle: {RUNTIME_ROLE}")
    print("DATABASE_URL_RUNTIME wurde in .env gespeichert (noch nicht von der App verwendet).")


if __name__ == "__main__":
    main()
