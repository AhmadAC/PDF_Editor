

# app/editor/canvas_events_move_key.py
"""Mouse move, hover cursor, marquee selection geometry, and keyboard handlers for PdfEditCanvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QRect, Qt

from app.editor.canvas_events_helpers import (
    _find_tab_editar,
    _resize_overlay_from_rect,
    _translate_overlay,
)
from app.editor.canvas_overlay1 import (
    CanvasOverlayManager,
    overlay_pdf_rect,
    snapshot_overlay,
)


class CanvasMoveKeyMixin:
    """Mixin handling mouse movement, drag-resize/move, marquee hit-testing, and keyboard shortcuts."""

    if TYPE_CHECKING:
        canvas: Any

        def _commit_overlay_mutation(
            self, ov: dict, before_snap: dict | None, force_committed: bool = False
        ) -> None: ...

    def _get_active_sel_handle_at(self, pos: QPoint) -> int:
        c = self.canvas
        if c._active_sel_rect is None or c._active_sel_page < 0 or c._active_sel_page >= len(c._page_offsets):
            return CanvasOverlayManager.HANDLE_NONE
        yo = c._page_offsets[c._active_sel_page][0]
        z = c._zoom
        r = c._active_sel_rect
        sx0 = int(r.x0 * z)
        sy0 = yo + int(r.y0 * z)
        sx1 = int(r.x1 * z)
        sy1 = yo + int(r.y1 * z)
        hs = 10
        if QRect(sx0 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return CanvasOverlayManager.HANDLE_TL
        if QRect(sx1 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return CanvasOverlayManager.HANDLE_TR
        if QRect(sx0 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return CanvasOverlayManager.HANDLE_BL
        if QRect(sx1 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return CanvasOverlayManager.HANDLE_BR
        return CanvasOverlayManager.HANDLE_NONE

    def _is_pos_inside_active_sel(self, pos: QPoint) -> bool:
        c = self.canvas
        if c._active_sel_rect is None or c._active_sel_page < 0 or c._active_sel_page >= len(c._page_offsets):
            return False
        yo = c._page_offsets[c._active_sel_page][0]
        z = c._zoom
        r = c._active_sel_rect
        sx0 = int(r.x0 * z)
        sy0 = yo + int(r.y0 * z)
        sx1 = int(r.x1 * z)
        sy1 = yo + int(r.y1 * z)
        return QRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0)).contains(pos)

    def handle_mouse_move(self, e) -> None:
        c = self.canvas
        pos = e.position().toPoint()

        if c._placing_signature:
            c._sig_cursor_pos = pos
            c.update()
            e.accept()
            return

        if getattr(c, "_placing_stamp", False):
            c._stamp_cursor_pos = pos
            c.update()
            e.accept()
            return

        is_add_text = bool(
            getattr(c, "_text_mode", False) and getattr(c, "_text_submode", "edit") == "add"
        )
        if is_add_text:
            c.setCursor(Qt.CursorShape.IBeamCursor)
            if c._drag_start and (e.buttons() & Qt.MouseButton.LeftButton):
                c._drag_rect = QRect(c._drag_start, pos).normalized()
                c.update()
            return

        if getattr(c, "_select_mode", False) and c._active_sel_rect is not None:
            z = c._zoom
            orig = getattr(c, "_active_sel_orig_rect", None)
            start = getattr(c, "_active_sel_drag_start", QPoint())
            handle = getattr(c, "_active_sel_handle", CanvasOverlayManager.HANDLE_NONE)

            if handle != CanvasOverlayManager.HANDLE_NONE and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                r = fitz.Rect(orig)
                min_s = 10.0
                if handle == CanvasOverlayManager.HANDLE_BR:
                    r.x1 = max(r.x0 + min_s, orig.x1 + dx)
                    r.y1 = max(r.y0 + min_s, orig.y1 + dy)
                elif handle == CanvasOverlayManager.HANDLE_BL:
                    r.x0 = min(r.x1 - min_s, orig.x0 + dx)
                    r.y1 = max(r.y0 + min_s, orig.y1 + dy)
                elif handle == CanvasOverlayManager.HANDLE_TR:
                    r.x1 = max(r.x0 + min_s, orig.x1 + dx)
                    r.y0 = min(r.y1 - min_s, orig.y0 + dy)
                elif handle == CanvasOverlayManager.HANDLE_TL:
                    r.x0 = min(r.x1 - min_s, orig.x0 + dx)
                    r.y0 = min(r.y1 - min_s, orig.y0 + dy)
                c._active_sel_rect = r
                c.update()
                e.accept()
                return

            if getattr(c, "_active_sel_moving", False) and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                w = orig.width
                h = orig.height
                c._active_sel_rect = fitz.Rect(orig.x0 + dx, orig.y0 + dy, orig.x0 + dx + w, orig.y0 + dy + h)
                c.update()
                e.accept()
                return

            h_id = self._get_active_sel_handle_at(pos)
            if h_id in (CanvasOverlayManager.HANDLE_TL, CanvasOverlayManager.HANDLE_BR):
                c.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if h_id in (CanvasOverlayManager.HANDLE_TR, CanvasOverlayManager.HANDLE_BL):
                c.setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
            if self._is_pos_inside_active_sel(pos):
                c.setCursor(Qt.CursorShape.SizeAllCursor)
                return

        if c._selected_overlay_idx >= 0 and 0 <= c._selected_overlay_idx < len(c._overlays):
            z = c._zoom
            orig = c._drag_start_rect
            start = c._drag_start_pos
            ov = c._overlays[c._selected_overlay_idx]
            snap = c._drag_before_snapshot or {}

            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                if (abs(pos.x() - start.x()) >= 1 or abs(pos.y() - start.y()) >= 1) and ov.get("_existing") and not ov.get("_dirty"):
                    ov["_dirty"] = True
                    c.refresh_pages({ov.get("page", 0)})

                handle = c._drag_handle
                r = fitz.Rect(orig)
                min_s = 8.0

                if handle == CanvasOverlayManager.HANDLE_BR:
                    r.x1 = max(r.x0 + min_s, orig.x1 + dx)
                    r.y1 = max(r.y0 + min_s, orig.y1 + dy)
                elif handle == CanvasOverlayManager.HANDLE_BL:
                    r.x0 = min(r.x1 - min_s, orig.x0 + dx)
                    r.y1 = max(r.y0 + min_s, orig.y1 + dy)
                elif handle == CanvasOverlayManager.HANDLE_TR:
                    r.x1 = max(r.x0 + min_s, orig.x1 + dx)
                    r.y0 = min(r.y1 - min_s, orig.y0 + dy)
                elif handle == CanvasOverlayManager.HANDLE_TL:
                    r.x0 = min(r.x1 - min_s, orig.x0 + dx)
                    r.y0 = min(r.y1 - min_s, orig.y0 + dy)

                _resize_overlay_from_rect(ov, snap, orig, r)
                c.overlay_changed.emit()
                c.update()
                e.accept()
                return

            if c._moving_overlay and snap:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                if (abs(pos.x() - start.x()) >= 1 or abs(pos.y() - start.y()) >= 1) and ov.get("_existing") and not ov.get("_dirty"):
                    ov["_dirty"] = True
                    c.refresh_pages({ov.get("page", 0)})
                _translate_overlay(ov, snap, dx, dy)
                c.overlay_changed.emit()
                c.update()
                e.accept()
                return

            h_id = c._overlay_mgr.get_overlay_handle_at(c._selected_overlay_idx, pos)
            if h_id in (CanvasOverlayManager.HANDLE_TL, CanvasOverlayManager.HANDLE_BR):
                c.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if h_id in (CanvasOverlayManager.HANDLE_TR, CanvasOverlayManager.HANDLE_BL):
                c.setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
            if c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
                c.setCursor(Qt.CursorShape.SizeAllCursor)
                return
            c.setCursor(Qt.CursorShape.IBeamCursor if c._text_mode else Qt.CursorShape.ArrowCursor)

        if c._draw_mode and c._current_stroke is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            if c._stroke_page < 0 or c._stroke_page >= len(c._page_offsets):
                return
            yo, pw, ph = c._page_offsets[c._stroke_page]
            sx = float(max(0, min(pos.x(), pw)))
            sy = float(max(yo, min(pos.y(), yo + ph)))
            last = c._current_stroke[-1]
            if (sx - last[0]) ** 2 + (sy - last[1]) ** 2 >= 3:
                c._current_stroke.append((sx, sy))
                c.update()
            return

        if c._drag_start and (e.buttons() & Qt.MouseButton.LeftButton):
            c._drag_rect = QRect(c._drag_start, pos).normalized()
            c.update()

    def handle_key_press(self, e) -> None:
        c = self.canvas
        key = e.key()
        modifiers = e.modifiers()

        if (modifiers & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_R and not (
            modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            tab = _find_tab_editar(c)
            if tab and hasattr(tab, "reset_pdf"):
                tab.reset_pdf()
                e.accept()
                return

        if (modifiers & Qt.KeyboardModifier.ControlModifier) and not (
            modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                digit = key - Qt.Key.Key_0
                tab = _find_tab_editar(c)
                if tab and hasattr(tab, "_apply_color_shortcut"):
                    if tab._apply_color_shortcut(digit):
                        e.accept()
                        return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if c._selected_overlay_idx >= 0:
                c.delete_selected_overlay()
                e.accept()
                return
            if getattr(c, "_select_mode", False) and getattr(c, "_active_sel_rect", None):
                c.delete_active_selection()
                e.accept()
                return

        if key == Qt.Key.Key_Escape:
            if c._placing_signature:
                c.cancel_signature_placement()
                e.accept()
                return
            if getattr(c, "_placing_stamp", False):
                c.cancel_stamp_placement()
                e.accept()
                return
            if c._selected_overlay_idx >= 0:
                c._selected_overlay_idx = -1
                c._overlay_mgr.discard_untouched_existing()
                c.update()
                e.accept()
                return
            if getattr(c, "_active_sel_rect", None):
                c.clear_active_selection()
                e.accept()
                return

        if (c._selected_overlay_idx >= 0 or (getattr(c, "_select_mode", False) and getattr(c, "_active_sel_rect", None))) and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 15.0 if (modifiers & Qt.KeyboardModifier.ShiftModifier) else (1.0 if (modifiers & Qt.KeyboardModifier.AltModifier) else 4.0)
            dx = 0.0
            dy = 0.0
            if key == Qt.Key.Key_Left: dx = -step
            elif key == Qt.Key.Key_Right: dx = step
            elif key == Qt.Key.Key_Up: dy = -step
            elif key == Qt.Key.Key_Down: dy = step

            if c._selected_overlay_idx >= 0 and 0 <= c._selected_overlay_idx < len(c._overlays):
                ov = c._overlays[c._selected_overlay_idx]
                before_snap = snapshot_overlay(ov)
                was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
                _translate_overlay(ov, before_snap, dx, dy)
                c._drag_start_rect = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                self._commit_overlay_mutation(ov, before_snap, force_committed=was_untouched)
                if was_untouched or ov.get("_z_back"):
                    c.refresh_pages({ov.get("page", 0)})
            elif c._active_sel_rect is not None:
                r = c._active_sel_rect
                c._active_sel_rect = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
                c.rect_selected.emit(c._active_sel_page, c._active_sel_rect)
            c.update()
            e.accept()
            return

        if c._selected_overlay_idx >= 0:
            if (modifiers & Qt.KeyboardModifier.ControlModifier) or key in (Qt.Key.Key_Plus, Qt.Key.Key_Minus):
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    c.scale_selected_overlay(1.10)
                    e.accept()
                    return
                elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    c.scale_selected_overlay(0.90)
                    e.accept()
                    return


