"""Finite, interruption-safe presentation transitions; no task state lives here."""
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QSplitter

from .theme import TOKENS


def owner(widget):
    root = widget
    while root is not None:
        if hasattr(root, "_liquid_preferences"):
            return root
        root = root.parentWidget()
    # Controllers may expose a .window facade; use Qt's actual window lookup.
    return QWidget.window(widget)


def enabled(widget):
    root = owner(widget)
    return getattr(root, "interface_mode", None) == "new" and getattr(
        root, "_liquid_preferences", {}).get("motion", True)


def release(animation):
    # Release Python/Qt callback cycles on the GUI thread before deferred deletion.
    animation.finished.disconnect()
    animation.valueChanged.disconnect()
    del animation._liquid_settle
    animation.deleteLater()


def animate(widget, name, start, end, setter, finished=None, duration=None):
    motions = getattr(widget, "_liquid_animations", None)
    if motions is None:
        motions = widget._liquid_animations = {}
    previous = motions.pop(name, None)
    if previous:
        previous.stop()
        release(previous)
    if not enabled(widget) or start == end:
        setter(end)
        if finished:
            finished()
        return None
    animation = QVariantAnimation(widget)
    animation.setDuration(duration or TOKENS["duration"])
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.valueChanged.connect(setter)
    animation._liquid_settle = lambda: (setter(end), finished() if finished else None)
    def destroyed():
        # Parent deletion can end a transition before finished is emitted.
        if motions.get(name) is animation:
            motions.pop(name)
        if hasattr(animation, "_liquid_settle"):
            del animation._liquid_settle
    animation.destroyed.connect(destroyed)
    def complete():
        if motions.get(name) is animation:
            motions.pop(name)
            animation._liquid_settle()
        release(animation)
    animation.finished.connect(complete)
    motions[name] = animation
    animation.start()
    return animation


def settle(root):
    for widget in [root, *root.findChildren(QWidget)]:
        motions = getattr(widget, "_liquid_animations", {})
        for animation in list(motions.values()):
            animation.stop()
            animation._liquid_settle()
            release(animation)
        motions.clear()


class Transition(QWidget):
    """A local view snapshot avoids nesting opacity effects around live shadows."""
    def __init__(self, target, snapshot):
        super().__init__(target)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.snapshot = snapshot
        self.alpha = 1.
        self.setGeometry(target.rect())
        self.show()
        self.raise_()
        animate(self, "page", 1., 0., self.set_alpha, self.deleteLater)

    def set_alpha(self, alpha):
        self.alpha = alpha
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.alpha)
        painter.drawPixmap(0, -int(TOKENS["slide"] * (1 - self.alpha)), self.snapshot)


def disclose(widget):
    if widget.isWindow() and enabled(widget):
        animate(widget, "entry", .90, 1., widget.setWindowOpacity, duration=TOKENS["duration_fast"])
    elif widget.isVisible() and enabled(widget):
        for old in widget.findChildren(Transition, options=Qt.FindChildOption.FindDirectChildrenOnly):
            old.hide()
            old.deleteLater()
        Transition(widget, widget.grab())


def reveal(widget, shown):
    """Slide a splitter inspector or expand a contextual drawer; retain its state."""
    parent = widget.parentWidget()
    if not enabled(widget):
        widget.setVisible(shown)
        return
    if isinstance(parent, QSplitter):
        index = parent.indexOf(widget)
        sizes = parent.sizes()
        total = sum(sizes)
        if not total or len(sizes) < 2:
            widget.setVisible(shown)
            return
        if not shown:
            widget.setProperty("liquidExpandedSize", sizes[index])
        widget.show()
        limits = getattr(widget, "_liquid_split_limits", (widget.minimumSize(), widget.maximumSize()))
        widget._liquid_split_limits = limits
        widget.setMinimumSize(0, 0)
        desired = int(widget.property("liquidExpandedSize") or TOKENS["workspace_inspector_width"])
        desired = min(desired, max(1, total // 2)) if shown else 0
        neighbor = index - 1 if index else 1
        end = sizes.copy()
        end[neighbor] += sizes[index] - desired
        end[index] = desired
        def resize(value):
            current = [round(a + (b - a) * value) for a, b in zip(sizes, end, strict=True)]
            # Qt's minimumSizeHint otherwise makes an inspector jump at the last frame.
            if parent.orientation() == Qt.Orientation.Horizontal:
                widget.setMaximumWidth(max(0, current[index]))
            else:
                widget.setMaximumHeight(max(0, current[index]))
            parent.setSizes(current)
        def finish_split():
            widget.setVisible(shown)
            widget.setMinimumSize(limits[0])
            widget.setMaximumSize(limits[1])
        animate(widget, "reveal", 0., 1., resize, finish_split, TOKENS["duration_panel"])
        return
    minimum, maximum = widget.minimumHeight(), widget.maximumHeight()
    prior = getattr(widget, "_liquid_drawer_limits", (minimum, maximum))
    widget._liquid_drawer_limits = prior
    start = widget.height() if not widget.isHidden() else 0
    widget.setMinimumHeight(0)
    widget.show()
    end = min(prior[1], max(prior[0], widget.sizeHint().height())) if shown else 0
    def finish():
        widget.setVisible(shown)
        widget.setMinimumHeight(prior[0])
        widget.setMaximumHeight(prior[1])
    animate(widget, "reveal", start, end, widget.setMaximumHeight, finish, TOKENS["duration_panel"])
