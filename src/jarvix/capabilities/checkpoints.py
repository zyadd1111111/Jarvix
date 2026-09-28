"""Current-user Windows DPAPI protection for private, local Operator checkpoints."""
import base64
import ctypes
import json
import sys
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if sys.platform != "win32":
        raise RuntimeError("Protected restart checkpoints currently require Windows.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "CryptUnprotectData" if decrypt else "CryptProtectData"
    method = getattr(crypt, name)
    method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    raw = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    entropy_bytes = b"Jarvix.Operator.Checkpoint.v1"
    entropy_raw = (ctypes.c_ubyte * len(entropy_bytes)).from_buffer_copy(entropy_bytes)
    source, entropy, target = Blob(len(data), raw), Blob(len(entropy_bytes), entropy_raw), Blob()
    if not method(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Windows could not protect or open this checkpoint.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class CheckpointStore:
    def __init__(self, records, codec=protect):
        self.records, self.codec = records, codec

    def save(self, id, plan, results, mutations):
        raw = json.dumps({"version": 1, "id": id, "plan": plan, "results": results,
                          "mutations": sorted(mutations)}, allow_nan=False).encode()
        if len(raw) > 128000:
            raise ValueError("Private checkpoint exceeded its bounded storage size.")
        ciphertext = self.codec(raw)
        self.records.put("operator_checkpoint", {"payload": base64.b64encode(ciphertext).decode()}, id)

    def load(self, id):
        row = self.records.get("operator_checkpoint", id)
        value = json.loads(self.codec(base64.b64decode(row["payload"], validate=True), decrypt=True))
        if value.get("version") != 1 or value.get("id") != id:
            raise ValueError("Checkpoint identity or version is invalid.")
        return value

    def delete(self, id):
        self.records.delete("operator_checkpoint", id)

