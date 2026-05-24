"""Tests for the sandbox executor and test runner."""

import pytest

from src.sandbox.executor import SandboxExecutor
from src.sandbox.test_runner import PytestRunner


async def test_execute_python_success():
    """Successful Python code returns stdout with exit code 0."""
    executor = SandboxExecutor()
    result = await executor.execute("print('hello world')")

    assert result.stdout == "hello world"
    assert result.exit_code == 0
    assert not result.timed_out
    assert result.duration_ms >= 0


async def test_execute_python_error():
    """Python code that raises returns non-zero exit code."""
    executor = SandboxExecutor()
    result = await executor.execute("raise ValueError('boom')")

    assert result.exit_code != 0
    assert "ValueError" in result.stderr
    assert not result.timed_out


async def test_execute_timeout():
    """Long-running code is killed after the timeout."""
    executor = SandboxExecutor()
    result = await executor.execute("import time; time.sleep(10)", timeout=1)

    assert result.timed_out
    assert result.exit_code != 0


async def test_execute_command_in_directory(tmp_path):
    """Commands run in the specified directory without shell interpolation."""
    script = tmp_path / "hello.py"
    script.write_text("print('from dir')\n")

    executor = SandboxExecutor()
    result = await executor.execute_command(["python3", "hello.py"], cwd=tmp_path)

    assert result.stdout == "from dir"
    assert result.exit_code == 0


async def test_execute_command_nonexistent_directory(tmp_path):
    """Passing a nonexistent directory raises ValueError."""
    executor = SandboxExecutor()
    fake_dir = tmp_path / "nonexistent"

    with pytest.raises(ValueError, match="does not exist"):
        await executor.execute_command(["echo", "hi"], cwd=fake_dir)


async def test_pytest_runner_passing_suite(tmp_path):
    """PytestRunner reports passing tests correctly."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_example.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n\ndef test_two():\n    assert True\n"
    )

    runner = PytestRunner()
    result = await runner.run_pytest(tmp_path)

    assert result.passed >= 2
    assert result.failed == 0
    assert len(result.failures) == 0


async def test_pytest_runner_failing_suite(tmp_path):
    """PytestRunner reports failures with structured TestFailure data."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_fail.py").write_text(
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert 1 == 2, 'math is broken'\n"
    )

    runner = PytestRunner()
    result = await runner.run_pytest(tmp_path)

    assert result.passed >= 1
    assert result.failed >= 1
    assert result.total >= 2
