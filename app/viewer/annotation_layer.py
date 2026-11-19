# app/viewer/annotation_layer.py
"""PDF_Editor – Annotation overlay facade for presentation mode coordinating strokes, text boxes, tools, and undo/redo."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QPainterPath, QPainterPathStroker
from PySide6.QtWidgets import QWidget

from app.viewer.annotation_events import AnnotationEventsMixin
from app.viewer.annotation_painter import AnnotationPainter
from app.viewer.annotation_types import (
    Stroke,
    TextBox,
    ToolMode,
    _DEFAULT_FONT_SIZE,
    _ERASER_TOL,
    _HANDLE_SIZE,
    _HIGHLIGHTER_WIDTH,
    _PEN_WIDTH,
    _copy_box,
    _copy_stroke,
    _cursor_for_tool,
    _draw_cloud_btn_icon,
    _draw_edge_type_icon,
    _draw_stroke_btn_icon,
    auto_fit_box_to_text,
    fit_font_size,
)

__all__ = [
    "AnnotationOverlay",
    "Stroke",
    "TextBox",
    "ToolMode",
    "_DEFAULT_FONT_SIZE",
    "_draw_cloud_btn_icon",
    "_draw_edge_type_icon",
    "_draw_stroke_btn_icon",
    "auto_fit_box_to_text",
    "fit_font_size",
]


class AnnotationOverlay(AnnotationEventsMixin, QWidget):
    """Transparent overlay capturing freehand drawings, text annotations, and laser pointers."""

    HANDLE_NONE = 0
    HANDLE_TL = 1
    HANDLE_TR = 2
    HANDLE_BL = 3
    HANDLE_BR = 4

    def __init__(self, parent: QWidget, dark_mode: bool):
        super().__init__(parent)
        self._dark_mode = bool(dark_mode)
        self._tool = ToolMode.POINTER
        self._pen_color = QColor("#EF4444")
        self._pen_width = _PEN_WIDTH
        self._highlighter_color = QColor(251, 191, 36, 90)
        self._highlighter_width = _HIGHLIGHTER_WIDTH

        self._strokes: dict[int, list[Stroke]] = {}
        self._text_boxes: dict[int, list[TextBox]] = {}
        self._current_stroke: Stroke | None = None
        self._current_page: int = 0
        self._laser_pos: QPoint | None = None

        self._active_box: TextBox | None = None
        self._cursor_pos: int = 0
        self._cursor_visible: bool = True
        self._sel_start: int = -1
        self._sel_end: int = -1
        self._drag_handle: int = self.HANDLE_NONE
        self._drag_start_pos: QPointF = QPointF()
        self._drag_start_rect: QRectF = QRectF()
        self._moving_box: bool = False

        self._default_text_stroke: bool = False
        self._default_text_cloud: bool = False

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_cursor_blink)

        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._apply_cursor()

    def set_tool(self, tool: int) -> None:
        new_tool = ToolMode(int(tool))
        if new_tool == self._tool:
            return
        if self._active_box and not self._active_box.text.strip():
            self._remove_box(self._active_box)
        self._active_box = None
        self._clear_text_selection()
        self._blink_timer.stop()
        self._current_stroke = None
        if self._tool == ToolMode.LASER and new_tool != ToolMode.LASER:
            self._laser_pos = None
        self._tool = new_tool
        passthrough = (new_tool == ToolMode.POINTER)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, passthrough)
        self._apply_cursor()
        self.update()

    def tool(self) -> int:
        return int(self._tool)

    def set_pen_color(self, color: QColor) -> None:
        c = color if isinstance(color, QColor) else QColor(color)
        if self._active_box:
            self._push_undo()
            self._active_box.color = QColor(c)
            self.update()
        if self._tool == ToolMode.HIGHLIGHTER:
            self._highlighter_color = QColor(c.red(), c.green(), c.blue(), 90)
        else:
            self._pen_color = QColor(c)

    def pen_color(self) -> QColor:
        return QColor(self._pen_color)

    def set_current_page(self, idx: int) -> None:
        if idx == self._current_page and self._current_stroke is None:
            return
        if self._active_box and not self._active_box.text.strip():
            self._remove_box(self._active_box)
        self._active_box = None
        self._clear_text_selection()
        self._blink_timer.stop()
        self._current_page = int(idx)
        self._current_stroke = None
        self._laser_pos = None
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.update()

    def clear_current_page(self) -> None:
        if self._strokes.get(self._current_page) or self._text_boxes.get(self._current_page):
            self._push_undo()
        self._strokes.pop(self._current_page, None)
        self._text_boxes.pop(self._current_page, None)
        self._active_box = None
        self._clear_text_selection()
        self._current_stroke = None
        self._blink_timer.stop()
        self.update()

    def clear_all(self, record_undo: bool = True) -> None:
        if record_undo and (self._strokes.get(self._current_page) or self._text_boxes.get(self._current_page)):
            self._push_undo()
        self._strokes.clear()
        self._text_boxes.clear()
        self._active_box = None
        self._clear_text_selection()
        self._current_stroke = None
        self._laser_pos = None
        self._blink_timer.stop()
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.update()

    def update_theme(self, dark: bool) -> None:
        self._dark_mode = bool(dark)
        self._apply_cursor()

    def set_laser_pos(self, pos: QPoint | None) -> None:
        if self._tool != ToolMode.LASER:
            return
        self._laser_pos = QPoint(pos) if pos is not None else None
        self.update()

    def toggle_text_stroke(self) -> bool:
        self._push_undo()
        if self._active_box:
            self._active_box.has_stroke = not self._active_box.has_stroke
            res = self._active_box.has_stroke
        else:
            self._default_text_stroke = not self._default_text_stroke
            res = self._default_text_stroke
        self.update()
        return res

    def toggle_text_cloud(self) -> bool:
        self._push_undo()
        if self._active_box:
            self._active_box.has_cloud = not self._active_box.has_cloud
            res = self._active_box.has_cloud
        else:
            self._default_text_cloud = not self._default_text_cloud
            res = self._default_text_cloud
        self.update()
        return res

    def is_active_box_stroke(self) -> bool:
        if self._active_box:
            return self._active_box.has_stroke
        return self._default_text_stroke

    def is_active_box_cloud(self) -> bool:
        if self._active_box:
            return self._active_box.has_cloud
        return self._default_text_cloud

    def _push_undo(self) -> None:
        page_state = {
            "page": self._current_page,
            "strokes": [_copy_stroke(s) for s in self._strokes.get(self._current_page, [])],
            "text_boxes": [_copy_box(b) for b in self._text_boxes.get(self._current_page, [])],
        }
        self._undo_stack.append(page_state)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        current_state = {
            "page": self._current_page,
            "strokes": [_copy_stroke(s) for s in self._strokes.get(self._current_page, [])],
            "text_boxes": [_copy_box(b) for b in self._text_boxes.get(self._current_page, [])],
        }
        self._redo_stack.append(current_state)
        prev = self._undo_stack.pop()
        p = prev["page"]
        self._current_page = p
        self._strokes[p] = [_copy_stroke(s) for s in prev["strokes"]]
        self._text_boxes[p] = [_copy_box(b) for b in prev["text_boxes"]]
        self._active_box = None
        self._clear_text_selection()
        self._blink_timer.stop()
        self.update()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        current_state = {
            "page": self._current_page,
            "strokes": [_copy_stroke(s) for s in self._strokes.get(self._current_page, [])],
            "text_boxes": [_copy_box(b) for b in self._text_boxes.get(self._current_page, [])],
        }
        self._undo_stack.append(current_state)
        nxt = self._redo_stack.pop()
        p = nxt["page"]
        self._current_page = p
        self._strokes[p] = [_copy_stroke(s) for s in nxt["strokes"]]
        self._text_boxes[p] = [_copy_box(b) for b in nxt["text_boxes"]]
        self._active_box = None
        self._clear_text_selection()
        self._blink_timer.stop()
        self.update()
        return True

    def _has_text_selection(self) -> bool:
        return (self._sel_start != -1 and self._sel_end != -1 and self._sel_start != self._sel_end)

    def _clear_text_selection(self) -> None:
        self._sel_start = -1
        self._sel_end = -1

    def _get_selection_range(self) -> tuple[int, int]:
        if not self._has_text_selection():
            return (self._cursor_pos, self._cursor_pos)
        return (min(self._sel_start, self._sel_end), max(self._sel_start, self._sel_end))

    def _toggle_cursor_blink(self) -> None:
        self._cursor_visible = not self._cursor_visible
        if self._active_box:
            self.update(self._active_box.rect.toRect().adjusted(-8, -8, 8, 8))

    def _remove_box(self, box: TextBox) -> None:
        boxes = self._text_boxes.get(self._current_page, [])
        if box in boxes:
            boxes.remove(box)
            if not boxes:
                self._text_boxes.pop(self._current_page, None)

    def _box_handles(self, rect: QRectF) -> dict[int, QRectF]:
        s = _HANDLE_SIZE
        hs = s / 2.0
        return {
            self.HANDLE_TL: QRectF(rect.left() - hs, rect.top() - hs, s, s),
            self.HANDLE_TR: QRectF(rect.right() - hs, rect.top() - hs, s, s),
            self.HANDLE_BL: QRectF(rect.left() - hs, rect.bottom() - hs, s, s),
            self.HANDLE_BR: QRectF(rect.right() - hs, rect.bottom() - hs, s, s),
        }

    def _handle_at(self, box: TextBox, pt: QPointF) -> int:
        for h_id, h_rect in self._box_handles(box.rect).items():
            if h_rect.adjusted(-3, -3, 3, 3).contains(pt):
                return h_id
        return self.HANDLE_NONE

    def _find_box_at(self, pt: QPointF) -> TextBox | None:
        for box in reversed(self._text_boxes.get(self._current_page, [])):
            if box.rect.adjusted(-4, -4, 4, 4).contains(pt):
                return box
        return None

    def _apply_cursor(self) -> None:
        if self._tool == ToolMode.POINTER:
            self.unsetCursor()
            return
        self.setCursor(_cursor_for_tool(self._tool, dark=self._dark_mode))

    def _untransformed_point(self, pos: QPoint) -> QPoint:
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_pan_x") and hasattr(parent, "_pan_y"):
            return QPoint(pos.x() - int(parent._pan_x), pos.y() - int(parent._pan_y))
        return pos

    def _new_stroke(self, start: QPoint) -> Stroke:
        unpanned_start = self._untransformed_point(start)
        if self._tool == ToolMode.HIGHLIGHTER:
            color = QColor(self._highlighter_color)
            width = self._highlighter_width
            kind = ToolMode.HIGHLIGHTER
        else:
            color = QColor(self._pen_color)
            width = self._pen_width
            kind = ToolMode.PEN
        path = QPainterPath()
        path.moveTo(unpanned_start)
        return Stroke(path=path, color=color, width=width, kind=kind,
                      points=[QPoint(unpanned_start)])

    def _erase_at(self, pos: QPoint) -> bool:
        unpanned_pos = self._untransformed_point(pos)
        page_strokes = self._strokes.get(self._current_page)
        hit_stroke = False
        if page_strokes:
            for i in range(len(page_strokes) - 1, -1, -1):
                s = page_strokes[i]
                stroker = QPainterPathStroker()
                stroker.setWidth(max(2, s.width) + _ERASER_TOL)
                stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
                stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                outline = stroker.createStroke(s.path)
                if outline.contains(unpanned_pos):
                    self._push_undo()
                    page_strokes.pop(i)
                    if not page_strokes:
                        self._strokes.pop(self._current_page, None)
                    hit_stroke = True
                    break

        page_boxes = self._text_boxes.get(self._current_page, [])
        for i in range(len(page_boxes) - 1, -1, -1):
            if page_boxes[i].rect.contains(unpanned_pos):
                self._push_undo()
                if self._active_box is page_boxes[i]:
                    self._active_box = None
                    self._clear_text_selection()
                    self._blink_timer.stop()
                page_boxes.pop(i)
                hit_stroke = True
                break

        return hit_stroke

    def paintEvent(self, _):
        AnnotationPainter.paint(self)