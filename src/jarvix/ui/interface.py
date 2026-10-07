"""Choose one presentation without duplicating services or runtime ownership."""
from enum import Enum

from PySide6.QtWidgets import QComboBox


class InterfaceMode(str, Enum):
    LEGACY = "legacy"
    NEXUS = "nexus"


def selected_interface(settings):
    try:
        return InterfaceMode(settings.get("ui.interface", "legacy"))
    except (TypeError, ValueError):
        return InterfaceMode.LEGACY


def create_window(services):
    if selected_interface(services.settings) is InterfaceMode.NEXUS:
        from .nexus.window import NexusWindow
        return NexusWindow(services)
    from .window import MainWindow
    return MainWindow(services)


def add_interface_setting(page, layout):
    page.interface_selector = QComboBox(page)
    for mode in InterfaceMode:
        page.interface_selector.addItem(mode.value.title(), mode.value)
    page.interface_selector.setCurrentIndex(
        page.interface_selector.findData(selected_interface(page.services.settings).value))
    page.setting_row(layout, "Interface", page.interface_selector,
        "Applies the next time Jarvix starts. Your current work stays open.")
    page.interface_selector.currentIndexChanged.connect(lambda _: page.guard(
        lambda: page.services.settings.set("ui.interface", page.interface_selector.currentData())))
