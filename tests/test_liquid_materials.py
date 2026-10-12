"""Material caching does not read private context or poll the database."""
from PySide6.QtWidgets import QMainWindow

from jarvix.ui.liquid.components import LiquidCanvas, LiquidPanel, LiquidPopover, LiquidSurface
from jarvix.ui.liquid.theme import MATERIALS, stylesheet
from test_ui import app as app


def test_levels_have_distinct_opacity_and_only_floating_surfaces_have_shadow(app):
    assert len({value[1] for value in MATERIALS.values()}) == len(MATERIALS)
    assert LiquidPanel().graphicsEffect() is None
    assert LiquidPopover().graphicsEffect() is not None
    assert "@" not in stylesheet()
    assert stylesheet({"density": "compact"}) != stylesheet()


def test_materials_share_one_local_blur_and_clear_cache_on_resize(app):
    window = QMainWindow()
    canvas = LiquidCanvas(window)
    window.setCentralWidget(canvas)
    window.resize(600, 400)
    first, second = LiquidPanel(parent=canvas), LiquidPanel(parent=canvas)
    first.setGeometry(10, 10, 200, 200)
    second.setGeometry(220, 10, 200, 200)
    window.show()
    app.processEvents()
    window.grab()
    assert set(canvas.frost) == {MATERIALS["panel"][2]}
    assert isinstance(first, LiquidSurface) and first._sample_key is not None
    revision = canvas.revision
    window.resize(620, 400)
    app.processEvents()
    assert canvas.revision > revision
    window._liquid_preferences = {"transparency": False}
    window.grab()
    window.close()
    window.deleteLater()
