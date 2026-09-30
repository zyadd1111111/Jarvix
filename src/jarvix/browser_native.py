"""Chrome/Edge stdio native host. This entry point never loads the desktop UI."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import queue
import threading
from pathlib import Path

from jarvix.browser_bridge import MAX_MESSAGE, receive_message, registry_path, send_message, vault_secret


def read_native(stream):
    header = stream.read(4)
    if not header:
        raise EOFError
    if len(header) != 4:
        raise ValueError("Incomplete native header.")
    size = struct.unpack("=I", header)[0]
    if not 1 <= size <= MAX_MESSAGE:
        raise ValueError("Invalid native message size.")
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            raise EOFError
        chunks.extend(part)
    value = json.loads(chunks)
    if not isinstance(value, dict):
        raise ValueError("Invalid native message.")
    return value


def write_native(stream, value):
    data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("Native message exceeds limit.")
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def configuration(origin, browser):
    if os.name != "nt":
        raise RuntimeError("The packaged native browser helper requires Windows.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path(browser)) as key:
        manifest_path = Path(winreg.QueryValueEx(key, "")[0]).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if origin not in manifest.get("allowed_origins", []):
        raise PermissionError("This extension is not registered.")
    if Path(manifest["path"]).resolve() != Path(sys.executable).resolve() and getattr(sys, "frozen", False):
        raise PermissionError("Native host path does not match registration.")
    if manifest_path.name != f"{browser}-host.json" or manifest_path.parent.name != "browser":
        raise PermissionError("Invalid Jarvix browser registration.")
    return manifest_path.parent.parent


def main():
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        origin = next((arg for arg in sys.argv[1:] if arg.startswith("chrome-extension://")), "")
        hello = read_native(sys.stdin.buffer)
        browser = hello.get("browser")
        data_dir = configuration(origin, browser)
        endpoint = json.loads((data_dir / "browser" / "endpoint.json").read_text(encoding="utf-8"))
        port = endpoint.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid local endpoint.")
        secret = vault_secret(data_dir)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.settimeout(.2)
            challenge = receive_message(connection, 3)["challenge"]
            extension = origin.removeprefix("chrome-extension://").rstrip("/")
            proof = hmac.new(secret, f"{challenge}:{browser}:{extension}".encode(), hashlib.sha256).hexdigest()
            send_message(connection, {"browser": browser, "extension_id": extension, "proof": proof})
            ready = receive_message(connection, 3)
            expected = hmac.new(secret, (challenge + ":server").encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, str(ready.get("server_proof", ""))):
                raise PermissionError("The Jarvix process could not be authenticated.")
            write_native(sys.stdout.buffer, {"connected": True})
            incoming, ended = queue.Queue(maxsize=2), threading.Event()
            def read_responses():
                try:
                    while not ended.is_set():
                        incoming.put(read_native(sys.stdin.buffer), timeout=1)
                except (EOFError, OSError, ValueError, queue.Full):
                    ended.set()
            threading.Thread(target=read_responses, daemon=True, name="browser-native-input").start()
            def check_connection():
                if ended.is_set():
                    raise EOFError
            while True:
                # A disconnected Jarvix immediately closes this socket and host.
                request = receive_message(connection, timeout=86400, cancel_check=check_connection)
                write_native(sys.stdout.buffer, request)
                response = incoming.get(timeout=15)
                if response.get("id") != request["id"]:
                    raise ValueError("Mismatched browser response.")
                send_message(connection, response)
    except (OSError, EOFError, ValueError, KeyError, PermissionError, RuntimeError, queue.Empty):
        # Never put diagnostics on stdout: it belongs to the framing protocol.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
