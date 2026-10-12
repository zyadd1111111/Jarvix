"""Create a portable ZIP only after every staged byte has been verified."""
import hashlib
from pathlib import Path
import sys
import zipfile


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def staged_files(folder):
    return {folder.name + "/" + path.relative_to(folder).as_posix(): path
            for path in sorted(folder.rglob("*")) if path.is_file()}


def create_archive(folder, archive):
    folder = folder.resolve(strict=True)
    archive = archive.resolve()
    if not folder.is_dir() or archive.is_relative_to(folder):
        raise ValueError("The archive must be outside the staged package directory.")
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    files = staged_files(folder)
    expected = {name: file_hash(path) for name, path in files.items()}
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for name, path in files.items():
                output.write(path, name)
        with zipfile.ZipFile(temporary) as output:
            if len(output.namelist()) != len(expected) or set(output.namelist()) != set(expected):
                raise RuntimeError("Archive entries differ from the staged package.")
            if bad := output.testzip():
                raise RuntimeError(f"Archive CRC validation failed: {bad}")
            for name, checksum in expected.items():
                with output.open(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                        raise RuntimeError(f"Archive SHA256 validation failed: {name}")
        current = {name: file_hash(path) for name, path in staged_files(folder).items()}
        if current != expected:
            raise RuntimeError("Staged files changed while creating the archive.")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = file_hash(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii")
    print(f"Verified {len(files)} archived files (CRC + SHA256): {archive}")
    return checksum


if __name__ == "__main__":
    create_archive(Path(sys.argv[1]), Path(sys.argv[2]))
