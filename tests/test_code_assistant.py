import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest

from app import code_assistant, railway_client
from app.code_assistant import CodeAssistantConfigError, CodeAssistantConflictError


@pytest.fixture(autouse=True)
def _reset_chat_state():
    code_assistant.reset_chat()
    yield
    code_assistant.reset_chat()


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


def _patch_tarball_fetch(monkeypatch, tarball_bytes):
    def fake_get(url, **kwargs):
        assert "tarball" in url
        return httpx.Response(200, content=tarball_bytes, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)


def _default_tarball():
    return _build_fake_tarball({"tests/test_dummy.py": PASSING_TEST_FILE})


# -- send_message: einzelne Nachricht -------------------------------------


def test_send_message_writes_file_and_reports_push_allowed(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/greeting.py", "content": "GRUSS = 'Hallo!'\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Fertig, Tests sind gruen.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Fuege eine Begruessung hinzu.")

    assert result["reply"] == "Fertig, Tests sind gruen."
    assert result["files_changed"] == ["app/greeting.py"]
    assert "+GRUSS" in result["diff"]
    assert result["push_allowed"] is True


def test_send_message_without_code_changes_needs_no_tests(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient([_response([_text("Das ist eine reine Erklaerung, keine Aenderung noetig.")])])
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Was macht chat_service.py?")

    assert result["files_changed"] == []
    assert result["push_allowed"] is False


def test_second_write_after_green_test_re_arms_dirty_flag(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/x.py", "content": "A = 1\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Erste Aenderung getestet.")]),
            _response([_tool_use("write_file", {"path": "app/x.py", "content": "A = 2\n"}, "tu3")]),
            _response([_text("Noch eine Aenderung, aber noch nicht neu getestet.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    first = code_assistant.send_message("Setze A auf 1.")
    assert first["push_allowed"] is True

    second = code_assistant.send_message("Aendere A auf 2.")
    assert second["push_allowed"] is False


def test_turn_cap_reached_within_single_message(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    responses = [
        _response([_tool_use("list_directory", {"path": ""}, f"tu{i}")])
        for i in range(code_assistant.MAX_TOOL_ROUNDS_PER_MESSAGE)
    ]
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Endlosschleife provozieren.")

    assert "maximale Anzahl" in result["reply"]
    assert result["push_allowed"] is False


def test_disallowed_path_returns_tool_error_without_crashing(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "secrets/leak.py", "content": "X = 1\n"}, "tu1")]),
            _response([_text("Konnte die Datei nicht schreiben.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Versuche etwas Verbotenes.")

    assert result["files_changed"] == []
    assert result["push_allowed"] is False


# -- Railway-Werkzeuge ------------------------------------------------------


def test_railway_deployment_status_tool_is_dispatched(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    monkeypatch.setattr(
        railway_client, "get_latest_deployment", lambda: {"id": "dep-1", "status": "SUCCESS", "createdAt": "now"}
    )
    fake_client = _FakeClient(
        [
            _response([_tool_use("railway_deployment_status", {}, "tu1")]),
            _response([_text("Der letzte Deploy war erfolgreich.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Ist der letzte Deploy durchgelaufen?")

    assert result["reply"] == "Der letzte Deploy war erfolgreich."
    tool_result_contents = [
        block["content"]
        for call in fake_client.messages.calls
        for msg in call["messages"]
        if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert any("SUCCESS" in c for c in tool_result_contents)


def test_railway_trigger_redeploy_tool_is_dispatched(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    monkeypatch.setattr(railway_client, "trigger_redeploy", lambda: True)
    fake_client = _FakeClient(
        [
            _response([_tool_use("railway_trigger_redeploy", {}, "tu1")]),
            _response([_text("Redeploy angestossen.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)

    result = code_assistant.send_message("Stoss bitte nochmal einen Deploy an.")
    assert result["reply"] == "Redeploy angestossen."


# -- get_state / reset_chat -------------------------------------------------


def test_get_state_reflects_conversation_and_diff(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/greeting.py", "content": "X = 1\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Fertig.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)
    code_assistant.send_message("Mach etwas.")

    state = code_assistant.get_state()

    assert len(state["display_messages"]) == 2  # user + assistant
    assert state["display_messages"][0] == {"role": "user", "text": "Mach etwas."}
    assert state["push_allowed"] is True
    assert "app/greeting.py" in state["files_changed"]


def test_reset_chat_clears_history_and_tmpdir(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient([_response([_text("Hallo.")])])
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)
    code_assistant.send_message("Hi.")
    tmpdir_before = code_assistant._chat.tmpdir
    assert tmpdir_before is not None

    code_assistant.reset_chat()

    assert code_assistant._chat.messages == []
    assert code_assistant._chat.display_messages == []
    assert code_assistant._chat.tmpdir is None
    assert not tmpdir_before.exists()


# -- push_current -----------------------------------------------------------


def _get_to_push_allowed(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/greeting.py", "content": "GRUSS = 'Hallo!'\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Fertig.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)
    code_assistant.send_message("Fuege eine Begruessung hinzu.")


def _patch_push_endpoints(monkeypatch, captured=None):
    captured = captured if captured is not None else {"posts": []}

    def fake_get(url, **kwargs):
        if url.endswith("/git/ref/heads/master"):
            return httpx.Response(200, json={"object": {"sha": "head-sha"}}, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}}, request=httpx.Request("GET", url))

    def fake_post(url, **kwargs):
        captured["posts"].append((url, kwargs.get("json")))
        if url.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-sha"}, request=httpx.Request("POST", url))
        if url.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree-sha"}, request=httpx.Request("POST", url))
        return httpx.Response(201, json={"sha": "new-commit-sha"}, request=httpx.Request("POST", url))

    def fake_patch(url, **kwargs):
        return httpx.Response(200, json={}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "patch", fake_patch)
    return captured


def test_push_current_resets_working_state_but_keeps_messages(monkeypatch):
    _get_to_push_allowed(monkeypatch)
    tmpdir_before = code_assistant._chat.tmpdir
    captured = _patch_push_endpoints(monkeypatch)

    result = code_assistant.push_current("Begruessung hinzugefuegt")

    assert result == {"commit_sha": "new-commit-sha"}
    assert code_assistant._chat.tmpdir is None
    assert not tmpdir_before.exists()
    assert code_assistant._chat.tests_green is False
    # Konversation bleibt erhalten, plus der neue Push-Hinweis.
    assert len(code_assistant._chat.display_messages) == 3
    assert "Gepusht" in code_assistant._chat.display_messages[-1]["text"]
    blob_call = next(p for p in captured["posts"] if p[0].endswith("/git/blobs"))
    assert blob_call[1]["content"] == "GRUSS = 'Hallo!'\n"


def test_push_current_rejects_when_nothing_tested(monkeypatch):
    _set_github_env(monkeypatch)
    with pytest.raises(CodeAssistantConfigError):
        code_assistant.push_current("Nichts zu pushen")


def test_push_current_rejects_when_dirty_since_last_test(monkeypatch):
    _set_github_env(monkeypatch)
    _patch_tarball_fetch(monkeypatch, _default_tarball())
    fake_client = _FakeClient(
        [
            _response([_tool_use("write_file", {"path": "app/x.py", "content": "A = 1\n"}, "tu1")]),
            _response([_tool_use("run_tests", {}, "tu2")]),
            _response([_text("Getestet.")]),
            _response([_tool_use("write_file", {"path": "app/x.py", "content": "A = 2\n"}, "tu3")]),
            _response([_text("Weitere Aenderung, ungetestet.")]),
        ]
    )
    monkeypatch.setattr(code_assistant, "_get_client", lambda: fake_client)
    code_assistant.send_message("Setze A auf 1.")
    code_assistant.send_message("Aendere A auf 2.")

    with pytest.raises(CodeAssistantConfigError):
        code_assistant.push_current("Sollte abgelehnt werden")


def test_push_current_raises_conflict_on_rejected_ref_update(monkeypatch):
    _get_to_push_allowed(monkeypatch)

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
        return httpx.Response(422, json={"message": "not a fast forward"}, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "patch", fake_patch)

    with pytest.raises(CodeAssistantConflictError):
        code_assistant.push_current("Sollte konfligieren")

    assert code_assistant._chat.tmpdir is None  # trotzdem aufgeraeumt


# -- _RunContext (direkte Werkzeug-Tests, ohne Agent-Schleife) -----------


def test_run_context_write_then_read_roundtrip(tmp_path):
    (tmp_path / "app").mkdir()
    ctx = code_assistant._RunContext(tmp_path)
    ctx.write_file("app/neu.py", "X = 1\n")
    assert ctx.read_file("app/neu.py") == "X = 1\n"
    assert ctx.originals["app/neu.py"] == ""


def test_run_context_run_tests_reports_failure(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dummy.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    ctx = code_assistant._RunContext(tmp_path)
    result = ctx.run_tests()
    assert result["returncode"] != 0
