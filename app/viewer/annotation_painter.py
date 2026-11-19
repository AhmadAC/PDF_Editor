# app/viewer/annotation_painter.py
"""PDF_Editor – Annotation overlay painter rendering strokes, text boxes, and laser pointer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen

from app.constants import ACCENT
from app.viewer.annotation_types import ToolMode, _LASER_RADIUS, _OUTLINE_OFFSETS

if TYPE_CHECKING:
    from app.viewer.annotation_layer import AnnotationOverlay


class AnnotationPainter:
    """Renders freehand strokes, transparent cloud boxes, text outlines, and laser highlights."""

    @staticmethod
    def paint(overlay: AnnotationOverlay) -> None:
        p = QPainter(overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. Draw strokes and text boxes in page canvas space (transforming with panning offsets)
        p.save()
        parent = overlay.parentWidget()
        if parent is not None and hasattr(parent, "_pan_x") and hasattr(parent, "_pan_y"):
            p.translate(float(parent._pan_x), float(parent._pan_y))

        for s in overlay._strokes.get(overlay._current_page, ()):
            pen = QPen(s.color, s.width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(s.path)

        if overlay._current_stroke is not None and len(overlay._current_stroke.points) >= 1:
            s = overlay._current_stroke
            pen = QPen(s.color, s.width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(s.path)

        for box in overlay._text_boxes.get(overlay._current_page, []):
            is_active = (box is overlay._active_box)
            font = QFont("Segoe UI", int(box.font_size))
            font.setPointSizeF(box.font_size)
            p.setFont(font)
            fm = QFontMetricsF(font)

            if box.has_cloud:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 102))
                p.drawRoundedRect(box.rect, 8.0, 8.0)
                p.setBrush(Qt.BrushStyle.NoBrush)

            inner_rect = box.rect.adjusted(7, 5, -7, -5)

            if box.has_stroke:
                p.setPen(QPen(QColor("#000000")))
                for dx, dy in _OUTLINE_OFFSETS:
                    p.drawText(
                        inner_rect.translated(dx, dy),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                        box.text,
                    )

            p.setPen(QPen(box.color))
            p.drawText(
                inner_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                box.text,
            )

            if is_active:
                p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(box.rect)

                for h_rect in overlay._box_handles(box.rect).values():
                    p.setPen(QPen(QColor(ACCENT), 1.5))
                    p.setBrush(QColor("#FFFFFF"))
                    p.drawRect(h_rect)

                if overlay._has_text_selection():
                    s_min, s_max = overlay._get_selection_range()
                    selected_chars = box.text[s_min:s_max]
                    before_sel = box.text[:s_min]
                    lines = before_sel.split("\n")
                    cur_line = lines[-1] if lines else ""
                    line_idx = max(0, len(lines) - 1)
                    sx = inner_rect.left() + fm.horizontalAdvance(cur_line)
                    sy = inner_rect.top() + line_idx * fm.lineSpacing()
                    sw = max(6.0, fm.horizontalAdvance(selected_chars.replace("\n", "")))
                    sh = fm.height()
                    p.fillRect(QRectF(sx, sy, sw, sh), QColor(0, 120, 212, 90))

                elif overlay._cursor_visible and overlay.hasFocus():
                    before_cursor = box.text[:overlay._cursor_pos]
                    lines = before_cursor.split("\n")
                    cur_line = lines[-1] if lines else ""
                    line_idx = max(0, len(lines) - 1)

                    cx = inner_rect.left() + fm.horizontalAdvance(cur_line)
                    cy = inner_rect.top() + line_idx * fm.lineSpacing()
                    cursor_h = max(14.0, fm.height())
                    p.setPen(QPen(box.color, 1.8))
                    p.drawLine(QPointF(cx, cy), QPointF(cx, cy + cursor_h))

        p.restore()

        # 2. Draw laser pointer (20% opacity) in screen space
        if overlay._tool == ToolMode.LASER and overlay._laser_pos is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(239, 68, 68, 51))
            p.drawEllipse(overlay._laser_pos, _LASER_RADIUS, _LASER_RADIUS)
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawEllipse(overlay._laser_pos, _LASER_RADIUS // 3, _LASER_RADIUS // 3)

        p.end()