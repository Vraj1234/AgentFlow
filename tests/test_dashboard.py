"""Tests for the web dashboard."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def kb():
    return KnowledgeBase()


@pytest.fixture
def task_graph():
    from src.orchestrator.task_graph import TaskGraph

    return TaskGraph()


@pytest.fixture
def app(bus, kb, task_graph):
    from src.interface.dashboard.app import create_app

    return create_app(bus, kb, task_graph)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_dashboard_status_endpoint(client):
    """GET /api/status returns valid JSON with pipeline status."""
    response = await client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "current_stage" in data
    assert "completed_stages" in data
    assert "subscriber_count" in data
    assert isinstance(data["completed_stages"], list)
    assert isinstance(data["subscriber_count"], int)


async def test_dashboard_agents_endpoint(client):
    """GET /api/agents returns agent listing."""
    response = await client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert isinstance(data["agents"], list)


async def test_dashboard_tasks_endpoint(client):
    """GET /api/tasks returns task graph data."""
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    data = response.json()
    assert "tasks" in data
    assert "total" in data
    assert isinstance(data["tasks"], list)


async def test_dashboard_knowledge_endpoint(client, kb):
    """GET /api/knowledge returns KB keys and values."""
    await kb.put("spec", {"title": "Test"}, author="test", tags=("spec",))

    response = await client.get("/api/knowledge")
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data
    assert len(data["entries"]) >= 1
    assert data["entries"][0]["key"] == "spec"
    assert data["entries"][0]["author"] == "test"
    assert "spec" in data["entries"][0]["tags"]


async def test_dashboard_root_serves_html(client):
    """GET / serves the dashboard HTML page."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AgentFlow" in response.text


async def test_dashboard_root_fallback_html(bus, kb, task_graph):
    """GET / serves fallback HTML when index.html is missing."""
    from src.interface.dashboard import app as dashboard_module
    from src.interface.dashboard.app import create_app

    # Reset cached HTML so fallback path is exercised
    original = dashboard_module._HTML_CONTENT
    dashboard_module._HTML_CONTENT = None

    try:
        with patch.object(dashboard_module, "_STATIC_DIR", dashboard_module.Path("/nonexistent")):
            fallback_app = create_app(bus, kb, task_graph)
            transport = ASGITransport(app=fallback_app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.get("/")
                assert response.status_code == 200
                assert "AgentFlow Dashboard" in response.text
    finally:
        dashboard_module._HTML_CONTENT = original


async def test_dashboard_security_headers(client):
    """Dashboard responses include security headers."""
    response = await client.get("/api/status")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
