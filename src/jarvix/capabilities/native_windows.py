"""Small, typed Win32 boundary. No shell commands or ambient screen polling."""
from __future__ import annotations

import ctypes as ct
import os
import struct
import uuid
from ctypes import wintypes as wt
from pathlib import Path


def windows_only():
    if os.name != "nt":
        raise RuntimeError("This capability requires Windows.")


class Win32:
    def __init__(self):
        windows_only()
        self.user = ct.WinDLL("user32", use_last_error=True)
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.gdi = ct.WinDLL("gdi32", use_last_error=True)
        self._bind(self.user, "GetForegroundWindow", wt.HWND, [])
        self._bind(self.user, "IsWindow", wt.BOOL, [wt.HWND])
        self._bind(self.user, "IsWindowVisible", wt.BOOL, [wt.HWND])
        self._bind(self.user, "GetWindowTextLengthW", ct.c_int, [wt.HWND])
        self._bind(self.user, "GetWindowTextW", ct.c_int, [wt.HWND, wt.LPWSTR, ct.c_int])
        self._bind(self.user, "GetWindowThreadProcessId", wt.DWORD, [wt.HWND, ct.POINTER(wt.DWORD)])
        self._bind(self.user, "IsIconic", wt.BOOL, [wt.HWND])
        self._bind(self.user, "IsZoomed", wt.BOOL, [wt.HWND])
        self._bind(self.user, "ShowWindowAsync", wt.BOOL, [wt.HWND, ct.c_int])
        self._bind(self.user, "SetForegroundWindow", wt.BOOL, [wt.HWND])
        self._bind(self.user, "PostMessageW", wt.BOOL, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM])
        self._bind(self.user, "GetWindowRect", wt.BOOL, [wt.HWND, ct.POINTER(wt.RECT)])
        self._bind(self.user, "OpenClipboard", wt.BOOL, [wt.HWND])
        self._bind(self.user, "CloseClipboard", wt.BOOL, [])
        self._bind(self.user, "EmptyClipboard", wt.BOOL, [])
        self._bind(self.user, "GetClipboardData", wt.HANDLE, [wt.UINT])
        self._bind(self.user, "SetClipboardData", wt.HANDLE, [wt.UINT, wt.HANDLE])
        self._bind(self.user, "IsClipboardFormatAvailable", wt.BOOL, [wt.UINT])
        self._bind(self.kernel, "GlobalLock", ct.c_void_p, [wt.HANDLE])
        self._bind(self.kernel, "GlobalUnlock", wt.BOOL, [wt.HANDLE])
        self._bind(self.kernel, "GlobalSize", ct.c_size_t, [wt.HANDLE])
        self._bind(self.kernel, "GlobalAlloc", wt.HANDLE, [wt.UINT, ct.c_size_t])
        self._bind(self.kernel, "GlobalFree", wt.HANDLE, [wt.HANDLE])

    @staticmethod
    def _bind(dll, name, result, arguments):
        function = getattr(dll, name)
        function.restype, function.argtypes = result, arguments
        return function

    @staticmethod
    def _checked(value, message):
        if not value:
            raise OSError(message)
        return value

    def window(self, handle):
        if not self.user.IsWindow(handle):
            raise ValueError("This window is no longer available.")
        title = ct.create_unicode_buffer(min(self.user.GetWindowTextLengthW(handle) + 1, 4096))
        self.user.GetWindowTextW(handle, title, len(title))
        process_id = wt.DWORD()
        self.user.GetWindowThreadProcessId(handle, ct.byref(process_id))
        return {"handle": int(handle), "title": title.value, "process_id": process_id.value,
                "minimized": bool(self.user.IsIconic(handle)), "maximized": bool(self.user.IsZoomed(handle))}

    def windows(self):
        items = []
        callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def visit(handle, _):
            if self.user.IsWindowVisible(handle):
                try:
                    item = self.window(handle)
                    if item["title"]:
                        items.append(item)
                except (OSError, ValueError):
                    pass
            return len(items) < 200

        enum = self._bind(self.user, "EnumWindows", wt.BOOL, [callback_type, wt.LPARAM])
        enum(callback_type(visit), 0)
        return items

    def foreground(self):
        handle = self.user.GetForegroundWindow()
        return self.window(handle) if handle else None

    def window_action(self, handle, process_id, action):
        item = self.window(handle)
        if item["process_id"] != process_id:
            raise ValueError("The window belongs to a different process. Inspect it again.")
        if action == "close":
            self._checked(self.user.PostMessageW(handle, 0x0010, 0, 0), "The close request failed.")
        elif action == "focus":
            if item["minimized"]:
                self.user.ShowWindowAsync(handle, 9)
            self._checked(self.user.SetForegroundWindow(handle), "Windows did not permit focus to change.")
        else:
            self._checked(self.user.ShowWindowAsync(handle, {"minimize": 6, "maximize": 3, "restore": 9}[action]),
                          "Windows could not change the window.")
        return {"action": action, "requested": True, "handle": handle, "process_id": process_id}

    def clipboard_read(self):
        self._checked(self.user.OpenClipboard(None), "Clipboard is busy. Try again.")
        try:
            if not self.user.IsClipboardFormatAvailable(13):
                return ""
            handle = self._checked(self.user.GetClipboardData(13), "Clipboard text is unavailable.")
            size = self.kernel.GlobalSize(handle)
            if size > 400_002:
                raise ValueError("Clipboard text exceeds 200,000 characters.")
            address = self._checked(self.kernel.GlobalLock(handle), "Clipboard cannot be read.")
            try:
                return ct.string_at(address, size).decode("utf-16-le", errors="replace").split("\0", 1)[0]
            finally:
                self.kernel.GlobalUnlock(handle)
        finally:
            self.user.CloseClipboard()

    def clipboard_write(self, text):
        payload = (text + "\0").encode("utf-16-le")
        handle = self._checked(self.kernel.GlobalAlloc(0x0042, len(payload)), "Clipboard allocation failed.")
        transferred = False
        try:
            address = self._checked(self.kernel.GlobalLock(handle), "Clipboard allocation could not be locked.")
            try:
                ct.memmove(address, payload, len(payload))
            finally:
                self.kernel.GlobalUnlock(handle)
            create = self._bind(self.user, "CreateWindowExW", wt.HWND,
                                [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ct.c_int, ct.c_int,
                                 ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ct.c_void_p])
            destroy = self._bind(self.user, "DestroyWindow", wt.BOOL, [wt.HWND])
            owner = self._checked(create(0, "STATIC", "Jarvix clipboard", 0, 0, 0, 0, 0,
                                         wt.HWND(-3), None, None, None), "Clipboard owner creation failed.")
            try:
                self._checked(self.user.OpenClipboard(owner), "Clipboard is busy. Try again.")
                try:
                    self._checked(self.user.EmptyClipboard(), "Clipboard could not be replaced.")
                    self._checked(self.user.SetClipboardData(13, handle), "Clipboard write failed.")
                    transferred = True
                finally:
                    self.user.CloseClipboard()
            finally:
                destroy(owner)
        finally:
            if not transferred:
                self.kernel.GlobalFree(handle)

    def monitors(self):
        class MonitorInfo(ct.Structure):
            _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT),
                        ("flags", wt.DWORD), ("device", wt.WCHAR * 32)]

        callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)
        get_info = self._bind(self.user, "GetMonitorInfoW", wt.BOOL, [wt.HANDLE, ct.POINTER(MonitorInfo)])
        rows = []

        def visit(handle, _dc, _rect, _data):
            info = MonitorInfo()
            info.size = ct.sizeof(info)
            if get_info(handle, ct.byref(info)):
                box = info.monitor
                rows.append({"index": len(rows), "device": info.device, "primary": bool(info.flags & 1),
                             "x": box.left, "y": box.top, "width": box.right - box.left,
                             "height": box.bottom - box.top})
            return True

        enum = self._bind(self.user, "EnumDisplayMonitors", wt.BOOL,
                          [wt.HDC, ct.POINTER(wt.RECT), callback_type, wt.LPARAM])
        self._checked(enum(None, None, callback_type(visit), 0), "Monitor information is unavailable.")
        return rows

    def graphics(self):
        class DisplayDevice(ct.Structure):
            _fields_ = [("size", wt.DWORD), ("name", wt.WCHAR * 32), ("description", wt.WCHAR * 128),
                        ("flags", wt.DWORD), ("device_id", wt.WCHAR * 128), ("key", wt.WCHAR * 128)]

        enum = self._bind(self.user, "EnumDisplayDevicesW", wt.BOOL,
                          [wt.LPCWSTR, wt.DWORD, ct.POINTER(DisplayDevice), wt.DWORD])
        result = []
        for index in range(64):
            device = DisplayDevice()
            device.size = ct.sizeof(device)
            if not enum(None, index, ct.byref(device), 0):
                break
            if not device.flags & 8:
                result.append({"name": device.description, "device": device.name,
                               "active": bool(device.flags & 1), "primary": bool(device.flags & 4)})
        return result

    def screenshot(self, target="monitor", monitor=0, handle=None, process_id=None):
        # Physical screen coordinates are scoped to this worker thread only.
        dpi = getattr(self.user, "SetThreadDpiAwarenessContext", None)
        previous = None
        if dpi:
            dpi.argtypes, dpi.restype = [ct.c_void_p], ct.c_void_p
            previous = dpi(ct.c_void_p(-4))
        try:
            if target == "window":
                item = self.window(handle)
                if item["process_id"] != process_id or item["minimized"]:
                    raise ValueError("The selected window is unavailable or minimized.")
                rect = wt.RECT()
                self._checked(self.user.GetWindowRect(handle, ct.byref(rect)), "Window bounds unavailable.")
                box = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
            else:
                monitors = self.monitors()
                if target == "all":
                    if not monitors:
                        raise ValueError("No display is connected.")
                    left, top = min(r["x"] for r in monitors), min(r["y"] for r in monitors)
                    right = max(r["x"] + r["width"] for r in monitors)
                    bottom = max(r["y"] + r["height"] for r in monitors)
                    box = (left, top, right - left, bottom - top)
                else:
                    if not 0 <= monitor < len(monitors):
                        raise ValueError("Monitor index is unavailable.")
                    row = monitors[monitor]
                    box = (row["x"], row["y"], row["width"], row["height"])
            return self._capture_bitmap(*box), {"x": box[0], "y": box[1], "width": box[2], "height": box[3]}
        finally:
            if dpi and previous:
                dpi(previous)

    def _capture_bitmap(self, x, y, width, height):
        if width <= 0 or height <= 0 or width * height > 40_000_000:
            raise ValueError("The screen region exceeds the capture limit.")
        get_dc = self._bind(self.user, "GetDC", wt.HDC, [wt.HWND])
        release_dc = self._bind(self.user, "ReleaseDC", ct.c_int, [wt.HWND, wt.HDC])
        create_dc = self._bind(self.gdi, "CreateCompatibleDC", wt.HDC, [wt.HDC])
        create_bitmap = self._bind(self.gdi, "CreateCompatibleBitmap", wt.HANDLE, [wt.HDC, ct.c_int, ct.c_int])
        select = self._bind(self.gdi, "SelectObject", wt.HANDLE, [wt.HDC, wt.HANDLE])
        blit = self._bind(self.gdi, "BitBlt", wt.BOOL,
                          [wt.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HDC, ct.c_int, ct.c_int, wt.DWORD])
        get_bits = self._bind(self.gdi, "GetDIBits", ct.c_int,
                             [wt.HDC, wt.HANDLE, wt.UINT, wt.UINT, ct.c_void_p, ct.c_void_p, wt.UINT])
        delete_object = self._bind(self.gdi, "DeleteObject", wt.BOOL, [wt.HANDLE])
        delete_dc = self._bind(self.gdi, "DeleteDC", wt.BOOL, [wt.HDC])
        screen = self._checked(get_dc(None), "Screen access failed.")
        memory, bitmap, old = None, None, None
        try:
            memory = self._checked(create_dc(screen), "Capture memory allocation failed.")
            bitmap = self._checked(create_bitmap(screen, width, height), "Capture bitmap allocation failed.")
            old = self._checked(select(memory, bitmap), "Capture bitmap selection failed.")
            self._checked(blit(memory, 0, 0, width, height, screen, x, y, 0x40CC0020), "Screen capture failed.")
            select(memory, old)
            old = None
            size = width * height * 4
            header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, size, 0, 0, 0, 0)
            info = ct.create_string_buffer(header, 40)
            pixels = ct.create_string_buffer(size)
            if get_bits(memory, bitmap, 0, height, pixels, info, 0) != height:
                raise OSError("Screen pixels could not be read.")
            return struct.pack("<2sIHHI", b"BM", 54 + size, 0, 0, 54) + header + pixels.raw
        finally:
            if old and memory:
                select(memory, old)
            if bitmap:
                delete_object(bitmap)
            if memory:
                delete_dc(memory)
            release_dc(None, screen)

    def media(self, key):
        event = self._bind(self.user, "keybd_event", None, [wt.BYTE, wt.BYTE, wt.DWORD, ct.c_size_t])
        code = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}[key]
        event(code, 0, 0, 0)
        event(code, 0, 2, 0)

    def lock(self):
        function = self._bind(self.user, "LockWorkStation", wt.BOOL, [])
        self._checked(function(), "Windows could not lock the session.")

    def sleep(self):
        dll = ct.WinDLL("powrprof", use_last_error=True)
        function = self._bind(dll, "SetSuspendState", ct.c_ubyte, [ct.c_ubyte, ct.c_ubyte, ct.c_ubyte])
        self._checked(function(False, False, False), "Windows could not enter sleep.")

    def system_directory(self):
        function = self._bind(self.kernel, "GetSystemDirectoryW", wt.UINT, [wt.LPWSTR, wt.UINT])
        buffer = ct.create_unicode_buffer(32768)
        self._checked(function(buffer, len(buffer)), "Windows system directory unavailable.")
        return buffer.value

    def recycle(self, path):
        """Windows 8+ recycle-only operation; unsupported volumes fail closed.

        FOFX_RECYCLEONDELETE requests recycling instead of permanent deletion.
        FOFX_EARLYFAILURE stops on errors, including a unavailable recycle bin.
        https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags
        """
        drive_type = self._bind(self.kernel, "GetDriveTypeW", wt.UINT, [wt.LPCWSTR])
        if drive_type(Path(path).anchor) != 3:  # DRIVE_FIXED only, never remote/removable.
            raise OSError("Only fixed local drives support this recycle operation.")

        class Guid(ct.Structure):
            _fields_ = [("value", ct.c_ubyte * 16)]

        def guid(value):
            return Guid((ct.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))

        def checked(result):
            if result < 0:
                raise OSError("Windows could not recycle this item; no permanent-delete fallback is used.")

        def call(pointer, index, argument_types, *arguments):
            table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            function = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, *argument_types)(table[index])
            checked(function(pointer, *arguments))

        def release(pointer):
            if pointer:
                table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
                ct.WINFUNCTYPE(wt.ULONG, ct.c_void_p)(table[2])(pointer)

        ole = ct.WinDLL("ole32")
        initialize = self._bind(ole, "CoInitializeEx", ct.c_long, [ct.c_void_p, wt.DWORD])
        status = initialize(None, 2)
        # Recycle actions run on a dedicated worker; do not switch COM apartments.
        checked(status)
        operation, item = ct.c_void_p(), ct.c_void_p()
        try:
            create = self._bind(ole, "CoCreateInstance", ct.c_long,
                                [ct.POINTER(Guid), ct.c_void_p, wt.DWORD, ct.POINTER(Guid), ct.POINTER(ct.c_void_p)])
            class_id = guid("3ad05575-8857-4850-9277-11b85bdb8e09")
            operation_id = guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
            checked(create(ct.byref(class_id), None, 1, ct.byref(operation_id), ct.byref(operation)))
            shell = ct.WinDLL("shell32")
            shell_item = self._bind(shell, "SHCreateItemFromParsingName", ct.c_long,
                                    [wt.LPCWSTR, ct.c_void_p, ct.POINTER(Guid), ct.POINTER(ct.c_void_p)])
            item_id = guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")
            checked(shell_item(path, None, ct.byref(item_id), ct.byref(item)))
            # SILENT | NOCONFIRMATION | NOERRORUI | RECYCLEONDELETE |
            # EARLYFAILURE | ADDUNDORECORD. Central dispatcher already confirmed.
            call(operation, 5, [wt.DWORD], 0x4 | 0x10 | 0x400 | 0x80000 | 0x100000 | 0x20000000)
            call(operation, 18, [ct.c_void_p, ct.c_void_p], item, None)
            call(operation, 21, [])
            aborted = wt.BOOL()
            call(operation, 22, [ct.POINTER(wt.BOOL)], ct.byref(aborted))
            if aborted.value:
                raise OSError("Windows cancelled the recycle operation.")
        finally:
            release(item)
            release(operation)
            self._bind(ole, "CoUninitialize", None, [])()

    def volume(self, action="status", percent=None):
        """Default render endpoint volume through Windows Core Audio, with balanced COM refs."""
        class Guid(ct.Structure):
            _fields_ = [("value", ct.c_ubyte * 16)]

        def guid(value):
            return Guid((ct.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))

        def call(pointer, index, argument_types, *arguments):
            table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            function = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, *argument_types)(table[index])
            result = function(pointer, *arguments)
            if result < 0:
                raise OSError("Windows audio operation failed.")

        def release(pointer):
            if pointer:
                table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
                ct.WINFUNCTYPE(wt.ULONG, ct.c_void_p)(table[2])(pointer)

        ole = ct.OleDLL("ole32")
        initialize = self._bind(ole, "CoInitializeEx", ct.c_long, [ct.c_void_p, wt.DWORD])
        # WinDLL avoids automatic HRESULT exceptions, needed for RPC_E_CHANGED_MODE.
        initialize = self._bind(ct.WinDLL("ole32"), "CoInitializeEx", ct.c_long, [ct.c_void_p, wt.DWORD])
        status = initialize(None, 0)
        if status < 0 and status != -2147417850:
            raise OSError("Windows audio initialization failed.")
        enum, endpoint, volume = ct.c_void_p(), ct.c_void_p(), ct.c_void_p()
        try:
            create = self._bind(ole, "CoCreateInstance", ct.c_long,
                                [ct.POINTER(Guid), ct.c_void_p, wt.DWORD, ct.POINTER(Guid), ct.POINTER(ct.c_void_p)])
            class_id = guid("bcde0395-e52f-467c-8e3d-c4579291692e")
            enum_id = guid("a95664d2-9614-4f35-a746-de8db63617e6")
            result = create(ct.byref(class_id), None, 23, ct.byref(enum_id), ct.byref(enum))
            if result < 0:
                raise OSError("Windows audio devices unavailable.")
            call(enum, 4, [ct.c_int, ct.c_int, ct.POINTER(ct.c_void_p)], 0, 1, ct.byref(endpoint))
            volume_id = guid("5cdf2c82-841e-4546-9722-0cf74078229a")
            call(endpoint, 3, [ct.POINTER(Guid), wt.DWORD, ct.c_void_p, ct.POINTER(ct.c_void_p)],
                 ct.byref(volume_id), 23, None, ct.byref(volume))
            level, muted = ct.c_float(), wt.BOOL()
            call(volume, 9, [ct.POINTER(ct.c_float)], ct.byref(level))
            if action in {"up", "down", "set"}:
                if action == "set":
                    next_value = percent / 100
                else:
                    next_value = max(0, min(1, level.value + (0.05 if action == "up" else -0.05)))
                call(volume, 7, [ct.c_float, ct.c_void_p], next_value, None)
            if action in {"mute", "unmute"}:
                call(volume, 14, [wt.BOOL, ct.c_void_p], action == "mute", None)
            call(volume, 9, [ct.POINTER(ct.c_float)], ct.byref(level))
            call(volume, 15, [ct.POINTER(wt.BOOL)], ct.byref(muted))
            return {"percent": round(level.value * 100, 1), "muted": bool(muted.value), "endpoint": "default playback"}
        finally:
            release(volume)
            release(endpoint)
            release(enum)
            if status >= 0:
                self._bind(ole, "CoUninitialize", None, [])()
