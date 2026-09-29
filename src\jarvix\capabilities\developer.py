"""Project inspection and explicitly approved, bounded command sessions."""
from __future__ import annotations

import codecs
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from jarvix.capabilities.files import EXTRA_TEXT, PATH, _safe_name
from jarvix.capabilities.schema import array, enum, integer, register, string
from jarvix.runtime import CURRENT, check_cancelled
from jarvix.tools.builtin import MAX_FILE_BYTES, TEXT_EXTENSIONS, TEXT_NAMES

LANGUAGES = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
             ".tsx": "TypeScript", ".lua": "Lua", ".luau": "Luau", ".rs": "Rust", ".go": "Go",
             ".cs": "C#", ".cpp": "C++", ".java": "Java", ".c": "C"}
MAX_OUTPUT = 1024 * 1024


class CommandSession:
    """Own one child process; history stores metadata, never output or arguments."""

    def __init__(self, process, timeout, cancel):
        self.process = process
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.output = ""
        self.offset = 0
        self.reason = None
        self.done = threading.Event()
        self.cancel = cancel
        self.timeout = timeout
        self.worker = threading.Thread(target=self._read, daemon=True, name="jarvix-command-output")
        self.watchdog = threading.Thread(target=self._watch, daemon=True, name="jarvix-command-timeout")
        self.worker.start()
        self.watchdog.start()

    def _read(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := self.process.stdout.read1(4096):
                self._append(decoder.decode(chunk))
            self._append(decoder.decode(b"", final=True))
        except (OSError, ValueError):
            with self.lock:
                self.reason = self.reason or "output_unavailable"
        finally:
            self.process.stdout.close()
            self.done.set()

    def _append(self, value):
        with self.lock:
            self.output += value
            excess = max(0, len(self.output) - MAX_OUTPUT)
            if excess:
                self.output = self.output[excess:]
                self.offset += excess

    def _watch(self):
        while self.process.poll() is None:
            if self.cancel and self.cancel.is_set():
                self.stop("cancelled")
                return
            if time.monotonic() - self.started >= self.timeout:
                self.stop("timeout")
                return
            # stdout can close before the process exits. Waiting on `done` then
            # would return immediately and spin a CPU until the timeout.
            time.sleep(0.1)

    def stop(self, reason="cancelled"):
        import psutil
        with self.lock:
            if self.process.poll() is not None:
                return False
            self.reason = reason
            try:
                owner = psutil.Process(self.process.pid)
                # The live Popen handle is retained; only this owned process and
                # its current children are considered. Never accept an arbitrary PID.
                children = owner.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                _, remaining = psutil.wait_procs(children, timeout=0.5)
                for child in remaining:
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
            except (OSError, psutil.Error):
                if self.process.poll() is None:
                    self.process.kill()
            return True

    def poll(self, offset=0, max_chars=12000):
        with self.lock:
            start = max(offset, self.offset)
            relative = max(0, start - self.offset)
            text = self.output[relative:relative + max_chars]
            code = self.process.poll()
            return {"running": code is None, "exit_code": code, "output": text,
                    "offset": start + len(text), "truncated_before": offset < self.offset,
                    "has_more": relative + len(text) < len(self.output),
                    "output_complete": self.done.is_set(), "stop_reason": self.reason}


class DeveloperService:
    def __init__(self, services):
        self.services = services
        self.files = services.files
        self.sessions = {}
        self.lock = threading.RLock()

    def _project(self, path):
        root = self.files.path(path)
        if not root.is_dir():
            raise ValueError("Select an approved project folder.")
        return root

    def inspect(self, path):
        root = self._project(path)
        languages = Counter()
        markers = []
        for item in self.files.walk(root):
            if item.is_file() and item.suffix.lower() in LANGUAGES:
                languages[LANGUAGES[item.suffix.lower()]] += 1
            if item.parent == root and item.name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt", "default.project.json"}:
                markers.append(item.name)
        git = root / ".git"
        return {"path": str(root), "languages": dict(languages.most_common()), "markers": markers,
                "git_repository": git.is_dir() and not git.is_symlink(), "primary_language": languages.most_common(1)[0][0] if languages else None}

    def find_files(self, path, query="", limit=50):
        return self.files.list(path, recursive=True, query=query, kind="file", max_bytes=2**53, limit=limit)

    def search(self, path, query, limit=40):
        root = self._project(path)
        if not query.strip():
            raise ValueError("Search text must not be empty.")
        rows = []
        scanned = 0
        skipped = 0
        needle = query.casefold()
        for item in self.files.walk(root):
            check_cancelled()
            if not item.is_file() or (item.suffix.lower() not in TEXT_EXTENSIONS | EXTRA_TEXT and item.name.lower() not in TEXT_NAMES):
                continue
            scanned += item.stat().st_size
            if scanned > 16 * 1024**2:
                return {"matches": rows, "truncated": True, "skipped": skipped, "reason": "16 MiB scan budget reached"}
            try:
                content = self.files.preview(str(item), max_chars=MAX_FILE_BYTES)["text"]
            except (UnicodeError, OSError, ValueError):
                skipped += 1
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if needle in line.casefold():
                    rows.append({"path": str(item), "line": number, "text": line[:500]})
                    if len(rows) >= limit:
                        return {"matches": rows, "truncated": True, "skipped": skipped}
        return {"matches": rows, "truncated": False, "skipped": skipped}

    def _git(self, path, arguments, *, config_query=False):
        root = self._project(path)
        git_dir = self.files.path(root / ".git")
        if not git_dir.is_dir():
            raise ValueError("Only self-contained Git repositories are supported; worktree pointers are not followed.")
        executable = shutil.which("git")
        if not executable:
            raise OSError("Git is not installed.")
        # Even status/diff may invoke a configured clean filter. Enumerate only
        # its keys, then neutralize every driver before inspecting the worktree.
        overrides = []
        if not config_query:
            filters = self._git(path, ["config", "--null", "--name-only", "--get-regexp",
                                       r"^filter\..*\.(clean|smudge|process|required)$"], config_query=True)
            if filters["truncated"]:
                raise ValueError("Repository filter configuration is too large to inspect safely.")
            for key in filters["text"].split("\0"):
                if key:
                    overrides.extend(["-c", key + ("=false" if key.endswith(".required") else "=")])
        # Disable configured helpers and external diff/text conversion. Optional
        # index-lock writes are disabled; submodule worktrees are not inspected.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        args = [executable, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "diff.external=",
                "-c", "core.hooksPath=" + os.devnull, "--no-pager", "--literal-pathspecs",
                "--git-dir=" + str(git_dir), "--work-tree=" + str(root), "-C", str(root), *overrides, *arguments]
        current = CURRENT.get()
        process = subprocess.Popen(args, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        session = CommandSession(process, 20, current.cancel if current else None)
        while process.poll() is None:
            try:
                check_cancelled()
            except InterruptedError:
                session.stop()
                raise
            session.done.wait(0.03)
        session.done.wait(1)
        output = session.poll(0, 20000)
        if output["exit_code"] not in ({0, 1} if config_query else {0}):
            raise ValueError("Git inspection failed.")
        return {"text": output["output"], "truncated": output["has_more"] or output["truncated_before"]}

    def git_status(self, path):
        return self._git(path, ["status", "--short", "--branch", "--untracked-files=normal", "--ignore-submodules=all"])

    def _changed_paths(self, path, scope="working", selected=None):
        root = self._project(path)
        arguments = ["diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"]
        if scope == "staged":
            arguments.append("--cached")
        changed = self._git(path, [*arguments, "--", *([selected] if selected else [])])
        if changed["truncated"]:
            raise ValueError("Too many changed filenames; select one file explicitly.")
        allowed, excluded = [], 0
        for filename in changed["text"].split("\0"):
            if not filename:
                continue
            try:
                target = self.files.path(root / filename, existing=False)
                if not target.is_relative_to(root):
                    raise ValueError("Changed file is outside the project.")
                if target.exists() and not target.is_file():
                    raise ValueError("Only ordinary files can be inspected.")
                allowed.append(str(target.relative_to(root)))
            except (ValueError, OSError):
                excluded += 1
        if len(allowed) > 100:
            raise ValueError("More than 100 changed files; select one file explicitly.")
        return allowed, excluded

    def git_changed(self, path):
        working, working_excluded = self._changed_paths(path)
        staged, staged_excluded = self._changed_paths(path, "staged")
        return {"working": working, "staged": staged, "excluded": working_excluded + staged_excluded}

    def git_diff(self, path, file="", scope="working"):
        root = self._project(path)
        files = []
        excluded = 0
        if scope not in {"working", "staged"}:
            raise ValueError("Select working or staged changes.")
        if file:
            target = self.files.path(root / file, existing=False)
            if not target.is_relative_to(root):
                raise ValueError("File must be inside this project.")
            if target.exists() and not target.is_file():
                raise ValueError("Select one ordinary file, not a directory.")
            selected = str(target.relative_to(root))
            files, excluded = self._changed_paths(path, scope, selected)
            # A deleted directory is also a valid Git pathspec. Never let that
            # broaden a single-file request to its possibly protected contents.
            if any(Path(value) != Path(selected) for value in files):
                raise ValueError("Select one changed file, not a directory.")
        else:
            files, excluded = self._changed_paths(path, scope)
        if not files:
            return {"text": "", "truncated": False, "excluded": excluded}
        args = ["diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--unified=3"]
        if scope == "staged":
            args.append("--cached")
        return {**self._git(path, [*args, "--", *files]), "excluded": excluded}

    def initialize(self, path, language):
        root = self.files.path(path, existing=False, mutate=True)
        self.files.path(root.parent)
        if root.exists():
            raise ValueError("Project initialization requires a new folder.")
        name = _safe_name(root.name)
        if language == "python":
            content = {"main.py": 'def main():\n    print("Hello from Jarvix")\n\n\nif __name__ == "__main__":\n    main()\n',
                       "pyproject.toml": '[project]\nname = "' + self._package_name(name) + '"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n',
                       ".gitignore": ".venv/\n__pycache__/\n*.pyc\n.env\n"}
        elif language == "node":
            import json
            content = {"index.js": 'console.log("Hello from Jarvix");\n',
                       "package.json": json.dumps({"name": self._package_name(name), "version": "0.1.0", "private": True, "type": "module", "scripts": {"start": "node index.js"}}, indent=2) + "\n",
                       ".gitignore": "node_modules/\n.env\n"}
        elif language == "lua":
            content = {"main.lua": 'print("Hello from Jarvix")\n', ".gitignore": ".env\n"}
        else:
            raise ValueError("Choose Python, Node, or Lua.")
        root.mkdir()
        for filename, body in content.items():
            check_cancelled()
            with self.files.path(root / filename, existing=False, mutate=True).open("x", encoding="utf-8") as handle:
                handle.write(body)
        result = self.files._record("create", None, root)
        return {**result, "language": language, "files": list(content), "dependencies_installed": False}

    @staticmethod
    def _package_name(name):
        import re
        result = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:80]
        return result or "jarvix-project"

    def git_initialize(self, path):
        root = self._project(path)
        if (root / ".git").exists():
            raise ValueError("A Git repository already exists.")
        executable = shutil.which("git")
        if not executable:
            raise OSError("Git is not installed.")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        # Empty template prevents hooks and unexpected custom files from being copied.
        result = subprocess.run([executable, "-c", "init.templateDir=", "init", "--", str(root)],
                                shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError("Git initialization failed.")
        self.services.repository.audit("developer", "Git repository initialized")
        return {"initialized": True, "path": str(root)}

    def project_note(self, path, title, body):
        root = self._project(path)
        note_id = self.services.save_note(title, body)
        self.services.records.put("project_note", {"note_id": note_id, "path": str(root)})
        return {"note_id": note_id, "project_path": str(root)}

    def command_preview(self, argv, cwd, timeout=60):
        if not argv or len(argv) > 60 or any(not isinstance(item, str) or "\x00" in item or len(item) > 4000 for item in argv):
            raise ValueError("Use a bounded array of command arguments.")
        root = self._project(cwd)
        if not argv[0] or argv[0].startswith("-"):
            raise ValueError("Specify an executable.")
        executable = shutil.which(argv[0])
        if not executable or not Path(executable).is_file():
            raise ValueError("Executable is unavailable.")
        if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1", ".sh"}:
            raise ValueError("Invoke an interpreter explicitly; implicit batch/script execution is disabled.")
        if not 1 <= timeout <= 300:
            raise ValueError("Command timeout must be 1–300 seconds.")
        return {"argv": [str(Path(executable).resolve()), *argv[1:]], "cwd": str(root), "timeout": timeout,
                "confirmation_required": True, "shell": False,
                "scope": "An approved terminal command can access the user's full OS permissions; this is not a sandbox."}

    def command_start(self, argv, cwd, timeout=60):
        plan = self.command_preview(argv, cwd, timeout)
        check_cancelled()
        with self.lock:
            if sum(item.process.poll() is None for item in self.sessions.values()) >= 4:
                raise ValueError("At most four commands may run simultaneously.")
            process = subprocess.Popen(plan["argv"], cwd=plan["cwd"], shell=False, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            context = CURRENT.get()
            session = CommandSession(process, timeout, context.cancel if context else None)
            session_id = uuid.uuid4().hex
            self.sessions[session_id] = session
            # Retain only bounded finished sessions; active session IDs remain stable.
            if len(self.sessions) > 50:
                for key, item in list(self.sessions.items()):
                    if len(self.sessions) <= 50:
                        break
                    if item.process.poll() is not None and key != session_id:
                        del self.sessions[key]
            self.services.records.put("command", {"executable": Path(plan["argv"][0]).name,
                                       "cwd": plan["cwd"], "timeout": timeout, "session_id": session_id}, session_id)
            self.services.repository.audit("developer", "Explicitly approved terminal command started")
        return {"session_id": session_id, "started": True, "poll_tool": "developer.command_output"}

    def _session(self, session_id):
        with self.lock:
            if session_id not in self.sessions:
                raise ValueError("Command session is unavailable; only sessions from this Jarvix run can be controlled.")
            return self.sessions[session_id]

    def command_output(self, session_id, offset=0, max_chars=12000):
        return self._session(session_id).poll(offset, max_chars)

    def command_cancel(self, session_id):
        session = self._session(session_id)
        stopped = session.stop()
        self.services.repository.audit("developer", "Owned command cancellation requested")
        return {"stopped": stopped, "session_id": session_id}

    def command_history(self, limit=30):
        rows = self.services.records.list("command")[:limit]
        for row in rows:
            session = self.sessions.get(row["id"])
            if session:
                code = session.process.poll()
                row.update({"running": code is None, "exit_code": code, "stop_reason": session.reason})
            else:
                row.update({"running": False, "status": "previous_session"})
        return {"items": rows}

    def close(self):
        for session in list(self.sessions.values()):
            session.stop("application_closed")


def setup(services, registry):
    developer = services.developer = DeveloperService(services)

    def add(name, description, properties, required, handler, level=1):
        register(registry, "developer." + name, description, properties, required, handler, level,
                 "files.read" if level == 1 else "computer.control")

    add("project_inspect", "Detect a project's languages, manifest files and local Git repository.", {"path": PATH}, ["path"], developer.inspect)
    add("find_files", "Find project files by filename, excluding dependency folders.", {"path": PATH, "query": string(300, 0), "limit": integer(1, 100)}, ["path"], developer.find_files)
    add("search_text", "Search bounded UTF-8 project text and source files; return filenames, line numbers and matching text.", {"path": PATH, "query": string(300), "limit": integer(1, 60)}, ["path", "query"], developer.search)
    add("git_status", "Read Git status without configured fsmonitor, hooks, or pager helpers.", {"path": PATH}, ["path"], developer.git_status)
    add("git_changed_files", "List tracked changes relative to HEAD with external diff/text conversion disabled.", {"path": PATH}, ["path"], developer.git_changed)
    add("git_diff", "Inspect working/staged Git diff, optionally limited to an approved project file; external diff/textconv disabled.", {"path": PATH, "file": string(4096, 0), "scope": enum("working", "staged")}, ["path"], developer.git_diff)
    add("initialize_project", "Create a NEW Python, Node, or Lua project without installing dependencies or running generated code. Undo available.", {"path": PATH, "language": enum("python", "node", "lua")}, ["path", "language"], developer.initialize, 2)
    add("git_initialize", "Initialize Git in an approved project without copying custom templates or invoking hooks.", {"path": PATH}, ["path"], developer.git_initialize, 2)
    add("project_note", "Create a note associated with an approved project folder.", {"path": PATH, "title": string(160), "body": string(16000, 0)}, ["path", "title", "body"], developer.project_note, 2)
    command_properties = {"argv": array(string(4000, 0), 60), "cwd": PATH, "timeout": integer(1, 300)}
    add("command_preview", "Preview an executable and arguments, resolved executable, working directory and timeout. Does not run anything.", command_properties, ["argv", "cwd"], developer.command_preview)
    add("command_start", "Execute a terminal command as an argument array with shell=False, a 1–300s timeout and streaming output handle. ALWAYS requires fresh confirmation; commands are NOT sandboxed and may access the full computer.", command_properties, ["argv", "cwd"], developer.command_start, 3)
    add("command_output", "Read incremental stdout/stderr from an owned command session; pass the returned offset to continue.", {"session_id": string(100), "offset": integer(0, 2**53), "max_chars": integer(100, 20000)}, ["session_id"], developer.command_output)
    add("command_cancel", "Stop an owned active command and its current child processes; no arbitrary PIDs accepted.", {"session_id": string(100)}, ["session_id"], developer.command_cancel, 2)
    add("command_history", "List command metadata without arguments/output; current sessions include running state and exit code.", {"limit": integer(1, 100)}, [], developer.command_history)
