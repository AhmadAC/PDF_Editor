# app/editor/overlay_actions.py
"""Overlay lifecycle, placement, deletion, duplication, z-order, rotation, flip, and opacity actions mixin."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, Qt

from app.editor.dialogs import _SignatureDialog, load_signature_pixmap
from app.editor.overlay_geometry import (
    _transform_drawing_items,
    _transform_pt,
    overlay_pdf_rect,
    snapshot_overlay,
)


class OverlayActionsMixin:
    """Mixin providing signature placement, deletion, duplication, z-order, rotation, flip, and opacity operations."""

    if TYPE_CHECKING:
        canvas: Any
        HANDLE_NONE: int

    def start_add_signature_flow(self, pos: QPoint | None = None):
        """Open signature selection dialog and enter cursor-follow placement mode."""
        dlg = _SignatureDialog(self.canvas)
        if dlg.exec() == _SignatureDialog.DialogCode.Accepted:
            path = dlg.selected_signature_path()
            if path and os.path.isfile(path):
                self.begin_signature_placement(path)

    def begin_signature_placement(self, sig_path: str):
        c = self.canvas
        self.discard_untouched_existing()  # type: ignore[attr-defined]
        c._selected_overlay_idx = -1
        c._placing_signature = True
        c._placing_sig_path = sig_path
        c._placing_sig_pixmap = load_signature_pixmap(sig_path, max_size=(320, 140))
        c.setCursor(Qt.CursorShape.CrossCursor)
        c.update()

    def cancel_signature_placement(self):
        c = self.canvas
        c._placing_signature = False
        c._placing_sig_path = ""
        c._placing_sig_pixmap = None
        c._sig_cursor_pos = None
        c.setCursor(Qt.CursorShape.ArrowCursor)
        c.update()

    def discard_untouched_existing(self) -> None:
        """Remove any temporary _existing overlay that was clicked/inspected but never modified or deleted."""
        c = self.canvas
        if not getattr(c, "_overlays", None):
            return
        kept: list[dict] = []
        removed_paths: list[str] = []
        selected_ov = (
            c._overlays[c._selected_overlay_idx]
            if 0 <= c._selected_overlay_idx < len(c._overlays)
            else None
        )
        for ov in c._overlays:
            if (
                ov.get("_existing")
                and not ov.get("_dirty")
                and not ov.get("_deleted")
                and ov.get("type") not in ("note", "delete_annot")
            ):
                if ov.get("_temp_file") and ov.get("path"):
                    removed_paths.append(ov["path"])
                if ov is selected_ov:
                    selected_ov = None
                continue
            kept.append(ov)

        if len(kept) != len(c._overlays):
            c._overlays[:] = kept
            if selected_ov is not None and selected_ov in c._overlays:
                c._selected_overlay_idx = c._overlays.index(selected_ov)
            else:
                c._selected_overlay_idx = -1
            for p in removed_paths:
                if p and os.path.isfile(p) and not any(o.get("path") == p for o in c._overlays):
                    with contextlib.suppress(OSError):
                        os.unlink(p)

    def delete_selected_overlay(self) -> dict | None:
        c = self.canvas
        if 0 <= c._selected_overlay_idx < len(c._overlays):
            idx = c._selected_overlay_idx
            overlay = c._overlays[idx]
            page_idx = overlay.get("page", 0)
            if overlay.get("_existing"):
                overlay["_deleted"] = True
                overlay["_dirty"] = True
                c._selected_overlay_idx = -1
            else:
                c._overlays.pop(idx)
                c._selected_overlay_idx = -1
            c._drag_handle = self.HANDLE_NONE
            c._moving_overlay = False
            c._drag_before_snapshot = None
            c.setCursor(Qt.CursorShape.IBeamCursor if getattr(c, "_text_mode", False) else Qt.CursorShape.ArrowCursor)
            c.overlay_deleted.emit(idx, overlay)
            c.overlay_changed.emit()
            if hasattr(c, "refresh_pages"):
                c.refresh_pages({page_idx})
            c.update()
            return overlay
        return None

    def duplicate_selected_overlay(self) -> None:
        c = self.canvas
        if 0 <= c._selected_overlay_idx < len(c._overlays):
            src = c._overlays[c._selected_overlay_idx]
            if src.get("_deleted"):
                return
            if src.get("_existing") and not src.get("_dirty"):
                src_copy = dict(src)
                self.discard_untouched_existing()
                src = src_copy

            new_overlay = dict(src)
            if new_overlay.get("type") == "drawing":
                orig_bb = src.get("_items_rect") or src.get("_orig_rect") or overlay_pdf_rect(src)
                if orig_bb:
                    rr = fitz.Rect(orig_bb)
                    new_overlay["_items_rect"] = [rr.x0, rr.y0, rr.x1, rr.y1]
            for meta_key in (
                "_existing", "_orig_rect", "_existing_xref", "_existing_annot",
                "_existing_span", "_annot_type", "_annot_bbox", "_dirty",
                "_deleted", "_bg_color", "_temp_file", "_z_back",
            ):
                new_overlay.pop(meta_key, None)

            if new_overlay.get("type") == "text_edit":
                new_overlay["type"] = "text"
                new_overlay["text"] = src.get("new_text", src.get("text", ""))
                new_overlay["size"] = float(src.get("font_size", src.get("size", 12)))
                c_val = src.get("color", 0)
                if isinstance(c_val, int):
                    new_overlay["color"] = (
                        ((c_val >> 16) & 0xFF) / 255.0,
                        ((c_val >> 8) & 0xFF) / 255.0,
                        (c_val & 0xFF) / 255.0,
                    )
                origin = src.get("origin")
                bb = src.get("bbox")
                if origin:
                    new_overlay["point"] = fitz.Point(origin[0] + 15, origin[1] + 15)
                elif bb:
                    new_overlay["point"] = fitz.Point(bb[0] + 15, bb[3] + 15)
                if bb:
                    new_overlay["rect"] = fitz.Rect(bb[0] + 15, bb[1] + 15, bb[2] + 15, bb[3] + 15)
                new_overlay.pop("bbox", None)
                new_overlay.pop("origin", None)
                new_overlay.pop("old_text", None)
                new_overlay.pop("new_text", None)
            else:
                if "rect" in new_overlay and new_overlay["rect"] is not None:
                    r = fitz.Rect(new_overlay["rect"])
                    new_overlay["rect"] = fitz.Rect(r.x0 + 15, r.y0 + 15, r.x1 + 15, r.y1 + 15)
                if "bbox" in new_overlay and new_overlay["bbox"] is not None:
                    bb = new_overlay["bbox"]
                    new_overlay["bbox"] = [bb[0] + 15, bb[1] + 15, bb[2] + 15, bb[3] + 15]
                if "rects" in new_overlay and new_overlay["rects"] is not None:
                    new_overlay["rects"] = [
                        fitz.Rect(r.x0 + 15, r.y0 + 15, r.x1 + 15, r.y1 + 15)
                        for r in new_overlay["rects"]
                    ]
                if "points" in new_overlay and new_overlay["points"] is not None:
                    new_overlay["points"] = [
                        (float(px) + 15.0, float(py) + 15.0)
                        for px, py in new_overlay["points"]
                    ]
                if "point" in new_overlay and new_overlay["point"] is not None:
                    pt = new_overlay["point"]
                    new_overlay["point"] = fitz.Point(pt.x + 15, pt.y + 15)

            if hasattr(c, "overlay_duplicated"):
                c.overlay_duplicated.emit(new_overlay)
            else:
                c._overlays.append(new_overlay)
            if new_overlay in c._overlays:
                c._selected_overlay_idx = c._overlays.index(new_overlay)
            c.overlay_changed.emit()
            c.update()

    def arrange_overlay(self, idx: int, mode: str) -> None:
        """Change z-order of overlay idx: 'front', 'forward', 'backward', or 'back'."""
        c = self.canvas
        if not (0 <= idx < len(c._overlays)):
            return
        ov = c._overlays[idx]
        if ov.get("_deleted"):
            return
        page_idx = ov.get("page", 0)

        before_order = list(c._overlays)
        before_z_back = bool(ov.get("_z_back", False))
        was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))

        same_pg_indices = [
            i for i, o in enumerate(c._overlays)
            if not o.get("_deleted") and o.get("page", 0) == page_idx
        ]
        if not same_pg_indices:
            return
        pos_in_pg = same_pg_indices.index(idx)

        if mode == "front":
            c._overlays.pop(idx)
            c._overlays.append(ov)
            ov["_z_back"] = False
        elif mode == "back":
            c._overlays.pop(idx)
            c._overlays.insert(0, ov)
            ov["_z_back"] = True
        elif mode == "forward":
            if ov.get("_z_back"):
                ov["_z_back"] = False
            elif pos_in_pg < len(same_pg_indices) - 1:
                nxt_idx = same_pg_indices[pos_in_pg + 1]
                c._overlays[idx], c._overlays[nxt_idx] = c._overlays[nxt_idx], c._overlays[idx]
        elif mode == "backward":
            if pos_in_pg > 0:
                prev_idx = same_pg_indices[pos_in_pg - 1]
                c._overlays[idx], c._overlays[prev_idx] = c._overlays[prev_idx], c._overlays[idx]
                if c._overlays[prev_idx] is ov and prev_idx == same_pg_indices[0]:
                    ov["_z_back"] = bool(c._overlays[idx].get("_z_back", False))
            else:
                ov["_z_back"] = True

        new_idx = c._overlays.index(ov)
        c._selected_overlay_idx = new_idx
        changed = (c._overlays != before_order) or (bool(ov.get("_z_back", False)) != before_z_back)
        if not changed:
            return

        if was_untouched:
            ov["_dirty"] = True

        if hasattr(c, "overlay_arranged"):
            c.overlay_arranged.emit({
                "edit": ov,
                "page": page_idx,
                "was_untouched": was_untouched,
                "before_order": before_order,
                "after_order": list(c._overlays),
                "before_z_back": before_z_back,
                "after_z_back": bool(ov.get("_z_back", False)),
            })
        c.overlay_changed.emit()
        if hasattr(c, "refresh_pages"):
            c.refresh_pages({page_idx})
        c.update()

    def rotate_overlay(self, idx: int, delta_deg: int) -> None:
        """Rotate overlay idx by delta_deg (90, -90/270, 180) clockwise."""
        c = self.canvas
        if not (0 <= idx < len(c._overlays)):
            return
        ov = c._overlays[idx]
        if ov.get("_deleted"):
            return

        r = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
        if r is None:
            return

        before_snap = snapshot_overlay(ov)
        was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))

        delta = int(delta_deg) % 360
        cx = (r.x0 + r.x1) / 2.0
        cy = (r.y0 + r.y1) / 2.0
        etype = ov.get("type")

        if delta in (90, 270):
            new_w = r.height
            new_h = r.width
            new_r = fitz.Rect(cx - new_w / 2.0, cy - new_h / 2.0, cx + new_w / 2.0, cy + new_h / 2.0)
        else:
            new_r = fitz.Rect(r)

        if etype == "drawing":
            mode = "rot90" if delta == 90 else ("rot270" if delta == 270 else "rot180")
            if "_items_rect" not in ov:
                orig_bb = ov.get("_orig_rect") or [r.x0, r.y0, r.x1, r.y1]
                ov["_items_rect"] = list(orig_bb)
            ir = fitz.Rect(ov["_items_rect"])
            icx, icy = (ir.x0 + ir.x1) / 2.0, (ir.y0 + ir.y1) / 2.0
            ov["items"] = _transform_drawing_items(ov.get("items", []), icx, icy, mode)
            p1 = _transform_pt(ir.x0, ir.y0, icx, icy, mode)
            p2 = _transform_pt(ir.x1, ir.y1, icx, icy, mode)
            ov["_items_rect"] = [min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])]
            ov["rect"] = new_r
        elif etype == "draw" and "points" in ov:
            mode = "rot90" if delta == 90 else ("rot270" if delta == 270 else "rot180")
            ov["points"] = [_transform_pt(px, py, cx, cy, mode) for px, py in ov["points"]]
        else:
            ov["rotation"] = (int(ov.get("rotation", 0)) + delta) % 360
            if "rect" in ov:
                ov["rect"] = new_r
            if "bbox" in ov:
                ov["bbox"] = [new_r.x0, new_r.y0, new_r.x1, new_r.y1]

        c._drag_start_rect = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
        if was_untouched:
            ov["_dirty"] = True
            if hasattr(c, "overlay_committed"):
                c.overlay_committed.emit(ov)
        elif hasattr(c, "overlay_transformed"):
            c.overlay_transformed.emit(ov, before_snap)
        c.overlay_changed.emit()
        if hasattr(c, "refresh_pages"):
            c.refresh_pages({ov.get("page", 0)})
        c.update()

    def flip_overlay(self, idx: int, horizontal: bool) -> None:
        """Flip overlay idx horizontally or vertically."""
        c = self.canvas
        if not (0 <= idx < len(c._overlays)):
            return
        ov = c._overlays[idx]
        if ov.get("_deleted"):
            return

        r = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
        if r is None:
            return

        before_snap = snapshot_overlay(ov)
        was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
        etype = ov.get("type")
        cx = (r.x0 + r.x1) / 2.0
        cy = (r.y0 + r.y1) / 2.0
        mode = "flip_h" if horizontal else "flip_v"

        if etype == "drawing":
            if "_items_rect" not in ov:
                orig_bb = ov.get("_orig_rect") or [r.x0, r.y0, r.x1, r.y1]
                ov["_items_rect"] = list(orig_bb)
            ir = fitz.Rect(ov["_items_rect"])
            icx, icy = (ir.x0 + ir.x1) / 2.0, (ir.y0 + ir.y1) / 2.0
            ov["items"] = _transform_drawing_items(ov.get("items", []), icx, icy, mode)
        elif etype == "draw" and "points" in ov:
            ov["points"] = [_transform_pt(px, py, cx, cy, mode) for px, py in ov["points"]]
        else:
            key = "flip_h" if horizontal else "flip_v"
            ov[key] = not bool(ov.get(key, False))

        if was_untouched:
            ov["_dirty"] = True
            if hasattr(c, "overlay_committed"):
                c.overlay_committed.emit(ov)
        elif hasattr(c, "overlay_transformed"):
            c.overlay_transformed.emit(ov, before_snap)
        c.overlay_changed.emit()
        if hasattr(c, "refresh_pages"):
            c.refresh_pages({ov.get("page", 0)})
        c.update()

    def set_overlay_opacity(self, idx: int, opacity: float, *, before_snap: dict | None = None) -> None:
        """Set opacity (0.0 .. 1.0) on overlay idx and record undo/redo."""
        c = self.canvas
        if not (0 <= idx < len(c._overlays)):
            return
        ov = c._overlays[idx]
        if ov.get("_deleted"):
            return

        snap = before_snap if before_snap is not None else snapshot_overlay(ov)
        was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
        ov["opacity"] = max(0.0, min(1.0, float(opacity)))

        if was_untouched:
            ov["_dirty"] = True
            if hasattr(c, "overlay_committed"):
                c.overlay_committed.emit(ov)
        elif hasattr(c, "overlay_transformed"):
            c.overlay_transformed.emit(ov, snap)
        c.overlay_changed.emit()
        if hasattr(c, "refresh_pages"):
            c.refresh_pages({ov.get("page", 0)})
        c.update()