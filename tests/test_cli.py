"""Tests for the CLI interface."""

from typer.testing import CliRunner

from src.interface.cli import app

runner = CliRunner()


def test_cli_version():
    """CLI --version flag works."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "agentflow" in result.stdout.lower() or "0.1" in result.stdout


def test_cli_help():
    """CLI --help flag works."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "new" in result.stdout.lower()


def test_cli_status_reports_idle():
    """CLI status command reports no active pipeline."""
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "no active pipeline" in result.stdout.lower()


def test_cli_new_no_api_key(monkeypatch):
    """CLI new command fails when ANTHROPIC_API_KEY is not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["new", "build a todo app"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.stdout


def test_cli_new_oversized_spec(monkeypatch):
    """CLI new command rejects oversized specs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    oversized = "x" * 60_000
    result = runner.invoke(app, ["new", oversized])
    assert result.exit_code == 1
    assert "exceeds" in result.stdout.lower()
