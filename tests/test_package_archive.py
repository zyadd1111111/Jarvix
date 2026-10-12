import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest


spec = importlib.util.spec_from_file_location("package_archive",
    Path(__file__).resolve().parents[1] / "scripts/create_package_archive.py")
package_archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_archive)


def test_portable_archive_preserves_runtime_and_documentation(tmp_path):
    folder = tmp_path / "Jarvix"
    contents = {"Jarvix.exe": b"MZ-test", "_internal/Qt6Widgets.dll": bytes(range(256)) * 200,
                "README.md": b"Install instructions", "docs/creme.txt": "Crème".encode(),
                ".hidden": b"included"}
    for name, data in contents.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    archive = tmp_path / "Jarvix-x64.zip"
    checksum = package_archive.create_archive(folder, archive)
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        assert set(output.namelist()) == {"Jarvix/" + name for name in contents}
        for name, data in contents.items():
            assert output.read("Jarvix/" + name) == data
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert archive.with_suffix(".zip.sha256").read_text() == f"{checksum}  {archive.name}\n"


def test_archive_corruption_does_not_replace_existing_download(tmp_path, monkeypatch):
    folder = tmp_path / "Jarvix"
    folder.mkdir()
    (folder / "Qt6Widgets.dll").write_bytes(b"original runtime")
    archive = tmp_path / "Jarvix-x64.zip"
    archive.write_bytes(b"previous verified download")

    class CorruptArchive(zipfile.ZipFile):
        def write(self, filename, arcname=None, **kwargs):
            self.writestr(arcname, b"corrupted runtime")

    monkeypatch.setattr(package_archive.zipfile, "ZipFile", CorruptArchive)
    with pytest.raises(RuntimeError, match="SHA256 validation failed"):
        package_archive.create_archive(folder, archive)
    assert archive.read_bytes() == b"previous verified download"
    assert not archive.with_suffix(".zip.tmp").exists()
    assert not archive.with_suffix(".zip.sha256").exists()
