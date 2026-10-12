"""Local, cached glass materials and compact controls for the NEW interface."""
from weakref import ref

from PySide6.QtCore import Qt, QPoint, QRectF, QSize, QRect, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QBrush, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QGraphicsDropShadowEffect, QPushButton, QLineEdit,
    QTableWidget, QHeaderView, QAbstractItemView, QMenu, QDialog,
)
from shiboken6 import isValid

from ..nexus.materials import DesktopCanvas
from ..widgets import label
from .icons import icon
from .theme import MATERIALS, TOKENS, ink, palette, material
from . import motion


class LiquidCanvas(DesktopCanvas):
    """Reuse the cached blur engine; sample only Jarvix's own painted desktop."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LiquidDesktop")
        self._palette_key = None

    def backdrop(self):
        preferences = getattr(motion.owner(self), "_liquid_preferences", {})
        colors = palette(preferences)
        key = (colors["canvas"], colors["canvas_light"], colors["canvas_shade"])
        if self._palette_key != key:
            self._palette_key = key
            self.revision += 1
            self.canvas = QPixmap()
            self.frost.clear()
        if self.canvas.isNull():
            ratio = self.devicePixelRatioF()
            self.canvas = QPixmap(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
            self.canvas.setDevicePixelRatio(ratio)
            self.canvas.fill(QColor(colors["canvas"]))
            painter = QPainter(self.canvas)
            lighting = QLinearGradient(0, 0, self.width(), self.height())
            lighting.setColorAt(0, QColor(colors["canvas_light"]))
            lighting.setColorAt(.5, QColor(colors["canvas"]))
            lighting.setColorAt(1, QColor(colors["canvas_shade"]))
            painter.fillRect(self.rect(), lighting)
            painter.end()
        return self.canvas

    def frosted(self, radius):
        self.backdrop()
        return super().frosted(radius)


class LiquidSurface(QFrame):
    material = "surface"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("liquidMaterial", self.material)
        self.setMinimumWidth(0)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        self._sample_key = None
        self._sample = QPixmap()
        self.activity = 0.
        if MATERIALS[self.material][4] >= 3:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(TOKENS["shadow"])
            effect.setOffset(0, 3)
            effect.setColor(QColor(42, 57, 45, 28))
            self.setGraphicsEffect(effect)

    def paintEvent(self, event):
        host = motion.owner(self)
        preferences = getattr(host, "_liquid_preferences", {})
        tint, opacity, blur, radius, elevation = material(preferences, self.material)
        colors = palette(preferences)
        dark = preferences.get("theme") == "graphite"
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsDropShadowEffect):
            shadow = QColor(colors["shadow"])
            shadow.setAlpha(70 if dark else 28)
            if effect.color() != shadow:
                effect.setColor(shadow)
        if preferences.get("glass") == "subtle":
            blur, opacity = max(4, blur // 2), min(255, opacity + 24)
        transparent = preferences.get("transparency", True) and preferences.get("glass") != "solid"
        canvas = host.centralWidget() if hasattr(host, "centralWidget") else None
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        if transparent and isinstance(canvas, LiquidCanvas):
            canvas.backdrop()
            point = canvas.mapFromGlobal(self.mapToGlobal(QPoint()))
            ratio = self.devicePixelRatioF()
            key = (self.size(), point, canvas.revision, ratio, blur)
            if key != self._sample_key:
                self._sample = canvas.frosted(blur)
                self._sample_key = key
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(QRectF(self.rect()), self._sample,
                QRectF(point.x() * ratio, point.y() * ratio, self.width() * ratio, self.height() * ratio))
            painter.restore()
        elif not transparent:
            opacity = 255
        color = QColor(tint)
        color.setAlpha(opacity)
        painter.fillPath(path, color)
        sheen = QLinearGradient(0, 0, 0, min(self.height(), 64))
        highlight = QColor(colors["highlight"])
        highlight.setAlpha((12 if dark else 22) + round(self.activity * 18))
        sheen.setColorAt(0, highlight)
        highlight.setAlpha(0)
        sheen.setColorAt(1, highlight)
        painter.fillPath(path, sheen)
        edge = QLinearGradient(0, 0, 0, self.height())
        highlight.setAlpha(95 if dark else 240)
        edge.setColorAt(0, highlight)
        highlight.setAlpha(45 if dark else 165)
        edge.setColorAt(.4, highlight)
        shade = QColor(colors["edge"])
        shade.setAlpha(55 + elevation * 8)
        edge.setColorAt(1, shade)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawPath(path)
        highlight.setAlpha((24 if dark else 90) + elevation * 10)
        painter.setPen(highlight)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        if self.activity:
            focus = QColor(ink(preferences, "accent"))
            focus.setAlpha(round(130 * self.activity))
            painter.setPen(QPen(focus, 1))
            painter.drawPath(path)

    def set_activity(self, value):
        self.activity = value
        self.update()

    def focus_material(self, active):
        motion.animate(self, "focus", self.activity, float(active), self.set_activity,
                       duration=TOKENS["duration_fast"])


class LiquidPanel(LiquidSurface):
    material = "panel"

    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        if title:
            self.body.addWidget(label(title, "SectionTitle"))


class LiquidSidebar(LiquidSurface):
    material = "sidebar"


class LiquidSelection(QFrame):
    """One moving selection surface, underneath the existing navigation buttons."""
    def __init__(self, parent):
        super().__init__(parent)
        self._target = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lower()

    def select(self, control):
        previous = self._target() if self._target else None
        if previous is not control:
            if previous is not None and isValid(previous):
                previous.removeEventFilter(self)
            self._target = ref(control)
            control.installEventFilter(self)
        destination = QRect(control.mapTo(self.parentWidget(), QPoint()), control.size())
        self.show()
        self.lower()
        start = self.geometry() if self.property("positioned") else destination
        self.setProperty("positioned", True)
        motion.animate(self, "selection", start, destination, self.setGeometry)

    def eventFilter(self, watched, event):
        if self._target and watched is self._target() and event.type() in (
            QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
        ):
            destination = QRect(watched.mapTo(self.parentWidget(), QPoint()), watched.size())
            # Layout changes invalidate the animation's old destination.
            motion.animate(self, "selection", destination, destination, self.setGeometry)
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        preferences = getattr(motion.owner(self), "_liquid_preferences", {})
        edge = QColor(ink(preferences, "edge"))
        edge.setAlpha(150)
        painter.setPen(edge)
        painter.setBrush(QColor(ink(preferences, "selected")))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)


class LiquidToolbar(LiquidSurface):
    material = "toolbar"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.body.setContentsMargins(10, 6, 10, 6)


class LiquidCommandBar(LiquidSurface):
    material = "command"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.body.setContentsMargins(12, 8, 12, 8)
        self.body.setSpacing(4)


class LiquidInspector(LiquidPanel):
    pass


class LiquidTimeline(LiquidPanel):
    pass


class LiquidPopover(LiquidSurface):
    material = "popover"


class LiquidSheet(LiquidSurface):
    material = "dialog"


class LiquidButton(QPushButton):
    def __init__(self, text, callback=None, style="", parent=None):
        super().__init__(text, parent)
        self.setObjectName(style)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if callback:
            self.clicked.connect(lambda _checked=False: callback())


class LiquidIconButton(LiquidButton):
    def __init__(self, name, description, callback=None, parent=None):
        super().__init__("", callback, "Quiet", parent)
        self.setIcon(icon(name))
        self.setIconSize(QSize(18, 18))
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.setFixedWidth(34)


class LiquidInput(QLineEdit):
    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder or "Text input")


class LiquidTable(QTableWidget):
    def __init__(self, headers=(), parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(32)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setSortingEnabled(True)


class LiquidMenu(QMenu):
    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setAccessibleName(title or "Actions")


class LiquidDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        sheet = LiquidSheet(self)
        self.body = sheet.body
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(sheet)
