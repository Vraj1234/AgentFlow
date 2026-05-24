"""CLI interface for AgentFlow."""

from __future__ import annotations

import asyncio
import logging
import os
from importlib.metadata import version as pkg_version

import typer
from rich.console import Console

from src.interface.pipeline import Pipeline
from src.llm.client import LLMClient
from src.orchestrator.knowledge_base import KnowledgeBase
from src.orchestrator.message_bus import MessageBus

logger = logging.getLogger(__name__)

app = typer.Typer(name="agentflow", help="AgentFlow — AI Software Development Factory")
console = Console()

MAX_SPEC_CHARS = 50_000


def _version_callback(value: bool) -> None:
    if value:
        try:
            ver = pkg_version("agentflow")
        except Exception:
            ver = "0.1.0"
        console.print(f"agentflow {ver}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """AgentFlow — give it a spec, get back production-ready software."""


@app.command()
def new(
    spec: str = typer.Argument(..., help="Raw specification text"),
) -> None:
    """Start a new AgentFlow pipeline from a spec."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error:[/red] ANTHROPIC_API_KEY environment variable not set")
        raise typer.Exit(code=1)

    if len(spec) > MAX_SPEC_CHARS:
        console.print(f"[red]Error:[/red] Spec exceeds {MAX_SPEC_CHARS} characters.")
        raise typer.Exit(code=1)

    bus = MessageBus()
    kb = KnowledgeBase()
    llm = LLMClient(api_key=api_key)

    def on_event(stage: str, status: str) -> None:
        icons = {
            "started": "[yellow]*[/yellow]",
            "completed": "[green]+[/green]",
            "failed": "[red]x[/red]",
        }
        icon = icons.get(status, "-")
        console.print(f"  {icon} {stage}: {status}")

    pipeline = Pipeline(llm, bus, kb, on_event=on_event)

    console.print("\n[bold]AgentFlow[/bold] — Starting pipeline for spec:")
    console.print(f"  [dim]{spec[:200]}{'...' if len(spec) > 200 else ''}[/dim]\n")

    try:
        asyncio.run(_run_pipeline(pipeline, spec))
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted.[/yellow]")
        raise typer.Exit(code=130)
    except RuntimeError:
        raise typer.Exit(code=1)


async def _run_pipeline(pipeline: Pipeline, spec: str) -> None:
    """Run the pipeline stages."""
    console.print("[bold]Stage 1: Spec Analysis[/bold]")

    try:
        result = await pipeline.run_spec_stage(spec, {})
        console.print(f"\n  Spec: [green]{result.title}[/green]")
        console.print(f"  Features: {', '.join(result.features)}")
    except Exception as exc:
        logger.exception("Pipeline run failed")
        console.print(f"\n  [red]Spec analysis failed: {exc}[/red]")
        raise RuntimeError("Pipeline failed") from exc


@app.command()
def status() -> None:
    """Show the current pipeline status.

    Note: Pipeline state is in-process only. This command is useful
    when integrated into the web dashboard; as a standalone CLI
    invocation it will always report idle.
    """
    console.print("No active pipeline. Use [bold]agentflow new[/bold] to start one.")
