# Jarvix 0.4 downloads

The source package is always available from the default branch:

- [Download the source ZIP](https://github.com/zyadd1111111/Jarvix/archive/refs/heads/main.zip)
- [Open the source tree](https://github.com/zyadd1111111/Jarvix/tree/main)

For a Windows desktop package, open the repository's **Actions** tab and select
the latest **Package Jarvix** run. Its `Jarvix-v0.4.0-windows-x64` artifact
contains the portable executable and wheel after the packaging workflow passes.

## Install from source

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m jarvix
```

The portable build includes `Jarvix.exe`; see [README.md](README.md) for
configuration, permissions, and the supported installation paths.

