"""Bounded filesystem operations confined to explicitly approved canonical roots."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from jarvix.capabilities.schema import BOOL, array, enum, integer, register, string
from jarvix.runtime import check_cancelled
from jarvix.tools.builtin import MAX_FILE_BYTES, TEXT_EXTENSIONS, TEXT_NAMES, _is_sensitive

MAX_ENTRIES = 5000
MAX_OPERATION_BYTES = 2 * 1024**3
MAX_ARCHIVE_BYTES = 512 * 1024**2
EXTRA_TEXT = {".lua", ".luau"}
SKIP_DIRECTORIES = {".git", "node_modules", ".venv", "venv", "__pycache__"}
PATH = string(4096)


def _safe_name(name: str) -> str:
    if (not name or name in {".", ".."} or name != name.strip() or name.endswith(".")
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError("Use a portable filename without separators or reserved characters.")
    if _is_sensitive(Path(name)):
        raise ValueError("Protected filename.")
    return name


def _linked(path: Path) -> bool:
    # Python 3.11 has no Path.is_junction(). Treat every Windows reparse
    # point as a link, including junctions that target a different drive.
    try:
        return path.is_symlink() or bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return path.is_symlink()
    except FileNotFoundError:
        return False


class FileService:
    """No implicit home access, destructive overwrites, or executable file evaluation."""

    def __init__(self, services):
        self.services = services

    def path(self, value: str | Path, *, existing=True, mutate=False) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() or _is_sensitive(candidate):
            raise ValueError("Path is protected or not absolute.")
        # Reject links even when they currently resolve inside a root. This also
        # prevents copying a nested link into a newly created directory tree.
        for part in (candidate, *candidate.parents):
            if _linked(part):
                raise ValueError("Symbolic links and junctions are not supported.")
        resolved = candidate.resolve(strict=existing)
        if _is_sensitive(resolved):
            raise ValueError("Protected path.")
        for raw in self.services.file_roots():
            configured = Path(raw)
            try:
                root = configured.resolve(strict=True)
                if root != configured or not root.is_dir() or _linked(root):
                    continue
                if resolved.is_relative_to(root):
                    if mutate and resolved == root:
                        raise ValueError("Cannot modify an approved root itself.")
                    for part in resolved.relative_to(root).parts:
                        _safe_name(part)
                    if existing and not (resolved.is_file() or resolved.is_dir()):
                        raise ValueError("Only regular files and directories are supported.")
                    return resolved
            except (OSError, RuntimeError):
                continue
        raise ValueError("Path is outside approved folders or the root has changed.")

    def walk(self, value, *, recursive=True, strict=False):
        root = self.path(value)
        if not root.is_dir():
            raise ValueError("Expected a folder.")
        count = 0
        for current, folders, files in os.walk(root, followlinks=False):
            check_cancelled()
            safe_folders = []
            for name in sorted(folders):
                candidate = Path(current) / name
                try:
                    item = self.path(candidate)
                    if name in SKIP_DIRECTORIES and not strict:
                        continue
                    safe_folders.append(name)
                    yield item
                except (OSError, ValueError):
                    if strict:
                        raise ValueError("The folder contains a protected or linked item.") from None
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError("Too many entries; choose a smaller folder.")
            folders[:] = safe_folders if recursive else []
            for name in sorted(files):
                check_cancelled()
                try:
                    yield self.path(Path(current) / name)
                except (OSError, ValueError):
                    if strict:
                        raise ValueError("The folder contains a protected or linked item.") from None
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError("Too many entries; choose a smaller folder.")

    @staticmethod
    def metadata(path):
        info = path.stat()
        return {"path": str(path), "name": path.name, "kind": "folder" if path.is_dir() else "file",
                "extension": path.suffix.lower(), "size": info.st_size if path.is_file() else None,
                "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()}

    def inspect(self, path):
        return self.metadata(self.path(path))

    def open(self, path):
        target = self.path(path)
        # File associations can execute programs. The ordinary open capability
        # is restricted to documents; scripts belong to the confirmed runner.
        documents = {".txt", ".md", ".markdown", ".pdf", ".csv", ".tsv", ".json",
                     ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                     ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
                     ".mp3", ".wav", ".flac", ".mp4", ".mkv", ".mov"}
        if not target.is_file() or target.suffix.lower() not in documents:
            raise ValueError("Select a supported document or media file; executable associations are disabled.")
        return self._open_native(target)

    def open_folder(self, path):
        target = self.path(path)
        if not target.is_dir():
            raise ValueError("Select an approved folder.")
        return self._open_native(target)

    @staticmethod
    def _open_native(target):
        check_cancelled()
        if os.name != "nt":
            raise OSError("Opening local items currently requires Windows.")
        os.startfile(str(target), "open")
        return {"opened": str(target)}

    def reveal(self, path):
        target = self.path(path)
        from jarvix.capabilities.native_windows import Win32
        explorer = Path(Win32().system_directory()).parent / "explorer.exe"
        check_cancelled()
        subprocess.Popen([str(explorer), "/select,", str(target)], shell=False,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"revealed": str(target)}

    def copy_path(self, path):
        target = self.path(path)
        return self.services.execute_tool("clipboard.write", {"text": str(target)})

    def list(self, path, recursive=False, query="", extension="", kind="any", min_bytes=0,
             max_bytes=MAX_OPERATION_BYTES, modified_after="", modified_before="", sort="name", limit=50):
        after = self._date(modified_after) if modified_after else None
        before = self._date(modified_before) if modified_before else None
        if min_bytes > max_bytes:
            raise ValueError("Minimum size exceeds maximum size.")
        wanted_extension = extension.casefold().lstrip(".")
        rows = []
        for candidate in self.walk(path, recursive=recursive):
            row = self.metadata(candidate)
            if query.casefold() not in row["name"].casefold() or (kind != "any" and row["kind"] != kind):
                continue
            if wanted_extension and row["extension"].lstrip(".") != wanted_extension:
                continue
            if row["kind"] == "file" and not min_bytes <= row["size"] <= max_bytes:
                continue
            stamp = datetime.fromisoformat(row["modified_at"])
            if (after and stamp < after) or (before and stamp > before):
                continue
            rows.append(row)
        rows.sort(key=lambda row: (row["size"] or 0) if sort == "size" else row["modified_at"] if sort == "modified" else row["name"].casefold(), reverse=sort != "name")
        return {"items": rows[:limit], "total": len(rows), "truncated": len(rows) > limit}

    @staticmethod
    def _date(value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Dates must include a timezone.")
        return result

    def summary(self, path):
        files = folders = size = 0
        extensions = Counter()
        for candidate in self.walk(path):
            if candidate.is_dir():
                folders += 1
            else:
                files += 1
                size += candidate.stat().st_size
                extensions[candidate.suffix.lower() or "[no extension]"] += 1
        return {"path": str(self.path(path)), "files": files, "folders": folders, "bytes": size,
                "extensions": dict(extensions.most_common(40)), "excludes": sorted(SKIP_DIRECTORIES)}

    def _hash(self, path):
        hasher = hashlib.sha256()
        size = 0
        with self.path(path).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                check_cancelled()
                size += len(chunk)
                if size > MAX_OPERATION_BYTES:
                    raise ValueError("File exceeds the operation size limit.")
                hasher.update(chunk)
        return hasher.hexdigest()

    def duplicates(self, path, limit=30):
        by_size = {}
        for candidate in self.walk(path):
            if candidate.is_file():
                by_size.setdefault(candidate.stat().st_size, []).append(candidate)
        matches = []
        processed = 0
        for size, candidates in by_size.items():
            if len(candidates) < 2:
                continue
            hashes = {}
            for candidate in candidates:
                processed += size
                if processed > MAX_OPERATION_BYTES:
                    raise ValueError("Duplicate scan exceeds 2 GiB; choose a smaller folder.")
                hashes.setdefault(self._hash(candidate), []).append(str(candidate))
            for digest, paths in hashes.items():
                if len(paths) > 1:
                    matches.append({"sha256": digest, "bytes_each": size, "paths": paths[:50], "copies": len(paths)})
        matches.sort(key=lambda row: row["bytes_each"] * (row["copies"] - 1), reverse=True)
        return {"groups": matches[:limit], "truncated": len(matches) > limit, "bytes_hashed": processed}

    def _fingerprint(self, path):
        root = self.path(path)
        entries = [root, *self.walk(root, strict=True)] if root.is_dir() else [root]
        rows = []
        total = 0
        for item in entries:
            check_cancelled()
            info = item.stat()
            total += info.st_size if item.is_file() else 0
            if total > MAX_OPERATION_BYTES:
                raise ValueError("Operation exceeds 2 GiB; choose fewer files.")
            rows.append([str(item.relative_to(root)), "d" if item.is_dir() else "f",
                         None if item.is_dir() else self._hash(item)])
        return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()

    def _record(self, action, source, destination):
        fingerprint = self._fingerprint(destination)
        record_id = self.services.records.put("file_operation", {"action": action, "source": str(source) if source else None,
                                                "destination": str(destination), "fingerprint": fingerprint, "undone": False})
        self.services.repository.audit("file_action", f"{action}: completed")
        return {"operation_id": record_id, "path": str(destination), "undo_available": True}

    def history(self, limit=30):
        return {"items": self.services.records.list("file_operation")[:limit]}

    def create_folder(self, path):
        target = self.path(path, existing=False, mutate=True)
        self.path(target.parent)
        target.mkdir(exist_ok=False)
        return self._record("create", None, target)

    def _destination(self, source, destination):
        src = self.path(source, mutate=True)
        dst = self.path(destination, existing=False, mutate=True)
        self.path(dst.parent)
        if dst.exists() or src == dst or dst.is_relative_to(src):
            raise ValueError("Destination exists or is inside the source.")
        self._fingerprint(src)  # Validate all descendants before any mutation.
        return src, dst

    def _copy(self, src, dst):
        # Exclusive creation avoids an accidental overwrite, including races.
        if src.is_dir():
            dst.mkdir(exist_ok=False)
            for child in sorted(src.iterdir()):
                check_cancelled()
                self._copy(self.path(child), self.path(dst / child.name, existing=False, mutate=True))
        else:
            with self.path(src).open("rb") as source, self.path(dst, existing=False, mutate=True).open("xb") as target:
                total = 0
                while chunk := source.read(1024 * 1024):
                    check_cancelled()
                    total += len(chunk)
                    if total > MAX_OPERATION_BYTES:
                        raise ValueError("File changed beyond the operation size limit.")
                    target.write(chunk)
            shutil.copystat(src, dst, follow_symlinks=False)

    def _remove_owned(self, path):
        root = self.path(path, mutate=True)
        if root.is_file():
            root.unlink()
            return
        descendants = list(self.walk(root, strict=True))
        for child in sorted(descendants, key=lambda p: len(p.parts), reverse=True):
            checked = self.path(child, mutate=True)
            checked.rmdir() if checked.is_dir() else checked.unlink()
        root.rmdir()

    def copy(self, source, destination):
        src, dst = self._destination(source, destination)
        self._copy(src, dst)
        return self._record("copy", src, dst)

    def move(self, source, destination):
        src, dst = self._destination(source, destination)
        before = self._fingerprint(src)
        self._copy(src, dst)
        if before != self._fingerprint(src) or before != self._fingerprint(dst):
            raise ValueError("Source changed during the operation; both copies were retained.")
        check_cancelled()
        self._remove_owned(src)
        return self._record("move", src, dst)

    def rename(self, path, name):
        src = self.path(path, mutate=True)
        return self.move(str(src), str(src.parent / _safe_name(name)))

    def undo(self, operation_id):
        record = self.services.records.get("file_operation", operation_id)
        if record["undone"]:
            raise ValueError("This operation was already undone.")
        destination = self.path(record["destination"], mutate=True)
        if self._fingerprint(destination) != record["fingerprint"]:
            raise ValueError("The result has changed; undo would affect later edits.")
        if record["action"] == "move":
            source = self.path(record["source"], existing=False, mutate=True)
            self._destination(destination, source)
            self._copy(destination, source)
            if self._fingerprint(source) != record["fingerprint"]:
                raise ValueError("Restored content differs; both copies were retained.")
        elif record["action"] not in {"create", "copy", "archive", "extract"}:
            raise ValueError("This operation cannot be undone automatically.")
        check_cancelled()
        if self._fingerprint(destination) != record["fingerprint"]:
            raise ValueError("The result changed during undo; retained for safety.")
        self._remove_owned(destination)
        self.services.records.put("file_operation", {**record, "undone": True}, operation_id)
        self.services.repository.audit("file_action", "File operation undone")
        return {"undone": operation_id}

    def batch_rename(self, paths, prefix="", suffix="", start=1, execute=False):
        planned = []
        targets = set()
        for number, value in enumerate(paths, start):
            src = self.path(value, mutate=True)
            name = _safe_name(f"{prefix}{number:03d}{suffix}{src.suffix if src.is_file() else ''}")
            dst = src.with_name(name)
            self._destination(src, dst)
            key = str(dst).casefold()
            if key in targets or any(src.is_relative_to(Path(row["source"])) or Path(row["source"]).is_relative_to(src) for row in planned):
                raise ValueError("Batch contains duplicate or overlapping paths.")
            targets.add(key)
            planned.append({"source": str(src), "destination": str(dst)})
        if not execute:
            return {"preview": planned, "count": len(planned)}
        results = []
        for row in planned:
            check_cancelled()
            results.append(self.move(**row))
        return {"operations": results}

    def organize(self, path, execute=False):
        root = self.path(path)
        categories = {"Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
                      "Documents": {".pdf", ".docx", ".txt", ".md", ".xlsx", ".pptx"},
                      "Videos": {".mp4", ".mkv", ".mov", ".avi"}, "Audio": {".mp3", ".wav", ".flac", ".m4a"},
                      "Archives": {".zip", ".7z", ".tar", ".gz"}}
        planned = []
        for item in self.walk(root, recursive=False):
            if not item.is_file():
                continue
            category = next((name for name, extensions in categories.items() if item.suffix.lower() in extensions), "Other")
            target = self.path(root / category / item.name, existing=False, mutate=True)
            if target.exists():
                raise ValueError("An organized destination already exists; resolve the conflict first.")
            planned.append({"source": str(item), "destination": str(target)})
        if len(planned) > 100:
            raise ValueError("Organize at most 100 files at a time.")
        if not execute:
            return {"preview": planned, "count": len(planned)}
        results = []
        folders = []
        for row in planned:
            check_cancelled()
            folder = Path(row["destination"]).parent
            if not folder.exists():
                folders.append(self.create_folder(str(folder)))
            results.append(self.move(**row))
        return {"operations": results, "created_folders": folders, "undo_order": "Undo file moves first, then empty created folders."}

    def zip_create(self, source, destination):
        src, dst = self._destination(source, destination)
        if dst.suffix.lower() != ".zip":
            raise ValueError("Destination must end with .zip.")
        entries = [src, *self.walk(src, strict=True)] if src.is_dir() else [src]
        with dst.open("xb") as output, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                check_cancelled()
                archive.write(self.path(entry), str(entry.relative_to(src.parent)))
        return self._record("archive", src, dst)

    def zip_extract(self, source, destination):
        src = self.path(source)
        dst = self.path(destination, existing=False, mutate=True)
        self.path(dst.parent)
        if dst.exists():
            raise ValueError("Extraction requires a new destination folder.")
        total = 0
        planned = []
        targets = set()
        with zipfile.ZipFile(src) as archive:
            if len(archive.infolist()) > 2000:
                raise ValueError("Archive contains too many entries.")
            for entry in archive.infolist():
                check_cancelled()
                parts = PurePosixPath(entry.filename).parts
                if not parts or entry.filename.startswith(("/", "\\")) or "\\" in entry.filename:
                    raise ValueError("Invalid archive path.")
                for part in parts:
                    _safe_name(part)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}) or entry.flag_bits & 1:
                    raise ValueError("Linked, special, and encrypted archive entries are unsupported.")
                target = self.path(dst.joinpath(*parts), existing=False, mutate=True)
                key = str(target).casefold()
                if key in targets or not target.is_relative_to(dst):
                    raise ValueError("Archive paths collide or escape the destination.")
                targets.add(key)
                total += entry.file_size
                if total > MAX_ARCHIVE_BYTES or (entry.file_size > 1024**2 and entry.file_size / max(entry.compress_size, 1) > 1000):
                    raise ValueError("Archive exceeds expansion limits.")
                planned.append((entry, target))
            # Reject file/directory collisions before creating the destination.
            file_targets = {str(target).casefold() for entry, target in planned if not entry.is_dir()}
            if any(str(parent).casefold() in file_targets for _, target in planned
                   for parent in target.parents if parent != dst and parent.is_relative_to(dst)):
                raise ValueError("Archive contains conflicting files and directories.")
            dst.mkdir()
            copied = 0
            for entry, target in planned:
                check_cancelled()
                self.path(target, existing=False, mutate=True)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source_handle, target.open("xb") as output:
                    while chunk := source_handle.read(1024 * 1024):
                        check_cancelled()
                        copied += len(chunk)
                        if copied > MAX_ARCHIVE_BYTES:
                            raise ValueError("Archive exceeds expansion limit.")
                        output.write(chunk)
        return self._record("extract", src, dst)

    def preview(self, path, max_chars=12000):
        target = self.path(path)
        if not target.is_file() or (target.suffix.lower() not in TEXT_EXTENSIONS | EXTRA_TEXT and target.name.lower() not in TEXT_NAMES):
            raise ValueError("Unsupported text file type.")
        with target.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
            raise ValueError("File is binary or exceeds 256 KiB.")
        text = raw.decode("utf-8-sig")
        return {"path": str(target), "text": text[:max_chars], "truncated": len(text) > max_chars,
                "lines": len(text.splitlines()), "language": target.suffix.lstrip(".")}

    def json_preview(self, path):
        target = self.path(path)
        if target.suffix.lower() != ".json":
            raise ValueError("Expected a JSON file.")
        content = self.preview(path, max_chars=MAX_FILE_BYTES)
        parsed = json.loads(content["text"])
        return {"type": type(parsed).__name__, "length": len(parsed) if isinstance(parsed, (dict, list)) else None,
                "keys": list(parsed)[:100] if isinstance(parsed, dict) else [], "preview": content["text"][:12000]}

    def csv_preview(self, path, rows=10):
        target = self.path(path)
        if target.suffix.lower() not in {".csv", ".tsv"}:
            raise ValueError("Expected CSV or TSV.")
        content = self.preview(path, max_chars=MAX_FILE_BYTES)
        reader = csv.reader(io.StringIO(content["text"]), delimiter="\t" if target.suffix.lower() == ".tsv" else ",")
        result = []
        for row in reader:
            result.append([value[:500] for value in row[:30]])
            if len(result) >= rows:
                break
        return {"rows": result, "row_limit": rows, "column_limit": 30}

    def pdf_metadata(self, path):
        from pypdf import PdfReader
        target = self.path(path)
        if target.suffix.lower() != ".pdf" or target.stat().st_size > 32 * 1024**2:
            raise ValueError("Select a PDF under 32 MiB.")
        reader = PdfReader(target, strict=True)
        if reader.is_encrypted:
            return {"encrypted": True, "pages": None}
        metadata = reader.metadata or {}
        return {"encrypted": False, "pages": len(reader.pages),
                "metadata": {str(key): str(value)[:500] for key, value in list(metadata.items())[:20]}}

    def recycle(self, path):
        """Use the Windows shell recycle facility; never fall back to permanent delete."""
        from jarvix.capabilities.native_windows import Win32
        target = self.path(path, mutate=True)
        self._fingerprint(target)
        check_cancelled()
        Win32().recycle(str(target))
        self.services.repository.audit("file_action", "Item sent to Windows Recycle Bin")
        return {"recycled": True, "restore": "Restore this item using Windows Recycle Bin."}


def setup(services, registry):
    files = services.files = FileService(services)

    def add(name, description, properties, required, handler, level=1):
        register(registry, "files." + name, description, properties, required, handler, level,
                 "files.read" if level == 1 else "computer.control")

    list_properties = {"path": PATH, "recursive": BOOL, "query": string(300, 0), "extension": string(20, 0),
                       "kind": enum("any", "file", "folder"), "min_bytes": integer(0, 2**53),
                       "max_bytes": integer(0, 2**53), "modified_after": string(40, 0), "modified_before": string(40, 0),
                       "sort": enum("name", "size", "modified"), "limit": integer(1, 100)}
    add("inspect", "Inspect one approved file or folder's metadata.", {"path": PATH}, ["path"], files.inspect)
    add("open", "Open a supported document or media file in its default Windows application. Executable/script associations are blocked.", {"path": PATH}, ["path"], files.open, 2)
    add("open_folder", "Open an approved directory in Windows Explorer.", {"path": PATH}, ["path"], files.open_folder, 2)
    add("reveal", "Reveal an approved item in Windows Explorer.", {"path": PATH}, ["path"], files.reveal, 2)
    add("copy_path", "Copy an approved item's absolute path to the clipboard after the clipboard permission check.", {"path": PATH}, ["path"], files.copy_path, 2)
    add("list", "List or recursively filter approved files/folders by name, extension, size, and timezone-aware dates. Up to 5,000 scanned entries.", list_properties, ["path"], files.list)
    add("largest", "List largest approved files in a folder (up to 5,000 entries scanned).", {"path": PATH, "limit": integer(1, 100)}, ["path"], lambda path, limit=20: files.list(path, recursive=True, kind="file", sort="size", max_bytes=2**53, limit=limit))
    add("recent", "List files most recently modified in an approved folder.", {"path": PATH, "limit": integer(1, 100)}, ["path"], lambda path, limit=20: files.list(path, recursive=True, kind="file", sort="modified", max_bytes=2**53, limit=limit))
    add("folder_summary", "Calculate folder size, file count, and extension breakdown without reading contents.", {"path": PATH}, ["path"], files.summary)
    add("duplicates", "Find content-identical files using SHA-256; read-only scan capped at 2 GiB.", {"path": PATH, "limit": integer(1, 50)}, ["path"], files.duplicates)
    add("create_folder", "Create one new folder inside an approved root. Parent must exist; never overwrites.", {"path": PATH}, ["path"], files.create_folder, 2)
    for name in ("copy", "move"):
        add(name, f"{name.title()} a file or folder within approved roots; no overwrite, protected descendants, or links. Up to 2 GiB.", {"source": PATH, "destination": PATH}, ["source", "destination"], getattr(files, name), 2)
    add("rename", "Rename a file or folder; destination must not exist.", {"path": PATH, "name": string(200)}, ["path", "name"], files.rename, 2)
    batch = {"paths": array(PATH, 100), "prefix": string(120, 0), "suffix": string(120, 0), "start": integer(1, 100000)}
    add("batch_rename_preview", "Preview numbered file/folder renames without changing files.", batch, ["paths"], files.batch_rename)
    add("batch_rename", "Execute numbered renames after reviewing batch_rename_preview; every rename is journaled for undo.", batch, ["paths"], lambda **args: files.batch_rename(**args, execute=True), 2)
    add("organize_preview", "Preview grouping the immediate files of Downloads, Desktop, or another approved folder by file type.", {"path": PATH}, ["path"], files.organize)
    add("organize", "Organize up to 100 immediate files by type after reviewing organize_preview; no overwrites; moves can be undone.", {"path": PATH}, ["path"], lambda path: files.organize(path, execute=True), 2)
    add("operation_history", "List recent local file operations and their undo IDs.", {"limit": integer(1, 50)}, [], files.history)
    add("undo", "Undo a Jarvix file operation only if content is unchanged and original paths are free.", {"operation_id": string(100)}, ["operation_id"], files.undo, 2)
    add("zip_create", "Create a new ZIP from an approved file/folder; never overwrite or include protected descendants.", {"source": PATH, "destination": PATH}, ["source", "destination"], files.zip_create, 2)
    add("zip_extract", "Extract ZIP to a new folder; reject traversal, links, encryption, collisions, and oversized expansion.", {"source": PATH, "destination": PATH}, ["source", "destination"], files.zip_extract, 2)
    add("preview", "Preview UTF-8 source/text, including Lua/Luau, inside approved folders (256 KiB max).", {"path": PATH, "max_chars": integer(100, 16000)}, ["path"], files.preview)
    add("json_preview", "Validate and inspect a bounded local JSON file without executing content.", {"path": PATH}, ["path"], files.json_preview)
    add("csv_preview", "Preview up to 30 columns of a local CSV/TSV file.", {"path": PATH, "rows": integer(1, 30)}, ["path"], files.csv_preview)
    add("pdf_metadata", "Inspect PDF page count, encryption flag and metadata (32 MiB maximum).", {"path": PATH}, ["path"], files.pdf_metadata)
    add("recycle", "Send an approved file/folder to Windows Recycle Bin. Always requires fresh explicit confirmation. Restore through Windows Recycle Bin.", {"path": PATH}, ["path"], files.recycle, 3)
