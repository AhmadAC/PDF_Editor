# app/editor/overlay_detect.py
"""Overlay hit-testing, handle detection, spatial graphic grouping, and existing PDF object extraction mixin."""

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


def _is_backdrop_graphic(
    r: fitz.Rect,
    page_w: float,
    page_h: float,
    *,
    items: list | None = None,
    fill=None,
    color=None,
) -> bool:
    """Identify page-wide backgrounds, large border frames, and long thin divider lines
    that should remain standalone and not bridge separate pictures into one cluster."""
    area = max(0.0, r.width) * max(0.0, r.height)
    page_area = max(1.0, page_w * page_h)
    if r.width >= page_w * 0.85 and r.height >= page_h * 0.45:
        return True
    if area >= page_area * 0.35:
        return True
    if (r.width >= page_w * 0.70 and r.height <= 4.0) or (r.height >= page_h * 0.70 and r.width <= 4.0):
        return True
    if items is not None and fill is None and color is not None and r.width >= page_w * 0.40 and r.height >= page_h * 0.40 and len(items) <= 4:
        return True
    return False


def _cluster_graphic_items(
    raw_items: list[dict],
    tol: float = 1.5,
) -> list[list[dict]]:
    """Group non-backdrop graphic items whose bounding boxes touch or overlap (within tol pt)
    using Union-Find, while keeping backdrop items as standalone singletons."""
    if not raw_items:
        return []

    non_bd_indices = [i for i, it in enumerate(raw_items) if not it.get("is_backdrop")]
    bd_indices = [i for i, it in enumerate(raw_items) if it.get("is_backdrop")]

    clusters: list[list[dict]] = []

    if non_bd_indices:
        parent = {i: i for i in non_bd_indices}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        sorted_idx = sorted(non_bd_indices, key=lambda idx: raw_items[idx]["rect"].x0)
        n = len(sorted_idx)
        for a_pos in range(n):
            i = sorted_idx[a_pos]
            ri = raw_items[i]["rect"]
            limit_x = ri.x1 + tol * 2.0
            for b_pos in range(a_pos + 1, n):
                j = sorted_idx[b_pos]
                rj = raw_items[j]["rect"]
                if rj.x0 > limit_x:
                    break
                if not (ri.y1 + tol * 2.0 < rj.y0 or rj.y1 + tol * 2.0 < ri.y0):
                    union(i, j)

        comp_map: dict[int, list[dict]] = {}
        for i in non_bd_indices:
            root = find(i)
            comp_map.setdefault(root, []).append(raw_items[i])

        for comp in comp_map.values():
            comp.sort(key=lambda it: it["order"])
            clusters.append(comp)

    for i in bd_indices:
        clusters.append([raw_items[i]])

    clusters.sort(key=lambda cl: cl[0]["order"])
    return clusters


def _format_group_name(children: list[dict], union_rect: fitz.Rect) -> str:
    rw = int(round(union_rect.width))
    rh = int(round(union_rect.height))
    types = {ch.get("type") for ch in children}
    if types == {"image"}:
        return f"Image Group ({len(children)} parts, {rw}×{rh})"
    if types == {"drawing"}:
        return f"Graphic Group ({len(children)} shapes, {rw}×{rh})"
    return f"Picture Group ({len(children)} objects, {rw}×{rh})"


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

    def _iter_page_existing_entries(self, page_idx: int):
        """Yield (top_level_overlay, item_overlay) pairs for _existing items on page_idx,
        descending into any 'group' overlays' children."""
        c = self.canvas
        for ov in c._overlays:
            if ov.get("page") != page_idx:
                continue
            if ov.get("type") == "group":
                for ch in ov.get("children", []):
                    if ch.get("_existing"):
                        yield ov, ch
            elif ov.get("_existing"):
                yield ov, ov

    def _is_existing_rect_claimed(
        self,
        page_idx: int,
        rect: fitz.Rect,
        *,
        src_draw_idx: int | None = None,
        xref: int | None = None,
    ) -> dict | str | None:
        """Check if an existing PDF object at rect (or inside a group) has already been loaded, moved, or deleted."""
        c = self.canvas
        for top_ov, item_ov in self._iter_page_existing_entries(page_idx):
            matched = False
            if src_draw_idx is not None and item_ov.get("_src_draw_idx") is not None:
                matched = (item_ov.get("_src_draw_idx") == src_draw_idx)
            elif xref is not None and item_ov.get("_existing_xref") is not None:
                orig = item_ov.get("_orig_rect") or item_ov.get("_annot_bbox")
                matched = (item_ov.get("_existing_xref") == xref and rects_close(orig, rect, tol=0.75))
            else:
                orig = item_ov.get("_orig_rect") or item_ov.get("_annot_bbox")
                matched = bool(orig is not None and rects_close(orig, rect, tol=0.75))

            if not matched:
                continue

            if top_ov.get("_deleted") or top_ov.get("_dirty") or item_ov.get("_deleted") or item_ov.get("_dirty"):
                return "CLAIMED"
            orig_check = item_ov.get("_orig_rect") or item_ov.get("_annot_bbox")
            cur = overlay_pdf_rect(item_ov, zoom=c._zoom or 1.0)
            if orig_check is not None and cur is not None and not rects_close(cur, orig_check, tol=0.75):
                return "CLAIMED"
            return top_ov
        return None

    def _is_annot_claimed(self, page_idx: int, annot) -> dict | str | None:
        c = self.canvas
        axref = getattr(annot, "xref", None)
        for top_ov, item_ov in self._iter_page_existing_entries(page_idx):
            if axref and item_ov.get("_existing_annot") == axref:
                if top_ov.get("_deleted") or top_ov.get("_dirty") or item_ov.get("_deleted") or item_ov.get("_dirty"):
                    return "CLAIMED"
                orig = item_ov.get("_orig_rect") or item_ov.get("_annot_bbox")
                cur = overlay_pdf_rect(item_ov, zoom=c._zoom or 1.0)
                if orig and cur and not rects_close(cur, orig, tol=0.75):
                    return "CLAIMED"
                return top_ov
            orig = item_ov.get("_orig_rect") or item_ov.get("_annot_bbox")
            if orig and rects_close(orig, annot.rect, tol=0.75):
                if top_ov.get("_deleted") or top_ov.get("_dirty") or item_ov.get("_deleted") or item_ov.get("_dirty"):
                    return "CLAIMED"
                cur = overlay_pdf_rect(item_ov, zoom=c._zoom or 1.0)
                if cur and not rects_close(cur, orig, tol=0.75):
                    return "CLAIMED"
                return top_ov
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

    def _collect_unclaimed_graphics(
        self, page, page_idx: int, *, include_full_page_drawings: bool = True
    ) -> list[dict]:
        """Collect all unclaimed raster images and vector drawings on page_idx in PDF paint order."""
        pw = max(1.0, float(page.rect.width))
        ph = max(1.0, float(page.rect.height))
        raw_items: list[dict] = []
        order_counter = 0

        # Raster images
        try:
            for img_info in page.get_images(full=True) or []:
                xref = int(img_info[0])
                smask = int(img_info[1]) if len(img_info) > 1 else 0
                if xref <= 0:
                    continue
                rects = page.get_image_rects(xref) or []
                for r in rects:
                    rr = fitz.Rect(r)
                    if rr.width < 2 or rr.height < 2:
                        continue
                    if self._is_existing_rect_claimed(page_idx, rr, xref=xref) is not None:
                        continue
                    raw_items.append({
                        "kind": "image",
                        "rect": rr,
                        "xref": xref,
                        "smask": smask,
                        "order": order_counter,
                        "is_backdrop": _is_backdrop_graphic(rr, pw, ph),
                    })
                    order_counter += 1
        except Exception:
            pass

        # Vector drawings
        try:
            for d_idx, d in enumerate(page.get_drawings() or []):
                if d.get("fill") is None and d.get("color") is None:
                    continue
                r = fitz.Rect(d["rect"]).normalize()
                max_w_ratio = 0.98 if include_full_page_drawings else 0.90
                max_h_ratio = 0.98 if include_full_page_drawings else 0.90
                if r.width >= pw * max_w_ratio and r.height >= ph * max_h_ratio:
                    continue
                if r.width < 2.0 and r.height < 2.0:
                    continue
                if self._is_existing_rect_claimed(page_idx, r, src_draw_idx=d_idx) is not None:
                    continue
                items = _serialize_drawing_items(d.get("items", []))
                if not items:
                    continue
                raw_items.append({
                    "kind": "drawing",
                    "rect": r,
                    "d": d,
                    "d_idx": d_idx,
                    "items": items,
                    "order": order_counter,
                    "is_backdrop": _is_backdrop_graphic(
                        r, pw, ph, items=items, fill=d.get("fill"), color=d.get("color")
                    ),
                })
                order_counter += 1
        except Exception:
            pass

        return raw_items

    def _materialize_graphic_item(self, doc, page, page_idx: int, item: dict) -> dict | None:
        """Convert a raw image or drawing descriptor into a full overlay dict."""
        kind = item["kind"]
        r: fitz.Rect = item["rect"]
        if kind == "image":
            try:
                tmp_path = self._extract_image_temp_png(doc, item["xref"], item["smask"])
                return {
                    "type": "image",
                    "page": page_idx,
                    "rect": fitz.Rect(r),
                    "path": tmp_path,
                    "_existing": True,
                    "_existing_xref": item["xref"],
                    "_orig_rect": [r.x0, r.y0, r.x1, r.y1],
                    "_bg_color": _sample_bg_color(page, r),
                    "_dirty": False,
                    "_temp_file": True,
                    "_name": f"Image ({int(r.width)}×{int(r.height)})",
                }
            except Exception:
                return None

        d = item["d"]
        fill_col = tuple(float(x) for x in d["fill"][:3]) if d.get("fill") else None
        stroke_col = tuple(float(x) for x in d["color"][:3]) if d.get("color") else None
        return {
            "type": "drawing",
            "page": page_idx,
            "rect": fitz.Rect(r),
            "items": item["items"],
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
            "_src_draw_idx": item["d_idx"],
            "_orig_rect": [r.x0, r.y0, r.x1, r.y1],
            "_items_rect": [r.x0, r.y0, r.x1, r.y1],
            "_bg_color": _sample_bg_color(page, r),
            "_dirty": False,
            "_name": f"Shape ({int(r.width)}×{int(r.height)})",
        }

    def _materialize_cluster(self, doc, page, page_idx: int, cluster: list[dict]) -> dict | None:
        """Convert a cluster of raw graphic items into either a single overlay (size 1) or a 'group' overlay (size >= 2)."""
        children: list[dict] = []
        for item in cluster:
            ov = self._materialize_graphic_item(doc, page, page_idx, item)
            if ov is not None:
                children.append(ov)
        if not children:
            return None
        if len(children) == 1:
            return children[0]

        union_r = fitz.Rect(children[0]["rect"])
        for ch in children[1:]:
            union_r |= ch["rect"]

        return {
            "type": "group",
            "page": page_idx,
            "rect": fitz.Rect(union_r),
            "children": children,
            "opacity": 1.0,
            "_existing": True,
            "_orig_rect": [union_r.x0, union_r.y0, union_r.x1, union_r.y1],
            "_dirty": False,
            "_name": _format_group_name(children, union_r),
        }

    def scan_page_objects(self, page_idx: int) -> list[dict]:
        """Scan and detect all existing objects (annotations, clustered graphics/images, and text blocks)
        on page_idx and add any un-tracked objects into self.canvas._overlays."""
        c = self.canvas
        if not c._doc or not (0 <= page_idx < c._doc.page_count):
            return []
        page = c._doc[page_idx]

        # 1. Annotations
        try:
            for annot in page.annots() or []:
                atype = annot.type[0] if isinstance(annot.type, (tuple, list)) else annot.type
                if atype in (_PDF_ANNOT_LINK, _PDF_ANNOT_WIDGET, _PDF_ANNOT_REDACT):
                    continue
                if atype == _PDF_ANNOT_TEXT:
                    continue
                ar = fitz.Rect(annot.rect)
                if self._is_annot_claimed(page_idx, annot) is not None:
                    continue
                if self._is_existing_rect_claimed(page_idx, ar) is not None:
                    continue
                try:
                    pix = annot.get_pixmap(dpi=200, alpha=True)
                    fd, tmp_path = tempfile.mkstemp(prefix="PDF_Editor_annot_", suffix=".png")
                    os.close(fd)
                    pix.save(tmp_path)
                    entry = {
                        "type": "image",
                        "page": page_idx,
                        "rect": fitz.Rect(ar),
                        "path": tmp_path,
                        "_existing": True,
                        "_existing_annot": getattr(annot, "xref", None),
                        "_orig_rect": [ar.x0, ar.y0, ar.x1, ar.y1],
                        "_annot_type": atype,
                        "_bg_color": _sample_bg_color(page, ar),
                        "_dirty": False,
                        "_temp_file": True,
                        "_name": f"Annotation ({int(ar.width)}×{int(ar.height)})",
                    }
                    c._overlays.append(entry)
                except Exception:
                    pass
        except Exception:
            pass

        # 2 & 3. Clustered Raster Images & Vector Drawings
        raw_graphics = self._collect_unclaimed_graphics(page, page_idx, include_full_page_drawings=True)
        clusters = _cluster_graphic_items(raw_graphics, tol=1.5)
        for cl in clusters:
            ov = self._materialize_cluster(c._doc, page, page_idx, cl)
            if ov is not None:
                c._overlays.append(ov)

        # 4. Text Blocks
        try:
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                spans = [
                    s for line in block.get("lines", [])
                    for s in line.get("spans", [])
                    if s.get("text", "").strip()
                ]
                if not spans:
                    continue
                full_txt = " ".join(s["text"].strip() for s in spans if s["text"].strip())
                if not full_txt:
                    continue
                bb = list(block.get("bbox", [0, 0, 0, 0]))
                r = fitz.Rect(bb)
                if r.width < 2.0 or r.height < 2.0:
                    continue
                if self._is_existing_rect_claimed(page_idx, r) is not None:
                    continue
                first_s = spans[0]
                sz = float(first_s.get("size") or first_s.get("font_size") or 12.0)
                font_name = first_s.get("font") or "Helvetica"
                color = first_s.get("color", 0)
                flags = int(first_s.get("flags", 0) or 0)
                origin = list(first_s.get("origin", (bb[0], bb[3])))
                short_txt = full_txt.replace("\n", " ").strip()
                disp_txt = f"\"{short_txt[:20]}...\"" if len(short_txt) > 20 else f"\"{short_txt}\""
                entry = {
                    "type": "text_edit",
                    "page": page_idx,
                    "bbox": bb,
                    "old_text": full_txt,
                    "new_text": full_txt,
                    "size": sz,
                    "font_size": sz,
                    "color": color,
                    "font": font_name,
                    "flags": flags,
                    "ascender": float(first_s.get("ascender") or 0.8),
                    "descender": float(first_s.get("descender") or -0.2),
                    "origin": origin,
                    "_orig_origin": list(origin),
                    "_existing": True,
                    "_existing_span": True,
                    "_orig_rect": list(bb),
                    "_bg_color": _sample_bg_color(page, r),
                    "_dirty": False,
                    "_name": f"Text: {disp_txt}",
                }
                c._overlays.append(entry)
        except Exception:
            pass

        return [o for o in c._overlays if o.get("page", 0) == page_idx and not o.get("_deleted")]

    def _detect_graphic_cluster_at(
        self, page_idx: int, pdf_pt: fitz.Point, *, kinds: set[str] | None = None
    ) -> dict | None:
        """Detect an unclaimed graphic (or clustered picture group) at pdf_pt."""
        c = self.canvas
        if not c._doc or not (0 <= page_idx < c._doc.page_count):
            return None
        page = c._doc[page_idx]
        raw_graphics = self._collect_unclaimed_graphics(page, page_idx, include_full_page_drawings=False)
        if not raw_graphics:
            return None

        clusters = _cluster_graphic_items(raw_graphics, tol=1.5)
        pad_rect = fitz.Rect(-3.0, -3.0, 3.0, 3.0)

        candidates: list[tuple[float, list[dict]]] = []
        for cl in clusters:
            if kinds is not None and not any(it["kind"] in kinds for it in cl):
                continue
            hit = any((it["rect"] + pad_rect).contains(pdf_pt) for it in cl)
            if not hit:
                continue
            union_r = fitz.Rect(cl[0]["rect"])
            for it in cl[1:]:
                union_r |= it["rect"]
            area = max(1.0, union_r.width) * max(1.0, union_r.height)
            candidates.append((area, cl))

        if not candidates:
            return None

        candidates.sort(key=lambda pair: pair[0])
        chosen_cluster = candidates[0][1]
        ov = self._materialize_cluster(c._doc, page, page_idx, chosen_cluster)
        if ov is not None:
            c._overlays.append(ov)
        return ov

    def detect_existing_media_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        """Detect existing annotations or raster images (or image-containing graphic groups) at pdf_pt."""
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
                    "_name": f"Annotation ({int(ar.width)}×{int(ar.height)})",
                }
                c._overlays.append(entry)
                return entry
            except Exception:
                pass

        return self._detect_graphic_cluster_at(page_idx, pdf_pt, kinds={"image"})

    def detect_existing_drawing_at(self, page_idx: int, pdf_pt: fitz.Point) -> dict | None:
        """Detect existing vector graphics/shapes (grouped into picture clusters by default) at pdf_pt."""
        return self._detect_graphic_cluster_at(page_idx, pdf_pt, kinds={"drawing"})

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
        txt = span.get("text", "")
        short_txt = txt.replace("\n", " ").strip()
        disp_txt = f"\"{short_txt[:20]}...\"" if len(short_txt) > 20 else f"\"{short_txt}\""
        entry = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": bb,
            "old_text": txt,
            "new_text": txt,
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
            "_name": f"Text: {disp_txt}",
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