"""Sandboxed code execution via async subprocesses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int


class SandboxExecutor:
    """Runs code snippets or shell commands in isolated subprocesses."""

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute a code snippet and return the result."""
        if language == "python":
            cmd = ["python3", "-c", code]
        else:
            raise ValueError(f"Unsupported language: {language}")

        return await self._run(cmd, timeout=timeout)

    async def execute_command(
        self,
        args: list[str],
        cwd: Path | None = None,
        timeout: int = 60,
    ) -> ExecutionResult:
        """Run a command as a list of arguments (no shell interpolation)."""
        if cwd is not None:
            resolved = cwd.resolve()
            if not resolved.is_dir():
                raise ValueError(f"Directory does not exist: {resolved}")

        return await self._run(args, timeout=timeout, cwd=cwd)

    async def _run(
        self,
        cmd: list[str],
        timeout: int,
        cwd: Path | None = None,
    ) -> ExecutionResult:
        """Execute a command with timeout handling."""
        start = time.monotonic()
        timed_out = False

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode if proc.returncode is not None else 1
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            timed_out = True
            stdout_bytes = b""
            stderr_bytes = b"Execution timed out"
            exit_code = proc.returncode if proc.returncode is not None else -1

        duration_ms = int((time.monotonic() - start) * 1000)

        return ExecutionResult(
            stdout=stdout_bytes.decode(errors="replace").strip(),
            stderr=stderr_bytes.decode(errors="replace").strip(),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
        )
