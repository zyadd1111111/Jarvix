# Jarvix downloads

The source package is always available from the default branch:

- [Download the source ZIP](https://github.com/zyadd1111111/Jarvix/archive/refs/heads/main.zip)
- [Open the source tree](https://github.com/zyadd1111111/Jarvix/tree/main)

Published portable packages are listed on [GitHub Releases](https://github.com/zyadd1111111/Jarvix/releases).
The local 0.5 build produces `dist/Jarvix-0.5.0-windows-x64.zip`; extract it and run
`Jarvix/Jarvix.exe`. Keep its `_internal` folder and browser helper alongside it.
The **Package Jarvix** workflow also supplies versioned artifacts after passing checks.
Building locally does not upload a GitHub release.

## Install from source

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m jarvix
```

The portable build includes `Jarvix.exe`; see [README.md](README.md) for
configuration, permissions, and the supported installation paths.
