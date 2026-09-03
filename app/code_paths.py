"""Pfad-Sicherheits-Leitplanken fuer den Entwickler-Chat (app/code_assistant.py):
nur app/, web/, tests/, docs/ sind sichtbar/schreibbar, nichts mit '.env' im
Namen, nichts unter '.git/', keine '..'-Traversal.

validate_path() ist eine reine String-Pruefung (kein Dateisystemzugriff).
resolve_within() prueft zusaetzlich auf Dateisystem-Ebene, dass ein Pfad
tatsaechlich unter einem gegebenen Tempordner bleibt (noetig, weil der
Entwickler-Chat echte Dateien auf der Festplatte liest/schreibt) - eine reine
String-Pruefung wuerde weder absolute Pfade noch ein Entkommen aus dem
Tempordner per Symlink/`..`-Aufloesung verhindern.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

ALLOWED_PREFIXES = ("app/", "web/", "tests/", "docs/")


class InvalidPathError(ValueError):
    """Pfad verstoesst gegen die Sicherheits-Leitplanken."""


def validate_path(path: str) -> str:
    """String-Ebene: keine '..'-Segmente, nichts mit '.env' im Namen, nichts
    unter '.git/', nur ALLOWED_PREFIXES. Gibt den normalisierten Pfad
    zurueck (fuehrende '/' entfernt); ein leerer Pfad (Repo-Wurzel) ist
    erlaubt und wird unveraendert durchgereicht."""
    normalized = path.strip().lstrip("/")
    if not normalized:
        return normalized
    if ".." in normalized.split("/"):
        raise InvalidPathError("Ungueltiger Pfad.")
    if ".env" in normalized:
        raise InvalidPathError("Diese Datei ist nicht editierbar.")
    if normalized.startswith(".git/") or normalized == ".git":
        raise InvalidPathError("Diese Datei ist nicht editierbar.")
    # Ordner-Eintraege kommen z.B. von der GitHub-API OHNE trailing slash
    # zurueck ("app" statt "app/") - ein reiner startswith(ALLOWED_PREFIXES)
    # wuerde das Reinklicken in einen Top-Level-Ordner selbst faelschlich
    # ablehnen, obwohl jede Datei darunter erlaubt waere.
    prefix_names = tuple(p.rstrip("/") for p in ALLOWED_PREFIXES)
    if not normalized.startswith(ALLOWED_PREFIXES) and normalized not in prefix_names:
        raise InvalidPathError(f"Nur Pfade unter {', '.join(ALLOWED_PREFIXES)} sind editierbar.")
    return normalized


def resolve_within(root: Path, path: str) -> Path:
    """Wie validate_path(), PLUS Dateisystem-Ebene: lehnt absolute Pfade ab
    und prueft per .resolve(), dass der Pfad wirklich unter `root` bleibt
    (Schutz gegen Symlink-/Traversal-Flucht aus einem Tempordner)."""
    normalized = validate_path(path)
    if PurePosixPath(path.strip()).is_absolute():
        raise InvalidPathError("Absolute Pfade sind nicht erlaubt.")
    root_resolved = root.resolve()
    resolved = (root / normalized).resolve() if normalized else root_resolved
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise InvalidPathError("Pfad verlaesst das erlaubte Verzeichnis.")
    return resolved
