from __future__ import annotations

import pytest

from safety.command_safety import CommandSafety, SafeCommandRunner


def test_allows_simple_read_only_commands() -> None:
    safety = CommandSafety()

    for command in ["pwd", "ls -la", "date", "whoami", "uptime", "df -h", "uname -s"]:
        validation = safety.validate(command)
        assert validation.allowed, command
        assert validation.argv


def test_allows_python_version_only() -> None:
    safety = CommandSafety()

    assert safety.validate("python3 --version").allowed
    assert not safety.validate("python3 -c print(1)").allowed


@pytest.mark.parametrize(
    "command",
    [
        "sudo ls",
        "rm -rf data",
        "curl https://example.com",
        "cat ~/.ssh/id_rsa",
        "ls /",
        "ls ~",
        "df /",
        "date tomorrow",
        "ls | cat",
        "date > output.txt",
        "whoami && pwd",
        "open -a Calculator",
        "echo $OPENAI_API_KEY",
    ],
)
def test_blocks_dangerous_or_sensitive_commands(command: str) -> None:
    assert not CommandSafety().validate(command).allowed


def test_runner_requires_confirmation() -> None:
    runner = SafeCommandRunner(CommandSafety())

    with pytest.raises(PermissionError, match="confirmation"):
        runner.run("pwd", confirmed=False)


def test_runner_executes_confirmed_allowed_command() -> None:
    runner = SafeCommandRunner(CommandSafety())

    result = runner.run("pwd", confirmed=True)

    assert result.returncode == 0
    assert result.stdout.strip()
