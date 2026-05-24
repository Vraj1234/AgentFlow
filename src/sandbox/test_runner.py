"""Sandboxed test runner for validating generated code."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.sandbox.executor import SandboxExecutor


@dataclass(frozen=True)
class TestFailure:
    test_name: str
    error_message: str
    file_path: str


@dataclass(frozen=True)
class TestResult:
    passed: int
    failed: int
    errors: int
    total: int
    output: str
    failures: tuple[TestFailure, ...]


class PytestRunner:
    """Runs pytest in a project directory and returns structured results."""

    def __init__(self, executor: SandboxExecutor | None = None) -> None:
        self._executor = executor or SandboxExecutor()

    async def run_pytest(
        self,
        project_dir: Path,
        test_path: str = "tests/",
    ) -> TestResult:
        """Run pytest and parse the results."""
        args = ["python3", "-m", "pytest", test_path, "--tb=short", "-q"]

        exec_result = await self._executor.execute_command(args, cwd=project_dir, timeout=120)

        output = exec_result.stdout
        if exec_result.stderr:
            output = f"{output}\n{exec_result.stderr}"

        return self._parse_output(output)

    def _parse_output(self, output: str) -> TestResult:
        """Parse pytest summary line to extract pass/fail counts."""
        passed = self._extract_count(output, r"(\d+) passed")
        failed = self._extract_count(output, r"(\d+) failed")
        errors = self._extract_count(output, r"(\d+) errors?")
        total = passed + failed + errors

        failures = self._extract_failures(output)

        return TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            total=total,
            output=output,
            failures=tuple(failures),
        )

    def _extract_count(self, text: str, pattern: str) -> int:
        """Extract a count from pytest output using a regex pattern."""
        match = re.search(pattern, text)
        return int(match.group(1)) if match else 0

    def _extract_failures(self, output: str) -> list[TestFailure]:
        """Extract failure details from pytest short traceback output."""
        failures: list[TestFailure] = []
        pattern = r"FAILED\s+(\S+?)::(\S+?)(?:\s+-\s+(.+))?$"
        for match in re.finditer(pattern, output, re.MULTILINE):
            failures.append(
                TestFailure(
                    test_name=match.group(2),
                    error_message=match.group(3) or "",
                    file_path=match.group(1),
                )
            )
        return failures
