"""Einmalig auszufuehren: legt einen Supabase-Auth-Nutzer fuer den
Superadmin-Bereich an - OHNE (wie app/firma_service.py:FirmaService.signup)
eine Firma-Zeile zu erzeugen, da dieser Login unabhaengig vom Firmenportal
ist (siehe app/superadmin_auth.py).

Aufruf:
    python scripts/create_superadmin.py <email> <passwort>

Danach:
1. Bestaetigungsmail bestaetigen (laeuft ueber das bereits eingerichtete
   Resend-SMTP + "Confirm signup"-Template).
2. SUPERADMIN_EMAILS auf Railway auf genau diese E-Mail setzen (mehrere
   E-Mails kommagetrennt moeglich).
3. Danach unter /superadmin einloggen - MFA direkt im Dashboard aktivieren
   wird angesichts der Reichweite des Code-Editors empfohlen.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv(override=True)

from app.password_policy import WeakPasswordError, validate_password_strength  # noqa: E402
from app.supabase_auth import SupabaseAuthError, sign_up  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("Aufruf: python scripts/create_superadmin.py <email> <passwort>")
        raise SystemExit(1)
    email, password = sys.argv[1], sys.argv[2]

    try:
        validate_password_strength(password)
    except WeakPasswordError as exc:
        print(f"Passwort zu schwach: {exc}")
        raise SystemExit(1)

    try:
        sign_up(email, password)
    except SupabaseAuthError as exc:
        print(f"Fehler bei Supabase Auth: {exc}")
        raise SystemExit(1)

    print(f"Supabase-Auth-Nutzer fuer {email} angelegt.")
    print("Naechste Schritte:")
    print("1. Bestaetigungsmail bestaetigen.")
    print(f"2. Auf Railway SUPERADMIN_EMAILS={email} setzen.")
    print("3. Unter /superadmin einloggen und MFA aktivieren.")


if __name__ == "__main__":
    main()
