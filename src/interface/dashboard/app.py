"""FastAPI web dashboard for AgentFlow pipeline visualization."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Read HTML once at import time to avoid blocking the event loop
_HTML_CONTENT: str | None = None


def _get_html() -> str:
    global _HTML_CONTENT
    if _HTML_CONTENT is None:
        path = _STATIC_DIR / "index.html"
        if path.exists():
            _HTML_CONTENT = path.read_text()
        else:
            _HTML_CONTENT = (
                "<html><head><title>AgentFlow</title></head>"
                "<body><h1>AgentFlow Dashboard</h1>"
                "<p>Dashboard is running.</p></body></html>"
            )
    return _HTML_CONTENT


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline';"
        )
        return response


def create_app(
    message_bus: Any,
    knowledge_base: Any,
    task_graph: Any,
    pipeline_status: dict[str, Any] | None = None,
) -> FastAPI:
    """Create a FastAPI dashboard app wired to the given infrastructure."""
    app = FastAPI(title="AgentFlow Dashboard", version="0.1.0")
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Serve the dashboard HTML page."""
        return HTMLResponse(content=_get_html())

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        """Return current pipeline status."""
        if pipeline_status:
            return pipeline_status
        return {
            "current_stage": None,
            "completed_stages": [],
            "subscriber_count": message_bus.subscriber_count,
        }

    @app.get("/api/agents")
    async def get_agents() -> dict[str, Any]:
        """Return list of registered agents."""
        history = message_bus.get_history()
        agent_ids: set[str] = set()
        for msg in history:
            if msg.sender:
                agent_ids.add(msg.sender)
            if msg.recipient:
                agent_ids.add(msg.recipient)
        return {"agents": sorted(agent_ids)}

    @app.get("/api/tasks")
    async def get_tasks() -> dict[str, Any]:
        """Return task graph data."""
        tasks = []
        waves = await task_graph.get_execution_order()
        for wave in waves:
            for task in wave:
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "assigned_to": task.assigned_to.value,
                        "status": task.status.value,
                        "dependencies": list(task.dependencies),
                    }
                )
        return {
            "tasks": tasks,
            "total": task_graph.task_count,
        }

    @app.get("/api/knowledge")
    async def get_knowledge() -> dict[str, Any]:
        """Return knowledge base entries."""
        keys = await knowledge_base.keys()
        entries = []
        for key in keys:
            entry = await knowledge_base.get(key)
            if entry is not None:
                entries.append(
                    {
                        "key": key,
                        "author": entry.author,
                        "tags": list(entry.tags),
                        "created_at": entry.created_at.isoformat(),
                    }
                )
        return {"entries": entries}

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket) -> None:
        """Stream real-time pipeline events via WebSocket."""
        await websocket.accept()
        try:
            # Keep alive until client disconnects.
            # Real event emission will be added when the pipeline
            # supports event broadcasting.
            data = await websocket.receive_text()
            logger.debug("WebSocket received: %s", data)
        except WebSocketDisconnect:
            logger.debug("WebSocket client disconnected")
        except Exception:
            logger.warning("WebSocket error", exc_info=True)

    return app
