# app/editor/canvas_overlay1.py

"""Overlay management: Foxit-style object detection, selection, resizing handles, hit-testing, duplication and deletion."""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any
import fitz
from PySide6.QtCore import Qt, QRect, QPoint

from app.editor.dialogs import _SignatureDialog, load_signature_pixmap
from app.editor.canvas_render1 import _NOTE_ICON_SIZE

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)
_PDF_ANNOT_LINK: int = getattr(fitz, "PDF_ANNOT_LINK", 1)
_PDF_ANNOT_WIDGET: int = getattr(fitz, "PDF_ANNOT_WIDGET", 20)
_PDF_ANNOT_REDACT: int = getattr(fitz, "PDF_ANNOT_REDACT", 12)


def rects_close(r1, r2, tol: float = 0.75) -> bool:
    """Return True if two rects match within tol PDF points."""
    if r1 is None or r2 is None:
        return False
    a = fitz.Rect(r1)
    b = fitz.Rect(r2)
    return (
        abs(a.x0 - b.x0) <= tol
        and abs(a.y0 - b.y0) <= tol
        and abs(a.x1 - b.x1) <= tol
        and abs(a.y1 - b.y1) <= tol
    )


def overlay_pdf_rect(ov: dict, zoom: float = 1.0) -> fitz.Rect | None:
    """Return the PDF-space bounding rectangle of any overlay dict."""
    etype = ov.get("type")
    if "rect" in ov and ov["rect"] is not None:
        return fitz.Rect(ov["rect"])
    if "bbox" in ov and ov["bbox"] is not None:
        return fitz.Rect(ov["bbox"])
    if etype == "highlight" and ov.get("rects"):
        rs = [fitz.Rect(r) for r in ov["rects"]]
        if rs:
            u = fitz.Rect(rs[0])
            for r in rs[1:]:
                u |= r
            return u
    if etype == "draw" and ov.get("points"):
        pts = ov["points"]
        if pts:
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            pad = max(2.0, float(ov.get("width", 2)) * 0.5 + 1.0)
            return fitz.Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    if etype == "note" and "point" in ov:
        pt = ov["point"]
        sz = float(_NOTE_ICON_SIZE) / max(0.1, zoom)
        return fitz.Rect(pt.x, pt.y - sz, pt.x + sz, pt.y)
    if etype == "text" and "point" in ov:
        pt = ov["point"]
        sz = float(ov.get("size", 12))
        txt = ov.get("text", "")
        tw = max(16.0, len(txt) * sz * 0.65)
        r = fitz.Rect(pt.x, pt.y - sz * 0.85, pt.x + tw, pt.y + sz * 0.25)
        ov["rect"] = r
        return r
    return None


def snapshot_overlay(ov: dict) -> dict:
    """Capture mutable geometry/style state of an overlay for undo/redo."""
    snap: dict = {}
    if "rect" in ov and ov["rect"] is not None:
        snap["rect"] = fitz.Rect(ov["rect"])
    if "bbox" in ov and ov["bbox"] is not None:
        snap["bbox"] = list(ov["bbox"])
    if "rects" in ov and ov["rects"] is not None:
        snap["rects"] = [fitz.Rect(r) for r in ov["rects"]]
    if "points" in ov and ov["points"] is not None:
        snap["points"] = [(float(p[0]), float(p[1])) for p in ov["points"]]
    if "point" in ov and ov["point"] is not None:
        snap["point"] = fitz.Point(ov["point"])
    if "origin" in ov and ov["origin"] is not None:
        snap["origin"] = list(ov["origin"])
    for k in ("size", "font_size", "width", "new_text", "text", "font", "color", "flags", "_font_changed", "_deleted", "_dirty"):
        if k in ov:
            snap[k] = ov[k]
    return snap


def restore_overlay_snapshot(ov: dict, snap: dict) -> None:
    """Restore a previously captured snapshot onto an overlay dict."""
    mutable_keys = (
        "rect", "bbox", "rects", "points", "point", "origin",
        "size", "font_size", "width", "new_text", "text", "font",
        "color", "flags", "_font_changed", "_deleted", "_dirty",
    )
    for k in mutable_keys:
        if k in snap:
            val = snap[k]
            if k == "rect":
                ov[k] = fitz.Rect(val)
            elif k in ("bbox", "origin"):
                ov[k] = list(val)
            elif k == "rects":
                ov[k] = [fitz.Rect(r) for r in val]
            elif k == "points":
                ov[k] = [(float(p[0]), float(p[1])) for p in val]
            elif k == "point":
                ov[k] = fitz.Point(val)
            else:
                ov[k] = val
        elif k in ov:
            del ov[k]


def _sample_bg_color(page, target_rect: fitz.Rect) -> tuple[int, int, int]:
    """Find the filled vector background color behind target_rect, defaulting to white."""
    try:
        bg = (255, 255, 255)
        tr = fitz.Rect(target_rect)
        for d in page.get_drawings() or []:
            fill = d.get("fill")
            if not fill or len(fill) < 3:
                continue
            dr = fitz.Rect(d["rect"]).normalize()
            if rects_close(dr, tr, tol=1.0):
                continue
            if dr.contains(tr) or (dr + fitz.Rect(-1, -1, 1, 1)).contains(tr):
                bg = (
                    max(0, min(255, int(round(fill[0] * 255)))),
                    max(0, min(255, int(round(fill[1] * 255)))),
                    max(0, min(255, int(round(fill[2] * 255)))),
                )
        return bg
    except Exception:
        return (255, 255, 255)


def _serialize_drawing_items(raw_items: list) -> list:
    """Convert PyMuPDF drawing path items into pure Python tuples."""
    out = []
    for item in raw_items or []:
        op = item[0]
        if op == "l":
            p1, p2 = item[1], item[2]
            out.append(("l", (float(p1.x), float(p1.y)), (float(p2.x), float(p2.y))))
        elif op == "re":
            r = fitz.Rect(item[1]).normalize()
            out.append(("re", (float(r.x0), float(r.y0), float(r.x1), float(r.y1))))
        elif op == "qu":
            q = item[1]
            out.append((
                "qu",
                (
                    (float(q.ul.x), float(q.ul.y)),
                    (float(q.ur.x), float(q.ur.y)),
                    (float(q.lr.x), float(q.lr.y)),
                    (float(q.ll.x), float(q.ll.y)),
                ),
            ))
        elif op == "c":
            out.append((
                "c",
                (float(item[1].x), float(item[1].y)),
                (float(item[2].x), float(item[2].y)),
                (float(item[3].x), float(item[3].y)),
                (float(item[4].x), float(item[4].y)),
            ))
    return out


class CanvasOverlayManager:
    HANDLE_NONE = 0
    HANDLE_TL   = 1
    HANDLE_TR   = 2
    HANDLE_BL   = 3
    HANDLE_BR   = 4

    _RESIZABLE_TYPES = (
        "signature", "image", "text_edit", "text", "redact",
        "check", "cross", "drawing", "highlight", "draw",
    )

    def __init__(self, canvas):
        self.canvas = canvas

    def start_add_signature_flow(self, pos: QPoint | None = None):
        """Open signature selection dialog and enter cursor-follow placement mode."""
        dlg = _SignatureDialog(self.canvas)
        if dlg.exec() == _SignatureDialog.DialogCode.Accepted:
            path = dlg.selected_signature_path()
            if path and os.path.isfile(path):
                self.begin_signature_placement(path)

    def begin_signature_placement(self, sig_path: str):
        c = self.canvas
        self.discard_untouched_existing()
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
                # Don't accidentally mark the untouched original dirty just because user duplicated it
                src_copy = dict(src)
                self.discard_untouched_existing()
                src = src_copy

            new_overlay = dict(src)
            for meta_key in (
                "_existing", "_orig_rect", "_existing_xref", "_existing_annot",
                "_existing_span", "_annot_type", "_annot_bbox", "_dirty",
                "_deleted", "_bg_color", "_temp_file",
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
                if new_overlay.get("type") == "drawing":
                    orig_bb = src.get("_orig_rect") or overlay_pdf_rect(src)
                    if orig_bb:
                        new_overlay["_orig_rect"] = [
                            float(fitz.Rect(orig_bb).x0),
                            float(fitz.Rect(orig_bb).y0),
                            float(fitz.Rect(orig_bb).x1),
                            float(fitz.Rect(orig_bb).y1),
                        ]
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
                c._selected_overlay_idx = len(c._overlays) - 1
            if new_overlay in c._overlays:
                c._selected_overlay_idx = c._overlays.index(new_overlay)
            c.overlay_changed.emit()
            c.update()

    def get_overlay_handle_at(self, idx: int, pos: QPoint) -> int:
        c = self.canvas
        if idx < 0 or idx >= len(c._overlays):
            return self.HANDLE_NONE
        e = c._overlays[idx]
        if e.get("_deleted"):
            return self.HANDLE_NONE
        etype = e.get("type")
        if etype not in self._RESIZABLE_TYPES:
            return self.HANDLE_NONE
        if etype == "highlight" and e.get("rects"):
            return self.HANDLE_NONE
        pg = e.get("page", 0)
        if pg >= len(c._page_offsets):
            return self.HANDLE_NONE
        yo = c._page_offsets[pg][0]
        z = c._zoom

        r = overlay_pdf_rect(e, zoom=z)
        if r is None:
            return self.HANDLE_NONE

        pad = 4 if etype == "drawing" and (r.width < 4 or r.height < 4) else 0
        sx0 = int(r.x0 * z) - pad
        sy0 = yo + int(r.y0 * z) - pad
        sx1 = int(r.x1 * z) + pad
        sy1 = yo + int(r.y1 * z) + pad
        hs = 12
        if QRect(sx0 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return self.HANDLE_TL
        if QRect(sx1 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return self.HANDLE_TR
        if QRect(sx0 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return self.HANDLE_BL
        if QRect(sx1 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return self.HANDLE_BR
        return self.HANDLE_NONE

    def is_pos_inside_overlay(self, idx: int, pos: QPoint) -> bool:
        c = self.canvas
        if idx < 0 or idx >= len(c._overlays):
            return False
        e = c._overlays[idx]
        if e.get("_deleted"):
            return False
        pg = e.get("page", 0)
        if pg >= len(c._page_offsets):
            return False
        yo = c._page_offsets[pg][0]
        z = c._zoom

        if e.get("type") == "highlight" and e.get("rects"):
            for r_item in e["rects"]:
                sx0 = int(r_item.x0 * z)
                sy0 = yo + int(r_item.y0 * z)
                sx1 = int(r_item.x1 * z)
                sy1 = yo + int(r_item.y1 * z)
                if QRect(sx0 - 4, sy0 - 4, max(12, sx1 - sx0 + 8), max(12, sy1 - sy0 + 8)).contains(pos):
                    return True
            return False

        r = overlay_pdf_rect(e, zoom=z)
        if r is None:
            return False

        sx0 = int(r.x0 * z)
        sy0 = yo + int(r.y0 * z)
        sx1 = int(r.x1 * z)
        sy1 = yo + int(r.y1 * z)
        return QRect(sx0 - 5, sy0 - 5, max(14, sx1 - sx0 + 10), max(14, sy1 - sy0 + 10)).contains(pos)

    def _is_existing_rect_claimed(self, page_idx: int, rect: fitz.Rect) -> dict | str | None:
        """Check if an existing PDF object at rect has already been loaded, moved, or deleted."""
        c = self.canvas
        for ov in c._overlays:
            if not ov.get("_existing") or ov.get("page") != page_idx:
                continue
            orig = ov.get("_orig_rect") or ov.get("_annot_bbox")
            if orig is None or not rects_close(orig, rect, tol=0.75):
                continue
            if ov.get("_deleted"):
                return "CLAIMED"
            cur = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
            if cur is not None and not rects_close(cur, orig, tol=0.75):
                return "CLAIMED"
            return ov
        return None

    def _is_annot_claimed(self, page_idx: int, annot) -> dict | str | None:
        c = self.canvas
        axref = getattr(annot, "xref", None)
        for ov in c._overlays:
            if not ov.get("_existing") or ov.get("page") != page_idx:
                continue
            if axref and ov.get("_existing_annot") == axref:
                if ov.get("_deleted"):
                    return "CLAIMED"
                orig = ov.get("_orig_rect") or ov.get("_annot_bbox")
                cur = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                if orig and cur and not rects_close(cur, orig, tol=0.75):
                    return "CLAIMED"
                return ov
            orig = ov.get("_orig_rect") or ov.get("_annot_bbox")
            if orig and rects_close(orig, annot.rect, tol=0.75):
                if ov.get("_deleted"):
                    return "CLAIMED"
                cur = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                if cur and not rects_close(cur, orig, tol=0.75):
                    return "CLAIMED"
                return ov
        return None

    @staticmethod
    def _extract_image_temp_png(doc, xref: int, smask: int = 0) -> str:
        """Extract an embedded PDF image (preserving transparency SMask) to a temp PNG file."""
        pix_base = fitz.Pixmap(doc, xref)
        if pix_base.n - pix_base.alpha > 3:
            pix_base = fitz.Pixmap(fitz.csRGB, pix_base)
        pix = pix_base
        if smask and smask > 0:
            try:
                pix_mask = fitz.Pixmap(doc, smask)
                if pix_mask.n > 1:
                    pix_mask = fitz.Pixmap(fitz.csGRAY, pix_mask)
                if pix_base.alpha:
                    pix_base = fitz.Pixmap(pix_base, 0)
                pix = fitz.Pixmap(pix_base, pix_mask)
            except Exception:
                pix = pix_base
        fd, tmp_path = tempfile.mkstemp(prefix="PDF_Editor_img_", suffix=".png")
        os.close(fd)
        pix.save(tmp_path)
        return tmp_path

    def detect_existing_media_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        """Detect existing annotations or raster images at pdf_pt (smallest/top-most match wins)."""
        c = self.canvas
        if not c._doc or not (0 <= page_idx < c._doc.page_count):
            return None
        page = c._doc[page_idx]

        # 1. Existing Annotations (excluding Text notes, Links, Widgets, Redacts)
        annot_candidates: list[tuple[float, Any]] = []
        try:
            for annot in page.annots() or []:
                atype = annot.type[0] if isinstance(annot.type, (tuple, list)) else annot.type
                if atype in (_PDF_ANNOT_TEXT, _PDF_ANNOT_LINK, _PDF_ANNOT_WIDGET, _PDF_ANNOT_REDACT):
                    continue
                ar = fitz.Rect(annot.rect)
                if (ar + fitz.Rect(-2, -2, 2, 2)).contains(pdf_pt):
                    claimed = self._is_annot_claimed(page_idx, annot)
                    if claimed == "CLAIMED":
                        continue
                    if isinstance(claimed, dict):
                        return claimed
                    area = max(1.0, ar.width) * max(1.0, ar.height)
                    annot_candidates.append((area, annot))
        except Exception:
            pass

        if annot_candidates:
            annot_candidates.sort(key=lambda item: item[0])
            annot = annot_candidates[0][1]
            try:
                pix = annot.get_pixmap(dpi=200, alpha=True)
                fd, tmp_path = tempfile.mkstemp(prefix="PDF_Editor_annot_", suffix=".png")
                os.close(fd)
                pix.save(tmp_path)
                ar = fitz.Rect(annot.rect)
                entry = {
                    "type": "image",
                    "page": page_idx,
                    "rect": ar,
                    "path": tmp_path,
                    "_existing": True,
                    "_existing_annot": annot.xref,
                    "_orig_rect": [ar.x0, ar.y0, ar.x1, ar.y1],
                    "_annot_type": annot.type[0] if isinstance(annot.type, (tuple, list)) else annot.type,
                    "_bg_color": _sample_bg_color(page, ar),
                    "_dirty": False,
                    "_temp_file": True,
                }
                c._overlays.append(entry)
                return entry
            except Exception:
                pass

        # 2. Embedded raster images (with alpha SMask support)
        img_candidates: list[tuple[float, int, int, fitz.Rect]] = []
        try:
            for img_info in page.get_images(full=True):
                xref = int(img_info[0])
                smask = int(img_info[1]) if len(img_info) > 1 else 0
                if xref <= 0:
                    continue
                rects = page.get_image_rects(xref)
                for r in rects:
                    rr = fitz.Rect(r)
                    if (rr + fitz.Rect(-2, -2, 2, 2)).contains(pdf_pt):
                        claimed = self._is_existing_rect_claimed(page_idx, rr)
                        if claimed == "CLAIMED":
                            continue
                        if isinstance(claimed, dict):
                            return claimed
                        area = max(1.0, rr.width) * max(1.0, rr.height)
                        img_candidates.append((area, xref, smask, rr))
        except Exception:
            pass

        if img_candidates:
            img_candidates.sort(key=lambda item: item[0])
            _, xref, smask, rr = img_candidates[0]
            try:
                tmp_path = self._extract_image_temp_png(c._doc, xref, smask)
                entry = {
                    "type": "image",
                    "page": page_idx,
                    "rect": fitz.Rect(rr),
                    "path": tmp_path,
                    "_existing": True,
                    "_existing_xref": xref,
                    "_orig_rect": [rr.x0, rr.y0, rr.x1, rr.y1],
                    "_bg_color": _sample_bg_color(page, rr),
                    "_dirty": False,
                    "_temp_file": True,
                }
                c._overlays.append(entry)
                return entry
            except Exception:
                pass

        return None

    def detect_existing_drawing_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        """Detect existing vector graphics/shapes on the PDF page at pdf_pt."""
        c = self.canvas
        if not c._doc or not (0 <= page_idx < c._doc.page_count):
            return None
        page = c._doc[page_idx]
        pw = max(1.0, float(page.rect.width))
        ph = max(1.0, float(page.rect.height))

        candidates: list[tuple[float, dict, fitz.Rect]] = []
        try:
            for d in page.get_drawings() or []:
                if d.get("fill") is None and d.get("color") is None:
                    continue
                r = fitz.Rect(d["rect"]).normalize()
                if r.width >= pw * 0.90 and r.height >= ph * 0.90:
                    continue
                if r.width < 1.0 and r.height < 1.0:
                    continue
                hit_r = r + fitz.Rect(-3.0, -3.0, 3.0, 3.0)
                if not hit_r.contains(pdf_pt):
                    continue
                claimed = self._is_existing_rect_claimed(page_idx, r)
                if claimed == "CLAIMED":
                    continue
                if isinstance(claimed, dict):
                    return claimed
                items = _serialize_drawing_items(d.get("items", []))
                if not items:
                    continue
                area = max(1.0, r.width) * max(1.0, r.height)
                candidates.append((area, d, r))
        except Exception:
            return None

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        _, d, r = candidates[0]
        fill_col = tuple(float(x) for x in d["fill"][:3]) if d.get("fill") else None
        stroke_col = tuple(float(x) for x in d["color"][:3]) if d.get("color") else None

        entry = {
            "type": "drawing",
            "page": page_idx,
            "rect": fitz.Rect(r),
            "items": _serialize_drawing_items(d.get("items", [])),
            "fill": fill_col,
            "color": stroke_col,
            "width": float(d.get("width") or 1.0),
            "fill_opacity": d.get("fill_opacity"),
            "stroke_opacity": d.get("stroke_opacity"),
            "even_odd": bool(d.get("even_odd", False)),
            "closePath": bool(d.get("closePath", False)),
            "dashes": d.get("dashes"),
            "lineCap": d.get("lineCap"),
            "lineJoin": d.get("lineJoin"),
            "_existing": True,
            "_orig_rect": [r.x0, r.y0, r.x1, r.y1],
            "_bg_color": _sample_bg_color(page, r),
            "_dirty": False,
        }
        c._overlays.append(entry)
        return entry

    def detect_existing_text_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        """Detect existing text span under click point and wrap as an editable/movable overlay."""
        c = self.canvas
        if not c._doc or not (0 <= page_idx < c._doc.page_count):
            return None
        span = c.get_span_at(page_idx, pdf_pt, max_dist=6.0)
        if not span:
            return None
        bb = list(span["bbox"])
        claimed = self._is_existing_rect_claimed(page_idx, fitz.Rect(bb))
        if claimed == "CLAIMED":
            return None
        if isinstance(claimed, dict):
            return claimed

        page = c._doc[page_idx]
        span_size = float(span.get("size") or 0)
        if span_size <= 0:
            span_size = max(6.0, float(bb[3] - bb[1]) * 0.75)
        font_name = span.get("font", "Helvetica")
        orig = list(span.get("origin", (bb[0], bb[3])))
        entry = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": bb,
            "old_text": span.get("text", ""),
            "new_text": span.get("text", ""),
            "size": span_size,
            "font_size": span_size,
            "color": span.get("color", 0),
            "font": font_name,
            "flags": int(span.get("flags", 0) or 0),
            "ascender": float(span.get("ascender") or 0.8),
            "descender": float(span.get("descender") or -0.2),
            "origin": orig,
            "_orig_origin": list(orig),
            "_existing": True,
            "_existing_span": True,
            "_orig_rect": list(bb),
            "_bg_color": _sample_bg_color(page, fitz.Rect(bb)),
            "_dirty": False,
        }
        c._overlays.append(entry)
        return entry

    def note_icon_at(self, pos: QPoint) -> int:
        c = self.canvas
        z = c._zoom
        margin = 10
        for i, e in enumerate(c._overlays):
            if e.get("type") != "note" or e.get("_deleted"):
                continue
            pg = e.get("page", 0)
            if pg >= len(c._page_offsets):
                continue
            yo = c._page_offsets[pg][0]
            pt = e["point"]
            px, py = int(pt.x * z), yo + int(pt.y * z)
            hit_r = QRect(px - margin, py - _NOTE_ICON_SIZE - margin,
                          _NOTE_ICON_SIZE + margin * 2, _NOTE_ICON_SIZE + margin * 2)
            if hit_r.contains(pos):
                return i
        return -1

    def annot_note_at(self, pos: QPoint):
        c = self.canvas
        if not c._doc:
            return -1, None
        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)
        page = c._doc[page_idx]
        for annot in page.annots() or []:
            if annot.type[0] == _PDF_ANNOT_TEXT or annot.type[1] == "Text":
                expanded = annot.rect + fitz.Rect(-10, -10, 10, 10)
                if expanded.contains(pdf_pt):
                    txt = annot.info.get("content", "") or annot.get_text() or ""
                    for i, e in enumerate(c._overlays):
                        if e.get("type") == "note" and not e.get("_deleted") and e.get("text", "").strip() == txt.strip():
                            return i, txt.strip()
                        if e.get("type") == "note" and e.get("_deleted") and e.get("_existing_annot") == annot.xref:
                            return -1, None
                    if txt.strip():
                        pt = fitz.Point(annot.rect.x0, annot.rect.y0 + annot.rect.height)
                        c._overlays.append({
                            "type": "note", "page": page_idx,
                            "point": pt, "text": txt.strip(),
                            "_existing": True,
                            "_existing_annot": annot.xref,
                            "_annot_type": annot.type[0],
                            "_annot_bbox": [annot.rect.x0, annot.rect.y0,
                                            annot.rect.x1, annot.rect.y1],
                            "_orig_rect": [annot.rect.x0, annot.rect.y0,
                                           annot.rect.x1, annot.rect.y1],
                            "_dirty": False,
                        })
                        return len(c._overlays) - 1, txt.strip()
        return -1, None