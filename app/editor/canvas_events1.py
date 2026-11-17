# app/editor/canvas_events1.py

"""Canvas interaction handler: mouse, drag handles, keyboard, stroke smoothing, vector stamps, Foxit-style object move/delete, and SVG context menus."""

from __future__ import annotations

import fitz
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtWidgets import QMenu, QApplication, QColorDialog
from PySide6.QtGui import QColor

from app.constants import ACCENT
from app.i18n import t
from app.utils import (
    SVG_CHECK,
    SVG_CLONE,
    SVG_CLOSE,
    SVG_COPY,
    SVG_EDIT,
    SVG_ERASER,
    SVG_FONT,
    SVG_HIGHLIGHTER,
    SVG_PEN,
    SVG_SIGNATURE,
    SVG_TRASH,
    SVG_VECTOR_SQUARE,
    SVG_ZOOM_IN,
    SVG_ZOOM_OUT,
    svg_icon,
)
from app.editor.canvas_overlay1 import (
    CanvasOverlayManager,
    overlay_pdf_rect,
    snapshot_overlay,
)
from app.viewer.canvas_action_menus import (
    PRESET_COLORS,
    make_circular_color_icon,
    make_rainbow_wheel_icon,
    _ScrollableMenuStyle,
)

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


def _find_tab_editar(canvas):
    p = canvas.parentWidget() if hasattr(canvas, "parentWidget") else None
    while p is not None:
        if hasattr(p, "_set_mode_by_idx") and hasattr(p, "_set_text_submode"):
            return p
        p = p.parentWidget()
    return None


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


def _overlay_geometry_changed(ov: dict, before_snap: dict | None) -> bool:
    if not before_snap:
        return False
    for key in ("rect", "bbox", "rects", "points", "point"):
        if ov.get(key) != before_snap.get(key):
            return True
    return False


def _translate_overlay(ov: dict, before_snap: dict, dx: float, dy: float) -> None:
    if "rect" in before_snap:
        orig_r = fitz.Rect(before_snap["rect"])
        ov["rect"] = fitz.Rect(orig_r.x0 + dx, orig_r.y0 + dy, orig_r.x1 + dx, orig_r.y1 + dy)
    if "bbox" in before_snap:
        orig_b = before_snap["bbox"]
        ov["bbox"] = [orig_b[0] + dx, orig_b[1] + dy, orig_b[2] + dx, orig_b[3] + dy]
    if "origin" in before_snap:
        orig_o = before_snap["origin"]
        ov["origin"] = [orig_o[0] + dx, orig_o[1] + dy]
    if "rects" in before_snap:
        ov["rects"] = [
            fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
            for r in before_snap["rects"]
        ]
    if "points" in before_snap:
        ov["points"] = [
            (float(px) + dx, float(py) + dy)
            for px, py in before_snap["points"]
        ]
    if "point" in before_snap:
        orig_pt = before_snap["point"]
        ov["point"] = fitz.Point(orig_pt.x + dx, orig_pt.y + dy)


def _resize_overlay_from_rect(ov: dict, before_snap: dict, orig_r: fitz.Rect, new_r: fitz.Rect) -> None:
    if "rect" in ov or "rect" in before_snap:
        ov["rect"] = fitz.Rect(new_r)
    if "bbox" in ov or "bbox" in before_snap:
        ov["bbox"] = [new_r.x0, new_r.y0, new_r.x1, new_r.y1]

    orig_h = max(4.0, orig_r.height)
    scale_h = new_r.height / orig_h
    scale_w = new_r.width / max(4.0, orig_r.width)

    if ov.get("type") in ("text", "text_edit"):
        base_sz = float(before_snap.get("font_size", before_snap.get("size", 12.0)))
        new_sz = max(4.0, base_sz * scale_h)
        if "font_size" in ov or "font_size" in before_snap:
            ov["font_size"] = new_sz
        if "size" in ov or "size" in before_snap:
            ov["size"] = new_sz
        if "origin" in before_snap:
            ov["origin"] = [new_r.x0, new_r.y0 + new_sz * 0.85]
        if "point" in before_snap:
            ov["point"] = fitz.Point(new_r.x0, new_r.y0 + new_sz * 0.85)
    elif ov.get("type") == "draw" and "points" in before_snap:
        sx = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
        sy = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
        ov["points"] = [
            (
                new_r.x0 + (px - orig_r.x0) * sx,
                new_r.y0 + (py - orig_r.y0) * sy,
            )
            for px, py in before_snap["points"]
        ]
    elif ov.get("type") in ("check", "cross"):
        base_w = float(before_snap.get("width", 2.2))
        ov["width"] = max(1.5, base_w * ((scale_w + scale_h) * 0.5))


class CanvasEventHandler:
    def __init__(self, canvas):
        self.canvas = canvas
        self._menu_style: _ScrollableMenuStyle | None = None

    def _emit_text_started_for_overlay(self, ov: dict) -> None:
        c = self.canvas
        if ov.get("type") not in ("text", "text_edit"):
            return
        fn = ov.get("font", "Helvetica")
        sz = float(ov.get("font_size", ov.get("size", 12)))
        color_val = ov.get("color", 0)
        flags = int(ov.get("flags", 0) or 0)
        if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
            col = tuple(float(x) for x in color_val[:3])
        elif isinstance(color_val, int):
            col = (
                ((color_val >> 16) & 0xFF) / 255.0,
                ((color_val >> 8) & 0xFF) / 255.0,
                (color_val & 0xFF) / 255.0,
            )
        else:
            col = (0.0, 0.0, 0.0)
        c.text_edit_started.emit({
            "font": fn, "size": sz, "color": col,
            "bold": bool(flags & 16), "italic": bool(flags & 2),
            "text": ov.get("new_text", ov.get("text", "")),
        })

    def _begin_inline_edit_for_overlay(self, ov: dict) -> None:
        c = self.canvas
        r = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
        bb = [r.x0, r.y0, r.x1, r.y1] if r else [0, 0, 100, 20]
        span = {
            "text": ov.get("new_text", ov.get("text", "")),
            "bbox": list(ov.get("bbox", bb)),
            "size": float(ov.get("font_size", ov.get("size", 12))),
            "font_size": float(ov.get("font_size", ov.get("size", 12))),
            "font": ov.get("font", "Helvetica"),
            "color": ov.get("color", 0),
            "flags": int(ov.get("flags", 0) or 0),
            "origin": list(ov.get("origin", [bb[0], ov["point"].y if "point" in ov else bb[3]])),
        }
        c._inline_mgr.begin_edit(span, ov.get("page", 0), target_overlay=ov)

    def _commit_overlay_mutation(self, ov: dict, before_snap: dict | None) -> None:
        c = self.canvas
        if ov.get("_existing") and not ov.get("_dirty"):
            ov["_dirty"] = True
            if hasattr(c, "overlay_committed"):
                c.overlay_committed.emit(ov)
        elif before_snap is not None and hasattr(c, "overlay_transformed"):
            c.overlay_transformed.emit(ov, before_snap)
        c.overlay_changed.emit()

    def handle_mouse_double_click(self, e):
        c = self.canvas
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()

        for idx in reversed(range(len(c._overlays))):
            ov = c._overlays[idx]
            if not ov.get("_deleted") and ov.get("type") in ("text", "text_edit"):
                if c._overlay_mgr.is_pos_inside_overlay(idx, pos):
                    self._begin_inline_edit_for_overlay(ov)
                    e.accept()
                    return

        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)
        span = c.get_span_at(page_idx, pdf_pt, max_dist=8.0)
        if span:
            c._overlay_mgr.discard_untouched_existing()
            c._selected_overlay_idx = -1
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
                sig_path = c._placing_sig_path
                c._placing_signature = False
                c._placing_sig_path = ""
                c._sig_cursor_pos = None
                c.setCursor(Qt.CursorShape.ArrowCursor)
                c.signature_added.emit(page_idx, r, sig_path)
                if c._overlays:
                    c._selected_overlay_idx = len(c._overlays) - 1
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
                c._placing_stamp = False
                c._placing_stamp_type = ""
                c._stamp_cursor_pos = None
                c.setCursor(Qt.CursorShape.ArrowCursor)
                c.stamp_added.emit(page_idx, r, st_type)
                if c._overlays:
                    c._selected_overlay_idx = len(c._overlays) - 1
                c.update()
                e.accept()
                return

        is_paint_tool = bool(
            c._draw_mode or getattr(c, "_highlight_mode", False) or getattr(c, "_redact_mode", False)
        )

        # 1. Active selected overlay handle resize / body move
        if not is_paint_tool and c._selected_overlay_idx >= 0 and e.button() == Qt.MouseButton.LeftButton:
            handle = c._overlay_mgr.get_overlay_handle_at(c._selected_overlay_idx, pos)
            if handle != CanvasOverlayManager.HANDLE_NONE:
                cur_ov = c._overlays[c._selected_overlay_idx]
                c._drag_handle = handle
                c._drag_start_pos = pos
                c._drag_before_snapshot = snapshot_overlay(cur_ov)
                c._drag_start_rect = overlay_pdf_rect(cur_ov, zoom=c._zoom or 1.0)
                e.accept()
                return
            if c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
                cur_ov = c._overlays[c._selected_overlay_idx]
                c._moving_overlay = True
                c._drag_start_pos = pos
                c._drag_before_snapshot = snapshot_overlay(cur_ov)
                c._drag_start_rect = overlay_pdf_rect(cur_ov, zoom=c._zoom or 1.0)
                e.accept()
                return
            c._selected_overlay_idx = -1
            c._overlay_mgr.discard_untouched_existing()
            c.update()

        if not is_paint_tool and e.button() == Qt.MouseButton.LeftButton:
            # 2. Any overlay already in _overlays (top-most first)
            for idx in reversed(range(len(c._overlays))):
                ov = c._overlays[idx]
                if getattr(c, "_note_mode", False) and ov.get("type") != "note":
                    continue
                if c._overlay_mgr.is_pos_inside_overlay(idx, pos):
                    c._selected_overlay_idx = idx
                    c._moving_overlay = True
                    c._drag_start_pos = pos
                    c._drag_before_snapshot = snapshot_overlay(ov)
                    c._drag_start_rect = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                    self._emit_text_started_for_overlay(ov)
                    c._active_sel_rect = None
                    c.update()
                    e.accept()
                    return

            # 3. Existing document elements (images, annotations, vector drawings)
            if not getattr(c, "_note_mode", False):
                c._overlay_mgr.discard_untouched_existing()
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
                pdf_pt = c._to_pdf(page_idx, lx, ly)

                existing_media = c._overlay_mgr.detect_existing_media_at(page_idx, pdf_pt)
                if existing_media:
                    c._selected_overlay_idx = c._overlays.index(existing_media)
                    c._moving_overlay = True
                    c._drag_start_pos = pos
                    c._drag_before_snapshot = snapshot_overlay(existing_media)
                    c._drag_start_rect = fitz.Rect(existing_media["rect"])
                    c._active_sel_rect = None
                    c.update()
                    e.accept()
                    return

                existing_drawing = c._overlay_mgr.detect_existing_drawing_at(page_idx, pdf_pt)
                if existing_drawing:
                    c._selected_overlay_idx = c._overlays.index(existing_drawing)
                    c._moving_overlay = True
                    c._drag_start_pos = pos
                    c._drag_before_snapshot = snapshot_overlay(existing_drawing)
                    c._drag_start_rect = fitz.Rect(existing_drawing["rect"])
                    c._active_sel_rect = None
                    c.update()
                    e.accept()
                    return

            # 4. Active Select Mode marquee handles / interior
            if getattr(c, "_select_mode", False):
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
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

        if c._selected_overlay_idx >= 0 and 0 <= c._selected_overlay_idx < len(c._overlays):
            z = c._zoom
            orig = c._drag_start_rect
            start = c._drag_start_pos
            ov = c._overlays[c._selected_overlay_idx]
            snap = c._drag_before_snapshot or {}

            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
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

        if c._selected_overlay_idx >= 0 and 0 <= c._selected_overlay_idx < len(c._overlays):
            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE or c._moving_overlay:
                ov = c._overlays[c._selected_overlay_idx]
                before_snap = c._drag_before_snapshot
                moved = _overlay_geometry_changed(ov, before_snap)
                c._drag_handle = CanvasOverlayManager.HANDLE_NONE
                c._moving_overlay = False
                c._drag_before_snapshot = None
                if moved:
                    self._commit_overlay_mutation(ov, before_snap)
                elif ov.get("type") == "note":
                    idx = c._selected_overlay_idx
                    c._open_note = None if c._open_note == idx else idx
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
                c._selected_overlay_idx = hit
                c.update()
                c._drag_start = None; c._drag_rect = None
                return
            if c._open_note is not None:
                c._open_note = None
                c.update()
            page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
            pdf_pt = c._to_pdf(page_idx, lx, ly)
            c._page_idx = page_idx

            if getattr(c, "_select_mode", False):
                existing_text = c._overlay_mgr.detect_existing_text_at(page_idx, pdf_pt)
                if existing_text:
                    c._selected_overlay_idx = c._overlays.index(existing_text)
                    c._active_sel_rect = None
                    self._emit_text_started_for_overlay(existing_text)
                    c.point_clicked.emit(page_idx, pdf_pt)
                    c.update()
                    c._drag_start = None; c._drag_rect = None
                    return

            c.point_clicked.emit(page_idx, pdf_pt)

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
                _translate_overlay(ov, before_snap, dx, dy)
                c._drag_start_rect = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                self._commit_overlay_mutation(ov, before_snap)
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
        tab = _find_tab_editar(c)

        if c._placing_signature:
            c.cancel_signature_placement()
            return
        if getattr(c, "_placing_stamp", False):
            c.cancel_stamp_placement()
            return

        if not (0 <= c._selected_overlay_idx < len(c._overlays) and c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos)):
            hit_idx = -1
            for idx in reversed(range(len(c._overlays))):
                if c._overlay_mgr.is_pos_inside_overlay(idx, pos):
                    hit_idx = idx
                    break
            if hit_idx >= 0:
                c._selected_overlay_idx = hit_idx
                c.update()
            else:
                c._overlay_mgr.discard_untouched_existing()
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
                pdf_pt = c._to_pdf(page_idx, lx, ly)
                el = (
                    c._overlay_mgr.detect_existing_media_at(page_idx, pdf_pt)
                    or c._overlay_mgr.detect_existing_drawing_at(page_idx, pdf_pt)
                    or c._overlay_mgr.detect_existing_text_at(page_idx, pdf_pt)
                )
                if el and el in c._overlays:
                    c._selected_overlay_idx = c._overlays.index(el)
                    c.update()

        menu = QMenu(c)
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        if 0 <= c._selected_overlay_idx < len(c._overlays) and c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
            ov = c._overlays[c._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                act_edit_ov = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
                act_edit_ov.triggered.connect(lambda _=False, o=ov: self._begin_inline_edit_for_overlay(o))
            if ov.get("type") in CanvasOverlayManager._RESIZABLE_TYPES:
                act_inc = menu.addAction(svg_icon(SVG_ZOOM_IN, color=ACCENT, size=15), "Increase Size (Ctrl+)")
                act_inc.triggered.connect(lambda _=False: c.scale_selected_overlay(1.10))
                act_dec = menu.addAction(svg_icon(SVG_ZOOM_OUT, color=ACCENT, size=15), "Decrease Size (Ctrl-)")
                act_dec.triggered.connect(lambda _=False: c.scale_selected_overlay(0.90))
            menu.addSeparator()
            act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), t("btn.delete") + " (Del)")
            act_del.triggered.connect(lambda _=False: c.delete_selected_overlay())
            if ov.get("type") != "note":
                act_dup = menu.addAction(svg_icon(SVG_CLONE, color=ACCENT, size=15), "Duplicate")
                act_dup.triggered.connect(lambda _=False: c.duplicate_selected_overlay())
            menu.exec(e.globalPos())
            return

        if getattr(c, "_select_mode", False) and (getattr(c, "_active_sel_rect", None) or self._is_pos_inside_active_sel(pos)):
            act_edit = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
            act_edit.triggered.connect(lambda _=False: c.edit_selection_requested.emit())
            act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Delete (Del)")
            act_del.triggered.connect(lambda _=False: c.delete_active_selection())
            menu.addSeparator()

            act_copy = menu.addAction(svg_icon(SVG_COPY, color=ACCENT, size=15), t("btn.copy"))
            act_copy.triggered.connect(lambda _=False: QApplication.clipboard().setText(c._sel_text if hasattr(c, "_sel_text") else ""))

            act_hi = menu.addAction(svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15), "Highlight Selection")
            act_hi.triggered.connect(lambda _=False: c.highlight_selection_requested.emit())

            act_redact = menu.addAction(svg_icon(SVG_ERASER, color="#EF4444", size=15), "Redact Selection")
            act_redact.triggered.connect(lambda _=False: c.redact_selection_requested.emit())
            menu.addSeparator()

        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)
        span = c.get_span_at(page_idx, pdf_pt, max_dist=8.0)

        def _activate_mode(m_idx: int, submode: str | None = None):
            if tab is not None:
                tab._set_mode_by_idx(m_idx)
                if submode and hasattr(tab, "_set_text_submode"):
                    tab._set_text_submode(submode)
            else:
                c.mode_change_requested.emit(m_idx)

        act_add_text = menu.addAction(svg_icon(SVG_FONT, color=ACCENT, size=15), "Add Text")
        act_add_text.triggered.connect(
            lambda _=False, p=page_idx, pt=pdf_pt: (
                _activate_mode(1, "add"),
                c.begin_inline_text_insert(p, pt, 12.0, (0.0, 0.0, 0.0)),
            )
        )

        act_edit_text = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
        if span:
            act_edit_text.triggered.connect(
                lambda _=False, s=span, p=page_idx: (
                    _activate_mode(1, "edit"),
                    c.begin_inline_text_edit(s, p),
                )
            )
        else:
            act_edit_text.triggered.connect(lambda _=False: _activate_mode(1, "edit"))

        menu.addSeparator()

        act_draw_pen = menu.addAction(svg_icon(SVG_PEN, color=ACCENT, size=15), "Draw (Freehand Pen)")
        act_draw_pen.triggered.connect(
            lambda _=False: (
                _activate_mode(8),
                c.set_draw_mode(True, color=c._draw_color),
            )
        )

        act_draw_hi_rect = menu.addAction(svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=15), "Draw Highlight (Rectangle)")
        act_draw_hi_rect.triggered.connect(
            lambda _=False: (
                _activate_mode(4),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.CrossCursor),
            )
        )

        act_hi_text = menu.addAction(svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15), "Highlight Text")
        act_hi_text.triggered.connect(
            lambda _=False: (
                _activate_mode(3),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.IBeamCursor),
            )
        )
        menu.addSeparator()

        color_menu = menu.addMenu(make_rainbow_wheel_icon(18), "Highlight & Pen Color")
        act_custom = color_menu.addAction(make_rainbow_wheel_icon(16), "Custom Color...")

        def _apply_chosen_color(c_tup: tuple[float, float, float]):
            c._highlight_color = c_tup
            c._draw_color = c_tup
            if tab is not None and hasattr(tab, "_sync_canvas_color"):
                tab._sync_canvas_color(c_tup)
            if c._highlight_mode:
                c.set_highlight_mode(True, c_tup, c.cursor().shape())
            elif c._draw_mode:
                c.set_draw_mode(True, c_tup)

        def _on_custom_col():
            curr = QColor(
                int(c._draw_color[0] * 255),
                int(c._draw_color[1] * 255),
                int(c._draw_color[2] * 255),
            )
            col = QColorDialog.getColor(curr, c, "Select Color")
            if col.isValid():
                _apply_chosen_color((col.redF(), col.greenF(), col.blueF()))

        act_custom.triggered.connect(lambda _=False: _on_custom_col())
        color_menu.addSeparator()

        for hex_code, label, rgb_tuple in PRESET_COLORS:
            act_c = color_menu.addAction(make_circular_color_icon(hex_code, 16), label)
            act_c.triggered.connect(lambda _=False, rgb=rgb_tuple: _apply_chosen_color(rgb))

        menu.addSeparator()
        act_add_check = menu.addAction(svg_icon(SVG_CHECK, color=ACCENT, size=15), "Add Checkmark")
        act_add_check.triggered.connect(lambda _=False: c.begin_stamp_placement("check"))
        act_add_cross = menu.addAction(svg_icon(SVG_CLOSE, color="#EF4444", size=15), "Add Crossmark")
        act_add_cross.triggered.connect(lambda _=False: c.begin_stamp_placement("cross"))
        menu.addSeparator()
        act_add_sig = menu.addAction(svg_icon(SVG_SIGNATURE, color=ACCENT, size=15), t("viewer.add_signature"))
        act_add_sig.triggered.connect(lambda _=False: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())