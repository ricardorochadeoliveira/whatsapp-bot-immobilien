"""Code-Editor im Superadmin-Bereich: liest/schreibt Dateien direkt ueber die
GitHub Contents API gegen das an Railway gekoppelte Repo. Kein lokales
`git`-Kommando (im Deploy-Container ohne Git-Credentials nicht robust
moeglich) - ein PUT gegen die Contents API erzeugt einen echten Commit auf
dem konfigurierten Branch und stoesst damit denselben Railway-Deploy an wie
ein manueller `git push`.

Bewusste Leitplanken (siehe Plan "Superadmin-Bereich"): nur Pfade unter
ALLOWED_PREFIXES, nichts mit ".env" im Namen, nichts unter ".git/", keine
".."-Segmente. Diese App erzwingt sie serverseitig - unabhaengig davon, was
das Frontend schickt.
"""
from __future__ import annotations

import base64
import os

import httpx

ALLOWED_PREFIXES = ("app/", "web/", "tests/", "docs/")
COMMIT_AUTHOR = {"name": "Wohnchat Superadmin", "email": "superadmin@wohnchat.ch"}


class GithubEditorError(RuntimeError):
    """Config-/Pfad-/API-Fehler - vom Aufrufer auf einen passenden HTTP-Code zu mappen."""


class GithubEditorConflictError(GithubEditorError):
    """Der mitgeschickte `sha` ist veraltet (Datei wurde seither anderswo
    geaendert) - GitHub lehnt den Schreibversuch mit 409 ab."""


def is_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN"))


def _config() -> tuple[str, str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GithubEditorError("GITHUB_TOKEN ist nicht gesetzt.")
    repo = os.environ.get("GITHUB_REPO", "ricardorochadeoliveira/whatsapp-bot-immobilien")
    branch = os.environ.get("GITHUB_BRANCH", "master")
    return token, repo, branch


def _validate_path(path: str) -> str:
    normalized = path.strip().lstrip("/")
    if not normalized:
        return normalized  # leerer Pfad = Repo-Wurzel, nur fuer list_directory erlaubt
    if ".." in normalized.split("/"):
        raise GithubEditorError("Ungueltiger Pfad.")
    if ".env" in normalized:
        raise GithubEditorError("Diese Datei ist nicht editierbar.")
    if normalized.startswith(".git/") or normalized == ".git":
        raise GithubEditorError("Diese Datei ist nicht editierbar.")
    if not normalized.startswith(ALLOWED_PREFIXES):
        raise GithubEditorError(
            f"Nur Pfade unter {', '.join(ALLOWED_PREFIXES)} sind editierbar."
        )
    return normalized


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def list_directory(path: str = "") -> list[dict]:
    token, repo, branch = _config()
    normalized = _validate_path(path)
    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/contents/{normalized}",
        headers=_headers(token),
        params={"ref": branch},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise GithubEditorError(resp.json().get("message") or resp.text)
    data = resp.json()
    if isinstance(data, dict):
        data = [data]
    return [
        {"name": entry["name"], "path": entry["path"], "type": entry["type"], "sha": entry["sha"]}
        for entry in data
    ]


def get_file(path: str) -> dict:
    token, repo, branch = _config()
    normalized = _validate_path(path)
    if not normalized:
        raise GithubEditorError("Kein Dateipfad angegeben.")
    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/contents/{normalized}",
        headers=_headers(token),
        params={"ref": branch},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise GithubEditorError(resp.json().get("message") or resp.text)
    data = resp.json()
    if data.get("type") != "file":
        raise GithubEditorError("Pfad ist keine Datei.")
    content = base64.b64decode(data["content"]).decode("utf-8")
    return {"path": normalized, "content": content, "sha": data["sha"]}


def update_file(path: str, content: str, sha: str, message: str) -> dict:
    token, repo, branch = _config()
    normalized = _validate_path(path)
    if not normalized:
        raise GithubEditorError("Kein Dateipfad angegeben.")
    if not message.strip():
        raise GithubEditorError("Commit-Message ist Pflicht.")

    resp = httpx.put(
        f"https://api.github.com/repos/{repo}/contents/{normalized}",
        headers=_headers(token),
        json={
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": branch,
            "author": COMMIT_AUTHOR,
            "committer": COMMIT_AUTHOR,
        },
        timeout=15,
    )
    if resp.status_code == 409:
        raise GithubEditorConflictError(
            "Die Datei wurde inzwischen anderswo geaendert - bitte neu laden und erneut versuchen."
        )
    if resp.status_code >= 400:
        raise GithubEditorError(resp.json().get("message") or resp.text)
    data = resp.json()
    return {"path": normalized, "sha": data["content"]["sha"], "commit_sha": data["commit"]["sha"]}
