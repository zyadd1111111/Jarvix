"""Documented DWM backdrop on supported Windows; safe Qt material elsewhere.

https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type
"""
import ctypes
import sys

from PySide6.QtGui import QGuiApplication


def apply_backdrop(window, transient=False, enabled=True):
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return "Qt material"
    if sys.getwindowsversion().build < 22621:
        return "Qt material"
    try:
        dwm = ctypes.WinDLL("dwmapi")
        set_attribute = dwm.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        set_attribute.restype = ctypes.c_long
        handle = ctypes.c_void_p(int(window.winId()))
        # MAINWINDOW is Mica for a long-lived window; transient Acrylic is for popups.
        backdrop = ctypes.c_int((3 if transient else 2) if enabled else 1)
        light = ctypes.c_int(0)
        set_attribute(handle, 20, ctypes.byref(light), ctypes.sizeof(light))
        if set_attribute(handle, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)) == 0:
            if not enabled:
                return "Qt material"
            return "DWM Acrylic" if transient else "DWM Mica + Qt material"
    except (OSError, AttributeError):
        pass
    return "Qt material"
