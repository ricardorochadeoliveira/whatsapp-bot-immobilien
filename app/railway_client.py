"""Duenner Client fuers Railway GraphQL Public API - genutzt vom Entwickler-
Chat (app/code_assistant.py), damit Claude im Chat selbst pruefen kann, ob
ein Deploy durchgelaufen ist, Logs ansehen und auf ausdrueckliche Bitte einen
Redeploy anstossen kann (siehe Plan "Superadmin: ein gemeinsamer Entwickler-
Chat").

Endpunkt/Operationen aus Railways eigenem "API Cookbook" uebernommen, nicht
geraten - siehe docs.railway.com/guides/api-cookbook."""
from __future__ import annotations

import os

import httpx

GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

# Aus der Railway-MCP-Diagnose des laufenden Deploy-Problems bekannt - als
# Default hinterlegt (gleiches Muster wie GITHUB_REPO in app/code_assistant.py),
# per Env-Var ueberschreibbar, falls das Projekt je migriert wird.
DEFAULT_PROJECT_ID = "d71e2a9c-97d9-4f28-b4ff-854d151e33b6"
DEFAULT_SERVICE_ID = "e98ea188-214c-4b04-a5ea-6d2fcd2a1bae"
DEFAULT_ENVIRONMENT_ID = "4ecfaba1-484b-4747-a771-12e7a8d87546"


class RailwayClientError(RuntimeError):
    """Config-/API-Fehler - vom Aufrufer als Tool-Fehler an Claude zurueckzugeben."""


def is_configured() -> bool:
    return bool(os.environ.get("RAILWAY_TOKEN"))


def _config() -> tuple[str, str, str, str]:
    token = os.environ.get("RAILWAY_TOKEN")
    if not token:
        raise RailwayClientError("RAILWAY_TOKEN ist nicht gesetzt.")
    project_id = os.environ.get("RAILWAY_PROJECT_ID", DEFAULT_PROJECT_ID)
    service_id = os.environ.get("RAILWAY_SERVICE_ID", DEFAULT_SERVICE_ID)
    environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", DEFAULT_ENVIRONMENT_ID)
    return token, project_id, service_id, environment_id


def _graphql(query: str, variables: dict) -> dict:
    token, *_ = _config()
    resp = httpx.post(
        GRAPHQL_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RailwayClientError(f"Railway-API nicht erreichbar (HTTP {resp.status_code}).")
    data = resp.json()
    if data.get("errors"):
        messages = "; ".join(e.get("message", "unbekannter Fehler") for e in data["errors"])
        raise RailwayClientError(f"Railway-API-Fehler: {messages}")
    return data["data"]


_DEPLOYMENTS_QUERY = """
query deployments($input: DeploymentListInput!) {
  deployments(input: $input, first: 1) {
    edges { node { id status createdAt } }
  }
}
"""

_DEPLOYMENT_LOGS_QUERY = """
query deploymentLogs($deploymentId: String!, $limit: Int) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
    timestamp
    message
    severity
  }
}
"""

_REDEPLOY_MUTATION = """
mutation serviceInstanceDeploy($serviceId: String!, $environmentId: String!) {
  serviceInstanceDeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


def get_latest_deployment() -> dict:
    _, project_id, service_id, environment_id = _config()
    data = _graphql(
        _DEPLOYMENTS_QUERY,
        {"input": {"projectId": project_id, "serviceId": service_id, "environmentId": environment_id}},
    )
    edges = data.get("deployments", {}).get("edges", [])
    if not edges:
        raise RailwayClientError("Keine Deployments gefunden.")
    return edges[0]["node"]


def get_deployment_logs(deployment_id: str, limit: int = 50) -> list[dict]:
    data = _graphql(_DEPLOYMENT_LOGS_QUERY, {"deploymentId": deployment_id, "limit": limit})
    return data.get("deploymentLogs", [])


def trigger_redeploy() -> bool:
    _, _project_id, service_id, environment_id = _config()
    data = _graphql(_REDEPLOY_MUTATION, {"serviceId": service_id, "environmentId": environment_id})
    return bool(data.get("serviceInstanceDeploy"))
