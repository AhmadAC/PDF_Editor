"""PDF_Editor – Mouse gesture routing for canvas interactions (signatures, crop, dragging)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import fitz
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas
    from app.viewer.canvas_action_menus import CanvasActionMenuBuilder
    from app.viewer.canvas_selection_engine import CanvasSelectionEngine


class CanvasGestureRouter:
    """Coordinates mouse movement, clicks, dragging, signatures, and crop adjustments."""

    def __init__(
        self,
        canvas: _SelectCanvas,
        selection_engine: CanvasSelectionEngine,
        menu_builder: CanvasActionMenuBuilder,
    ):
        self.canvas = canvas
        self.selection_engine = selection_engine
        self.menu_builder = menu_builder

    def get_sig_handle_at(self, pos: QPoint) -> int:
        c = self.canvas
        sig = c._active_sig
        if not sig:
            return c.HANDLE_NONE
        page_idx = sig["page"]
        if not (0 <= page_idx < len(c._entries)):
            return c.HANDLE_NONE
        entry = c._entries[page_idx]
        x_off = c.page_x_offset(entry)
        z = c._zoom
        r = sig["rect"]
        sx0 = x_off + int(round(r.x0 * z))
        sy0 = entry.y_off + int(round(r.y0 * z))
        sx1 = x_off + int(round(r.x1 * z))
        sy1 = entry.y_off + int(round(r.y1 * z))
        hs = 10
        if QRect(sx0 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_TL
        if QRect(sx1 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_TR
        if QRect(sx0 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_BL
        if QRect(sx1 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_BR
        return c.HANDLE_NONE

    def is_pos_inside_active_sig(self, pos: QPoint) -> bool:
        c = self.canvas
        sig = c._active_sig
        if not sig:
            return False
        page_idx = sig["page"]
        if not (0 <= page_idx < len(c._entries)):
            return False
        entry = c._entries[page_idx]
        x_off = c.page_x_offset(entry)
        z = c._zoom
        r = sig["rect"]
        sx0 = x_off + int(round(r.x0 * z))
        sy0 = entry.y_off + int(round(r.y0 * z))
        sx1 = x_off + int(round(r.x1 * z))
        sy1 = entry.y_off + int(round(r.y1 * z))
        return QRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0)).contains(pos)

    def mouse_press(self, e) -> None:
        c = self.canvas
        pos = e.position().toPoint()
        page_idx = c.page_at_y(pos.y())
        c._active_page_idx = page_idx

        # 1. Placing Signature from cursor
        if c._placing_signature:
            if e.button() == Qt.MouseButton.RightButton:
                c.cancel_signature_placement()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                if 0 <= page_idx < len(c._entries):
                    entry = c._entries[page_idx]
                    x_off = c.page_x_offset(entry)
                    z = c._zoom
                    px = (pos.x() - x_off) / z
                    py = (pos.y() - entry.y_off) / z
                    pix = c._placing_sig_pixmap
                    aspect = (pix.height() / pix.width()) if pix and pix.width() > 0 else 0.35
                    sig_w = min(180.0, (entry.w / z) * 0.6)
                    sig_h = sig_w * aspect
                    x0 = max(0.0, min(entry.w / z - sig_w, px - sig_w / 2.0))
                    y0 = max(0.0, min(entry.h / z - sig_h, py - sig_h / 2.0))
                    rect = fitz.Rect(x0, y0, x0 + sig_w, y0 + sig_h)
                    c._active_sig = {
                        "page": page_idx,
                        "rect": rect,
                        "path": c._placing_sig_path,
                        "pixmap": c._placing_sig_pixmap,
                        "resizing": False,
                        "moving": False,
                        "handle": c.HANDLE_NONE,
                        "drag_start": pos,
                        "orig_rect": fitz.Rect(rect),
                    }
                    c._placing_signature = False
                    c._placing_sig_path = ""
                    c._sig_cursor_pos = None
                    c.setCursor(Qt.CursorShape.ArrowCursor)
                    c.update()
                e.accept()
                return

        # 2. Interacting with placed Active Signature
        sig = c._active_sig
        if sig is not None:
            handle = self.get_sig_handle_at(pos)
            if handle != c.HANDLE_NONE and e.button() == Qt.MouseButton.LeftButton:
                sig["resizing"] = True
                sig["handle"] = handle
                sig["drag_start"] = pos
                sig["orig_rect"] = fitz.Rect(sig["rect"])
                e.accept()
                return
            if self.is_pos_inside_active_sig(pos) and e.button() == Qt.MouseButton.LeftButton:
                sig["moving"] = True
                sig["drag_start"] = pos
                sig["orig_rect"] = fitz.Rect(sig["rect"])
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                c.commit_active_signature()

        # 3. Crop mode
        if c._crop_mode and e.button() == Qt.MouseButton.LeftButton:
            c.setFocus()
            c._crop_active_page = page_idx
            c._crop_drag_start = pos
            c._crop_drag_cur = pos
            c.update()
            e.accept()
            return

        # 4. Standard text selection
        if e.button() == Qt.MouseButton.LeftButton:
            c.setFocus()
            self.selection_engine.clear_selection()
            c.text_copied.emit("")
            c._drag_start = pos
            c._drag_end = None
            self.selection_engine.is_dragging_text = False
            c.update()
            e.accept()

    def mouse_move(self, e) -> None:
        c = self.canvas
        pos = e.position().toPoint()

        if c._placing_signature:
            c._sig_cursor_pos = pos
            c.update()
            e.accept()
            return

        sig = c._active_sig
        if sig is not None:
            z = c._zoom
            orig = sig.get("orig_rect")
            start = sig.get("drag_start", pos)
            page_idx = sig["page"]
            entry = c._entries[page_idx] if 0 <= page_idx < len(c._entries) else None
            max_w = entry.w / z if entry else 1000.0
            max_h = entry.h / z if entry else 1000.0

            if sig.get("resizing") and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                handle = sig.get("handle", c.HANDLE_NONE)
                r = fitz.Rect(orig)
                min_s = 20.0

                if handle == c.HANDLE_BR:
                    r.x1 = max(r.x0 + min_s, min(max_w, orig.x1 + dx))
                    r.y1 = max(r.y0 + min_s, min(max_h, orig.y1 + dy))
                elif handle == c.HANDLE_BL:
                    r.x0 = min(r.x1 - min_s, max(0.0, orig.x0 + dx))
                    r.y1 = max(r.y0 + min_s, min(max_h, orig.y1 + dy))
                elif handle == c.HANDLE_TR:
                    r.x1 = max(r.x0 + min_s, min(max_w, orig.x1 + dx))
                    r.y0 = min(r.y1 - min_s, max(0.0, orig.y0 + dy))
                elif handle == c.HANDLE_TL:
                    r.x0 = min(r.x1 - min_s, max(0.0, orig.x0 + dx))
                    r.y0 = min(r.y1 - min_s, max(0.0, orig.y0 + dy))

                sig["rect"] = r
                c.update()
                e.accept()
                return

            if sig.get("moving") and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                w = orig.width
                h = orig.height
                new_x0 = max(0.0, min(max_w - w, orig.x0 + dx))
                new_y0 = max(0.0, min(max_h - h, orig.y0 + dy))
                sig["rect"] = fitz.Rect(new_x0, new_y0, new_x0 + w, new_y0 + h)
                c.update()
                e.accept()
                return

            h_id = self.get_sig_handle_at(pos)
            if h_id in (c.HANDLE_TL, c.HANDLE_BR):
                c.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if h_id in (c.HANDLE_TR, c.HANDLE_BL):
                c.setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
            if self.is_pos_inside_active_sig(pos):
                c.setCursor(Qt.CursorShape.SizeAllCursor)
                return
            c.setCursor(Qt.CursorShape.ArrowCursor)

        if c._crop_mode and c._crop_drag_start:
            c._crop_drag_cur = pos
            c.update()
            e.accept()
            return

        if c._drag_start and (e.buttons() & Qt.MouseButton.LeftButton):
            if not self.selection_engine.is_dragging_text:
                dx = pos.x() - c._drag_start.x()
                dy = pos.y() - c._drag_start.y()
                if abs(dx) >= 3 or abs(dy) >= 3:
                    self.selection_engine.is_dragging_text = True
            if self.selection_engine.is_dragging_text:
                c._drag_end = pos
                self.selection_engine.compute_selection()
                c.update()
                e.accept()

    def mouse_double_click(self, e) -> None:
        c = self.canvas
        if c._active_sig is not None and e.button() == Qt.MouseButton.LeftButton:
            c.commit_active_signature()
            e.accept()
            return
        if c._crop_mode and e.button() == Qt.MouseButton.LeftButton:
            c.crop_applied.emit()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            page_idx = c.page_at_y(pos.y())
            c._active_page_idx = page_idx
            if self.selection_engine.select_word_at(page_idx, pos):
                e.accept()

    def mouse_release(self, e) -> None:
        c = self.canvas

        sig = c._active_sig
        if sig is not None:
            if sig.get("resizing") or sig.get("moving"):
                sig["resizing"] = False
                sig["moving"] = False
                sig["handle"] = c.HANDLE_NONE
                c.update()
                e.accept()
                return

        if c._crop_mode and c._crop_drag_start:
            start = c._crop_drag_start
            end = e.position().toPoint()
            page_idx = c._crop_active_page
            c._crop_drag_start = None
            c._crop_drag_cur = None
            if (abs(end.x() - start.x()) > 5 and abs(end.y() - start.y()) > 5
                    and 0 <= page_idx < len(c._entries)):
                entry = c._entries[page_idx]
                x_off = c.page_x_offset(entry)
                z = c._zoom
                p_x0 = max(0.0, min(start.x() - x_off, end.x() - x_off) / z)
                p_y0 = max(0.0, min(start.y() - entry.y_off, end.y() - entry.y_off) / z)
                p_x1 = min(entry.w / z, max(start.x() - x_off, end.x() - x_off) / z)
                p_y1 = min(entry.h / z, max(start.y() - entry.y_off, end.y() - entry.y_off) / z)
                pw = entry.w / z
                ph = entry.h / z
                c.crop_selected.emit(page_idx, (p_x0, p_y0, p_x1, p_y1, pw, ph))
            c.update()
            e.accept()
            return

        if e.button() != Qt.MouseButton.LeftButton or not c._drag_start:
            return

        drag_start = c._drag_start
        pos = e.position().toPoint()
        was_dragging = self.selection_engine.is_dragging_text
        c._drag_start = None
        c._drag_end = None
        self.selection_engine.is_dragging_text = False

        if was_dragging:
            c._drag_end = pos
            c._drag_start = drag_start
            self.selection_engine.compute_selection()
            c._drag_start = None
            c._drag_end = None
            if c._sel_text:
                QApplication.clipboard().setText(c._sel_text)
                c.text_copied.emit(c._sel_text)
            c.update()
            e.accept()
            return

        hit = self.menu_builder.note_icon_at(pos)
        if hit is not None:
            c._open_note = None if c._open_note == hit else hit
            c.update()
            e.accept()
            return

        if c._open_note is not None:
            c._open_note = None
            c.update()

        self.selection_engine.clear_selection()
        c.text_copied.emit("")
        c.update()
        e.accept()