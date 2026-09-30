"""Verify x64 binaries and frozen application code against the current checkout."""
import hashlib
import struct
import sys
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def digest(code):
    values = [getattr(code, name) for name in ("co_code", "co_names", "co_varnames", "co_freevars",
        "co_cellvars", "co_flags", "co_argcount", "co_posonlyargcount", "co_kwonlyargcount",
        "co_exceptiontable")]
    for value in code.co_consts:
        values.append(digest(value) if isinstance(value, types.CodeType) else
                      tuple(sorted(value, key=repr)) if isinstance(value, frozenset) else value)
    return hashlib.sha256(repr(values).encode()).hexdigest()


def main(folder):
    root = Path(__file__).resolve().parent.parent
    archives = []
    for name in ("Jarvix.exe", "JarvixBrowserHost.exe"):
        path = folder / name
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError(f"{name} has no valid Windows executable header.")
            stream.seek(0x3C)
            offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(offset)
            if stream.read(6) != b"PE\0\0\x64\x86":
                raise RuntimeError(f"{name} is not an x64 Windows executable.")
        archives.append(CArchiveReader(str(path)).open_embedded_archive("PYZ.pyz"))
    checked = 0
    for path in (root / "src" / "jarvix").rglob("*.py"):
        parts = list(path.relative_to(root / "src").with_suffix("").parts)
        if parts[-1] == "__main__":
            continue  # Desktop entry script lives in the outer executable archive.
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        found = [archive.extract(name) for archive in archives if name in archive.toc]
        if not found:
            raise RuntimeError(f"Application module missing from package: {name}")
        source = compile(path.read_text(encoding="utf-8-sig"), str(path), "exec", dont_inherit=True)
        if any(digest(frozen) != digest(source) for frozen in found):
            raise RuntimeError(f"Source changed or stale code was packaged: {name}")
        checked += 1
    print(f"Verified both x64 binaries; {checked} frozen application modules match source.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
