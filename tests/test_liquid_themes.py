"""Complete NEW palettes stay local, readable and persistent across restart."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from PySide6.QtWidgets import QApplication, QMainWindow, QLabel

from jarvix.ui.liquid.components import LiquidCanvas, LiquidPanel
from jarvix.ui.liquid.icons import icon
from jarvix.ui.nexus.icons import icon as original_icon
from jarvix.ui.liquid.theme import THEMES, MATERIALS, palette, ink, material, stylesheet, markdown_stylesheet
from jarvix.ui.liquid.theme import TOKENS
from jarvix.ui.nexus_frontend.design import stylesheet as nexus_stylesheet
from jarvix.ui.theme import STYLESHEET
from test_interface_modes import opened_interface
from test_ui import app as app


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722), strict=True))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


@pytest.mark.parametrize("theme", THEMES)
def test_complete_palettes_keep_body_metadata_and_primary_controls_readable(theme):
    preferences = {"theme": theme}
    values = palette(preferences)
    for role in ("canvas", "surface", "raised", "canvas_shade", "glass_command", "glass_dialog"):
        assert contrast(values["text"], values[role]) >= 4.5
        assert contrast(values["muted"], values[role]) >= 4.5
    assert contrast(values["primary_text"], values["accent"]) >= 4.5
    assert "@" not in stylesheet(preferences)
    assert values["text"] in markdown_stylesheet(preferences)
    assert values["code"] in markdown_stylesheet(preferences)
    assert ink(preferences, "text") == values["text"]
    for name in MATERIALS:
        assert material(preferences, name)[1:] == MATERIALS[name][1:]
        assert material(preferences, name)[0] == values["glass_" + name]


def test_themes_change_the_entire_material_palette_without_mutating_defaults():
    assert set(THEMES) == {"cream", "frost", "graphite", "sand"}
    for role in ("canvas", "surface", "text", "border", "glass_panel", "glass_sidebar", "glass_command"):
        assert len({palette({"theme": name})[role] for name in THEMES}) == 4
    assert len({stylesheet({"theme": name}) for name in THEMES}) == 4
    default = palette()
    changed = palette({"theme": "graphite"})
    changed["canvas"] = "#abcdef"
    assert palette() == default
    assert palette({"theme": "unknown"}) == default
    assert palette({"theme": []}) == default
    for name in THEMES:
        for accent in ("teal", "slate"):
            values = palette({"theme": name, "accent": accent})
            assert contrast(values["primary_text"], values["accent"]) >= 4.5


def test_graphite_control_glyphs_keep_the_shared_family_and_readable_contrast():
    assets = Path(__file__).resolve().parents[1] / "src/jarvix/assets"
    values = palette({"theme": "graphite"})
    for original, replacement, background in (
        ("chevron-down.svg", "chevron-down-dark.svg", values["glass_command"]),
        ("check.svg", "check-dark.svg", values["accent"]),
    ):
        old = ET.fromstring((assets / "nexus" / original).read_text())
        new = ET.fromstring((assets / "liquid" / replacement).read_text())
        assert list(old)[0].attrib == list(new)[0].attrib
        assert old.attrib["stroke-width"] == new.attrib["stroke-width"]
        assert contrast(new.attrib["stroke"], background) >= 3
        assert replacement in stylesheet({"theme": "graphite"})


def test_graphite_svg_ink_changes_without_modifying_original_icon_cache(app):
    before = original_icon("check").pixmap(24, 24).toImage()
    themed = icon("check", {"theme": "graphite"}).pixmap(24, 24).toImage()
    expected = ink({"theme": "graphite"}, "muted")
    opaque = [themed.pixelColor(x, y).name() for y in range(themed.height())
              for x in range(themed.width()) if themed.pixelColor(x, y).alpha() == 255]
    assert opaque and set(opaque) == {expected}
    assert before != themed and original_icon("check").pixmap(24, 24).toImage() == before
    assert icon("check").cacheKey() == original_icon("check").cacheKey()
    assert contrast(expected, ink({"theme": "graphite"}, "glass_panel")) >= 3


def test_switching_theme_invalidates_shared_backdrop_without_resizing(app):
    window = QMainWindow()
    window._liquid_preferences = {"theme": "cream"}
    canvas = LiquidCanvas(window)
    window.setCentralWidget(canvas)
    window.resize(300, 200)
    panel = LiquidPanel(parent=canvas)
    panel.setGeometry(20, 20, 200, 120)
    window.show()
    try:
        app.processEvents()
        before = canvas.frosted(18).toImage()
        revision = canvas.revision
        window._liquid_preferences = {"theme": "graphite"}
        after = canvas.frosted(18).toImage()
        window.grab()
        assert canvas.revision > revision
        assert before.pixelColor(100, 100) != after.pixelColor(100, 100)
        assert set(canvas.frost) == {18}
        assert panel._sample_key[2] == canvas.revision
    finally:
        window.close()
        window.deleteLater()


def test_theme_and_motion_preferences_survive_restart_and_leave_old_untouched(app, tmp_path, monkeypatch):
    application_style = QApplication.instance().styleSheet()
    profile = tmp_path / "profile"
    with opened_interface(app, monkeypatch, tmp_path / "old", "legacy") as old:
        old_palette = old.palette()
        with opened_interface(app, monkeypatch, profile, "new") as new:
            settings = new.pages["Settings"]
            assert any(control.text() == "Theme" for control in settings.findChildren(QLabel))
            assert [settings.nexus_theme.itemText(index) for index in range(settings.nexus_theme.count())] == [
                "Cream", "Frost", "Graphite", "Sand"]
            settings.nexus_theme.setCurrentIndex(settings.nexus_theme.findData("graphite"))
            assert settings.categories.item(0).icon().cacheKey() == icon("settings", {"theme": "graphite"}).cacheKey()
            settings.accent.setCurrentIndex(settings.accent.findData("theme"))
            settings.reduced_motion.setChecked(True)
            settings.motion_enabled.setChecked(True)
            assert new.services.settings.get("liquid.theme") == "graphite"
            assert new.services.settings.get("liquid.reduced_motion") is True
            assert new.services.settings.get("liquid.motion") is True
            assert new.services.settings.get("ui.interface") == "new"
            assert not new.services.settings.get("control.enabled", False)
        assert old.styleSheet() == STYLESHEET and old.palette() == old_palette
        assert QApplication.instance().styleSheet() == application_style
    with opened_interface(app, monkeypatch, profile) as new:
        settings = new.pages["Settings"]
        assert settings.nexus_theme.currentData() == "graphite"
        assert settings.reduced_motion.isChecked() and settings.motion_enabled.isChecked()
        assert new._liquid_preferences["theme"] == "graphite"
        assert not new._liquid_preferences["motion"]
        assert nexus_stylesheet(new._liquid_preferences) == new.styleSheet()


def test_wide_settings_keep_labels_and_controls_in_one_readable_workspace(app, tmp_path, monkeypatch):
    with opened_interface(app, monkeypatch, tmp_path / "profile", "new") as new:
        new.services.settings.set("liquid.reduced_motion", True)
        new.configure_materials()
        new.resize(3840, 2160)
        new.navigate("Settings")
        settings = new.pages["Settings"]
        settings.categories.setCurrentRow(1)
        app.processEvents()
        row = settings.row_widgets[settings.nexus_theme]
        assert row.rect().contains(settings.nexus_theme.geometry())
        assert row.width() <= TOKENS["conversation_width"]
        settings.nexus_theme.setCurrentIndex(settings.nexus_theme.findData("sand"))
        assert new.services.settings.get("liquid.theme") == "sand"
