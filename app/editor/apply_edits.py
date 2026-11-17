# app/editor/apply_edits.py
"""PDF_Editor – Two-phase edit-application dispatcher supporting moving/deleting images, annotations, vector drawings, text, stamps, and highlights."""

import os
import logging
from dataclasses import dataclass, field

from app.editor.text_reinsert import (
    _build_embed_archive,
    _reinsert_edited_text,
    _text_edit_redaction_rect,
    _text_edit_size,
)

_log = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Outcome of apply_pending_edits."""

    text_fit_warnings: list = field(default_factory=list)
    embedded_font: bool = False


def _scale_drawing_pt(pt: tuple[float, float], orig_r, new_r) -> tuple[float, float]:
    sx = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
    sy = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
    return (
        new_r.x0 + (float(pt[0]) - orig_r.x0) * sx,
        new_r.y0 + (float(pt[1]) - orig_r.y0) * sy,
    )


def _insert_drawing_overlay(fitz, pg, e: dict) -> None:
    new_r = fitz.Rect(e["rect"])
    orig_raw = e.get("_orig_rect")
    orig_r = fitz.Rect(orig_raw) if orig_raw else fitz.Rect(new_r)

    shape = pg.new_shape()
    for item in e.get("items", []):
        op = item[0]
        if op == "l":
            p1 = fitz.Point(*_scale_drawing_pt(item[1], orig_r, new_r))
            p2 = fitz.Point(*_scale_drawing_pt(item[2], orig_r, new_r))
            shape.draw_line(p1, p2)
        elif op == "re":
            rx0, ry0, rx1, ry1 = item[1]
            tl = _scale_drawing_pt((rx0, ry0), orig_r, new_r)
            br = _scale_drawing_pt((rx1, ry1), orig_r, new_r)
            shape.draw_rect(fitz.Rect(tl[0], tl[1], br[0], br[1]).normalize())
        elif op == "qu":
            pts = [fitz.Point(*_scale_drawing_pt(pt, orig_r, new_r)) for pt in item[1]]
            if len(pts) == 4:
                shape.draw_quad(fitz.Quad(pts[0], pts[1], pts[3], pts[2]))
        elif op == "c":
            p1 = fitz.Point(*_scale_drawing_pt(item[1], orig_r, new_r))
            p2 = fitz.Point(*_scale_drawing_pt(item[2], orig_r, new_r))
            p3 = fitz.Point(*_scale_drawing_pt(item[3], orig_r, new_r))
            p4 = fitz.Point(*_scale_drawing_pt(item[4], orig_r, new_r))
            shape.draw_bezier(p1, p2, p3, p4)

    sx = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
    sy = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
    avg_scale = (sx + sy) * 0.5

    stroke_col = e.get("color")
    fill_col = e.get("fill")
    stroke_rgb = tuple(float(x) for x in stroke_col[:3]) if stroke_col else None
    fill_rgb = tuple(float(x) for x in fill_col[:3]) if fill_col else None

    finish_kw: dict = {
        "color": stroke_rgb,
        "fill": fill_rgb,
        "width": max(0.5, float(e.get("width", 1.0)) * avg_scale),
        "even_odd": bool(e.get("even_odd", False)),
        "closePath": bool(e.get("closePath", False)),
    }
    if e.get("fill_opacity") is not None:
        finish_kw["fill_opacity"] = max(0.0, min(1.0, float(e["fill_opacity"])))
    if e.get("stroke_opacity") is not None:
        finish_kw["stroke_opacity"] = max(0.0, min(1.0, float(e["stroke_opacity"])))

    lc = e.get("lineCap")
    if isinstance(lc, (tuple, list)) and lc:
        finish_kw["lineCap"] = int(max(lc))
    elif isinstance(lc, int):
        finish_kw["lineCap"] = lc

    lj = e.get("lineJoin")
    if isinstance(lj, int):
        finish_kw["lineJoin"] = lj

    dashes = e.get("dashes")
    if isinstance(dashes, str) and dashes.strip() and not dashes.strip().startswith("[]"):
        finish_kw["dashes"] = dashes

    shape.finish(**finish_kw)
    shape.commit()


def _delete_existing_annot(fitz, pg, e: dict) -> bool:
    axref = e.get("_existing_annot")
    if axref:
        for annot in list(pg.annots() or []):
            if getattr(annot, "xref", None) == axref:
                pg.delete_annot(annot)
                return True
    target_bbox = e.get("_orig_rect") or e.get("_annot_bbox") or e.get("bbox")
    if target_bbox is not None:
        target_rect = fitz.Rect(target_bbox)
        target_type = e.get("_annot_type", e.get("annot_type"))
        for annot in list(pg.annots() or []):
            atype = annot.type[0] if isinstance(annot.type, (tuple, list)) else annot.type
            if target_type is not None and atype != target_type:
                continue
            if abs(annot.rect.x0 - target_rect.x0) < 1.5 and abs(annot.rect.y0 - target_rect.y0) < 1.5:
                pg.delete_annot(annot)
                return True
    return False


def apply_pending_edits(doc, pending, *, warn_fn=None) -> ApplyResult:
    """Apply pending edits to open doc in two phases so moved/deleted elements never collide or duplicate."""
    import fitz

    img_none = getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
    img_remove = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1)
    la_none = getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0)
    la_covered = getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 1)
    txt_remove = getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0)
    txt_none = getattr(fitz, "PDF_REDACT_TEXT_NONE", 1)

    result = ApplyResult()

    def _collect_warning(edit):
        result.text_fit_warnings.append(edit)
        if warn_fn is not None:
            warn_fn(edit)

    active_edits = [
        e for e in pending
        if not (
            e.get("_existing")
            and not e.get("_dirty")
            and not e.get("_deleted")
            and e.get("type") != "delete_annot"
        )
    ]

    # Pre-extract embedded font archives before Phase 1 redacts text from pages
    prebuilt_archives: dict[int, tuple] = {}
    for e in active_edits:
        if e.get("type") == "text_edit" and not e.get("_deleted"):
            new_txt = (e.get("new_text") or "").strip()
            if new_txt:
                pg = doc[e["page"]]
                prebuilt_archives[id(e)] = _build_embed_archive(fitz, doc, pg, e, new_txt)

    # Phase 1: Remove or redact all original existing elements being moved, edited, or deleted
    for e in active_edits:
        etype = e.get("type")
        pg = doc[e["page"]]

        if e.get("_existing"):
            if e.get("_existing_annot") or e.get("_annot_bbox"):
                _delete_existing_annot(fitz, pg, e)
                continue

            if etype in ("image", "signature") and e.get("_orig_rect"):
                xref = e.get("_existing_xref")
                removed_via_xref = False
                if xref:
                    try:
                        used_elsewhere = any(
                            op.number != pg.number and any(im[0] == xref for im in op.get_images())
                            for op in doc
                        )
                        same_pg_rects = pg.get_image_rects(xref)
                        if not used_elsewhere and len(same_pg_rects) <= 1 and hasattr(pg, "delete_image"):
                            pg.delete_image(xref)
                            removed_via_xref = True
                    except Exception:
                        removed_via_xref = False
                if not removed_via_xref:
                    try:
                        pg.add_redact_annot(fitz.Rect(e["_orig_rect"]), fill=False, cross_out=False)
                        pg.apply_redactions(images=img_remove, graphics=la_none, text=txt_none)
                    except Exception:
                        pg.add_redact_annot(fitz.Rect(e["_orig_rect"]), fill=(1, 1, 1))
                        pg.apply_redactions()
                continue

            if etype == "drawing" and e.get("_orig_rect"):
                pad = max(1.0, float(e.get("width", 1.0)) * 0.75 + 0.5)
                orig_r = fitz.Rect(e["_orig_rect"]) + fitz.Rect(-pad, -pad, pad, pad)
                try:
                    pg.add_redact_annot(orig_r, fill=False, cross_out=False)
                    pg.apply_redactions(images=img_none, graphics=la_covered, text=txt_none)
                except Exception:
                    pg.add_redact_annot(orig_r, fill=(1, 1, 1))
                    pg.apply_redactions()
                continue

        if etype == "text_edit":
            size = _text_edit_size(e)
            redact_r = _text_edit_redaction_rect(fitz, e, size)
            try:
                pg.add_redact_annot(redact_r, fill=False, cross_out=False)
                pg.apply_redactions(images=img_none, graphics=la_none, text=txt_remove)
            except Exception:
                pg.add_redact_annot(redact_r, fill=(1, 1, 1))
                pg.apply_redactions()

    # Phase 2: Insert all moved, edited, or newly created elements
    embedded_font = False
    for e in active_edits:
        if e.get("_deleted"):
            continue
        etype = e.get("type")
        pg = doc[e["page"]]

        if etype == "redact":
            pg.add_redact_annot(e["rect"], fill=e["fill"])
            pg.apply_redactions()

        elif etype == "check":
            r = fitz.Rect(e["rect"])
            w = r.width
            h = r.height
            p1 = fitz.Point(r.x0 + 0.16 * w, r.y0 + 0.52 * h)
            p2 = fitz.Point(r.x0 + 0.40 * w, r.y0 + 0.82 * h)
            p3 = fitz.Point(r.x0 + 0.86 * w, r.y0 + 0.18 * h)
            stroke_w = max(1.5, float(e.get("width", min(w, h) * 0.12)))
            color = e.get("color", (0, 0, 0))
            col = tuple(float(x) for x in color[:3]) if isinstance(color, (list, tuple)) and len(color) >= 3 else (0.0, 0.0, 0.0)
            shape = pg.new_shape()
            shape.draw_polyline([p1, p2, p3])
            shape.finish(color=col, width=stroke_w, lineCap=1, lineJoin=1)
            shape.commit()

        elif etype == "cross":
            r = fitz.Rect(e["rect"])
            w = r.width
            h = r.height
            p1 = fitz.Point(r.x0 + 0.20 * w, r.y0 + 0.20 * h)
            p2 = fitz.Point(r.x0 + 0.80 * w, r.y0 + 0.80 * h)
            p3 = fitz.Point(r.x0 + 0.80 * w, r.y0 + 0.20 * h)
            p4 = fitz.Point(r.x0 + 0.20 * w, r.y0 + 0.80 * h)
            stroke_w = max(1.5, float(e.get("width", min(w, h) * 0.12)))
            color = e.get("color", (0, 0, 0))
            col = tuple(float(x) for x in color[:3]) if isinstance(color, (list, tuple)) and len(color) >= 3 else (0.0, 0.0, 0.0)
            shape = pg.new_shape()
            shape.draw_line(p1, p2)
            shape.draw_line(p3, p4)
            shape.finish(color=col, width=stroke_w, lineCap=1, lineJoin=1)
            shape.commit()

        elif etype == "drawing":
            _insert_drawing_overlay(fitz, pg, e)

        elif etype == "text":
            fname = (e.get("font", "") or "").lower()
            if "times" in fname or "serif" in fname or "roman" in fname:
                fontname = "tiro"
            elif "mono" in fname or "courier" in fname or "consol" in fname:
                fontname = "cour"
            else:
                fontname = "helv"
            flags = int(e.get("flags", 0) or 0)
            if (flags & 16) or "bold" in fname:
                if fontname == "helv": fontname = "hebo"
                elif fontname == "tiro": fontname = "tibo"
                elif fontname == "cour": fontname = "cobo"
            if (flags & 2) or "italic" in fname or "oblique" in fname:
                if fontname == "helv": fontname = "heit"
                elif fontname == "hebo": fontname = "hebi"
                elif fontname == "tiro": fontname = "tiit"
                elif fontname == "tibo": fontname = "tibi"
                elif fontname == "cour": fontname = "coit"
                elif fontname == "cobo": fontname = "cobi"
            c = e.get("color", (0, 0, 0))
            if isinstance(c, (list, tuple)) and len(c) >= 3:
                color = tuple(float(x) for x in c[:3])
            else:
                color = (0.0, 0.0, 0.0)
            pg.insert_text(e["point"], e["text"], fontsize=e.get("size", 12),
                           color=color, fontname=fontname)

        elif etype in ("image", "signature"):
            path = e.get("path", "")
            if not path or not os.path.isfile(path):
                continue
            if path.lower().endswith((".svg", ".svgz")):
                try:
                    sdoc = fitz.open(path)
                    spix = sdoc[0].get_pixmap(dpi=300, alpha=True)
                    pg.insert_image(e["rect"], stream=spix.tobytes("png"))
                    sdoc.close()
                except Exception:
                    try:
                        from PySide6.QtSvg import QSvgRenderer
                        from PySide6.QtGui import QImage, QPainter
                        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
                        renderer = QSvgRenderer(path)
                        if renderer.isValid():
                            ds = renderer.defaultSize()
                            w = 1200
                            h = max(10, int(w * (ds.height() / ds.width()))) if ds.width() > 0 else 400
                            qimg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
                            qimg.fill(Qt.GlobalColor.transparent)
                            qp = QPainter(qimg)
                            renderer.render(qp)
                            qp.end()
                            ba = QByteArray()
                            buf = QBuffer(ba)
                            buf.open(QIODevice.OpenModeFlag.WriteOnly)
                            qimg.save(buf, b"PNG")
                            pg.insert_image(e["rect"], stream=ba.data())
                        else:
                            pg.insert_image(e["rect"], filename=path)
                    except Exception:
                        pg.insert_image(e["rect"], filename=path)
            else:
                pg.insert_image(e["rect"], filename=path)

        elif etype == "highlight":
            rects = e.get("rects")
            stroke_color = e.get("color", (1, 1, 0))
            if rects:
                for r_item in rects:
                    a = pg.add_highlight_annot(fitz.Rect(r_item))
                    a.set_colors(stroke=stroke_color)
                    a.update()
            elif "rect" in e:
                a = pg.add_highlight_annot(fitz.Rect(e["rect"]))
                a.set_colors(stroke=stroke_color)
                a.update()

        elif etype == "note":
            pg.add_text_annot(e["point"], e["text"])

        elif etype == "draw":
            stroke = [(float(x), float(y))
                      for x, y in e.get("points", [])]
            if len(stroke) >= 2:
                annot = pg.add_ink_annot([stroke])
                annot.set_colors(stroke=e.get("color", (1, 0, 0)))
                annot.set_border(width=max(1, int(e.get("width", 2))))
                annot.update()

        elif etype == "delete_annot":
            target_type = e.get("annot_type")
            target_bbox = e.get("bbox")
            if target_bbox is not None:
                target_rect = fitz.Rect(target_bbox)
                for annot in list(pg.annots() or []):
                    if (annot.type[0] == target_type
                            and abs(annot.rect.x0 - target_rect.x0) < 1
                            and abs(annot.rect.y0 - target_rect.y0) < 1):
                        pg.delete_annot(annot)
                        break

        elif etype == "text_edit":
            if _reinsert_edited_text(
                fitz, doc, pg, e,
                warn_fn=_collect_warning,
                redact=False,
                prebuilt_archive=prebuilt_archives.get(id(e)),
            ):
                embedded_font = True

    result.embedded_font = embedded_font
    if embedded_font:
        try:
            doc.subset_fonts()
        except Exception:
            _log.exception("subset_fonts after text edit failed")
    return result