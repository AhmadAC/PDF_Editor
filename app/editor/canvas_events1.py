
# app/editor/canvas_events1.py

"""Canvas interaction handler: mouse, drag handles, keyboard, stroke smoothing, vector stamps, and rich context menus."""

from __future__ import annotations

import fitz
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtWidgets import QMenu, QMessageBox, QApplication, QColorDialog
from PySide6.QtGui import QColor
import qtawesome as qta

from app.constants import ACCENT
from app.i18n import t
from app.editor.canvas_overlay1 import CanvasOverlayManager
from app.viewer.canvas_action_menus import (
    PRESET_COLORS,
    make_circular_color_icon,
    make_rainbow_wheel_icon,
    _ScrollableMenuStyle,
)

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


def smooth_stroke_points(pts: list[tuple[float, float]], iterations: int = 2) -> list[tuple[float, float]]:
    """Apply Chaikin's corner-cutting curve algorithm to smooth freehand strokes into natural curves."""
    if len(pts) <= 2:
        return pts
    smoothed = pts
    for _ in range(iterations):
        new_pts = [smoothed[0]]
        for i in range(len(smoothed) - 1):
            p0 = smoothed[i]
            p1 = smoothed[i + 1]
            q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
            r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
            new_pts.append(q)
            new_pts.append(r)
        new_pts.append(smoothed[-1])
        smoothed = new_pts
    return smoothed


class CanvasEventHandler:
    def __init__(self, canvas):
        self.canvas = canvas
        self._menu_style: _ScrollableMenuStyle | None = None

    def handle_mouse_double_click(self, e):
        c = self.canvas
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)
        span = c.get_span_at(page_idx, pdf_pt, max_dist=8.0)
        if span:
            c.begin_inline_text_edit(span, page_idx)
            e.accept()
            return

    def handle_mouse_press(self, e):
        c = self.canvas
        pos = e.position().toPoint()
        c.setFocus()

        if c._placing_signature:
            if e.button() == Qt.MouseButton.RightButton:
                c.cancel_signature_placement()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
                z = c._zoom or 1.0
                px = lx / z
                py = ly / z
                pix = c._placing_sig_pixmap
                aspect = (pix.height() / pix.width()) if pix and pix.width() > 0 else 0.35
                sig_w = 160.0
                sig_h = sig_w * aspect
                r = fitz.Rect(max(0.0, px - sig_w / 2), max(0.0, py - sig_h / 2),
                              px + sig_w / 2, py + sig_h / 2)
                edit = {
                    "type": "signature",
                    "page": page_idx,
                    "rect": r,
                    "path": c._placing_sig_path,
                }
                c._overlays.append(edit)
                c._selected_overlay_idx = len(c._overlays) - 1
                c._placing_signature = False
                c._placing_sig_path = ""
                c._sig_cursor_pos = None
                c.setCursor(Qt.CursorShape.ArrowCursor)
                c.signature_added.emit(page_idx, r, edit["path"])
                c.update()
                e.accept()
                return

        if getattr(c, "_placing_stamp", False):
            if e.button() == Qt.MouseButton.RightButton:
                c.cancel_stamp_placement()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
                z = c._zoom or 1.0
                px = lx / z
                py = ly / z
                st_size = getattr(c, "_stamp_size", 20.0)
                st_type = getattr(c, "_placing_stamp_type", "check")
                r = fitz.Rect(max(0.0, px - st_size / 2.0),
                              max(0.0, py - st_size / 2.0),
                              px + st_size / 2.0,
                              py + st_size / 2.0)
                color = getattr(c, "_stamp_color", (0, 0, 0))
                edit = {
                    "type": st_type,
                    "page": page_idx,
                    "rect": r,
                    "color": color,
                    "width": max(1.5, st_size * 0.12),
                }
                c._overlays.append(edit)
                c._selected_overlay_idx = len(c._overlays) - 1
                c._placing_stamp = False
                c._placing_stamp_type = ""
                c._stamp_cursor_pos = None
                c.setCursor(Qt.CursorShape.ArrowCursor)
                c.stamp_added.emit(page_idx, r, st_type)
                c.update()
                e.accept()
                return

        if c._selected_overlay_idx >= 0 and e.button() == Qt.MouseButton.LeftButton:
            handle = c._overlay_mgr.get_overlay_handle_at(c._selected_overlay_idx, pos)
            if handle != CanvasOverlayManager.HANDLE_NONE:
                c._drag_handle = handle
                c._drag_start_pos = pos
                cur_ov = c._overlays[c._selected_overlay_idx]
                r_val = cur_ov.get("rect") if "rect" in cur_ov else fitz.Rect(cur_ov.get("bbox", (0, 0, 0, 0)))
                c._drag_start_rect = fitz.Rect(r_val)
                e.accept()
                return
            if c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
                c._moving_overlay = True
                c._drag_start_pos = pos
                cur_ov = c._overlays[c._selected_overlay_idx]
                r_val = cur_ov.get("rect") if "rect" in cur_ov else fitz.Rect(cur_ov.get("bbox", (0, 0, 0, 0)))
                c._drag_start_rect = fitz.Rect(r_val)
                e.accept()
                return
            c._selected_overlay_idx = -1
            c.update()

        if e.button() == Qt.MouseButton.LeftButton:
            for idx in reversed(range(len(c._overlays))):
                if c._overlay_mgr.is_pos_inside_overlay(idx, pos):
                    c._selected_overlay_idx = idx
                    c._moving_overlay = True
                    c._drag_start_pos = pos
                    cur_ov = c._overlays[idx]
                    r_val = cur_ov.get("rect") if "rect" in cur_ov else fitz.Rect(cur_ov.get("bbox", (0, 0, 0, 0)))
                    c._drag_start_rect = fitz.Rect(r_val)
                    if cur_ov.get("type") in ("text", "text_edit"):
                        fn = cur_ov.get("font", "Helvetica")
                        sz = float(cur_ov.get("font_size", cur_ov.get("size", 12)))
                        color_val = cur_ov.get("color", 0)
                        flags = int(cur_ov.get("flags", 0) or 0)
                        if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
                            col = tuple(float(x) for x in color_val[:3])
                        elif isinstance(color_val, int):
                            col = (((color_val >> 16) & 0xFF) / 255.0, ((color_val >> 8) & 0xFF) / 255.0, (color_val & 0xFF) / 255.0)
                        else:
                            col = (0.0, 0.0, 0.0)
                        c.text_edit_started.emit({
                            "font": fn, "size": sz, "color": col,
                            "bold": bool(flags & 16), "italic": bool(flags & 2),
                            "text": cur_ov.get("new_text", cur_ov.get("text", "")),
                        })
                    c._active_sel_rect = None
                    c.update()
                    e.accept()
                    return

            page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
            pdf_pt = c._to_pdf(page_idx, lx, ly)
            existing_media = c._overlay_mgr.detect_existing_media_at(page_idx, pdf_pt)
            if existing_media:
                c._selected_overlay_idx = c._overlays.index(existing_media)
                c._moving_overlay = True
                c._drag_start_pos = pos
                c._drag_start_rect = fitz.Rect(existing_media["rect"])
                c._active_sel_rect = None
                c.update()
                e.accept()
                return

            if getattr(c, "_select_mode", False):
                if c._active_sel_rect is not None and c._active_sel_page == page_idx:
                    h_id = self._get_active_sel_handle_at(pos)
                    if h_id != CanvasOverlayManager.HANDLE_NONE:
                        c._active_sel_handle = h_id
                        c._active_sel_drag_start = pos
                        c._active_sel_orig_rect = fitz.Rect(c._active_sel_rect)
                        e.accept()
                        return
                    if self._is_pos_inside_active_sel(pos):
                        c._active_sel_moving = True
                        c._active_sel_drag_start = pos
                        c._active_sel_orig_rect = fitz.Rect(c._active_sel_rect)
                        e.accept()
                        return

                existing_text = c._overlay_mgr.detect_existing_text_at(page_idx, pdf_pt)
                if existing_text:
                    c._selected_overlay_idx = c._overlays.index(existing_text)
                    c._moving_overlay = True
                    c._drag_start_pos = pos
                    cur_ov = existing_text
                    r_val = cur_ov.get("rect") if "rect" in cur_ov else fitz.Rect(cur_ov.get("bbox", (0, 0, 0, 0)))
                    c._drag_start_rect = fitz.Rect(r_val)
                    c._active_sel_rect = fitz.Rect(r_val)
                    c._active_sel_page = page_idx
                    fn = cur_ov.get("font", "Helvetica")
                    sz = float(cur_ov.get("font_size", cur_ov.get("size", 12)))
                    c.text_edit_started.emit({
                        "font": fn, "size": sz, "color": (0, 0, 0),
                        "bold": False, "italic": False,
                        "text": cur_ov.get("new_text", cur_ov.get("text", "")),
                    })
                    c.rect_selected.emit(page_idx, c._active_sel_rect)
                    c.update()
                    e.accept()
                    return

                c._active_sel_rect = None

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if c._draw_mode and c._page_offsets:
            page_idx, _lx, _ly = c._page_and_local(pos.x(), pos.y())
            c._stroke_page = page_idx
            c._current_stroke = [(float(pos.x()), float(pos.y()))]
            c.update()
            return

        c._drag_start = pos
        c._drag_rect = None

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

    def handle_mouse_move(self, e):
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

        if c._selected_overlay_idx >= 0:
            z = c._zoom
            orig = c._drag_start_rect
            start = c._drag_start_pos

            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                handle = c._drag_handle
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

                ov = c._overlays[c._selected_overlay_idx]
                if "rect" in ov:
                    ov["rect"] = r
                elif "bbox" in ov:
                    ov["bbox"] = [r.x0, r.y0, r.x1, r.y1]
                if "point" in ov:
                    ov["point"] = fitz.Point(r.x0, r.y0 + ov.get("size", 12) * 0.82)
                c.overlay_changed.emit()
                c.update()
                e.accept()
                return

            if c._moving_overlay and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                w = orig.width
                h = orig.height
                ov = c._overlays[c._selected_overlay_idx]
                new_r = fitz.Rect(orig.x0 + dx, orig.y0 + dy, orig.x0 + dx + w, orig.y0 + dy + h)
                if "rect" in ov:
                    ov["rect"] = new_r
                elif "bbox" in ov:
                    ov["bbox"] = [new_r.x0, new_r.y0, new_r.x1, new_r.y1]
                    if "origin" in ov:
                        ov["origin"] = [new_r.x0, new_r.y1]
                if "point" in ov:
                    ov["point"] = fitz.Point(new_r.x0, new_r.y0 + ov.get("size", 12) * 0.82)
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
            c.setCursor(Qt.CursorShape.ArrowCursor)

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

    def handle_mouse_release(self, e):
        c = self.canvas

        if getattr(c, "_select_mode", False):
            if getattr(c, "_active_sel_handle", CanvasOverlayManager.HANDLE_NONE) != CanvasOverlayManager.HANDLE_NONE or getattr(c, "_active_sel_moving", False):
                c._active_sel_handle = CanvasOverlayManager.HANDLE_NONE
                c._active_sel_moving = False
                c.rect_selected.emit(c._active_sel_page, c._active_sel_rect)
                c.update()
                e.accept()
                return

        if c._selected_overlay_idx >= 0:
            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE or c._moving_overlay:
                c._drag_handle = CanvasOverlayManager.HANDLE_NONE
                c._moving_overlay = False
                c.update()
                e.accept()
                return

        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()

        if c._draw_mode and c._current_stroke is not None:
            if len(c._current_stroke) >= 2 and c._stroke_page >= 0:
                z = c._zoom or 1.0
                yo = c._page_offsets[c._stroke_page][0]
                smoothed_screen = smooth_stroke_points(c._current_stroke, iterations=2)
                pdf_points = [(round(x / z, 2), round((y - yo) / z, 2))
                              for x, y in smoothed_screen]
                c._page_idx = c._stroke_page
                c.stroke_finished.emit(c._stroke_page, pdf_points)
            c._current_stroke = None
            c._stroke_page = -1
            c.update()
            return

        if c._drag_rect and c._drag_rect.width() > 6 and c._drag_rect.height() > 6:
            page_idx, lx, ly = c._page_and_local(c._drag_rect.left(), c._drag_rect.top())
            yo = c._page_offsets[page_idx][0] if page_idx < len(c._page_offsets) else 0
            local_rect = QRect(c._drag_rect.left(), c._drag_rect.top() - yo,
                               c._drag_rect.width(), c._drag_rect.height())
            c._page_idx = page_idx
            pdf_rect = c._rect_to_pdf(page_idx, local_rect)
            if pdf_rect is not None:
                if getattr(c, "_select_mode", False):
                    c._active_sel_rect = fitz.Rect(pdf_rect)
                    c._active_sel_page = page_idx
                c.rect_selected.emit(page_idx, pdf_rect)
        else:
            hit = c._overlay_mgr.note_icon_at(pos)
            if hit < 0:
                hit, _ = c._overlay_mgr.annot_note_at(pos)
            if hit >= 0:
                c._open_note = None if c._open_note == hit else hit
                c.update()
                c._drag_start = None; c._drag_rect = None
                return
            if c._open_note is not None:
                c._open_note = None
                c.update()
            page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
            c._page_idx = page_idx
            c.point_clicked.emit(page_idx, c._to_pdf(page_idx, lx, ly))

        c._drag_start = None
        c._drag_rect = None
        c.update()

    def handle_key_press(self, e):
        c = self.canvas
        key = e.key()
        modifiers = e.modifiers()

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

            if c._selected_overlay_idx >= 0:
                ov = c._overlays[c._selected_overlay_idx]
                if "rect" in ov:
                    r = ov["rect"]
                    ov["rect"] = fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
                    if "point" in ov:
                        ov["point"] = fitz.Point(ov["point"].x + dx, ov["point"].y + dy)
                elif "bbox" in ov:
                    b = ov["bbox"]
                    ov["bbox"] = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
                    if "origin" in ov:
                        ov["origin"] = [b[0] + dx, b[3] + dy]
                    if "point" in ov:
                        ov["point"] = fitz.Point(ov["point"].x + dx, ov["point"].y + dy)
                c._drag_start_rect = fitz.Rect(ov["rect"]) if "rect" in ov else fitz.Rect(ov["bbox"])
                c.overlay_changed.emit()
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

    def handle_context_menu(self, e):
        c = self.canvas
        pos = e.pos()

        if c._placing_signature:
            c.cancel_signature_placement()
            return
        if getattr(c, "_placing_stamp", False):
            c.cancel_stamp_placement()
            return

        menu = QMenu(c)
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        if c._selected_overlay_idx >= 0 and c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
            act_inc = menu.addAction(qta.icon("fa5s.search-plus", color=ACCENT), "Increase Size (Ctrl+)")
            act_inc.triggered.connect(lambda _=False: c.scale_selected_overlay(1.10))
            act_dec = menu.addAction(qta.icon("fa5s.search-minus", color=ACCENT), "Decrease Size (Ctrl-)")
            act_dec.triggered.connect(lambda _=False: c.scale_selected_overlay(0.90))
            menu.addSeparator()
            act_del = menu.addAction(qta.icon("fa5s.trash-alt", color="#EF4444"), t("btn.delete"))
            act_del.triggered.connect(lambda _=False: c.delete_selected_overlay())
            act_dup = menu.addAction(qta.icon("fa5s.clone", color=ACCENT), t("Duplicate", default="Duplicate"))
            act_dup.triggered.connect(lambda _=False: c.duplicate_selected_overlay())
            menu.exec(e.globalPos())
            return

        if getattr(c, "_select_mode", False) and (getattr(c, "_active_sel_rect", None) or self._is_pos_inside_active_sel(pos)):
            act_edit = menu.addAction(qta.icon("fa5s.edit", color=ACCENT), "Edit Text")
            act_edit.triggered.connect(lambda _=False: c.edit_selection_requested.emit())
            act_del = menu.addAction(qta.icon("fa5s.trash-alt", color="#EF4444"), "Delete (Del)")
            act_del.triggered.connect(lambda _=False: c.delete_active_selection())
            menu.addSeparator()

            act_copy = menu.addAction(qta.icon("fa5s.copy", color=ACCENT), t("btn.copy", default="Copy"))
            act_copy.triggered.connect(lambda _=False: QApplication.clipboard().setText(c._sel_text if hasattr(c, "_sel_text") else ""))

            act_hi = menu.addAction(qta.icon("fa5s.highlighter", color=ACCENT), "Highlight Selection")
            act_hi.triggered.connect(lambda _=False: c.highlight_selection_requested.emit())

            act_redact = menu.addAction(qta.icon("fa5s.eraser", color="#EF4444"), "Redact Selection")
            act_redact.triggered.connect(lambda _=False: c.redact_selection_requested.emit())
            menu.addSeparator()

        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)

        act_add_text = menu.addAction(qta.icon("fa5s.font", color=ACCENT), "Add Text")
        act_add_text.triggered.connect(
            lambda _=False, p=page_idx, pt=pdf_pt: (
                c.mode_change_requested.emit(1),
                c.set_text_mode(True),
                c.begin_inline_text_insert(p, pt, 12.0, (0.0, 0.0, 0.0)),
            )
        )

        span = c.get_span_at(page_idx, pdf_pt, max_dist=8.0)
        if span:
            act_edit_click = menu.addAction(qta.icon("fa5s.edit", color=ACCENT), "Edit Text")
            act_edit_click.triggered.connect(
                lambda _=False, s=span, p=page_idx: (
                    c.mode_change_requested.emit(1),
                    c.set_text_mode(True),
                    c.begin_inline_text_edit(s, p),
                )
            )

        act_draw_pen = menu.addAction(qta.icon("fa5s.pen", color=ACCENT), "Draw (Freehand Pen)")
        act_draw_pen.triggered.connect(
            lambda _=False: (
                c.mode_change_requested.emit(8),
                c.set_draw_mode(True),
            )
        )

        act_draw_hi_rect = menu.addAction(qta.icon("fa5s.vector-square", color=ACCENT), "Draw Highlight (Rectangle)")
        act_draw_hi_rect.triggered.connect(
            lambda _=False: (
                c.mode_change_requested.emit(4),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.CrossCursor),
            )
        )

        act_hi_text = menu.addAction(qta.icon("fa5s.highlighter", color=ACCENT), "Highlight Text")
        act_hi_text.triggered.connect(
            lambda _=False: (
                c.mode_change_requested.emit(3),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.IBeamCursor),
            )
        )
        menu.addSeparator()

        color_menu = menu.addMenu(make_rainbow_wheel_icon(18), "Highlight & Pen Color")
        act_custom = color_menu.addAction(make_rainbow_wheel_icon(16), "Custom Color...")

        def _on_custom_col():
            curr = QColor(
                int(c._highlight_color[0] * 255),
                int(c._highlight_color[1] * 255),
                int(c._highlight_color[2] * 255),
            )
            col = QColorDialog.getColor(curr, c, "Select Color")
            if col.isValid():
                c_tup = (col.redF(), col.greenF(), col.blueF())
                c._highlight_color = c_tup
                c._draw_color = c_tup
                if c._highlight_mode:
                    c.set_highlight_mode(True, c_tup)
                elif c._draw_mode:
                    c.set_draw_mode(True, c_tup)

        act_custom.triggered.connect(lambda _=False: _on_custom_col())
        color_menu.addSeparator()

        for hex_code, label, rgb_tuple in PRESET_COLORS:
            act_c = color_menu.addAction(make_circular_color_icon(hex_code, 16), label)
            act_c.triggered.connect(
                lambda _=False, rgb=rgb_tuple: (
                    setattr(c, "_highlight_color", rgb),
                    setattr(c, "_draw_color", rgb),
                    c.set_highlight_mode(True, rgb) if c._highlight_mode else (
                        c.set_draw_mode(True, rgb) if c._draw_mode else None
                    ),
                )
            )

        menu.addSeparator()
        act_add_check = menu.addAction(qta.icon("fa5s.check", color=ACCENT), "Add Checkmark (✔)")
        act_add_check.triggered.connect(lambda _=False: c.begin_stamp_placement("check"))
        act_add_cross = menu.addAction(qta.icon("fa5s.times", color="#EF4444"), "Add Crossmark (✖)")
        act_add_cross.triggered.connect(lambda _=False: c.begin_stamp_placement("cross"))
        menu.addSeparator()
        act_add_sig = menu.addAction(qta.icon("fa5s.signature", color=ACCENT), t("viewer.add_signature"))
        act_add_sig.triggered.connect(lambda _=False: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())
