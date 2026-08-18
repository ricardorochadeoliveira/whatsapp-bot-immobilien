// Projekt-URL + Anon-Key fuer den direkten Browser-zu-Supabase-Zugriff des
// Firmen-Portals. Der Anon-Key ist bewusst oeffentlich - er identifiziert
// nur das Projekt und die unauthentifizierte Rolle, gewaehrt aber keinerlei
// Zugriff auf eigene Faust. Jede Tabellen-/Zeilen-Berechtigung wird
// ausschliesslich durch Postgres Row-Level-Security durchgesetzt (siehe
// scripts/migrate_rls_native_auth.py) - niemals den Service-Role-Key hier
// eintragen, der wuerde RLS vollstaendig umgehen.
const SUPABASE_URL = 'https://wbzpdklukvmibzqlcotw.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndienBka2x1a3ZtaWJ6cWxjb3R3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODUxMzYxNTksImV4cCI6MjEwMDcxMjE1OX0.tLwtz9g6SF81_HUDkHKK1Uo_T2Q3I5YXSDWzOI7BtP4';
