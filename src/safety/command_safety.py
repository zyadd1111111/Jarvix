from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
import subprocess


ALLOWED_COMMANDS = {
    "pwd",
    "ls",
    "date",
    "whoami",
    "uptime",
    "df",
    "uname",
    "python",
    "python3",
}

ALLOWED_VERSION_FLAGS = {"--version", "-V", "-v"}

BLOCKED_COMMANDS = {
    "sudo",
    "su",
    "rm",
    "rmdir",
    "mv",
    "cp",
    "chmod",
    "chown",
    "kill",
    "killall",
    "pkill",
    "launchctl",
    "shutdown",
    "reboot",
    "curl",
    "wget",
    "ssh",
    "scp",
    "nc",
    "netcat",
    "ftp",
}

SHELL_METACHARS = re.compile(r"[;&|`$<>*?{}[\]()]")
SENSITIVE_TERMS = re.compile(
    r"(password|passwd|secret|token|cookie|keychain|credential|bank|ssh|\.env|"
    r"id_rsa|id_ed25519|private[_-]?key)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CommandValidation:
    allowed: bool
    reason: str
    argv: tuple[str, ...] = ()


class CommandSafety:
    """Validates commands against a small read-only allowlist."""

    def validate(self, command: str) -> CommandValidation:
        clean_command = command.strip()
        if not clean_command:
            return CommandValidation(False, "Command is empty.")

        if SHELL_METACHARS.search(clean_command):
            return CommandValidation(False, "Shell operators and metacharacters are blocked.")

        if SENSITIVE_TERMS.search(clean_command):
            return CommandValidation(False, "Commands referencing sensitive data are blocked.")

        try:
            argv = tuple(shlex.split(clean_command))
        except ValueError as exc:
            return CommandValidation(False, f"Command could not be parsed: {exc}")

        if not argv:
            return CommandValidation(False, "Command is empty.")

        executable = argv[0]
        if executable in BLOCKED_COMMANDS:
            return CommandValidation(False, f"`{executable}` is blocked.")

        if executable not in ALLOWED_COMMANDS:
            return CommandValidation(False, f"`{executable}` is not in the Jarvix v1 allowlist.")

        if executable in {"python", "python3"}:
            if len(argv) == 2 and argv[1] in ALLOWED_VERSION_FLAGS:
                return CommandValidation(True, "Version check allowed.", argv)
            return CommandValidation(False, "Only Python version checks are allowed.")

        if executable in {"pwd", "date", "whoami", "uptime"} and len(argv) > 1:
            return CommandValidation(False, f"`{executable}` does not accept arguments in v1.")

        if executable == "ls":
            for part in argv[1:]:
                if part.startswith("-") and part not in {"-l", "-a", "-la", "-al"}:
                    return CommandValidation(False, "Only simple ls flags are allowed.")
                if not part.startswith("-") and part not in {".", "./"}:
                    return CommandValidation(False, "Only current-directory ls checks are allowed.")

        if executable == "df":
            if any(part != "-h" for part in argv[1:]):
                return CommandValidation(False, "Only `df` and `df -h` are allowed.")

        if executable == "uname":
            if any(part not in {"-a", "-s", "-m", "-r"} for part in argv[1:]):
                return CommandValidation(False, "Only simple uname flags are allowed.")

        return CommandValidation(True, "Command is allowed.", argv)


class SafeCommandRunner:
    """Runs validated commands after the UI has obtained user confirmation."""

    def __init__(self, safety: CommandSafety | None = None) -> None:
        self.safety = safety or CommandSafety()

    def run(self, command: str, confirmed: bool = False, timeout: int = 10) -> subprocess.CompletedProcess[str]:
        validation = self.safety.validate(command)
        if not validation.allowed:
            raise PermissionError(validation.reason)
        if not confirmed:
            raise PermissionError("Command execution requires explicit confirmation.")
        return subprocess.run(
            validation.argv,
            shell=False,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
