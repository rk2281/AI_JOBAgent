"""Smoke tests for the application's HTTP surface.

These protect the Day 1 foundation. Every future stage must keep
them passing.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_root_endpoint_responds() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "message" in response.json()


def test_health_endpoint_reports_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_endpoint_reports_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] in {"ready", "degraded"}

    dependencies = payload["dependencies"]
    assert "telegram" in dependencies
    assert "database" in dependencies
    assert isinstance(dependencies["database"]["connected"], bool)
