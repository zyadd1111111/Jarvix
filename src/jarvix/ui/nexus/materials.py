"""Cached local glass. No screen capture, polling or continuous compositing."""
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QPixmap, QBrush, QImage, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QGraphicsScene, QGraphicsBlurEffect, QGraphicsDropShadowEffect

from .theme import COLORS, MATERIALS, TOKENS


class DesktopCanvas(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NexusDesktop")
        self.revision = 0
        self.canvas = QPixmap()
        self.frost = {}

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.revision += 1
        self.canvas = QPixmap()
        self.frost.clear()

    def backdrop(self):
        if self.canvas.isNull():
            ratio = self.devicePixelRatioF()
            self.canvas = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
            self.canvas.setDevicePixelRatio(ratio)
            self.canvas.fill(QColor(COLORS["canvas"]))
            painter = QPainter(self.canvas)
            light = QLinearGradient(0, 0, self.width(), self.height())
            light.setColorAt(0, QColor("#f0eee6"))
            light.setColorAt(.55, QColor("#e9e8e0"))
            light.setColorAt(1, QColor("#e1e4da"))
            painter.fillRect(self.rect(), light)
            grain = QImage(8, 8, QImage.Format.Format_ARGB32)
            grain.fill(Qt.GlobalColor.transparent)
            for y in range(8):
                for x in range(8):
                    grain.setPixelColor(x, y, QColor(86, 91, 73, (x * 7 + y * 3) % 4))
            painter.fillRect(self.rect(), QBrush(grain))
            painter.end()
        return self.canvas

    def frosted(self, radius):
        # One blur per desktop size/strength, shared by every pane instead of N effects.
        if radius not in self.frost:
            sample = self.backdrop().copy()
            ratio = sample.devicePixelRatioF()
            sample.setDevicePixelRatio(1)
            scene = QGraphicsScene(self)
            item = scene.addPixmap(sample)
            effect = QGraphicsBlurEffect(self)
            effect.setBlurRadius(radius * ratio)
            item.setGraphicsEffect(effect)
            result = QPixmap(sample.size())
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            scene.render(painter, QRectF(result.rect()), QRectF(sample.rect()))
            painter.end()
            result.setDevicePixelRatio(ratio)
            self.frost[radius] = result
            scene.deleteLater()
        return self.frost[radius]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.backdrop())


class GlassSurface(QFrame):
    material = "surface"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("nexusMaterial", self.material)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        self._cache_key = None
        self._backdrop = QPixmap()
        if MATERIALS[self.material][2] >= 2:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(TOKENS["shadow"])
            shadow.setOffset(0, 2)
            shadow.setColor(QColor(57, 67, 49, 22))
            self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        opacity, blur, elevation = MATERIALS[self.material]
        window = self.window()
        preferences = getattr(window, "_nexus_preferences", {})
        transparent = preferences.get("transparency", True)
        strong = preferences.get("glass", "frosted") == "solid"
        canvas = window.centralWidget() if hasattr(window, "centralWidget") else None
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, TOKENS["radius"], TOKENS["radius"])
        if isinstance(canvas, DesktopCanvas) and transparent and not strong:
            point = self.mapTo(canvas, QPoint(0, 0))
            ratio = self.devicePixelRatioF()
            key = (self.size(), point, canvas.revision, ratio, blur)
            if self._cache_key != key:
                self._backdrop = canvas.frosted(blur).copy(int(point.x() * ratio), int(point.y() * ratio),
                    max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
                self._backdrop.setDevicePixelRatio(ratio)
                self._cache_key = key
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, self._backdrop)
            painter.restore()
        else:
            opacity = 255
        tint = QColor(COLORS["raised"])
        tint.setAlpha(opacity)
        painter.fillPath(path, tint)
        # Refractive edge and inner highlight differ from the ordinary content borders.
        edge = QLinearGradient(0, 0, 0, self.height())
        edge.setColorAt(0, QColor(255, 255, 250, 245))
        edge.setColorAt(.45, QColor(255, 255, 248, 170))
        edge.setColorAt(1, QColor(124, 138, 117, 40 + elevation * 5))
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawPath(path)
        inner = QRectF(rect).adjusted(1, 1, -1, -1)
        painter.setPen(QColor(255, 255, 252, 75 + elevation * 12))
        painter.drawRoundedRect(inner, TOKENS["radius"] - 1, TOKENS["radius"] - 1)
        if elevation:
            painter.setPen(QColor(82, 91, 75, 10 + elevation * 3))
            painter.drawRoundedRect(rect.adjusted(0, 1, 0, 1), TOKENS["radius"], TOKENS["radius"])


class GlassPanel(GlassSurface):
    material = "panel"


class GlassSidebar(GlassSurface):
    material = "sidebar"


class GlassToolbar(GlassSurface):
    material = "toolbar"


class GlassCommandSurface(GlassSurface):
    material = "command"


class GlassPopover(GlassSurface):
    material = "popover"


class GlassDialog(GlassSurface):
    material = "dialog"
