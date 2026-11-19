# app/viewer/annotation_types.py
"""PDF_Editor – Presentation annotation data types, geometry metrics, and cursor helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from PySide6.QtCore import Qt, QPoint, QRectF, QPointF
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QPainter, QPainterPath,
    QPen, QPixmap, QTransform,
)
import qtawesome as qta


class ToolMode(IntEnum):
    POINTER = 0
    PEN = 1
    HIGHLIGHTER = 2
    ERASER = 3
    LASER = 4
    TYPE = 5


@dataclass
class Stroke:
    path: QPainterPath
    color: QColor
    width: int
    kind: int = ToolMode.PEN
    points: list = field(default_factory=list)


@dataclass
class TextBox:
    rect: QRectF
    text: str
    color: QColor
    font_size: float = 24.0
    id: int = 0
    has_stroke: bool = False
    has_cloud: bool = False


_PEN_WIDTH = 3
_HIGHLIGHTER_WIDTH = 18
_LASER_RADIUS = 14
_ERASER_TOL = 6
_POINT_MERGE_SQ = 4
_CURSOR_ICON_PX = 24
_OUTLINE_PAD = 2
_HANDLE_SIZE = 8.0
_DEFAULT_FONT_SIZE = 24.0

_OUTLINE_OFFSETS = (
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (1, 1), (-1, 1), (1, -1),
)


def _copy_stroke(s: Stroke) -> Stroke:
    """Clone a Stroke using native Qt copy constructors rather than pickle."""
    return Stroke(
        path=QPainterPath(s.path),
        color=QColor(s.color),
        width=s.width,
        kind=s.kind,
        points=[QPoint(p) for p in s.points],
    )


def _copy_box(b: TextBox) -> TextBox:
    """Clone a TextBox safely."""
    return TextBox(
        rect=QRectF(b.rect),
        text=b.text,
        color=QColor(b.color),
        font_size=b.font_size,
        id=b.id,
        has_stroke=b.has_stroke,
        has_cloud=b.has_cloud,
    )


def fit_font_size(text: str, rect: QRectF, min_size: float = 8.0, max_size: float = 220.0) -> float:
    """Calculate the font size so text fits within a manually resized bounding box."""
    if rect.width() < 16 or rect.height() < 16:
        return min_size
    lines = text.split("\n") if text else [" "]
    avail_w = max(12.0, rect.width() - 14.0)
    avail_h = max(12.0, rect.height() - 10.0)

    if not text.strip():
        return max(min_size, min(max_size, round(rect.height() * 0.65, 1)))

    low = min_size
    high = max_size
    best = min_size

    for _ in range(12):
        mid = (low + high) / 2.0
        font = QFont("Segoe UI", int(mid))
        font.setPointSizeF(mid)
        fm = QFontMetricsF(font)

        line_h = fm.lineSpacing()
        total_h = line_h * len(lines)
        max_line_w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)

        if total_h <= avail_h and max_line_w <= avail_w:
            best = mid
            low = mid + 0.5
        else:
            high = mid - 0.5

    return max(min_size, round(best, 1))


def auto_fit_box_to_text(box: TextBox, overlay_width: float, overlay_height: float,
                         pad_x: float = 16.0, pad_y: float = 12.0) -> None:
    """Dynamically expand or shrink the text box to fit typed text at its current font size."""
    font = QFont("Segoe UI", int(box.font_size))
    font.setPointSizeF(box.font_size)
    fm = QFontMetricsF(font)

    lines = box.text.split("\n") if box.text else [""]
    max_line_w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
    cursor_reserve = max(12.0, fm.averageCharWidth() * 1.5)

    needed_w = max(52.0, max_line_w + pad_x + cursor_reserve)
    line_h = fm.lineSpacing()
    needed_h = max(34.0, line_h * max(1, len(lines)) + pad_y)

    x0 = box.rect.left()
    y0 = box.rect.top()

    if x0 + needed_w > overlay_width - 10.0:
        x0 = max(10.0, overlay_width - 10.0 - needed_w)

    max_avail_w = max(52.0, overlay_width - 16.0)
    needed_w = min(needed_w, max_avail_w)

    max_avail_h = max(34.0, overlay_height - 10.0 - y0)
    needed_h = min(needed_h, max_avail_h)

    box.rect = QRectF(x0, y0, needed_w, needed_h)


def _draw_edge_type_icon(painter: QPainter, size: int, color: QColor) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, max(1.5, size * 0.08), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    margin = size * 0.12
    box_rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.18
    painter.drawRoundedRect(box_rect, radius, radius)

    bar_y = size * 0.32
    stem_bottom = size * 0.72
    mid_x = size * 0.5
    bar_left = size * 0.30
    bar_right = size * 0.70
    serif_len = size * 0.10

    painter.drawLine(QPointF(bar_left, bar_y), QPointF(bar_right, bar_y))
    painter.drawLine(QPointF(bar_left, bar_y), QPointF(bar_left, bar_y + serif_len))
    painter.drawLine(QPointF(bar_right, bar_y), QPointF(bar_right, bar_y + serif_len))
    painter.drawLine(QPointF(mid_x, bar_y), QPointF(mid_x, stem_bottom))
    base_w = size * 0.14
    painter.drawLine(QPointF(mid_x - base_w, stem_bottom), QPointF(mid_x + base_w, stem_bottom))


def _draw_stroke_btn_icon(painter: QPainter, size: int, color: QColor) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, max(1.6, size * 0.08), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    w_bar = size * 0.68
    h_bar = size * 0.18
    left = (size - w_bar) / 2.0
    top = size * 0.20
    painter.drawRoundedRect(QRectF(left, top, w_bar, h_bar), 2, 2)

    stem_w = size * 0.22
    stem_h = size * 0.44
    stem_x = (size - stem_w) / 2.0
    stem_y = top + h_bar
    painter.drawRoundedRect(QRectF(stem_x, stem_y, stem_w, stem_h), 2, 2)


def _draw_cloud_btn_icon(painter: QPainter, size: int, color: QColor) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, max(1.6, size * 0.08), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    path = QPainterPath()
    bx = size * 0.20
    by = size * 0.68
    bw = size * 0.60
    path.moveTo(bx + 4, by)
    path.lineTo(bx + bw - 4, by)
    path.arcTo(QRectF(bx + bw - 10, by - 12, 14, 14), -90, 110)
    path.arcTo(QRectF(size * 0.54, size * 0.32, size * 0.28, size * 0.28), 0, 130)
    path.arcTo(QRectF(size * 0.26, size * 0.24, size * 0.32, size * 0.32), 60, 135)
    path.arcTo(QRectF(bx - 4, by - 14, 16, 16), 135, 135)
    path.closeSubpath()
    painter.drawPath(path)


def _icon_with_outline(
    icon_name: str,
    fill_color: str,
    rotation: float,
    size: int,
    outline_color: str = "#000000",
) -> QPixmap:
    outer = size + _OUTLINE_PAD * 2
    result = QPixmap(outer, outer)
    result.fill(Qt.GlobalColor.transparent)

    if icon_name == "type":
        p = QPainter(result)
        for dx, dy in _OUTLINE_OFFSETS:
            p.save()
            p.translate(_OUTLINE_PAD + dx, _OUTLINE_PAD + dy)
            _draw_edge_type_icon(p, size, QColor(outline_color))
            p.restore()
        p.save()
        p.translate(_OUTLINE_PAD, _OUTLINE_PAD)
        _draw_edge_type_icon(p, size, QColor(fill_color))
        p.restore()
        p.end()
        return result

    base_pix = qta.icon(icon_name, color=fill_color).pixmap(size, size)
    outline_pix = qta.icon(icon_name, color=outline_color).pixmap(size, size)

    if rotation:
        t = QTransform()
        t.rotate(rotation)
        mode = Qt.TransformationMode.SmoothTransformation
        base_pix = base_pix.transformed(t, mode)
        outline_pix = outline_pix.transformed(t, mode)

    painter = QPainter(result)
    for dx, dy in _OUTLINE_OFFSETS:
        painter.drawPixmap(_OUTLINE_PAD + dx, _OUTLINE_PAD + dy, outline_pix)
    painter.drawPixmap(_OUTLINE_PAD, _OUTLINE_PAD, base_pix)
    painter.end()
    return result


def _cursor_for_tool(tool: ToolMode, dark: bool = True) -> QCursor:
    if tool == ToolMode.POINTER:
        return QCursor(Qt.CursorShape.ArrowCursor)
    if tool == ToolMode.LASER:
        return QCursor(Qt.CursorShape.BlankCursor)
    if tool == ToolMode.TYPE:
        icon_color = "#FFFFFF" if dark else "#000000"
        pix = _icon_with_outline("type", icon_color, 0, _CURSOR_ICON_PX)
        return QCursor(pix, _CURSOR_ICON_PX // 2, _CURSOR_ICON_PX // 2)

    icon_color = "#FFFFFF" if dark else "#000000"
    size = _CURSOR_ICON_PX

    if tool == ToolMode.PEN:
        pix = _icon_with_outline("fa5s.pen", icon_color, 90, size)
        return QCursor(pix, 2 + _OUTLINE_PAD, 2 + _OUTLINE_PAD)
    if tool == ToolMode.HIGHLIGHTER:
        pix = _icon_with_outline("fa5s.highlighter", icon_color, 90, size)
        return QCursor(pix, 2 + _OUTLINE_PAD, 2 + _OUTLINE_PAD)
    if tool == ToolMode.ERASER:
        pix = _icon_with_outline("fa5s.eraser", icon_color, 0, size)
        c = (size + _OUTLINE_PAD * 2) // 2
        return QCursor(pix, c, c)
    return QCursor(Qt.CursorShape.ArrowCursor)