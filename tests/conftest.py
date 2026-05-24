"""Shared test fixtures for AgentFlow test suite."""

import pytest

from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus


@pytest.fixture
def shared_bus():
    """A fresh MessageBus for cross-module tests."""
    return MessageBus()


@pytest.fixture
def shared_kb():
    """A fresh KnowledgeBase for cross-module tests."""
    return KnowledgeBase()


@pytest.fixture
async def populated_kb():
    """KnowledgeBase pre-loaded with a sample spec and architecture."""
    kb = KnowledgeBase()
    await kb.put(
        "spec",
        {
            "title": "Task Manager API",
            "features": ["user auth", "task CRUD"],
            "constraints": ["PostgreSQL"],
            "tech_stack": ["Python", "FastAPI"],
            "acceptance_criteria": ["users can create tasks"],
            "non_functional_requirements": ["<200ms response time"],
        },
        author="spec_analyst",
        tags=("spec",),
    )
    await kb.put(
        "api_contract",
        {
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/tasks",
                    "description": "Create a task",
                    "request_schema": {"title": "string"},
                    "response_schema": {"id": "string"},
                }
            ]
        },
        author="architect",
        tags=("architecture",),
    )
    return kb
