"""Einmalige Migration: zusaetzliche RLS-Policies fuer den direkten
Browser-zu-Supabase-Zugriff (neues Hostpoint-Portal, siehe
docs/produkt-abgleich.md).

WICHTIG: Diese Policies werden ADDITIV angelegt (neue Namen: *_native), NICHT
als Ersatz fuer die bestehenden `tenant_isolation`/`self_only`-Policies aus
scripts/migrate_multi_tenancy.py. Die alten Policies pruefen
current_setting('app.current_firma_id'/'app.current_auth_user_id') - das
setzt ausschliesslich unser altes Backend per SET LOCAL (app/db.py:
tenant_session()). Die neuen Policies pruefen auth.uid() - das setzt
ausschliesslich Supabases PostgREST anhand eines echten, verifizierten JWTs.
Beide Mechanismen sind unabhaengig voneinander und liefern fuer die jeweils
andere Zugriffsart NULL/false - Postgres kombiniert mehrere permissive
Policies mit OR, daher koennen alte und neue Policy gleichzeitig aktiv sein,
ohne sich gegenseitig zu stoeren. Das alte Firmen-Portal (Railway) bleibt
dadurch unveraendert funktionsfaehig, waehrend das neue (Hostpoint,
Supabase-direkt) parallel Zugriff bekommt.

Laeuft ueber die Superuser-Verbindung (DATABASE_URL). Idempotent.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv(override=True)

from app.db import get_engine

TENANT_TABLES = ["immobilie", "kunde", "suchprofil", "match_log", "lead"]

SQL_FUNCTIONS = """
CREATE OR REPLACE FUNCTION public.current_firma_id()
RETURNS varchar
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = ''
AS $$
  SELECT id FROM public.firma WHERE auth_user_id = auth.uid()::text;
$$;

CREATE OR REPLACE FUNCTION public.mfa_ok()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = ''
AS $$
  SELECT
    COALESCE((SELECT auth.jwt() ->> 'aal'), 'aal1') = 'aal2'
    OR NOT EXISTS (
      SELECT 1 FROM auth.mfa_factors
      WHERE user_id = auth.uid() AND status = 'verified'
    );
$$;

REVOKE ALL ON FUNCTION public.current_firma_id() FROM public;
REVOKE ALL ON FUNCTION public.mfa_ok() FROM public;
GRANT EXECUTE ON FUNCTION public.current_firma_id() TO authenticated;
GRANT EXECUTE ON FUNCTION public.mfa_ok() TO authenticated;

GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON firma, kunde, suchprofil, immobilie, match_log, lead
    TO authenticated;
"""

SQL_STORAGE_POLICIES = """
DROP POLICY IF EXISTS "firma upload own bilder" ON storage.objects;
CREATE POLICY "firma upload own bilder" ON storage.objects
    FOR INSERT TO authenticated
    WITH CHECK (
        bucket_id = 'immobilien-bilder'
        AND (storage.foldername(name))[1] = public.current_firma_id()
        AND public.mfa_ok()
    );

DROP POLICY IF EXISTS "firma delete own bilder" ON storage.objects;
CREATE POLICY "firma delete own bilder" ON storage.objects
    FOR DELETE TO authenticated
    USING (
        bucket_id = 'immobilien-bilder'
        AND (storage.foldername(name))[1] = public.current_firma_id()
        AND public.mfa_ok()
    );
"""


def sql_tenant_policies() -> str:
    # WICHTIG: "TO authenticated" ist hier zwingend. Ohne explizite TO-Klausel
    # gilt eine Policy fuer ALLE Rollen (PUBLIC) - Postgres wertet dann bei
    # JEDER Abfrage (auch vom alten app_runtime-Pfad) automatisch auch diese
    # Policy aus (permissive Policies werden mit OR kombiniert), was hier
    # einen Aufruf von mfa_ok()/current_firma_id() erzwingen wuerde. Da
    # app_runtime kein EXECUTE auf diese Funktionen hat, wuerde das den
    # kompletten alten Login-Pfad mit "permission denied for function
    # mfa_ok" zum Absturz bringen - genau das darf laut Sequenzierung nicht
    # passieren. Mit "TO authenticated" wertet Postgres diese Policy fuer
    # app_runtime-Abfragen gar nicht erst aus.
    statements = []
    for table in TENANT_TABLES:
        statements.append(f'DROP POLICY IF EXISTS tenant_isolation_native ON "{table}";')
        statements.append(
            f"""CREATE POLICY tenant_isolation_native ON "{table}"
                TO authenticated
                USING (firma_id = public.current_firma_id() AND public.mfa_ok())
                WITH CHECK (firma_id = public.current_firma_id() AND public.mfa_ok());"""
        )

    statements.append("DROP POLICY IF EXISTS self_only_native ON firma;")
    statements.append(
        """CREATE POLICY self_only_native ON firma
            TO authenticated
            USING (auth_user_id = auth.uid()::text AND public.mfa_ok())
            WITH CHECK (auth_user_id = auth.uid()::text AND public.mfa_ok());"""
    )
    return "\n".join(statements)


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(SQL_FUNCTIONS)
        conn.exec_driver_sql(sql_tenant_policies())
        conn.exec_driver_sql(SQL_STORAGE_POLICIES)

    print("Migration abgeschlossen: current_firma_id()/mfa_ok() + *_native-Policies angelegt.")
    print("Alte Policies (tenant_isolation/self_only) unveraendert - altes Portal bleibt funktionsfaehig.")


if __name__ == "__main__":
    main()
