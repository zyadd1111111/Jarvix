"""A bounded desktop operator layered over the existing native services."""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from contextlib import contextmanager

from jarvix.capabilities.schema import BOOL, ID, enum, integer, register, string
from jarvix.capabilities.windows_uia import WindowsUIAutomation
from jarvix.runtime import check_cancelled


SECRET = re.compile(r"(?i)(?:password|passcode|credential|api[_ -]?key|access[_ -]?token|secret|authorization)\s*[:=]\s*\S+|\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{15,})")
PROTECTED = re.compile(r"(?i)password|passcode|credential|one.time.code|verification.code|security.code|api.key|access.token|secret.key|captcha")
CONFIRMATION = re.compile(r"(?i)^\s*(yes|ok|okay|confirm|allow|approve|authorize|accept|grant|trust|continue|run anyway)\b|bypass|disable.security|turn.off.protection")
SHORTCUTS = {
    "CTRL+F": [0x11, 0x46], "CTRL+L": [0x11, 0x4C], "CTRL+A": [0x11, 0x41],
    "CTRL+C": [0x11, 0x43], "CTRL+TAB": [0x11, 0x09], "CTRL+SHIFT+TAB": [0x11, 0x10, 0x09],
    "TAB": [0x09], "SHIFT+TAB": [0x10, 0x09], "ESC": [0x1B],
    "HOME": [0x24], "END": [0x23], "PAGEUP": [0x21], "PAGEDOWN": [0x22],
}
SETTINGS_PAGES = {"home": "", "bluetooth": "bluetooth", "display": "display", "sound": "sound",
                  "wifi": "network-wifi", "network": "network-status", "apps": "appsfeatures"}


def redact(value):
    return SECRET.sub("[redacted]", str(value))[:512]


class DesktopOperatorService:
    REF_TTL = 120

    def __init__(self, services, backend=None):
        self.services = services
        self.backend = backend or WindowsUIAutomation(self._checkpoint)
        self._refs = {}
        self._refs_lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._paused = threading.Event()
        self._indicator = None
        self._wait_deadline = None
        self._state = {"active": False, "paused": False}

    def set_indicator(self, callback):
        """The desktop UI must acknowledge the visible HUD before every write."""
        self._indicator = callback

    def status(self):
        return {**self._state, "paused": self._paused.is_set()}

    def pause(self):
        self._paused.set()
        return self.status()

    def resume(self):
        self._paused.clear()
        return self.status()

    def cancel(self):
        self._cancelled.set()
        self._paused.clear()
        return {"cancellation_requested": self._state["active"]}

    def _checkpoint(self):
        check_cancelled()
        if self._wait_deadline is not None and time.monotonic() >= self._wait_deadline:
            raise TimeoutError("Desktop wait timed out.")
        if self._state["active"]:
            while self._paused.is_set():
                if self._cancelled.wait(0.05):
                    raise InterruptedError("Desktop operation cancelled.")
                check_cancelled()
                if self._wait_deadline is not None and time.monotonic() >= self._wait_deadline:
                    raise TimeoutError("Desktop wait timed out while paused.")
            if self._cancelled.is_set():
                raise InterruptedError("Desktop operation cancelled.")

    def _screen_gate(self):
        self._checkpoint()
        if not self.services.settings.get("screenshots.enabled", False):
            raise PermissionError("Enable screen access before inspecting another application's controls.")

    @contextmanager
    def _action(self, action, handle=None, process_id=None, visible=True):
        check_cancelled()
        if not self._action_lock.acquire(blocking=False):
            raise RuntimeError("Another desktop action is active.")
        self._cancelled.clear()
        self._paused.clear()
        self._state = {"active": True, "action": action, "handle": handle, "process_id": process_id}
        try:
            if visible and (self._indicator is None or self._indicator({**self._state, "state": "running"}) is not True):
                raise PermissionError("Desktop control needs the visible Jarvix control indicator.")
            self._checkpoint()
            yield
        finally:
            self._paused.clear()
            self._state = {"active": False, "paused": False}
            try:
                if visible and self._indicator:
                    self._indicator({"state": "idle", "active": False})
            finally:
                self._action_lock.release()

    def windows(self, query=""):
        self._checkpoint()
        return [{**row, "title": redact(row["title"])} for row in self.services.windows.list(query)]

    def active_window(self):
        self._checkpoint()
        row = self.services.windows.foreground()
        return {**row, "title": redact(row["title"])} if row else {"available": False}

    def _window(self, handle, process_id):
        row = next((row for row in self.services.windows.list()
                    if row["handle"] == handle and row["process_id"] == process_id), None)
        if row is None:
            raise ValueError("Window is no longer visible or belongs to a different process.")
        return row

    def inspect_ui(self, handle, process_id, limit=120, depth=10):
        self._screen_gate()
        self._window(handle, process_id)
        if not 1 <= limit <= 180 or not 1 <= depth <= 16:
            raise ValueError("Inspection bounds exceed the supported limits.")
        result = self.backend.call("inspect", handle=handle, process_id=process_id, limit=limit, depth=depth)
        self._screen_gate()
        public = []
        now = time.monotonic()
        with self._refs_lock:
            self._refs = {key: value for key, value in self._refs.items() if value["expires"] > now}
            for row in result["elements"][:limit]:
                protected = bool(row.get("password") or PROTECTED.search(row.get("name", "") + " " + row.get("automation_id", "")) or SECRET.search(row.get("name", "")))
                view = {key: value for key, value in row.items() if key not in {"runtime_id", "value", "text"}}
                view["name"] = "[protected]" if protected else redact(row.get("name", ""))
                view["automation_id"] = "" if protected else redact(row.get("automation_id", ""))
                view["password"] = protected
                if not protected:
                    reference = uuid.uuid4().hex
                    self._refs[reference] = {"handle": handle, "process_id": process_id,
                                             "process_started": result["process_started"], "root_id": result["root_id"],
                                             "runtime_id": row["runtime_id"], "expected_name": row.get("name", ""),
                                             "expected_id": row.get("automation_id", ""), "expected_type": row["control_type"],
                                             "expires": now + self.REF_TTL}
                    view["element_ref"] = reference
                public.append(view)
            if len(self._refs) > 1200:
                self._refs = dict(list(self._refs.items())[-1200:])
        return {"handle": handle, "process_id": process_id, "application": redact(result.get("application", "")),
                "elements": public, "bounded": result.get("bounded", False), "reference_lifetime_seconds": self.REF_TTL,
                "source": "Windows UI Automation; password fields and values are excluded"}

    def find_element(self, handle, process_id, name="", control_type="", exact=False):
        if not name and not control_type:
            raise ValueError("Specify a control name or type.")
        result = self.inspect_ui(handle, process_id, limit=180)
        needle = name.casefold()
        matches = [row for row in result["elements"] if not row["password"]
                   and (not control_type or row["control_type"].casefold() == control_type.casefold())
                   and (not name or (row["name"].casefold() == needle if exact else needle in row["name"].casefold()))]
        return {"matches": matches[:30], "count": len(matches), "ambiguous": len(matches) > 1,
                "bounded": result["bounded"], "handle": handle, "process_id": process_id}

    def _reference(self, element_ref):
        self._screen_gate()
        with self._refs_lock:
            row = dict(self._refs.get(element_ref, {}))
        if not row or row.pop("expires") <= time.monotonic():
            raise ValueError("The control reference expired. Inspect the window again.")
        if CONFIRMATION.search(row["expected_name"]):
            raise PermissionError("Application/security confirmations must be handled directly by the user.")
        self._window(row["handle"], row["process_id"])
        return row

    def _element_action(self, operation, element_ref, capture=False, **arguments):
        row = self._reference(element_ref)
        with self._action(operation, row["handle"], row["process_id"]):
            shots = {}
            if capture:
                shots["before"] = self.services.vision.capture("window", handle=row["handle"], process_id=row["process_id"])["id"]
            self._screen_gate()
            result = self.backend.call(operation, **row, **arguments)
            self._checkpoint()
            if capture:
                shots["after"] = self.services.vision.capture("window", handle=row["handle"], process_id=row["process_id"])["id"]
            return {**result, "handle": row["handle"], "process_id": row["process_id"],
                    "element_ref": element_ref, "screenshots": shots}

    def click_element(self, element_ref, button="left", count=1, pointer_fallback=False, capture=False):
        if button not in {"left", "right"} or count not in {1, 2}:
            raise ValueError("Unsupported click.")
        return self._element_action("click", element_ref, capture, button=button, count=count,
                                    pointer_fallback=pointer_fallback)

    def focus_element(self, element_ref):
        return self._element_action("focus", element_ref)

    def type_text(self, element_ref, text, capture=False):
        if not isinstance(text, str) or len(text) > 12000 or "\0" in text or SECRET.search(text):
            raise PermissionError("Text must be bounded and cannot contain credentials.")
        return self._element_action("type", element_ref, capture, text=text)

    def scroll(self, element_ref, direction="down", steps=1):
        if direction not in {"up", "down", "left", "right"} or not 1 <= steps <= 10:
            raise ValueError("Unsupported scroll.")
        return self._element_action("scroll", element_ref, direction=direction, steps=steps)

    def send_shortcut(self, handle, process_id, shortcut):
        self._screen_gate()
        self._window(handle, process_id)
        shortcut = shortcut.upper().replace(" ", "")
        if shortcut not in SHORTCUTS:
            raise PermissionError("Only navigation/copy shortcuts are supported; execution/confirmation shortcuts are excluded.")
        if shortcut == "CTRL+C" and not self.services.settings.get("clipboard.enabled", False):
            raise PermissionError("Enable clipboard access before copying application content.")
        snapshot = self.backend.call("inspect", handle=handle, process_id=process_id, limit=1)
        with self._action("shortcut " + shortcut, handle, process_id):
            self._screen_gate()
            self._window(handle, process_id)
            return self.backend.call("shortcut", handle=handle, process_id=process_id, codes=SHORTCUTS[shortcut],
                                     root_id=snapshot["root_id"], process_started=snapshot["process_started"])

    def focus_window(self, handle, process_id):
        self._window(handle, process_id)
        snapshot = self.backend.call("inspect", handle=handle, process_id=process_id, limit=1)
        with self._action("focus window", handle, process_id):
            return {**self.backend.call("focus_window", handle=handle, process_id=process_id,
                                        root_id=snapshot["root_id"], process_started=snapshot["process_started"]),
                    "handle": handle, "process_id": process_id}

    def window_bounds(self, handle, process_id):
        self._window(handle, process_id)
        return self.backend.call("bounds", handle=handle, process_id=process_id)

    def move_resize_window(self, handle, process_id, x, y, width, height):
        if not -32768 <= x <= 32768 or not -32768 <= y <= 32768 or not 100 <= width <= 16384 or not 100 <= height <= 16384:
            raise ValueError("Window geometry exceeds supported bounds.")
        self._window(handle, process_id)
        snapshot = self.backend.call("inspect", handle=handle, process_id=process_id, limit=1)
        with self._action("move/resize window", handle, process_id):
            return self.backend.call("move", handle=handle, process_id=process_id, x=x, y=y, width=width, height=height,
                                     root_id=snapshot["root_id"], process_started=snapshot["process_started"])

    def _wait(self, inspect, predicate, timeout):
        if not 0 <= timeout <= 60:
            raise ValueError("Wait timeout must be between 0 and 60 seconds.")
        with self._action("waiting for UI", visible=False):
            # A hung accessibility worker must respect this wait's own deadline,
            # not just its longer per-request timeout. Zero allows one quick probe.
            deadline = time.monotonic() + max(timeout, 0.1)
            self._wait_deadline = deadline
            observed = None
            try:
                while True:
                    self._checkpoint()
                    observed = inspect()
                    if predicate(observed):
                        return {"matched": True, "observed": observed}
                    if timeout == 0 or time.monotonic() >= deadline:
                        return {"matched": False, "timed_out": True, "observed": observed}
                    time.sleep(min(0.15, max(0, deadline - time.monotonic())))
            except TimeoutError:
                return {"matched": False, "timed_out": True, "observed": observed}
            finally:
                self._wait_deadline = None

    def wait_for_window(self, query, timeout=10):
        if not query.strip():
            raise ValueError("Specify a window title.")
        return self._wait(lambda: self.windows(query), bool, timeout)

    def wait_for_element(self, handle, process_id, name, control_type="", timeout=10):
        return self._wait(lambda: self.find_element(handle, process_id, name, control_type),
                          lambda row: row["count"] > 0, timeout)

    def verify_state(self, handle, process_id, name="", control_type="", expected="exists"):
        if expected == "active":
            row = self.active_window()
            return {"verified": row.get("handle") == handle and row.get("process_id") == process_id,
                    "expected": "active", "observed": row}
        if expected not in {"exists", "absent", "focused", "enabled"}:
            raise ValueError("Unsupported expected state.")
        observed = self.find_element(handle, process_id, name, control_type)
        if expected == "absent":
            verified = observed["count"] == 0 and not observed["bounded"]
        elif expected in {"focused", "enabled"}:
            verified = any(row.get(expected, False) for row in observed["matches"])
        else:
            verified = observed["count"] > 0
        return {"verified": verified, "expected": expected, "observed": observed}

    def open_settings(self, page="home"):
        if page not in SETTINGS_PAGES or os.name != "nt":
            raise ValueError("Unsupported Windows Settings page.")
        with self._action("open Windows Settings: " + page):
            os.startfile("ms-settings:" + SETTINGS_PAGES[page])
            return {"requested": True, "verified": False, "page": page,
                    "verification": "Inspect the Settings window to verify the page is ready."}


def setup(services, registry):
    from jarvix.capabilities.desktop_vision import DesktopVisionService

    services.desktop = desktop = DesktopOperatorService(services)
    services.vision = vision = DesktopVisionService(services)

    def tool(name, description, handler, properties=None, required=(), level=1, permission="local.read"):
        register(registry, name, description, properties or {}, required, handler, level, permission)

    window = {"handle": integer(1, 2**64 - 1), "process_id": integer(1, 2**32 - 1)}
    target = {"element_ref": ID}
    find = {**window, "name": string(256, 0), "control_type": string(80, 0)}
    tool("desktop.get_windows", "List visible windows with stable HWND/PID targets; no background tracking.", desktop.windows, {"query": string(256, 0)})
    tool("desktop.get_active_window", "Inspect the current foreground application window once.", desktop.active_window)
    tool("desktop.inspect_ui", "Inspect a bounded accessibility tree. Requires screen access; protected fields excluded. Use returned element refs for actions.",
         desktop.inspect_ui, {**window, "limit": integer(1, 180), "depth": integer(1, 16)}, tuple(window), permission="screen.capture")
    tool("desktop.find_element", "Find controls by accessible name/type. Multiple matches require choosing a specific returned reference.",
         desktop.find_element, {**find, "exact": BOOL}, tuple(window), permission="screen.capture")
    tool("desktop.click_element", "Invoke an inspected control; ALWAYS confirm first because it may submit/delete. Pointer fallback must be explicitly selected and remains bound to the exact visible control.",
         desktop.click_element, {**target, "button": enum("left", "right"), "count": integer(1, 2), "pointer_fallback": BOOL, "capture": BOOL},
         ("element_ref",), 3, "computer.control")
    tool("desktop.type_text", "Set an inspected accessible text field, replacing its value. ALWAYS confirm first. Password/credential entry is forbidden; returns observed verification, never the text.",
         desktop.type_text, {**target, "text": string(12000, 0), "capture": BOOL}, ("element_ref", "text"), 3, "computer.control")
    tool("desktop.focus_element", "Focus one inspected, nonprotected UI control and verify focus.", desktop.focus_element, target, ("element_ref",), 2, "computer.control")
    tool("desktop.send_shortcut", "Send an allowlisted navigation shortcut to an already foreground HWND/PID. No Enter, execution, or security confirmation keys.",
         desktop.send_shortcut, {**window, "shortcut": enum(*SHORTCUTS)}, (*window, "shortcut"), 2, "computer.control")
    tool("desktop.scroll", "Scroll a known control using its accessibility scroll pattern; verify actual scroll change.",
         desktop.scroll, {**target, "direction": enum("up", "down", "left", "right"), "steps": integer(1, 10)}, ("element_ref",), 2, "computer.control")
    tool("desktop.focus_window", "Bring an inspected HWND/PID to the foreground and verify the active window.", desktop.focus_window, window, tuple(window), 2, "computer.control")
    tool("desktop.window_bounds", "Read window geometry for an inspected HWND/PID.", desktop.window_bounds, window, tuple(window))
    tool("desktop.move_resize_window", "Move/resize a known HWND/PID and compare the resulting geometry.", desktop.move_resize_window,
         {**window, "x": integer(-32768, 32768), "y": integer(-32768, 32768), "width": integer(100, 16384), "height": integer(100, 16384)},
         (*window, "x", "y", "width", "height"), 2, "computer.control")
    tool("desktop.wait_for_window", "Wait for a matching visible window with bounded timeout and cancellation.", desktop.wait_for_window,
         {"query": string(256), "timeout": integer(0, 60)}, ("query",))
    tool("desktop.wait_for_element", "Wait for an accessible control with bounded timeout and cancellation.", desktop.wait_for_element,
         {**find, "timeout": integer(0, 60)}, (*window, "name"), permission="screen.capture")
    tool("desktop.verify_state", "Observe the expected resulting UI state; incomplete tree inspection cannot prove absence.", desktop.verify_state,
         {**find, "expected": enum("exists", "absent", "focused", "enabled", "active")}, tuple(window), permission="screen.capture")
    tool("desktop.open_settings", "Open an allowlisted Windows Settings page through its native URI.", desktop.open_settings,
         {"page": enum(*SETTINGS_PAGES)}, level=2, permission="computer.control")
    tool("desktop.status", "Read the current direct-control operation and pause state.", desktop.status)
    tool("desktop.cancel", "Cancel the active direct-control operation.", desktop.cancel)
    tool("vision.capture", "Explicitly capture the active/selected app or monitor once. Screen permission required; no continuous recording.", vision.capture,
         {"target": enum("active", "window", "monitor"), "monitor": integer(0, 63), **window}, level=2, permission="screen.capture")
    tool("vision.inspect", "Read accessible visible text/control names and likely error text from a chosen window. This is accessibility context, not pixel OCR or remote image analysis.",
         vision.inspect, window, tuple(window), permission="screen.capture")
    tool("vision.compare", "Compare two saved screenshot pixel states locally; reports changed pixels, not inferred task completion.", vision.compare,
         {"before_id": ID, "after_id": ID}, ("before_id", "after_id"), permission="screen.capture")
    tool("vision.read_text", "Recognize text in one explicitly saved screenshot using installed Windows OCR languages, locally. Requires screen access; credential-like text is suppressed. No remote image upload.",
         vision.read_text, {"screenshot_id": ID}, ("screenshot_id",), permission="screen.capture")
    tool("vision.history", "List explicit screen captures from local history.", vision.history,
         {"limit": integer(1, 100)}, permission="screen.capture")
