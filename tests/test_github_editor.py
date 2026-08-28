import base64

import httpx
import pytest

from app.github_editor import GithubEditorConflictError, GithubEditorError, get_file, list_directory, update_file


def _set_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    monkeypatch.setenv("GITHUB_BRANCH", "master")


def test_list_directory_rejects_disallowed_prefix(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(GithubEditorError):
        list_directory("secrets/")


def test_get_file_rejects_env_file(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(GithubEditorError):
        get_file("app/.env")


def test_get_file_rejects_path_traversal(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(GithubEditorError):
        get_file("app/../.env")


def test_get_file_decodes_content_and_returns_sha(monkeypatch):
    _set_env(monkeypatch)
    content = "print('hallo')"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    def fake_get(url, headers=None, params=None, timeout=None):
        return httpx.Response(
            200,
            json={"type": "file", "content": encoded, "sha": "abc123"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = get_file("app/chat_service.py")
    assert result["content"] == content
    assert result["sha"] == "abc123"


def test_update_file_raises_conflict_on_409(monkeypatch):
    _set_env(monkeypatch)

    def fake_put(url, headers=None, json=None, timeout=None):
        return httpx.Response(409, json={"message": "sha mismatch"}, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "put", fake_put)
    with pytest.raises(GithubEditorConflictError):
        update_file("app/chat_service.py", "neuer inhalt", "veraltete-sha", "Testcommit")


def test_update_file_requires_commit_message(monkeypatch):
    _set_env(monkeypatch)
    with pytest.raises(GithubEditorError):
        update_file("app/chat_service.py", "neuer inhalt", "sha123", "   ")


def test_update_file_success_posts_base64_content(monkeypatch):
    _set_env(monkeypatch)
    captured = {}

    def fake_put(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return httpx.Response(
            200,
            json={"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}},
            request=httpx.Request("PUT", url),
        )

    monkeypatch.setattr(httpx, "put", fake_put)
    result = update_file("app/chat_service.py", "neuer inhalt", "sha123", "Testcommit")

    assert base64.b64decode(captured["json"]["content"]).decode("utf-8") == "neuer inhalt"
    assert captured["json"]["message"] == "Testcommit"
    assert captured["json"]["sha"] == "sha123"
    assert result["sha"] == "new-sha"
