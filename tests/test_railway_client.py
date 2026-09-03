import httpx
import pytest

from app import railway_client
from app.railway_client import RailwayClientError


def _set_env(monkeypatch):
    monkeypatch.setenv("RAILWAY_TOKEN", "railway_test_token")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "proj-1")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc-1")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "env-1")


def test_is_configured_false_without_token(monkeypatch):
    monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
    assert railway_client.is_configured() is False


def test_get_latest_deployment_posts_expected_variables(monkeypatch):
    _set_env(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(
            200,
            json={"data": {"deployments": {"edges": [{"node": {"id": "dep-1", "status": "SUCCESS", "createdAt": "now"}}]}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = railway_client.get_latest_deployment()

    assert result == {"id": "dep-1", "status": "SUCCESS", "createdAt": "now"}
    assert captured["url"] == railway_client.GRAPHQL_URL
    assert captured["headers"]["Authorization"] == "Bearer railway_test_token"
    assert captured["json"]["variables"]["input"] == {
        "projectId": "proj-1",
        "serviceId": "svc-1",
        "environmentId": "env-1",
    }


def test_get_latest_deployment_raises_when_no_deployments(monkeypatch):
    _set_env(monkeypatch)

    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(
            200, json={"data": {"deployments": {"edges": []}}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RailwayClientError):
        railway_client.get_latest_deployment()


def test_graphql_errors_array_raises(monkeypatch):
    _set_env(monkeypatch)

    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(
            200, json={"errors": [{"message": "not authorized"}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RailwayClientError):
        railway_client.get_latest_deployment()


def test_get_deployment_logs_passes_deployment_id_and_limit(monkeypatch):
    _set_env(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["variables"] = json["variables"]
        return httpx.Response(
            200,
            json={"data": {"deploymentLogs": [{"timestamp": "t1", "message": "hello", "severity": "info"}]}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    logs = railway_client.get_deployment_logs("dep-1", limit=20)

    assert logs == [{"timestamp": "t1", "message": "hello", "severity": "info"}]
    assert captured["variables"] == {"deploymentId": "dep-1", "limit": 20}


def test_trigger_redeploy_posts_service_and_environment(monkeypatch):
    _set_env(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["variables"] = json["variables"]
        return httpx.Response(
            200, json={"data": {"serviceInstanceDeploy": True}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    assert railway_client.trigger_redeploy() is True
    assert captured["variables"] == {"serviceId": "svc-1", "environmentId": "env-1"}


def test_missing_token_raises_before_network_call(monkeypatch):
    monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
    with pytest.raises(RailwayClientError):
        railway_client.get_latest_deployment()
