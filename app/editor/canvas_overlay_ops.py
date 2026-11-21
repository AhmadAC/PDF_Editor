# app/editor/canvas_overlay_ops.py
"""Overlay manipulation operations, geometric transforms, and query methods for PdfEditCanvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QWidget

try:
    from app.editor.canvas_overlay1 import overlay_pdf_rect, snapshot_overlay
    from app.editor.canvas_events_helpers import _resize_overlay_from_rect
except ImportError:
    from .canvas_overlay1 import overlay_pdf_rect, snapshot_overlay  # type: ignore
    from .canvas_events_helpers import _resize_overlay_from_rect  # type: ignore

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object


class CanvasOverlayOpsMixin(_Base):
    """Mixin providing overlay transformations, groupings, and spatial queries."""

    if TYPE_CHECKING:
        _overlays: list
        _open_note: Any
        _selected_overlay_idx: int
        _page_idx: int
        _zoom: float
        _drag_start_rect: fitz.Rect | None
        _active_sel_rect: fitz.Rect | None
        _overlay_mgr: Any

        overlay_committed: Any
        overlay_transformed: Any
        overlay_changed: Any
        delete_selection_requested: Any

        def update(self) -> None: ...
        def parentWidget(self) -> Any: ...
        def set_page(self, idx: int) -> None: ...
        def refresh_pages(self, page_indices: set[int] | list[int] | None = None) -> None: ...
        def cancel_stamp_placement(self) -> None: ...

    def set_overlays(self, overlays: list):
        self._overlays = overlays
        self._open_note = None
        if self._selected_overlay_idx >= len(self._overlays):
            self._selected_overlay_idx = -1
        self.update()

    def select_overlay_by_index(self, idx: int) -> None:
        """Select an overlay by index (used by SelectionPane) and jump to its page if needed."""
        if 0 <= idx < len(self._overlays):
            ov = self._overlays[idx]
            self._selected_overlay_idx = idx
            target_p = ov.get("page", 0)
            if target_p != self._page_idx:
                self.set_page(target_p)
                parent_tab = self.parentWidget()
                while parent_tab and not hasattr(parent_tab, "_scroll_to"):
                    parent_tab = parent_tab.parentWidget()
                if parent_tab and hasattr(parent_tab, "_scroll_to"):
                    parent_tab._scroll_to(target_p)
            self.update()
        else:
            self._selected_overlay_idx = -1
            self.update()

    def delete_active_selection(self):
        if self._selected_overlay_idx >= 0:
            return self.delete_selected_overlay()
        if self._active_sel_rect is not None:
            self.delete_selection_requested.emit()

    def ungroup_selected_overlay(self, idx: int | None = None) -> list[dict] | None:
        target = self._selected_overlay_idx if idx is None else idx
        return self._overlay_mgr.ungroup_overlay(target)

    def group_overlays(self, indices: list[int]) -> dict | None:
        return self._overlay_mgr.group_overlays(indices)

    def arrange_selected_overlay(self, mode: str, idx: int | None = None) -> None:
        target = self._selected_overlay_idx if idx is None else idx
        if 0 <= target < len(self._overlays) and self._overlays[target].get("_locked"):
            return
        self._overlay_mgr.arrange_overlay(target, mode)

    def rotate_selected_overlay(self, delta_deg: int, idx: int | None = None) -> None:
        target = self._selected_overlay_idx if idx is None else idx
        if 0 <= target < len(self._overlays) and self._overlays[target].get("_locked"):
            return
        self._overlay_mgr.rotate_overlay(target, delta_deg)

    def flip_selected_overlay(self, horizontal: bool, idx: int | None = None) -> None:
        target = self._selected_overlay_idx if idx is None else idx
        if 0 <= target < len(self._overlays) and self._overlays[target].get("_locked"):
            return
        self._overlay_mgr.flip_overlay(target, horizontal)

    def set_selected_overlay_opacity(self, opacity: float, idx: int | None = None) -> None:
        target = self._selected_overlay_idx if idx is None else idx
        self._overlay_mgr.set_overlay_opacity(target, opacity)

    def scale_selected_overlay(self, factor: float) -> bool:
        if not (0 <= self._selected_overlay_idx < len(self._overlays)):
            return False
        ov = self._overlays[self._selected_overlay_idx]
        if ov.get("_deleted") or ov.get("_locked"):
            return False

        r = overlay_pdf_rect(ov, zoom=self._zoom or 1.0)
        if r is None:
            return False

        before_snap = snapshot_overlay(ov)
        was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))

        min_s = 8.0
        old_w = max(min_s, r.width)
        old_h = max(min_s, r.height)
        new_w = max(min_s, old_w * factor)
        new_h = max(min_s, old_h * factor)

        cx = (r.x0 + r.x1) / 2.0
        cy = (r.y0 + r.y1) / 2.0
        new_r = fitz.Rect(cx - new_w / 2.0, cy - new_h / 2.0, cx + new_w / 2.0, cy + new_h / 2.0)

        if ov.get("type") == "group":
            _resize_overlay_from_rect(ov, before_snap, r, new_r)
        elif ov.get("type") == "draw" and "points" in ov:
            ov["points"] = [
                (cx + (float(px) - cx) * factor, cy + (float(py) - cy) * factor)
                for px, py in ov["points"]
            ]
        elif ov.get("type") == "highlight" and ov.get("rects"):
            ov["rects"] = [
                fitz.Rect(
                    cx + (hr.x0 - cx) * factor,
                    cy + (hr.y0 - cy) * factor,
                    cx + (hr.x1 - cx) * factor,
                    cy + (hr.y1 - cy) * factor,
                )
                for hr in ov["rects"]
            ]
        else:
            if "rect" in ov:
                ov["rect"] = new_r
            if "bbox" in ov:
                ov["bbox"] = [new_r.x0, new_r.y0, new_r.x1, new_r.y1]
            if "origin" in ov:
                new_sz = max(4.0, float(ov.get("font_size", ov.get("size", 12))) * factor)
                ov["origin"] = [new_r.x0, new_r.y0 + new_sz * 0.85]
            if "point" in ov:
                new_sz = max(4.0, float(ov.get("size", 12)) * factor)
                ov["point"] = fitz.Point(new_r.x0, new_r.y0 + new_sz * 0.85)
            if "font_size" in ov:
                ov["font_size"] = max(4.0, float(ov["font_size"]) * factor)
            if "size" in ov and ov.get("type") in ("text", "text_edit"):
                ov["size"] = max(4.0, float(ov["size"]) * factor)
            if ov.get("type") in ("check", "cross"):
                ov["width"] = max(1.5, float(ov.get("width", 2.2)) * factor)
            if ov.get("type") == "drawing":
                ov["width"] = max(0.5, float(ov.get("width", 1.0)) * factor)

        self._drag_start_rect = overlay_pdf_rect(ov, zoom=self._zoom or 1.0)
        if was_untouched:
            ov["_dirty"] = True
            if ov.get("type") == "group":
                for ch in ov.get("children", []):
                    if ch.get("_existing"):
                        ch["_dirty"] = True
            self.overlay_committed.emit(ov)
        else:
            self.overlay_transformed.emit(ov, before_snap)
        if was_untouched or ov.get("_z_back"):
            self.refresh_pages({ov.get("page", 0)})
        self.overlay_changed.emit()
        self.update()
        return True

    def start_add_signature_flow(self, pos: QPoint | None = None):
        self._overlay_mgr.start_add_signature_flow(pos)

    def begin_signature_placement(self, sig_path: str):
        self.cancel_stamp_placement()
        self._overlay_mgr.begin_signature_placement(sig_path)

    def cancel_signature_placement(self):
        self._overlay_mgr.cancel_signature_placement()

    def delete_selected_overlay(self) -> dict | None:
        if 0 <= self._selected_overlay_idx < len(self._overlays):
            if self._overlays[self._selected_overlay_idx].get("_locked"):
                return None
        return self._overlay_mgr.delete_selected_overlay()

    def duplicate_selected_overlay(self) -> None:
        self._overlay_mgr.duplicate_selected_overlay()

    def _get_overlay_handle_at(self, idx: int, pos: QPoint) -> int:
        return self._overlay_mgr.get_overlay_handle_at(idx, pos)

    def _is_pos_inside_overlay(self, idx: int, pos: QPoint) -> bool:
        return self._overlay_mgr.is_pos_inside_overlay(idx, pos)

    def _detect_existing_media_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        return self._overlay_mgr.detect_existing_media_at(page_idx, pdf_pt)

    def _note_icon_at(self, pos: QPoint) -> int:
        return self._overlay_mgr.note_icon_at(pos)

    def _annot_note_at(self, pos: QPoint):
        return self._overlay_mgr.annot_note_at(pos)