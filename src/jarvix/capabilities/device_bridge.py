"""Offline, opt-in authenticated device envelopes. No transport or remote tool execution."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from contextlib import contextmanager

from jsonschema import Draft202012Validator

from jarvix import __version__
from jarvix.capabilities.diagnostics import access
from jarvix.capabilities.productivity import timestamp
from jarvix.capabilities.schema import ID, array, enum, integer, register, schema, string
from jarvix.providers._transport import load_json
from jarvix.security import CredentialVault
from jarvix.services import required_text
from jarvix.storage import now_iso
from jarvix.runtime import check_cancelled

CAPABILITIES = ("status", "task_proposal", "note_proposal")
PUBLIC_IDENTITY = schema({"version": {"const": 1}, "device_id": string(64),
                          "signing_key": string(44), "exchange_key": string(44)},
                         ("version", "device_id", "signing_key", "exchange_key"))
PAYLOADS = {"status": schema(),
            "task_proposal": schema({"title": string(300), "due_at": string(40), "project_id": ID}, ("title",)),
            "note_proposal": schema({"title": string(200), "body": string(4000), "project_id": ID}, ("title", "body"))}
HEADER = schema({"version": {"const": 1}, "sender": string(64), "recipient": string(64),
                 "sequence": integer(1, 2**53 - 1), "issued_at": integer(0, 2**53 - 1),
                 "expires_at": integer(0, 2**53 - 1), "kind": enum(*CAPABILITIES), "nonce": string(16)},
                ("version", "sender", "recipient", "sequence", "issued_at", "expires_at", "kind", "nonce"))
ENVELOPE = schema({"header": HEADER, "ciphertext": string(10000), "signature": string(88)},
                  ("header", "ciphertext", "signature"))
DOMAIN = b"Jarvix.DeviceBridge.v1\x00"


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.exceptions import InvalidSignature, InvalidTag
    except ImportError:
        raise RuntimeError("The packaged cryptography dependency is required for device pairing.") from None
    return ed25519, x25519, ChaCha20Poly1305, HKDF, hashes, InvalidSignature, InvalidTag


def _encoded(value, maximum=16000):
    try:
        raw = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Use bounded, standard JSON for device data.") from None
    if len(raw) > maximum:
        raise ValueError("Device data exceeds its bounded size limit.")
    return raw


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _bytes(value, size):
    if not isinstance(value, str):
        raise ValueError("Invalid device key or envelope encoding.")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid device key or envelope encoding.") from None
    if len(raw) != size:
        raise ValueError("Invalid device key or envelope size.")
    return raw


def _identity(value):
    if not Draft202012Validator(PUBLIC_IDENTITY).is_valid(value):
        raise ValueError("Use the complete public identity from the other device.")
    signing, exchange = _bytes(value["signing_key"], 32), _bytes(value["exchange_key"], 32)
    fingerprint = hashlib.sha256(DOMAIN + signing + exchange).hexdigest()
    if value["device_id"] != fingerprint:
        raise ValueError("Device identity and public-key fingerprint differ.")
    return dict(value)


class DeviceBridgeService:
    def __init__(self, services, vault_backend=None, clock=None):
        self.s, self._vault_backend = services, vault_backend
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._account = hashlib.sha256(str(services.data_dir.resolve()).casefold().encode()).hexdigest()

    @contextmanager
    def _transaction(self, *tools):
        # Other windows/processes can open this profile. The SQLite writer lock,
        # rather than the instance lock alone, owns identity and replay updates.
        with self._lock, self.s.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            check_cancelled()
            for tool in tools:
                access(self.s, tool)
            yield conn

    def _record(self, conn, device_id):
        row = conn.execute("SELECT * FROM records WHERE kind=? AND id=?", ("device_peer", device_id)).fetchone()
        if row is None:
            raise ValueError("Record not found.")
        return self.s.records._decode(dict(row))

    @staticmethod
    def _save_peer(conn, row, device_id):
        value = {key: value for key, value in row.items() if key not in {"id", "created_at", "updated_at"}}
        data = _encoded(value).decode("utf-8")
        stamp = now_iso()
        # Bound writes on the existing ProtectedConnection retain DPAPI policy.
        conn.execute("INSERT INTO records(id,kind,data,created_at,updated_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(id,kind) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                     (device_id, "device_peer", data, stamp, stamp))

    def _backend(self):
        if self._vault_backend is not None:
            return self._vault_backend
        return CredentialVault()._backend()

    def _keys(self, create=False):
        ed25519, x25519, *_ = _crypto()
        backend = self._backend()
        raw = backend.get_password("Jarvix.DeviceBridge", self._account)
        if not raw:
            if not create:
                return None
            signing, exchange = ed25519.Ed25519PrivateKey.generate(), x25519.X25519PrivateKey.generate()
            raw = json.dumps({"version": 1, "signing": _b64(signing.private_bytes_raw()),
                              "exchange": _b64(exchange.private_bytes_raw())})
            backend.set_password("Jarvix.DeviceBridge", self._account, raw)
        if not isinstance(raw, str) or len(raw) > 1000:
            raise RuntimeError("The device identity in the OS vault is damaged.")
        value = load_json(raw)
        if not isinstance(value, dict) or set(value) != {"version", "signing", "exchange"} or value["version"] != 1:
            raise RuntimeError("The device identity in the OS vault is incompatible.")
        return (ed25519.Ed25519PrivateKey.from_private_bytes(_bytes(value["signing"], 32)),
                x25519.X25519PrivateKey.from_private_bytes(_bytes(value["exchange"], 32)))

    @staticmethod
    def _public(keys):
        signing, exchange = (key.public_key().public_bytes_raw() for key in keys)
        return {"version": 1, "device_id": hashlib.sha256(DOMAIN + signing + exchange).hexdigest(),
                "signing_key": _b64(signing), "exchange_key": _b64(exchange)}

    def identity(self):
        access(self.s, "devices.identity")
        with self._lock:
            keys = self._keys()
            if keys is None:
                return {"initialized": False, "transport": "offline", "listener_started": False}
            public = self._public(keys)
        return {"initialized": True, "identity": public, "fingerprint": public["device_id"],
                "review": "Compare this fingerprint on both devices through a trusted channel before pairing.",
                "transport": "offline", "listener_started": False}

    def initialize(self):
        access(self.s, "devices.initialize")
        access(self.s, "devices.identity")
        with self._transaction("devices.initialize", "devices.identity"):
            self._keys(create=True)
        return self.identity()

    def pair(self, name, identity, fingerprint, capabilities):
        access(self.s, "devices.pair")
        access(self.s, "devices.identity")
        peer = _identity(identity)
        if (fingerprint != peer["device_id"] or not isinstance(capabilities, list) or not capabilities
                or len(capabilities) > 3 or set(capabilities) - set(CAPABILITIES)):
            raise ValueError("Review the exact identity fingerprint and choose only supported capabilities.")
        name = required_text(name, "Device name", 100)
        with self._transaction("devices.pair", "devices.identity") as conn:
            keys = self._keys()
            if keys is None:
                raise ValueError("Initialize this device identity before pairing.")
            own = self._public(keys)
            if peer["device_id"] == own["device_id"]:
                raise ValueError("A device cannot pair with its own identity.")
            # Reject invalid X25519 exchanges before saving a reviewed peer.
            self._key(keys, peer, own["device_id"], peer["device_id"])
            try:
                previous = self._record(conn, peer["device_id"])
            except ValueError:
                previous = {}
            active = sum(not load_json(row["data"]).get("revoked", True)
                         for row in conn.execute("SELECT data FROM records WHERE kind=?", ("device_peer",)))
            if active >= 20 and (not previous or previous.get("revoked", True)):
                raise ValueError("Revoke an unused pairing before adding more than 20 active devices.")
            row = {"name": name, "identity": peer, "capabilities": sorted(set(capabilities)), "revoked": False,
                   "last_received_sequence": previous.get("last_received_sequence", 0),
                   "last_sent_sequence": previous.get("last_sent_sequence", 0), "paired_at": now_iso()}
            self._save_peer(conn, row, peer["device_id"])
        return {"device_id": peer["device_id"], "name": name, "fingerprint": fingerprint,
                "capabilities": row["capabilities"], "transport": "offline", "remote_execution": False}

    def list(self):
        access(self.s, "devices.list")
        return {"items": [{"device_id": row["id"], "name": row["name"], "fingerprint": row["id"],
                           "capabilities": row["capabilities"], "revoked": row.get("revoked", True),
                           "paired_at": row.get("paired_at")} for row in self.s.records.list("device_peer")],
                "transport": "offline", "listener_started": False, "remote_execution": False}

    def revoke(self, device_id):
        access(self.s, "devices.revoke")
        with self._transaction("devices.revoke") as conn:
            row = self._record(conn, device_id)
            row.update(revoked=True, revoked_at=now_iso())
            self._save_peer(conn, row, device_id)
        return {"device_id": device_id, "revoked": True}

    def _peer(self, device_id, kind, conn):
        row = self._record(conn, device_id)
        if row.get("revoked", True) or kind not in row.get("capabilities", []):
            raise PermissionError("The device is revoked or lacks this reviewed capability.")
        _identity(row["identity"])
        if row["identity"]["device_id"] != device_id:
            raise PermissionError("Saved pairing identity is inconsistent.")
        return row

    @staticmethod
    def _key(keys, identity, sender, recipient):
        _, x25519, _, HKDF, hashes, *_ = _crypto()
        shared = keys[1].exchange(x25519.X25519PublicKey.from_public_bytes(_bytes(identity["exchange_key"], 32)))
        return HKDF(algorithm=hashes.SHA256(), length=32,
                    salt=hashlib.sha256(DOMAIN + "|".join(sorted((sender, recipient))).encode()).digest(),
                    info=DOMAIN + sender.encode() + b":" + recipient.encode()).derive(shared)

    def _payload(self, kind, payload, receiving=False):
        if kind not in PAYLOADS or not Draft202012Validator(PAYLOADS[kind]).is_valid(payload):
            raise ValueError("Use only supported bounded status, task or note proposals.")
        _encoded(payload, 6500)
        if kind == "status":
            return None
        if payload.get("due_at"):
            timestamp(payload["due_at"])
        tool = "tasks.create" if kind == "task_proposal" else "notes.create"
        access(self.s, tool)
        # Proposals are untrusted input. They never receive arbitrary tool names or arguments.
        arguments = {key: value for key, value in payload.items() if key != "project_id"}
        if self.s.registry.validate(tool, arguments):
            raise ValueError("The proposal does not match the current local tool schema.")
        project = None
        if receiving and payload.get("project_id"):
            project = self.s.context_graph.resolve("project", payload["project_id"])
        return {"tool": tool, "arguments": arguments, "project": project,
                "requires_local_review": True, "executed": False, "content_trust": "untrusted_peer_input"}

    def seal(self, device_id, kind, payload, ttl_seconds=120):
        access(self.s, "devices.seal")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 300:
            raise ValueError("Device envelopes expire after 30–300 seconds.")
        with self._transaction("devices.seal") as conn:
            self._payload(kind, payload)
            row, keys = self._peer(device_id, kind, conn), self._keys()
            if keys is None:
                raise ValueError("Initialize this device identity before sealing data.")
            own = self._public(keys)
            sequence = row.get("last_sent_sequence", 0) + 1
            if sequence > 2**53 - 1:
                raise ValueError("Device sequence exhausted; create a new identity before sending.")
            _, _, cipher, *_ = _crypto()
            import os
            issued = int(self._clock())
            nonce = os.urandom(12)
            header = {"version": 1, "sender": own["device_id"], "recipient": device_id, "sequence": sequence,
                      "issued_at": issued, "expires_at": issued + ttl_seconds, "kind": kind, "nonce": _b64(nonce)}
            aad = DOMAIN + _encoded(header)
            ciphertext = cipher(self._key(keys, row["identity"], own["device_id"], device_id)).encrypt(nonce, _encoded(payload, 6500), aad)
            signature = keys[0].sign(aad + ciphertext)
            row["last_sent_sequence"] = sequence
            access(self.s, "devices.seal")
            self._save_peer(conn, row, device_id)
        return {"envelope": {"header": header, "ciphertext": _b64(ciphertext), "signature": _b64(signature)},
                "sent": False, "transport": "offline", "expires_at": header["expires_at"]}

    def receive(self, envelope):
        access(self.s, "devices.receive")
        _encoded(envelope)
        if not Draft202012Validator(ENVELOPE).is_valid(envelope):
            raise ValueError("Invalid or unsupported device envelope.")
        header = envelope["header"]
        with self._transaction("devices.receive") as conn:
            row, keys = self._peer(header["sender"], header["kind"], conn), self._keys()
            if keys is None or header["recipient"] != self._public(keys)["device_id"]:
                raise PermissionError("Device envelope was addressed to another identity.")
            now = int(self._clock())
            if (header["issued_at"] > now + 30 or header["expires_at"] <= now
                    or not 30 <= header["expires_at"] - header["issued_at"] <= 300
                    or header["sequence"] <= row.get("last_received_sequence", 0)):
                raise PermissionError("Device envelope expired or was already received.")
            ed25519, _, cipher, _, _, InvalidSignature, InvalidTag = _crypto()
            aad = DOMAIN + _encoded(header)
            try:
                ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
                ed25519.Ed25519PublicKey.from_public_bytes(_bytes(row["identity"]["signing_key"], 32)).verify(
                    _bytes(envelope["signature"], 64), aad + ciphertext)
                plaintext = cipher(self._key(keys, row["identity"], header["sender"], header["recipient"])).decrypt(
                    _bytes(header["nonce"], 12), ciphertext, aad)
            except (InvalidSignature, InvalidTag, ValueError):
                raise PermissionError("Device envelope authentication failed.") from None
            if len(plaintext) > 6500:
                raise ValueError("Device payload exceeds its size limit.")
            proposal = self._payload(header["kind"], load_json(plaintext), receiving=True)
            access(self.s, "devices.receive")
            # Persist acceptance before returning anything. Repeated/reordered envelopes
            # cannot replay a proposal after restart, and no peer content is retained.
            row["last_received_sequence"] = header["sequence"]
            row["last_received_at"] = now_iso()
            self._save_peer(conn, row, header["sender"])
        result = {"accepted": True, "device_id": header["sender"], "kind": header["kind"],
                  "sequence": header["sequence"], "executed": False, "proposal_content_stored": False}
        if proposal:
            result["proposal"] = proposal
        else:
            result["status"] = {"jarvix_version": __version__, "remote_execution": False, "transport": "offline"}
        return result


def setup(s, registry):
    service = s.devices = DeviceBridgeService(s)
    register(registry, "devices.identity", "Inspect only the public device identity and trusted-channel pairing fingerprint; never generates keys implicitly.",
             {}, (), service.identity)
    register(registry, "devices.initialize", "Create this profile's Ed25519/X25519 identity in the OS credential vault after fresh confirmation. Starts no listener.",
             {}, (), service.initialize, 3, "devices.pair")
    register(registry, "devices.pair", "Review the exact other-device public identity, compare its fingerprint through a trusted channel, and approve only named proposal capabilities.",
             {"name": string(100), "identity": PUBLIC_IDENTITY, "fingerprint": string(64),
              "capabilities": {**array(enum(*CAPABILITIES), 3), "minItems": 1, "uniqueItems": True}},
             ("name", "identity", "fingerprint", "capabilities"), service.pair, 3, "devices.pair")
    register(registry, "devices.list", "List reviewed device identities, capabilities and revocations; no network discovery.", {}, (), service.list)
    register(registry, "devices.revoke", "Revoke a paired identity immediately, keeping replay counters so repairing cannot accept old envelopes.",
             {"device_id": ID}, ("device_id",), service.revoke, 3, "devices.pair")
    register(registry, "devices.seal", "Encrypt and sign the exact bounded proposal for the reviewed peer after fresh confirmation. Returns an offline envelope; never transmits it.",
             {"device_id": ID, "kind": enum(*CAPABILITIES), "payload": {"type": "object"}, "ttl_seconds": integer(30, 300)},
             ("device_id", "kind", "payload"), service.seal, 3, "devices.disclose")
    register(registry, "devices.receive", "Authenticate an explicit offline envelope, reject expired/replayed/revoked messages, and return only status or a task/note proposal for local review. Never executes peer instructions.",
             {"envelope": ENVELOPE}, ("envelope",), service.receive, 2, "devices.receive")
