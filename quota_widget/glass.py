"""Draw a restrained frosted-glass surface over the native translucent HWND."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


def draw_liquid_glass(p: QPainter, rect: QRectF, radius: float) -> None:
    """Paint a readable blue-gray glass surface with a thin, soft edge.

    The painter layers are intentionally low contrast: Windows can provide its
    native blur underneath, while this surface remains a complete fallback on
    Windows 10 and on systems where composition is unavailable.
    """
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(rect, radius, radius)

    shadow = QPainterPath(shape)
    shadow.translate(QPointF(0, 1.2))
    p.fillPath(shadow, QColor(4, 10, 20, 28))

    p.fillPath(shape, QColor(18, 29, 43, 188))
    base = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    base.setColorAt(0.0, QColor(165, 187, 211, 29))
    base.setColorAt(0.42, QColor(109, 137, 166, 12))
    base.setColorAt(1.0, QColor(62, 88, 119, 24))
    p.fillPath(shape, base)

    p.save()
    p.setClipPath(shape)
    top_light = QRadialGradient(QPointF(rect.left() + rect.width() * .34, rect.top()), rect.width() * .9)
    top_light.setColorAt(0.0, QColor(225, 237, 248, 20))
    top_light.setColorAt(.65, QColor(190, 211, 232, 5))
    top_light.setColorAt(1.0, QColor(190, 211, 232, 0))
    p.fillRect(rect, top_light)
    ambient = QRadialGradient(QPointF(rect.right(), rect.bottom() * .82), rect.width() * .68)
    ambient.setColorAt(0.0, QColor(87, 137, 186, 12))
    ambient.setColorAt(1.0, QColor(87, 137, 186, 0))
    p.fillRect(rect, ambient)
    p.restore()

    # A single quiet outer contour and a faint inner reflection define the rim.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(218, 232, 245, 43), 1.0))
    p.drawPath(shape)
    inner = QPainterPath()
    inner.addRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), max(0, radius - 1.5), max(0, radius - 1.5))
    inner_pen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    inner_pen.setColorAt(0.0, QColor(242, 248, 253, 18))
    inner_pen.setColorAt(.55, QColor(218, 232, 246, 3))
    inner_pen.setColorAt(1.0, QColor(130, 168, 202, 16))
    p.setPen(QPen(inner_pen, .7))
    p.drawPath(inner)
    p.restore()
