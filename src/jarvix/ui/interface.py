"""Choose one presentation without duplicating services or runtime ownership."""
from enum import Enum

from PySide6.QtWidgets import QComboBox


class InterfaceMode(str, Enum):
    LEGACY = "legacy"
    NEXUS = "nexus"
    OLD = "old"
    NEW = "new"


def selected_interface(settings):
    try:
        return InterfaceMode(settings.get("ui.interface", "legacy"))
    except (TypeError, ValueError):
        return InterfaceMode.LEGACY


def create_window(services):
    mode = selected_interface(services.settings)
    if mode is InterfaceMode.NEW:
        from .nexus_frontend.window import NexusWindow
        return NexusWindow(services)
    if mode is InterfaceMode.OLD:
        mode = services.settings.get("ui.old_interface", "legacy")
    if mode == InterfaceMode.NEXUS:
        from .nexus.window import NexusWindow
        return NexusWindow(services)
    from .window import MainWindow
    return MainWindow(services)


def add_interface_setting(page, layout):
    page.interface_selector = QComboBox(page)
    for mode in (InterfaceMode.OLD, InterfaceMode.NEW):
        page.interface_selector.addItem(mode.value.upper(), mode.value)
    page.interface_selector.setCurrentIndex(
        1 if selected_interface(page.services.settings) is InterfaceMode.NEW else 0)
    page.setting_row(layout, "Interface", page.interface_selector,
        "Applies the next time Jarvix starts. Your current work stays open.")
    def save():
        current = selected_interface(page.services.settings)
        if current in (InterfaceMode.LEGACY, InterfaceMode.NEXUS):
            page.services.settings.set("ui.old_interface", current.value)
        page.services.settings.set("ui.interface", page.interface_selector.currentData())
    page.interface_selector.currentIndexChanged.connect(lambda _: page.guard(save))
