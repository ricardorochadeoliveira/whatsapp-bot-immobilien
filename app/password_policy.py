"""Passwort-Mindestanforderungen fuer die Firmen-Registrierung. Supabase Auth
selbst erzwingt nur eine sehr niedrige Standard-Mindestlaenge - diese Pruefung
laeuft VOR dem Supabase-Call, damit schwache Passwoerter gar nicht erst
angelegt werden."""
from __future__ import annotations

MIN_LENGTH = 8


class WeakPasswordError(ValueError):
    """Passwort erfuellt die Mindestanforderungen nicht."""


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPasswordError(f"Passwort muss mindestens {MIN_LENGTH} Zeichen lang sein.")
    if not any(c.isalpha() for c in password):
        raise WeakPasswordError("Passwort muss mindestens einen Buchstaben enthalten.")
    if not any(c.isdigit() for c in password):
        raise WeakPasswordError("Passwort muss mindestens eine Ziffer enthalten.")
