

# app/editor/canvas_events_press_release.py
"""Mouse double-click, press, and release interaction handlers for PdfEditCanvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QRect, Qt

from app.editor.canvas_events_helpers import (
    _overlay_geometry_changed,
    smooth_stroke_points,
)
from app.editor.canvas_overlay1 import (
    CanvasOverlayManager,
    overlay_pdf_rect,
    snapshot_overlay,
)


class CanvasPressReleaseMixin:
    """Mixin handling mouse double-click, mouse press, and mouse release interactions."""

    if TYPE_CHECKING:
        canvas: Any
        _started_untouched_drag: bool

        def _get_active_sel_handle_at(self, pos: QPoint) -> int: ...
        def _is_pos_inside_active_sel(self, pos: QPoint) -> bool: ...

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
            "runs": [dict(run) for run in ov["runs"]] if ov.get("runs") else None,
        }
        c._inline_mgr.begin_edit(span, ov.get("page", 0), target_overlay=ov)

    def _commit_overlay_mutation(self, ov: dict, before_snap: dict | None, force_committed: bool = False) -> None:
        c = self.canvas
        page_idx = ov.get("page", 0)
        if force_committed or (ov.get("_existing") and not ov.get("_dirty")):
            ov["_dirty"] = True
            if hasattr(c, "overlay_committed"):
                c.overlay_committed.emit(ov)
        elif before_snap is not None and hasattr(c, "overlay_transformed"):
            c.overlay_transformed.emit(ov, before_snap)
        c.overlay_changed.emit()

    def handle_mouse_double_click(self, e) -> None:
        c = self.canvas
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()

        is_add_text = bool(
            getattr(c, "_text_mode", False) and getattr(c, "_text_submode", "edit") == "add"
        )
        if is_add_text:
            return

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

    def handle_mouse_press(self, e) -> None:
        c = self.canvas
        pos = e.position().toPoint()
        c.setFocus()
        self._started_untouched_drag = False

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
        is_add_text = bool(
            getattr(c, "_text_mode", False) and getattr(c, "_text_submode", "edit") == "add"
        )

        if is_add_text:
            c._selected_overlay_idx = -1
            c._active_sel_rect = None
            c._overlay_mgr.discard_untouched_existing()

        # 1. Active selected overlay handle resize / body move (only when NOT in add text mode)
        if not is_paint_tool and not is_add_text and c._selected_overlay_idx >= 0 and e.button() == Qt.MouseButton.LeftButton:
            handle = c._overlay_mgr.get_overlay_handle_at(c._selected_overlay_idx, pos)
            if handle != CanvasOverlayManager.HANDLE_NONE:
                cur_ov = c._overlays[c._selected_overlay_idx]
                c._drag_handle = handle
                c._drag_start_pos = pos
                c._drag_before_snapshot = snapshot_overlay(cur_ov)
                c._drag_start_rect = overlay_pdf_rect(cur_ov, zoom=c._zoom or 1.0)
                self._started_untouched_drag = bool(cur_ov.get("_existing") and not cur_ov.get("_dirty"))
                e.accept()
                return
            if c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
                cur_ov = c._overlays[c._selected_overlay_idx]
                c._moving_overlay = True
                c._drag_start_pos = pos
                c._drag_before_snapshot = snapshot_overlay(cur_ov)
                c._drag_start_rect = overlay_pdf_rect(cur_ov, zoom=c._zoom or 1.0)
                self._started_untouched_drag = bool(cur_ov.get("_existing") and not cur_ov.get("_dirty"))
                e.accept()
                return
            c._selected_overlay_idx = -1
            c._overlay_mgr.discard_untouched_existing()
            c.update()

        if not is_paint_tool and not is_add_text and e.button() == Qt.MouseButton.LeftButton:
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
                    self._started_untouched_drag = bool(ov.get("_existing") and not ov.get("_dirty"))
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
                    self._started_untouched_drag = bool(existing_media.get("_existing") and not existing_media.get("_dirty"))
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
                    self._started_untouched_drag = bool(existing_drawing.get("_existing") and not existing_drawing.get("_dirty"))
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

    def handle_mouse_release(self, e) -> None:
        c = self.canvas
        is_add_text = bool(
            getattr(c, "_text_mode", False) and getattr(c, "_text_submode", "edit") == "add"
        )

        if not is_add_text and getattr(c, "_select_mode", False):
            if getattr(c, "_active_sel_handle", CanvasOverlayManager.HANDLE_NONE) != CanvasOverlayManager.HANDLE_NONE or getattr(c, "_active_sel_moving", False):
                c._active_sel_handle = CanvasOverlayManager.HANDLE_NONE
                c._active_sel_moving = False
                c.rect_selected.emit(c._active_sel_page, c._active_sel_rect)
                c.update()
                e.accept()
                return

        if not is_add_text and c._selected_overlay_idx >= 0 and 0 <= c._selected_overlay_idx < len(c._overlays):
            if c._drag_handle != CanvasOverlayManager.HANDLE_NONE or c._moving_overlay:
                ov = c._overlays[c._selected_overlay_idx]
                before_snap = c._drag_before_snapshot
                moved = _overlay_geometry_changed(ov, before_snap)
                started_untouched = self._started_untouched_drag
                self._started_untouched_drag = False
                c._drag_handle = CanvasOverlayManager.HANDLE_NONE
                c._moving_overlay = False
                c._drag_before_snapshot = None
                if moved:
                    self._commit_overlay_mutation(ov, before_snap, force_committed=started_untouched)
                    if ov.get("_z_back"):
                        c.refresh_pages({ov.get("page", 0)})
                else:
                    if started_untouched and ov.get("_dirty"):
                        ov["_dirty"] = False
                        c.refresh_pages({ov.get("page", 0)})
                    if ov.get("type") == "note":
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
                if not is_add_text and getattr(c, "_select_mode", False):
                    c._active_sel_rect = fitz.Rect(pdf_rect)
                    c._active_sel_page = page_idx
                c.rect_selected.emit(page_idx, pdf_rect)
        else:
            if not is_add_text:
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

            if not is_add_text and getattr(c, "_select_mode", False):
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

