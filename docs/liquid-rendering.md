# NEW rendering decisions

`ui/nexus_frontend` owns the separate shell and page presentations; `ui/liquid`
supplies reusable materials and transitions. OLD, services and storage are unchanged.
Selection remains an existing local setting.

The Nexus conversation sidebar and workspace layouts reuse existing controllers.
Cream, Frost, Graphite and Sand each own the whole material palette; no global
Qt palette or OLD stylesheet is changed. Native file/app icons retain their colors.

- [Qt translucent windows](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html):
  full Windows translucency requires a frameless window. Keep native resizing,
  snapping and titlebar behavior; do not introduce a fragile frameless shell.
- [DWM system backdrops](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type):
  reuse the guarded documented Mica/Acrylic calls on build 22621+, with Qt fallback.
  Qt owns the readable content plane; native effects are supplemental.
- [Composition attributes](https://learn.microsoft.com/en-us/windows/win32/dwm/setwindowcompositionattribute):
  Microsoft discourages this undocumented route. Reviewed the
  [PyQt-Frameless-Window implementation](https://github.com/zhiyiYo/PyQt-Frameless-Window/blob/master/qframelesswindow/windows/window_effect.py)
  as a comparison; no code copied or dependency added.
- [Qt blur](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QGraphicsBlurEffect.html):
  blur a cached local canvas, never live text or other apps. Reuse per-radius
  samples, invalidate on resize/DPI changes. Solid surfaces preserve contrast.
- [QPainter](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QPainter.html):
  distinct tints/alpha per elevation, refractive edge and inner highlight.
  Shadows belong to floating controls and sheets, not every content pane.
- [Qt animation](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QVariantAnimation.html):
  finite 140–210ms OutCubic interpolation for layout, selection, focus and progress.
  Local view snapshots avoid stacking opacity effects around live shadow effects.
  Cancellation settles presentation; Reduced Motion applies final states directly.
