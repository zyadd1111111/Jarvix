"""Check packaged native framing without registering a browser or opening the vault."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys


def main():
    helper = Path(sys.argv[1]).resolve(strict=True)
    unsupported = json.dumps({"browser": "unsupported"}).encode()
    # EOF/invalid lengths fail before configuration; an unsupported browser also
    # fails before registry access. Any stray stdout corrupts native messaging.
    for payload in (b"", struct.pack("=I", 0), struct.pack("=I", len(unsupported)) + unsupported):
        completed = subprocess.run([str(helper)], input=payload, capture_output=True,
                                   timeout=15, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode != 1 or completed.stdout or completed.stderr:
            raise RuntimeError("Native helper failed closed-input protocol validation.")
    print("Packaged browser helper: framed input rejected cleanly; no registration changes.")


if __name__ == "__main__":
    main()
