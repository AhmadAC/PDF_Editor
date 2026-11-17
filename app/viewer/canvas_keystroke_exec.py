# app/viewer/canvas_keystroke_exec.py
"""PDF_Editor – Keyboard navigation, shortcuts, undo/redo, page clipboard, and active item manipulation."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas


class CanvasKeystrokeHandler:
    """Interprets keyboard inputs and executes navigation, zoom, and canvas transformations."""

    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas

    def handle_key_press(
        self,
        e,
        select_all_fn: Callable[[], None],
        clear_sel_fn: Callable[[], None],
    ) -> bool:
        c = self.canvas
        key = e.key()
        modifiers = e.modifiers()

        # 1. Zoom shortcuts
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                c.zoom_in()
                return True
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                c.zoom_out()
                return True
            if key == Qt.Key.Key_0:
                c.zoom_reset()
                return True

        # 2. Placing signature cancellation
        if c._placing_signature and key == Qt.Key.Key_Escape:
            c.cancel_signature_placement()
            return True

        # 3. Active Signature manipulation
        if c._active_sig is not None:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                c.commit_active_signature()
                return True
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                c.delete_active_signature()
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = 15.0 if (modifiers & Qt.KeyboardModifier.ShiftModifier) else (1.0 if (modifiers & Qt.KeyboardModifier.AltModifier) else 4.0)
                dx, dy = 0.0, 0.0
                if key == Qt.Key.Key_Left:
                    dx = -step
                elif key == Qt.Key.Key_Right:
                    dx = step
                elif key == Qt.Key.Key_Up:
                    dy = -step
                elif key == Qt.Key.Key_Down:
                    dy = step
                z = c._zoom or 1.0
                page_idx = c._active_sig.get("page", 0)
                entry = c._entries[page_idx] if 0 <= page_idx < len(c._entries) else None
                max_w = entry.w / z if entry else 1000.0
                max_h = entry.h / z if entry else 1000.0
                r = c._active_sig["rect"]
                w, h = r.width, r.height
                new_x0 = max(0.0, min(max_w - w, r.x0 + dx))
                new_y0 = max(0.0, min(max_h - h, r.y0 + dy))
                c._active_sig["rect"] = fitz.Rect(new_x0, new_y0, new_x0 + w, new_y0 + h)
                c._active_sig["orig_rect"] = fitz.Rect(c._active_sig["rect"])
                c.update()
                return True

        # 4. Crop Mode shortcuts
        if c._crop_mode and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            c.crop_applied.emit()
            return True
        if c._crop_mode and (modifiers & Qt.KeyboardModifier.ControlModifier):
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    c.crop_redo_requested.emit()
                else:
                    c.crop_undo_requested.emit()
                return True
            elif key == Qt.Key.Key_Y:
                c.crop_redo_requested.emit()
                return True

        # 5. Page switching Left / Right
        if not (modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                cur_p = getattr(c, "_active_page_idx", None)
                if cur_p is None:
                    sa = c._get_scroll_area()
                    sb_val = sa.verticalScrollBar().value() if sa else 0
                    cur_p = c.page_at_y(sb_val)
                if key == Qt.Key.Key_Left and cur_p > 0:
                    target = cur_p - 1
                    c._active_page_idx = target
                    c.page_action_requested.emit("go_to_page", target)
                    return True
                elif key == Qt.Key.Key_Right and cur_p < len(c._entries) - 1:
                    target = cur_p + 1
                    c._active_page_idx = target
                    c.page_action_requested.emit("go_to_page", target)
                    return True

        # 6. Scroll navigation
        if c._active_sig is None and not c._placing_signature:
            if not (modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End):
                    if c._sel_text and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                        clear_sel_fn()
                        c.text_copied.emit("")
                        c.update()
                    sa = c._get_scroll_area()
                    if sa:
                        sb = sa.verticalScrollBar()
                        if sb:
                            step = 150 if (modifiers & Qt.KeyboardModifier.ShiftModifier) else 60
                            if key == Qt.Key.Key_Down:
                                sb.setValue(min(sb.maximum(), sb.value() + step))
                                return True
                            elif key == Qt.Key.Key_Up:
                                sb.setValue(max(0, sb.value() - step))
                                return True
                            elif key == Qt.Key.Key_PageDown:
                                sb.setValue(min(sb.maximum(), sb.value() + sb.pageStep()))
                                return True
                            elif key == Qt.Key.Key_PageUp:
                                sb.setValue(max(0, sb.value() - sb.pageStep()))
                                return True
                            elif key == Qt.Key.Key_Home:
                                sb.setValue(0)
                                return True
                            elif key == Qt.Key.Key_End:
                                sb.setValue(sb.maximum())
                                return True

        # 7. Document operations
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            sa = c._get_scroll_area()
            sb_val = sa.verticalScrollBar().value() if sa else 0
            idx = c.page_at_y(sb_val)
            if key == Qt.Key.Key_Z:
                action = "redo" if (modifiers & Qt.KeyboardModifier.ShiftModifier) else "undo"
                c.page_action_requested.emit(action, [idx])
                return True
            elif key == Qt.Key.Key_Y:
                c.page_action_requested.emit("redo", [idx])
                return True
            elif key == Qt.Key.Key_D:
                c.page_action_requested.emit("duplicate", [idx])
                return True
            elif key == Qt.Key.Key_X and not c._sel_text:
                c.page_action_requested.emit("cut", [idx])
                return True
            elif key == Qt.Key.Key_V:
                from app.viewer.panel_page_ops import has_cut_pages
                if has_cut_pages():
                    c.page_action_requested.emit("paste_pages", [idx])
                    return True
            elif key == Qt.Key.Key_Left:
                c.page_action_requested.emit("rotate_left", [idx])
                return True
            elif key == Qt.Key.Key_Right:
                c.page_action_requested.emit("rotate_right", [idx])
                return True
            elif key == Qt.Key.Key_C and c._sel_text:
                QApplication.clipboard().setText(c._sel_text)
                win: Any = c.window()
                set_status = getattr(win, "_set_status", None)
                if callable(set_status):
                    set_status(f"Copied {len(c._sel_text):,} characters to clipboard")
                return True
            elif key == Qt.Key.Key_A:
                select_all_fn()
                return True

        if key == Qt.Key.Key_Escape and c._sel_text:
            clear_sel_fn()
            c.text_copied.emit("")
            c.update()
            return True

        if not c._crop_mode and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            sa = c._get_scroll_area()
            sb_val = sa.verticalScrollBar().value() if sa else 0
            idx = c.page_at_y(sb_val)
            c.page_action_requested.emit("delete", [idx])
            return True

        return False

