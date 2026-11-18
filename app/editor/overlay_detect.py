# app/editor/overlay_detect.py
"""Overlay hit-testing, handle detection, and existing PDF object/text/drawing extraction mixin."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QRect

from app.editor.canvas_render1 import _NOTE_ICON_SIZE
from app.editor.overlay_geometry import (
    _sample_bg_color,
    _serialize_drawing_items,
    overlay_pdf_rect,
    rects_close,
)

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)
_PDF_ANNOT_LINK: int = getattr(fitz, "PDF_ANNOT_LINK", 1)
_PDF_ANNOT_WIDGET: int = getattr(fitz, "PDF_ANNOT_WIDGET", 20)
_PDF_ANNOT_REDACT: int = getattr(fitz, "PDF_ANNOT_REDACT", 12)


class OverlayDetectorMixin:
    """Mixin providing hit-testing and on-page PDF element detection for CanvasOverlayManager."""

    if TYPE_CHECKING:
        canvas: Any
        HANDLE_NONE: int
        HANDLE_TL: int
        HANDLE_TR: int
        HANDLE_BL: int
        HANDLE_BR: int
        _RESIZABLE_TYPES: tuple[str, ...]

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
            if ov.get("_deleted") or ov.get("_dirty"):
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
                if ov.get("_deleted") or ov.get("_dirty"):
                    return "CLAIMED"
                orig = ov.get("_orig_rect") or ov.get("_annot_bbox")
                cur = overlay_pdf_rect(ov, zoom=c._zoom or 1.0)
                if orig and cur and not rects_close(cur, orig, tol=0.75):
                    return "CLAIMED"
                return ov
            orig = ov.get("_orig_rect") or ov.get("_annot_bbox")
            if orig and rects_close(orig, annot.rect, tol=0.75):
                if ov.get("_deleted") or ov.get("_dirty"):
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

        annot_candidates: list[tuple[float, object]] = []
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
                pix = annot.get_pixmap(dpi=200, alpha=True)  # type: ignore[attr-defined]
                fd, tmp_path = tempfile.mkstemp(prefix="PDF_Editor_annot_", suffix=".png")
                os.close(fd)
                pix.save(tmp_path)
                ar = fitz.Rect(annot.rect)  # type: ignore[attr-defined]
                entry = {
                    "type": "image",
                    "page": page_idx,
                    "rect": ar,
                    "path": tmp_path,
                    "_existing": True,
                    "_existing_annot": getattr(annot, "xref", None),
                    "_orig_rect": [ar.x0, ar.y0, ar.x1, ar.y1],
                    "_annot_type": annot.type[0] if isinstance(annot.type, (tuple, list)) else annot.type,  # type: ignore[attr-defined]
                    "_bg_color": _sample_bg_color(page, ar),
                    "_dirty": False,
                    "_temp_file": True,
                }
                c._overlays.append(entry)
                return entry
            except Exception:
                pass

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
            "_items_rect": [r.x0, r.y0, r.x1, r.y1],
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