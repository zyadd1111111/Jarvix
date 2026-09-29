import os
import stat
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvix.capabilities.files import _linked
from jarvix.runtime import operation
from jarvix.services import Services


@pytest.fixture
def services(tmp_path):
    service = Services(tmp_path / "profile", vault=SimpleNamespace(get=lambda _: None))
    root = tmp_path / "allowed"
    root.mkdir()
    service.add_file_root(str(root))
    service.test_root = root
    yield service
    service.close()


def test_file_boundaries_protect_roots_secrets_and_invalid_names(services, tmp_path):
    files, root = services.files, services.test_root
    (root / "visible.txt").write_text("readable")
    (root / ".env").write_text("secret")
    (root / "folder").mkdir()
    (root / "folder" / "credentials.json").write_text("private")
    assert files.preview(str(root / "visible.txt"))["text"] == "readable"
    for path in (tmp_path / "outside.txt", root / ".env", root / "folder" / "credentials.json",
                 root / "CON.txt", root / "alternate:stream"):
        with pytest.raises(ValueError):
            files.path(path, existing=False)
    with pytest.raises(ValueError):
        files.move(str(root), str(tmp_path / "moved"))
    assert {item["name"] for item in files.list(str(root), recursive=True)["items"]} == {"visible.txt", "folder"}


def test_reparse_points_rejected_on_python_without_path_is_junction(monkeypatch):
    path = Path("C:/fixture/junction")
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT))
    assert _linked(path)


def test_copy_move_undo_refuses_overwrite_and_later_edits(services):
    files, root = services.files, services.test_root
    source, target = root / "source.txt", root / "target.txt"
    source.write_text("original")
    copied = files.copy(str(source), str(target))
    assert target.read_text() == "original"
    target.write_text("later edit")
    with pytest.raises(ValueError, match="changed"):
        files.undo(copied["operation_id"])
    assert target.read_text() == "later edit"
    with pytest.raises(ValueError):
        files.move(str(source), str(target))
    assert source.read_text() == "original"
    moved_path = root / "renamed.txt"
    moved = files.move(str(source), str(moved_path))
    assert not source.exists()
    files.undo(moved["operation_id"])
    assert source.read_text() == "original" and not moved_path.exists()
    with pytest.raises(ValueError):
        files.undo(moved["operation_id"])


def test_folder_operations_validate_every_descendant_before_mutation(services):
    files, root = services.files, services.test_root
    source = root / "project"
    source.mkdir()
    (source / "code.py").write_text("print('safe')")
    (source / ".env").write_text("secret")
    destination = root / "copy"
    for action in (files.copy, files.move, files.zip_create):
        with pytest.raises(ValueError):
            action(str(source), str(destination if action != files.zip_create else destination.with_suffix(".zip")))
    assert source.exists() and not destination.exists()
    assert not destination.with_suffix(".zip").exists()


def test_batch_preview_is_read_only_and_conflicts_are_preflighted(services):
    files, root = services.files, services.test_root
    paths = [root / "alpha.txt", root / "beta.txt"]
    for path in paths:
        path.write_text(path.name)
    preview = files.batch_rename([str(path) for path in paths], prefix="lesson-")
    assert preview["count"] == 2 and all(path.exists() for path in paths)
    conflict = root / "lesson-002.txt"
    conflict.write_text("existing")
    with pytest.raises(ValueError):
        files.batch_rename([str(path) for path in paths], prefix="lesson-", execute=True)
    assert all(path.exists() for path in paths)
    conflict.unlink()
    result = files.batch_rename([str(path) for path in paths], prefix="lesson-", execute=True)
    assert (root / "lesson-001.txt").read_text() == "alpha.txt"
    for item in reversed(result["operations"]):
        files.undo(item["operation_id"])
    assert all(path.exists() for path in paths)


def test_organize_preview_and_undo_order(services):
    files, root = services.files, services.test_root
    (root / "photo.png").write_bytes(b"image fixture")
    (root / "report.pdf").write_bytes(b"pdf fixture")
    preview = files.organize(str(root))
    assert preview["count"] == 2 and not (root / "Images").exists()
    result = files.organize(str(root), execute=True)
    assert (root / "Images" / "photo.png").is_file()
    for item in reversed(result["operations"] + []):
        files.undo(item["operation_id"])
    for item in reversed(result["created_folders"]):
        files.undo(item["operation_id"])
    assert (root / "photo.png").is_file() and not (root / "Images").exists()


@pytest.mark.parametrize("entries", [
    [("../escape.txt", "bad")], [("folder/../../escape.txt", "bad")], [("C:/escape.txt", "bad")],
    [(".env", "bad")], [("file", "one"), ("FILE", "two")],
    [("file", "one"), ("file/child.txt", "two")],
])
def test_zip_rejects_unsafe_entries_before_creating_destination(services, entries):
    root = services.test_root
    archive = root / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, data in entries:
            output.writestr(name, data)
    with pytest.raises(ValueError):
        services.files.zip_extract(str(archive), str(root / "unpacked"))
    assert not (root / "unpacked").exists()


def test_zip_rejects_links_and_expansion_bombs(services, monkeypatch):
    root = services.test_root
    archive = root / "input.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, "outside")
    with pytest.raises(ValueError):
        services.files.zip_extract(str(archive), str(root / "links"))
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("large.txt", "a" * 100)
    monkeypatch.setattr("jarvix.capabilities.files.MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(ValueError):
        services.files.zip_extract(str(archive), str(root / "large"))
    assert not (root / "links").exists() and not (root / "large").exists()


def test_zip_roundtrip_and_duplicate_detection(services):
    files, root = services.files, services.test_root
    source = root / "docs"
    source.mkdir()
    (source / "a.txt").write_text("matching")
    (source / "b.txt").write_text("matching")
    assert files.duplicates(str(source))["groups"][0]["copies"] == 2
    files.zip_create(str(source), str(root / "docs.zip"))
    extracted = files.zip_extract(str(root / "docs.zip"), str(root / "unpacked"))
    assert (root / "unpacked/docs/a.txt").read_text() == "matching"
    files.undo(extracted["operation_id"])
    assert not (root / "unpacked").exists()


def test_cancelled_file_operation_never_removes_source(services):
    source = services.test_root / "a.txt"
    source.write_text("kept")
    cancel = threading.Event()
    with operation(cancel=cancel):
        cancel.set()
        with pytest.raises(InterruptedError):
            services.files.move(str(source), str(source.with_name("b.txt")))
    assert source.read_text() == "kept"
    assert not source.with_name("b.txt").exists()


def test_open_documents_never_launches_executable_associations(services, monkeypatch):
    opened = []
    monkeypatch.setattr(services.files, "_open_native", lambda target: opened.append(target))
    for name in ("program.exe", "run.py", "page.html", "shortcut.lnk", "macro.docm"):
        target = services.test_root / name
        target.write_text("fixture")
        with pytest.raises(ValueError):
            services.files.open(str(target))
    document = services.test_root / "notes.txt"
    document.write_text("fixture")
    services.files.open(str(document))
    services.files.open_folder(str(services.test_root))
    assert opened == [document, services.test_root]


def test_recycle_failure_never_uses_permanent_delete(services, monkeypatch):
    target = services.test_root / "keep.txt"
    target.write_text("keep")
    def fail(*_):
        raise OSError("recycle unavailable")
    monkeypatch.setattr("jarvix.capabilities.native_windows.Win32.recycle", fail)
    if os.name == "nt":
        with pytest.raises(OSError):
            services.files.recycle(str(target))
        assert target.read_text() == "keep"


def test_new_file_tool_schemas_are_strict_and_recycle_is_sensitive(services):
    assert services.registry.get("files.recycle").permission_level == 3
    assert services.registry.get("files.open_folder").permission_level == 2
    assert services.registry.validate("files.copy", {"source": "x", "destination": "y", "overwrite": True})
    assert services.registry.validate("files.batch_rename", {"paths": ["x"] * 101})
