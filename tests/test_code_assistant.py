import io
import shutil
import tarfile
from types import SimpleNamespace

import httpx
import pytest

from app import code_assistant
from app.code_assistant import (
    CodeAssistantConfigError,
    CodeAssistantConflictError,
    SessionNotFoundError,
)


@pytest.fixture(autouse=True)
def _reset_sessions():
    code_assistant._SESSIONS.clear()
    yield
    for session in code_assistant._SESSIONS.values():
        shutil.rmtree(session.tmpdir, ignore_errors=True)
    code_assistant._SESSIONS.clear()


def _set_github_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    monkeypatch.setenv("GITHUB_BRANCH", "master")


def _build_fake_tarball(files: dict) -> bytes:
    """Baut einen minimalen GitHub-artigen Tarball: alles unter einem
    Top-Level-Ordner ('owner-repo-abc123/'), wie GitHub ihn ausliefert."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        top = "owner-repo-abc123"
        for rel_path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"{top}/{rel_path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tool_use(name, tool_input, id_="tu1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=tool_input)


def _text(content):
    return SimpleNamespace(type="text", text=content)


def _response(blocks):
    return SimpleNamespace(content=blocks)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("Keine weitere gescriptete Antwort mehr vorhanden.")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


PASSING_TEST_FILE = "def test_ok():\n    assert True\n"
FAILING_TEST_FILE = "def test_fail():\n    assert False\n"


def _patch_tarball_fetch(monkeypatch, tarball_bytes):
    def fake_get(url, **kwargs):
        assert "tarball" in url
        return httpx.Response(200, content=tarball_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)


# -- _safe_extract_tar --------------------------------------------------


def test_safe_extract_tar_strips_top_level_dir_and_writes_files(tmp_path):
    tarball = _build_fake_tarball({"app/foo.py": "X = 1\n", "tests/test_dummy.py": PASSING_TEST_FILE})
    dest = tmp_path / "extracted"
    dest.mkdir()
    code_assistant._safe_extract_tar(tarball, dest)
    assert (dest / "app" / "foo.py").read_text() == "X = 1\n"
    assert (dest / "tests" / "test_dummy.py").read_text() == PASSING_TEST_FILE


def test_safe_extract_tar_skips_symlinks(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        top = "owner-repo-abc123"
        info = tarfile.TarInfo(name=f"{top}/app/evil_link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    dest = tmp_path / "extracted"
    dest.mkdir()
    code_assistant._safe_extract_tar(buf.getvalue(), dest)
    assert not (dest / "app" / "evil_link").exists()


# -- run_assistant: end-to-end mit gescriptetem Claude-Client ----------


def test_run_assistant_success_when_tests_pass(monkeypatch):
    _set_github_env(monkeypatch)
    tarball = _build_fake_tarball({"tests/test_dummy.py": PASSING_TEST_FILE})
    _patch_tarball_fetch(monkeypatch, tarball)

    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/greeting.py", "content": "GRUSS = 'Hallo!'\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Fertig, Tests sind gruen.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.run_assistant("Fuege eine Begruessung hinzu.")

    assert result.success is True
    assert result.files_changed == ["app/greeting.py"]
    assert "app/greeting.py" in result.diff
    assert "+GRUSS" in result.diff
    assert result.session_id
    assert result.session_id in code_assistant._SESSIONS


def test_run_assistant_failing_tests_block_push(monkeypatch):
    _set_github_env(monkeypatch)
    tarball = _build_fake_tarball({"tests/test_dummy.py": FAILING_TEST_FILE})
    _patch_tarball_fetch(monkeypatch, tarball)

    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/greeting.py", "content": "X = 1\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Tests sind leider rot.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.run_assistant("Etwas kaputtes.")

    assert result.success is False
    assert result.session_id == ""
    assert code_assistant._SESSIONS == {}


def test_run_assistant_turn_cap_reached(monkeypatch):
    _set_github_env(monkeypatch)
    tarball = _build_fake_tarball({"tests/test_dummy.py": PASSING_TEST_FILE})
    _patch_tarball_fetch(monkeypatch, tarball)

    # Immer wieder list_directory aufrufen, nie eine finale Textantwort -
    # muss nach MAX_TURNS sauber abbrechen statt endlos zu laufen.
    responses = [_response([_tool_use("list_directory", {"path": ""}, f"tu{i}")]) for i in range(code_assistant.MAX_TURNS)]
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.run_assistant("Endlosschleife provozieren.")

    assert "maximale Anzahl" in result.summary
    assert result.success is False
    assert result.session_id == ""


def test_run_assistant_rejects_disallowed_path_without_crashing(monkeypatch):
    _set_github_env(monkeypatch)
    tarball = _build_fake_tarball({"tests/test_dummy.py": PASSING_TEST_FILE})
    _patch_tarball_fetch(monkeypatch, tarball)

    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "secrets/leak.py", "content": "X = 1\n"}, "tu1")]),
            _response([_text("Konnte die Datei nicht schreiben.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.run_assistant("Versuche etwas Verbotenes.")

    assert result.files_changed == []
    assert result.success is False
    # Der Fehler wurde als Tool-Result zurueckgegeben, nicht als Exception hochgereicht.
    tool_result_contents = [
        block["content"]
        for call in fake_client.messages.calls
        for msg in call["messages"]
        if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert any("Fehler" in c for c in tool_result_contents)


def test_run_assistant_rejects_max_concurrent_sessions(monkeypatch, tmp_path):
    _set_github_env(monkeypatch)
    monkeypatch.setattr(code_assistant, "_get_client", lambda: _FakeClient([]))
    for i in range(code_assistant.MAX_CONCURRENT_SESSIONS):
        fake_dir = tmp_path / f"session-{i}"
        fake_dir.mkdir()
        code_assistant._SESSIONS[f"sid-{i}"] = code_assistant.AssistantSession(
            tmpdir=fake_dir, files_changed=["app/x.py"], diff="diff"
        )

    with pytest.raises(CodeAssistantConfigError):
        code_assistant.run_assistant("Noch ein Lauf, sollte abgelehnt werden.")


def test_run_assistant_requires_instruction(monkeypatch):
    _set_github_env(monkeypatch)
    with pytest.raises(CodeAssistantConfigError):
        code_assistant.run_assistant("   ")


def test_sweep_expired_removes_old_sessions_and_tmpdir(monkeypatch, tmp_path):
    fake_dir = tmp_path / "expired-session"
    fake_dir.mkdir()
    code_assistant._SESSIONS["old-sid"] = code_assistant.AssistantSession(
        tmpdir=fake_dir, files_changed=["app/x.py"], diff="diff", created_at=0.0
    )
    monkeypatch.setattr(code_assistant.time, "time", lambda: code_assistant.SESSION_TTL_SECONDS + 1000)

    code_assistant._sweep_expired()

    assert "old-sid" not in code_assistant._SESSIONS
    assert not fake_dir.exists()


# -- push_session ---------------------------------------------------------


def test_push_session_unknown_session_raises():
    with pytest.raises(SessionNotFoundError):
        code_assistant.push_session("does-not-exist", "Commit-Message")


def test_push_session_success_posts_blob_tree_commit_ref(monkeypatch, tmp_path):
    _set_github_env(monkeypatch)
    session_dir = tmp_path / "push-session"
    session_dir.mkdir()
    (session_dir / "app").mkdir()
    (session_dir / "app" / "greeting.py").write_text("GRUSS = 'Hallo!'\n", encoding="utf-8")
    code_assistant._SESSIONS["sid-push"] = code_assistant.AssistantSession(
        tmpdir=session_dir, files_changed=["app/greeting.py"], diff="diff"
    )

    captured = {"posts": []}

    def fake_get(url, **kwargs):
        if url.endswith("/git/ref/heads/master"):
            return httpx.Response(200, json={"object": {"sha": "head-sha"}}, request=httpx.Request("GET", url))
        if "/git/commits/" in url:
            return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}}, request=httpx.Request("GET", url))
        raise AssertionError(f"Unerwarteter GET: {url}")

    def fake_post(url, **kwargs):
        captured["posts"].append((url, kwargs.get("json")))
        if url.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"}, request=httpx.Request("POST", url))
        if url.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"}, request=httpx.Request("POST", url))
        if url.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit-sha"}, request=httpx.Request("POST", url))
        raise AssertionError(f"Unerwarteter POST: {url}")

    def fake_patch(url, **kwargs):
        assert url.endswith("/git/refs/heads/master")
        assert kwargs["json"]["force"] is False
        return httpx.Response(200, json={}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "patch", fake_patch)

    result = code_assistant.push_session("sid-push", "Begruessung hinzugefuegt")

    assert result == {"commit_sha": "new-commit-sha"}
    assert "sid-push" not in code_assistant._SESSIONS
    assert not session_dir.exists()
    blob_call = next(p for p in captured["posts"] if p[0].endswith("/git/blobs"))
    assert blob_call[1]["content"] == "GRUSS = 'Hallo!'\n"


def test_push_session_conflict_on_rejected_ref_update(monkeypatch, tmp_path):
    _set_github_env(monkeypatch)
    session_dir = tmp_path / "push-conflict"
    session_dir.mkdir()
    (session_dir / "app").mkdir()
    (session_dir / "app" / "greeting.py").write_text("X = 1\n", encoding="utf-8")
    code_assistant._SESSIONS["sid-conflict"] = code_assistant.AssistantSession(
        tmpdir=session_dir, files_changed=["app/greeting.py"], diff="diff"
    )

    def fake_get(url, **kwargs):
        if url.endswith("/git/ref/heads/master"):
            return httpx.Response(200, json={"object": {"sha": "head-sha"}}, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}}, request=httpx.Request("GET", url))

    def fake_post(url, **kwargs):
        if url.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"}, request=httpx.Request("POST", url))
        if url.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"}, request=httpx.Request("POST", url))
        return httpx.Response(201, json={"sha": "new-commit-sha"}, request=httpx.Request("POST", url))

    def fake_patch(url, **kwargs):
        return httpx.Response(422, json={"message": "Update is not a fast forward"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "patch", fake_patch)

    with pytest.raises(CodeAssistantConflictError):
        code_assistant.push_session("sid-conflict", "Sollte konfligieren")

    # Aufraeumen passiert trotzdem, egal ob Erfolg oder Fehler.
    assert not session_dir.exists()


def test_push_session_requires_commit_message(tmp_path):
    session_dir = tmp_path / "push-nomsg"
    session_dir.mkdir()
    code_assistant._SESSIONS["sid-nomsg"] = code_assistant.AssistantSession(
        tmpdir=session_dir, files_changed=[], diff=""
    )
    with pytest.raises(CodeAssistantConfigError):
        code_assistant.push_session("sid-nomsg", "   ")


# -- _RunContext (direkte Werkzeug-Tests, ohne Agent-Schleife) -----------


def test_run_context_write_then_read_roundtrip(tmp_path):
    (tmp_path / "app").mkdir()
    ctx = code_assistant._RunContext(tmp_path)
    ctx.write_file("app/neu.py", "X = 1\n")
    assert ctx.read_file("app/neu.py") == "X = 1\n"
    assert ctx.originals["app/neu.py"] == ""  # war neu, kein Originalinhalt


def test_run_context_list_directory_returns_entries(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("1", encoding="utf-8")
    ctx = code_assistant._RunContext(tmp_path)
    entries = ctx.list_directory("app")
    assert {"name": "a.py", "path": "app/a.py", "type": "file"} in entries


def test_run_context_run_tests_reports_failure(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dummy.py").write_text(FAILING_TEST_FILE, encoding="utf-8")
    ctx = code_assistant._RunContext(tmp_path)
    result = ctx.run_tests()
    assert result["returncode"] != 0
    assert ctx.last_test_result is result
