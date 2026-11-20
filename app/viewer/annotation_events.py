# app/viewer/annotation_events.py
"""PDF_Editor – Mouse, keyboard, and context-menu interaction mixin for presentation AnnotationOverlay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import QApplication, QWidget

from app.viewer.annotation_types import (
    Stroke, TextBox, ToolMode, _DEFAULT_FONT_SIZE, _POINT_MERGE_SQ,
    auto_fit_box_to_text, fit_font_size,
)

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object


class AnnotationEventsMixin(_Base):
    """Mixin implementing mouse clicks, resizing, text input, nudging, and context menu dispatch."""

    if TYPE_CHECKING:
        HANDLE_NONE: int
        HANDLE_TL: int
        HANDLE_TR: int
        HANDLE_BL: int
        HANDLE_BR: int

        _tool: ToolMode
        _active_box: TextBox | None
        _drag_handle: int
        _drag_start_pos: QPointF
        _drag_start_rect: QRectF
        _moving_box: bool
        _cursor_pos: int
        _cursor_visible: bool
        _sel_start: int
        _sel_end: int
        _blink_timer: Any
        _current_stroke: Stroke | None
        _current_page: int
        _text_boxes: dict[int, list[TextBox]]
        _strokes: dict[int, list[Stroke]]
        _pen_color: QColor
        _default_text_stroke: bool
        _default_text_cloud: bool
        _laser_pos: QPoint | None

        def parentWidget(self) -> QWidget | None: ...
        def update(self, *args: Any) -> None: ...
        def setFocus(self) -> None: ...
        def setCursor(self, cursor: Any) -> None: ...
        def width(self) -> int: ...
        def height(self) -> int: ...
        def undo(self) -> bool: ...
        def redo(self) -> bool: ...
        def _push_undo(self) -> None: ...
        def _clear_text_selection(self) -> None: ...
        def _has_text_selection(self) -> bool: ...
        def _get_selection_range(self) -> tuple[int, int]: ...
        def _remove_box(self, box: TextBox) -> None: ...
        def _handle_at(self, box: TextBox, pt: QPointF) -> int: ...
        def _find_box_at(self, pt: QPointF) -> TextBox | None: ...
        def _apply_cursor(self) -> None: ...
        def _untransformed_point(self, pos: QPoint) -> QPoint: ...
        def _new_stroke(self, start: QPoint) -> Stroke: ...
        def _erase_at(self, pos: QPoint) -> bool: ...

    def contextMenuEvent(self, e) -> None:
        if self._active_box is not None and not self._active_box.text.strip():
            self._remove_box(self._active_box)
            self._active_box = None
            self._clear_text_selection()
            self._blink_timer.stop()
            self.update()
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "contextMenuEvent"):
            parent.contextMenuEvent(e)
            e.accept()
        else:
            super().contextMenuEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        pos = e.position().toPoint()
        unpanned_pos_f = QPointF(self._untransformed_point(pos))

        parent = self.parentWidget()
        show_hud_fn = getattr(parent, "_show_hud", None)
        if callable(show_hud_fn):
            show_hud_fn()

        if self._active_box is not None:
            handle = self._handle_at(self._active_box, unpanned_pos_f)
            if handle != self.HANDLE_NONE:
                self._drag_handle = handle
                self._drag_start_pos = unpanned_pos_f
                self._drag_start_rect = QRectF(self._active_box.rect)
                e.accept()
                return
            if self._active_box.rect.contains(unpanned_pos_f):
                self._clear_text_selection()
                self._cursor_pos = len(self._active_box.text)
                self._moving_box = True
                self._drag_start_pos = unpanned_pos_f
                self._drag_start_rect = QRectF(self._active_box.rect)
                self.setFocus()
                self.update()
                e.accept()
                return

        if self._tool in (ToolMode.TYPE, ToolMode.POINTER):
            hit_box = self._find_box_at(unpanned_pos_f)
            if hit_box:
                if self._active_box and not self._active_box.text.strip() and self._active_box is not hit_box:
                    self._remove_box(self._active_box)
                self._active_box = hit_box
                self._clear_text_selection()
                self._cursor_pos = len(hit_box.text)
                self._cursor_visible = True
                self._blink_timer.start()
                self._moving_box = True
                self._drag_start_pos = unpanned_pos_f
                self._drag_start_rect = QRectF(hit_box.rect)
                self.setFocus()
                self.update()
                e.accept()
                return

        if self._tool == ToolMode.TYPE:
            if self._active_box and not self._active_box.text.strip():
                self._remove_box(self._active_box)
            self._push_undo()

            font = QFont("Segoe UI", int(_DEFAULT_FONT_SIZE))
            font.setPointSizeF(_DEFAULT_FONT_SIZE)
            fm = QFontMetricsF(font)
            init_w = max(52.0, fm.averageCharWidth() * 3 + 16.0)
            init_h = max(34.0, fm.lineSpacing() + 12.0)

            x_init = unpanned_pos_f.x()
            y_init = unpanned_pos_f.y()

            pw = getattr(parent, "_rendered_pw", float(self.width())) if parent else float(self.width())
            ph = getattr(parent, "_rendered_ph", float(self.height())) if parent else float(self.height())

            if x_init + init_w > pw - 10:
                x_init = max(10.0, pw - init_w - 10)
            if y_init + init_h > ph - 10:
                y_init = max(10.0, ph - init_h - 10)

            new_box = TextBox(
                rect=QRectF(x_init, y_init, init_w, init_h),
                text="",
                color=QColor(self._pen_color),
                font_size=_DEFAULT_FONT_SIZE,
                id=int(Qt.Key.Key_T) + len(self._text_boxes.get(self._current_page, [])),
                has_stroke=self._default_text_stroke,
                has_cloud=self._default_text_cloud,
            )
            self._text_boxes.setdefault(self._current_page, []).append(new_box)
            self._active_box = new_box
            self._clear_text_selection()
            self._cursor_pos = 0
            self._cursor_visible = True
            self._blink_timer.start()
            self.setFocus()
            self.update()
            e.accept()
            return

        if self._active_box is not None:
            if not self._active_box.text.strip():
                self._remove_box(self._active_box)
            self._active_box = None
            self._clear_text_selection()
            self._blink_timer.stop()
            self.update()

        if self._tool in (ToolMode.PEN, ToolMode.HIGHLIGHTER):
            self._current_stroke = self._new_stroke(pos)
            self.update()
            e.accept()
            return

        if self._tool == ToolMode.ERASER:
            if self._erase_at(pos):
                self.update()
            e.accept()
            return

        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        parent = self.parentWidget()
        show_hud_fn = getattr(parent, "_show_hud", None)
        if callable(show_hud_fn):
            show_hud_fn()
        pos = e.position().toPoint()
        unpanned_pos_f = QPointF(self._untransformed_point(pos))

        if self._active_box and self._drag_handle != self.HANDLE_NONE:
            dx = unpanned_pos_f.x() - self._drag_start_pos.x()
            dy = unpanned_pos_f.y() - self._drag_start_pos.y()
            r = QRectF(self._drag_start_rect)
            min_w, min_h = 36.0, 24.0

            if self._drag_handle == self.HANDLE_BR:
                r.setRight(max(r.left() + min_w, r.right() + dx))
                r.setBottom(max(r.top() + min_h, r.bottom() + dy))
            elif self._drag_handle == self.HANDLE_BL:
                r.setLeft(min(r.right() - min_w, r.left() + dx))
                r.setBottom(max(r.top() + min_h, r.bottom() + dy))
            elif self._drag_handle == self.HANDLE_TR:
                r.setRight(max(r.left() + min_w, r.right() + dx))
                r.setTop(min(r.bottom() - min_h, r.top() + dy))
            elif self._drag_handle == self.HANDLE_TL:
                r.setLeft(min(r.right() - min_w, r.left() + dx))
                r.setTop(min(r.bottom() - min_h, r.top() + dy))

            self._active_box.rect = r
            self._active_box.font_size = fit_font_size(self._active_box.text, r)
            self.update()
            e.accept()
            return

        if self._active_box and self._moving_box and (e.buttons() & Qt.MouseButton.LeftButton):
            dx = unpanned_pos_f.x() - self._drag_start_pos.x()
            dy = unpanned_pos_f.y() - self._drag_start_pos.y()
            r = QRectF(self._drag_start_rect)
            r.translate(dx, dy)
            self._active_box.rect = r
            self.update()
            e.accept()
            return

        if self._active_box and self._tool in (ToolMode.TYPE, ToolMode.POINTER):
            handle = self._handle_at(self._active_box, unpanned_pos_f)
            if handle in (self.HANDLE_TL, self.HANDLE_BR):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if handle in (self.HANDLE_TR, self.HANDLE_BL):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
            if self._active_box.rect.contains(unpanned_pos_f):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
            self._apply_cursor()

        if self._tool == ToolMode.LASER:
            self._laser_pos = pos
            self.update()
            e.accept()
            return

        if self._current_stroke is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            unpanned_curr = self._untransformed_point(pos)
            last = self._current_stroke.points[-1]
            dx = unpanned_curr.x() - last.x()
            dy = unpanned_curr.y() - last.y()
            if dx * dx + dy * dy >= _POINT_MERGE_SQ:
                self._current_stroke.path.lineTo(unpanned_curr)
                self._current_stroke.points.append(QPoint(unpanned_curr))
                self.update()
            e.accept()
            return

        if self._tool == ToolMode.ERASER and (e.buttons() & Qt.MouseButton.LeftButton):
            if self._erase_at(pos):
                self.update()
            e.accept()
            return

        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            return

        if self._drag_handle != self.HANDLE_NONE or self._moving_box:
            self._drag_handle = self.HANDLE_NONE
            self._moving_box = False
            self._push_undo()
            self.update()
            e.accept()
            return

        if self._current_stroke is not None:
            if len(self._current_stroke.points) >= 2:
                self._push_undo()
                self._strokes.setdefault(self._current_page, []).append(self._current_stroke)
            self._current_stroke = None
            self.update()
            e.accept()
            return

        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        key = e.key()
        modifiers = e.modifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self.redo()
                else:
                    self.undo()
                e.accept()
                return
            if key == Qt.Key.Key_Y:
                self.redo()
                e.accept()
                return

        if self._active_box is not None:
            parent = self.parentWidget()
            pw = getattr(parent, "_rendered_pw", float(self.width())) if parent else float(self.width())
            ph = getattr(parent, "_rendered_ph", float(self.height())) if parent else float(self.height())

            # Ctrl + Arrow keys nudge the text box position by pixels
            if (modifiers & Qt.KeyboardModifier.ControlModifier) and key in (
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
            ):
                self._push_undo()
                step = 15.0 if (modifiers & Qt.KeyboardModifier.ShiftModifier) else (1.0 if (modifiers & Qt.KeyboardModifier.AltModifier) else 4.0)
                dx = 0.0
                dy = 0.0
                if key == Qt.Key.Key_Left:
                    dx = -step
                elif key == Qt.Key.Key_Right:
                    dx = step
                elif key == Qt.Key.Key_Up:
                    dy = -step
                elif key == Qt.Key.Key_Down:
                    dy = step

                r = QRectF(self._active_box.rect)
                r.translate(dx, dy)

                if pw > 20 and ph > 20:
                    if r.left() < 5.0:
                        r.moveLeft(5.0)
                    if r.right() > pw - 5.0:
                        r.moveRight(pw - 5.0)
                    if r.top() < 5.0:
                        r.moveTop(5.0)
                    if r.bottom() > ph - 5.0:
                        r.moveBottom(ph - 5.0)

                self._active_box.rect = r
                self.update()
                e.accept()
                return

            if (modifiers & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_A:
                self._sel_start = 0
                self._sel_end = len(self._active_box.text)
                self._cursor_pos = self._sel_end
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._push_undo()
                t_str = self._active_box.text
                s_min, s_max = self._get_selection_range()
                t_str = t_str[:s_min] + "\n" + t_str[s_max:]
                self._active_box.text = t_str
                self._cursor_pos = s_min + 1
                self._clear_text_selection()
                auto_fit_box_to_text(self._active_box, pw, ph)
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Backspace:
                self._push_undo()
                t_str = self._active_box.text
                if self._has_text_selection():
                    s_min, s_max = self._get_selection_range()
                    self._active_box.text = t_str[:s_min] + t_str[s_max:]
                    self._cursor_pos = s_min
                    self._clear_text_selection()
                elif self._cursor_pos > 0:
                    self._active_box.text = t_str[:self._cursor_pos - 1] + t_str[self._cursor_pos:]
                    self._cursor_pos -= 1
                auto_fit_box_to_text(self._active_box, pw, ph)
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Delete:
                t_str = self._active_box.text
                if self._has_text_selection():
                    self._push_undo()
                    s_min, s_max = self._get_selection_range()
                    self._active_box.text = t_str[:s_min] + t_str[s_max:]
                    self._cursor_pos = s_min
                    self._clear_text_selection()
                    auto_fit_box_to_text(self._active_box, pw, ph)
                    self._cursor_visible = True
                    self.update()
                elif self._cursor_pos < len(t_str):
                    self._push_undo()
                    self._active_box.text = t_str[:self._cursor_pos] + t_str[self._cursor_pos + 1:]
                    auto_fit_box_to_text(self._active_box, pw, ph)
                    self._cursor_visible = True
                    self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Left:
                if self._has_text_selection():
                    self._cursor_pos = min(self._sel_start, self._sel_end)
                    self._clear_text_selection()
                else:
                    self._cursor_pos = max(0, self._cursor_pos - 1)
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Right:
                if self._has_text_selection():
                    self._cursor_pos = max(self._sel_start, self._sel_end)
                    self._clear_text_selection()
                else:
                    self._cursor_pos = min(len(self._active_box.text), self._cursor_pos + 1)
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Home:
                self._cursor_pos = 0
                self._clear_text_selection()
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_End:
                self._cursor_pos = len(self._active_box.text)
                self._clear_text_selection()
                self._cursor_visible = True
                self.update()
                e.accept()
                return

            if key == Qt.Key.Key_Escape:
                if not self._active_box.text.strip():
                    self._remove_box(self._active_box)
                self._active_box = None
                self._clear_text_selection()
                self._blink_timer.stop()
                self.update()
                e.accept()
                return

            if (modifiers & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_C:
                if self._has_text_selection():
                    s_min, s_max = self._get_selection_range()
                    QApplication.clipboard().setText(self._active_box.text[s_min:s_max])
                else:
                    QApplication.clipboard().setText(self._active_box.text)
                e.accept()
                return

            if (modifiers & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_V:
                clip_text = QApplication.clipboard().text()
                if clip_text:
                    self._push_undo()
                    t_str = self._active_box.text
                    s_min, s_max = self._get_selection_range()
                    t_str = t_str[:s_min] + clip_text + t_str[s_max:]
                    self._active_box.text = t_str
                    self._cursor_pos = s_min + len(clip_text)
                    self._clear_text_selection()
                    auto_fit_box_to_text(self._active_box, pw, ph)
                    self._cursor_visible = True
                    self.update()
                e.accept()
                return

            char = e.text()
            if char and char.isprintable():
                self._push_undo()
                t_str = self._active_box.text
                s_min, s_max = self._get_selection_range()
                t_str = t_str[:s_min] + char + t_str[s_max:]
                self._active_box.text = t_str
                self._cursor_pos = s_min + len(char)
                self._clear_text_selection()
                auto_fit_box_to_text(self._active_box, pw, ph)
                self._cursor_visible = True
                self.update()
                e.accept()
                return

        super().keyPressEvent(e)

    def leaveEvent(self, e):
        if self._tool == ToolMode.LASER and self._laser_pos is not None:
            self._laser_pos = None
            self.update()
        super().leaveEvent(e)