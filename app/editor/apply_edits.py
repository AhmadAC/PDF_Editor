# app/editor/apply_edits.py
"""PDF_Editor – Two-phase edit-application dispatcher supporting grouped objects, z-order, opacity, rotation, flip, and batched surgical image/drawing removal."""

import io
import os
import re
import logging
from dataclasses import dataclass, field

from app.editor.text_reinsert import (
    _build_embed_archive,
    _reinsert_edited_text,
    _text_edit_redaction_rect,
    _text_edit_size,
)
from app.utils import find_system_font_file

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


def _build_transform_matrix(fitz, rotation: int = 0, flip_h: bool = False, flip_v: bool = False):
    """Build a 2x2 linear fitz.Matrix for clockwise rotation followed by horizontal/vertical flip."""
    rot = int(rotation) % 360
    if rot == 90:
        a, b, c, d = 0.0, 1.0, -1.0, 0.0
    elif rot == 180:
        a, b, c, d = -1.0, 0.0, 0.0, -1.0
    elif rot == 270:
        a, b, c, d = 0.0, -1.0, 1.0, 0.0
    else:
        a, b, c, d = 1.0, 0.0, 0.0, 1.0

    if flip_h:
        a, c = -a, -c
    if flip_v:
        b, d = -b, -d
    return fitz.Matrix(a, b, c, d, 0.0, 0.0)


def _prepare_image_bytes(
    fitz,
    path: str,
    rotation: int = 0,
    flip_h: bool = False,
    flip_v: bool = False,
    opacity: float = 1.0,
) -> bytes | None:
    """Return PNG byte stream if SVG or image transforms (rotate/flip/opacity) are needed, else None."""
    rot = int(rotation) % 360
    op = max(0.0, min(1.0, float(opacity)))
    needs_transform = bool(rot != 0 or flip_h or flip_v or op < 0.999)
    is_svg = path.lower().endswith((".svg", ".svgz"))

    raw_bytes: bytes | None = None
    if is_svg:
        try:
            sdoc = fitz.open(path)
            spix = sdoc[0].get_pixmap(dpi=300, alpha=True)
            raw_bytes = spix.tobytes("png")
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
                    raw_bytes = bytes(ba.data())
            except Exception:
                raw_bytes = None

    if not needs_transform:
        return raw_bytes

    try:
        from PIL import Image
        if raw_bytes is not None:
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
        else:
            img = Image.open(path).convert("RGBA")

        if rot != 0:
            img = img.rotate(-rot, expand=True)
        if flip_h:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if flip_v:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if op < 0.999:
            r_c, g_c, b_c, a_c = img.split()
            a_c = a_c.point(lambda a: int(round(a * op)))
            img.putalpha(a_c)

        out_buf = io.BytesIO()
        img.save(out_buf, format="PNG")
        return out_buf.getvalue()
    except Exception:
        _log.exception("Failed to transform image %s", path)
        return raw_bytes


def _remove_single_image_do_from_page(doc, pg, xref: int) -> bool:
    """Remove a single /Name Do image invocation directly from page content streams
    so other pages sharing xref and overlapping background images stay intact."""
    try:
        infos = [im for im in pg.get_images(full=True) if im[0] == xref]
        if len(infos) != 1:
            return False
        if len(pg.get_image_rects(xref)) != 1:
            return False
        img_name = infos[0][7]
        ref_xref = infos[0][9] if len(infos[0]) > 9 else 0
        if not img_name or ref_xref != 0:
            return False

        pattern = re.compile(rb"/" + re.escape(img_name.encode("latin-1")) + rb"\s+Do\b")
        contents_xrefs = pg.get_contents() or []
        matches: list[tuple[int, bytes, int]] = []
        total_hits = 0
        for cxref in contents_xrefs:
            stream = doc.xref_stream(cxref)
            if not stream:
                continue
            hits = len(pattern.findall(stream))
            if hits > 0:
                total_hits += hits
                matches.append((cxref, stream, hits))

        if total_hits != 1 or len(matches) != 1:
            return False

        cxref, stream, _ = matches[0]
        new_stream = pattern.sub(b"", stream, count=1)
        doc.update_stream(cxref, new_stream)
        return True
    except Exception:
        return False


def _insert_drawing_overlay(fitz, pg, e: dict) -> None:
    new_r = fitz.Rect(e["rect"])
    orig_raw = e.get("_items_rect") or e.get("_orig_rect")
    orig_r = fitz.Rect(orig_raw) if orig_raw else fitz.Rect(new_r)
    op = max(0.0, min(1.0, float(e.get("opacity", 1.0))))
    overlay_flag = not bool(e.get("_z_back", False))

    shape = pg.new_shape()
    for item in e.get("items", []):
        op_code = item[0]
        if op_code == "l":
            p1 = fitz.Point(*_scale_drawing_pt(item[1], orig_r, new_r))
            p2 = fitz.Point(*_scale_drawing_pt(item[2], orig_r, new_r))
            shape.draw_line(p1, p2)
        elif op_code == "re":
            rx0, ry0, rx1, ry1 = item[1]
            tl = _scale_drawing_pt((rx0, ry0), orig_r, new_r)
            br = _scale_drawing_pt((rx1, ry1), orig_r, new_r)
            shape.draw_rect(fitz.Rect(tl[0], tl[1], br[0], br[1]).normalize())
        elif op_code == "qu":
            pts = [fitz.Point(*_scale_drawing_pt(pt, orig_r, new_r)) for pt in item[1]]
            if len(pts) == 4:
                shape.draw_quad(fitz.Quad(pts[0], pts[1], pts[3], pts[2]))
        elif op_code == "c":
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

    base_fo = float(e["fill_opacity"]) if e.get("fill_opacity") is not None else 1.0
    base_so = float(e["stroke_opacity"]) if e.get("stroke_opacity") is not None else 1.0

    finish_kw: dict = {
        "color": stroke_rgb,
        "fill": fill_rgb,
        "width": max(0.5, float(e.get("width", 1.0)) * avg_scale),
        "even_odd": bool(e.get("even_odd", False)),
        "closePath": bool(e.get("closePath", False)),
        "fill_opacity": max(0.0, min(1.0, base_fo * op)),
        "stroke_opacity": max(0.0, min(1.0, base_so * op)),
    }

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
    shape.commit(overlay=overlay_flag)


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


def _flatten_active_edits(
    edits: list[dict],
    *,
    parent_op: float = 1.0,
    parent_deleted: bool = False,
    parent_dirty: bool = False,
    parent_z_back: bool = False,
    parent_visible: bool = True,
    parent_page: int | None = None,
) -> list[dict]:
    """Expand 'group' overlays recursively into flat child edits while propagating group properties."""
    flat: list[dict] = []
    for e in edits:
        if (
            e.get("_existing")
            and not e.get("_dirty")
            and not e.get("_deleted")
            and not parent_dirty
            and not parent_deleted
            and e.get("type") != "delete_annot"
        ):
            continue

        if e.get("type") == "group":
            g_op = max(0.0, min(1.0, float(e.get("opacity", 1.0)) * parent_op))
            g_del = bool(e.get("_deleted") or parent_deleted)
            g_dirty = bool(e.get("_dirty") or parent_dirty)
            g_z_back = bool(e.get("_z_back") or parent_z_back)
            g_vis = bool(e.get("_visible", True) and parent_visible)
            g_page = e.get("page") if e.get("page") is not None else parent_page
            flat.extend(
                _flatten_active_edits(
                    e.get("children", []),
                    parent_op=g_op,
                    parent_deleted=g_del,
                    parent_dirty=g_dirty,
                    parent_z_back=g_z_back,
                    parent_visible=g_vis,
                    parent_page=g_page,
                )
            )
            continue

        c_copy = dict(e)
        if parent_page is not None and c_copy.get("page") is None:
            c_copy["page"] = parent_page
        if parent_deleted:
            c_copy["_deleted"] = True
        if parent_dirty:
            c_copy["_dirty"] = True
        if parent_z_back:
            c_copy["_z_back"] = True
        if not parent_visible:
            c_copy["_visible"] = False
        c_copy["opacity"] = max(0.0, min(1.0, float(c_copy.get("opacity", 1.0)) * parent_op))
        flat.append(c_copy)
    return flat


def _apply_batched_redactions(pg, rect_bg_pairs: list[tuple], *, images: int, graphics: int, text: int) -> None:
    if not rect_bg_pairs:
        return
    try:
        for rect, _bg in rect_bg_pairs:
            pg.add_redact_annot(rect, fill=False, cross_out=False)
        pg.apply_redactions(images=images, graphics=graphics, text=text)
    except Exception:
        for rect, bg_col in rect_bg_pairs:
            fill_col = tuple(c / 255.0 for c in bg_col) if bg_col else (1.0, 1.0, 1.0)
            pg.add_redact_annot(rect, fill=fill_col)
            pg.apply_redactions()


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

    active_edits = _flatten_active_edits(pending)

    print(f"[DEBUG] [apply_pending_edits] Total active edits to apply: {len(active_edits)}", flush=True)

    # Pre-extract embedded font archives before Phase 1 redacts text from pages
    prebuilt_archives: dict[int, tuple] = {}
    for e in active_edits:
        if e.get("type") == "text_edit" and not e.get("_deleted") and e.get("_visible", True):
            new_txt = (e.get("new_text") or "").strip()
            if new_txt:
                p_idx = min(len(doc) - 1, max(0, e.get("page", 0)))
                pg = doc[p_idx]
                prebuilt_archives[id(e)] = _build_embed_archive(fitz, doc, pg, e, new_txt)

    # Phase 1: Remove or redact all original existing elements being moved, edited, hidden, or deleted
    img_redactions: dict[int, list[tuple]] = {}
    draw_redactions: dict[int, list[tuple]] = {}
    text_redactions: dict[int, list[tuple]] = {}

    for e in active_edits:
        etype = e.get("type")
        p_idx = min(len(doc) - 1, max(0, e.get("page", 0)))
        pg = doc[p_idx]

        if e.get("_existing"):
            if e.get("_existing_annot") or e.get("_annot_bbox"):
                _delete_existing_annot(fitz, pg, e)
                print(f"[DEBUG] [apply_pending_edits] Phase 1: Deleted existing annotation on page {p_idx + 1}", flush=True)
                continue

            if etype in ("image", "signature") and e.get("_orig_rect"):
                xref = e.get("_existing_xref")
                orig_r = fitz.Rect(e["_orig_rect"])

                if not xref or not any(im[0] == xref for im in pg.get_images()):
                    for im in pg.get_images():
                        for ir in pg.get_image_rects(im[0]):
                            if abs(ir.x0 - orig_r.x0) < 2.5 and abs(ir.y0 - orig_r.y0) < 2.5:
                                xref = im[0]
                                break
                        if xref and any(im[0] == xref for im in pg.get_images()):
                            break

                removed_cleanly = False
                if xref:
                    try:
                        used_elsewhere = any(
                            op.number != pg.number and any(im[0] == xref for im in op.get_images())
                            for op in doc
                        )
                        same_pg_rects = pg.get_image_rects(xref)
                        if not used_elsewhere and len(same_pg_rects) <= 1 and hasattr(pg, "delete_image"):
                            pg.delete_image(xref)
                            removed_cleanly = True
                            print(f"[DEBUG] [apply_pending_edits] Phase 1: Image xref {xref} deleted cleanly from page {p_idx + 1}", flush=True)
                        else:
                            removed_cleanly = _remove_single_image_do_from_page(doc, pg, xref)
                            print(f"[DEBUG] [apply_pending_edits] Phase 1: Image xref {xref} /Do removal={removed_cleanly} from page {p_idx + 1}", flush=True)
                    except Exception as err:
                        print(f"[DEBUG] [apply_pending_edits] Phase 1: Image deletion error: {err}", flush=True)
                        removed_cleanly = False
                if not removed_cleanly:
                    img_redactions.setdefault(p_idx, []).append((orig_r, e.get("_bg_color")))
                continue

            if etype == "drawing" and e.get("_orig_rect"):
                pad = max(1.0, float(e.get("width", 1.0)) * 0.75 + 0.5)
                orig_r = fitz.Rect(e["_orig_rect"]) + fitz.Rect(-pad, -pad, pad, pad)
                draw_redactions.setdefault(p_idx, []).append((orig_r, e.get("_bg_color")))
                continue

        if etype == "text_edit":
            size = _text_edit_size(e)
            redact_r = _text_edit_redaction_rect(fitz, e, size)
            text_redactions.setdefault(p_idx, []).append((redact_r, e.get("_bg_color")))

    for p_idx, pairs in img_redactions.items():
        _apply_batched_redactions(doc[p_idx], pairs, images=img_remove, graphics=la_none, text=txt_none)
    for p_idx, pairs in draw_redactions.items():
        _apply_batched_redactions(doc[p_idx], pairs, images=img_none, graphics=la_covered, text=txt_none)
    for p_idx, pairs in text_redactions.items():
        _apply_batched_redactions(doc[p_idx], pairs, images=img_none, graphics=la_none, text=txt_remove)

    # Phase 2: Insert back-layer items first (in reverse so index 0 ends up furthest back), then foreground items in order
    visible_edits = [e for e in active_edits if not e.get("_deleted") and e.get("_visible", True)]
    back_edits = [e for e in visible_edits if e.get("_z_back")]
    front_edits = [e for e in visible_edits if not e.get("_z_back")]
    ordered_phase2 = list(reversed(back_edits)) + front_edits

    embedded_font = False
    for e in ordered_phase2:
        etype = e.get("type")
        p_idx = min(len(doc) - 1, max(0, e.get("page", 0)))
        pg = doc[p_idx]
        op = max(0.0, min(1.0, float(e.get("opacity", 1.0))))
        overlay_flag = not bool(e.get("_z_back", False))
        rot = int(e.get("rotation", 0)) % 360
        flip_h = bool(e.get("flip_h", False))
        flip_v = bool(e.get("flip_v", False))

        if etype == "redact":
            pg.add_redact_annot(e["rect"], fill=e["fill"])
            pg.apply_redactions()
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted redaction on page {p_idx + 1}", flush=True)

        elif etype == "check":
            r = fitz.Rect(e["rect"])
            w = r.width
            h = r.height
            pts = [
                (r.x0 + 0.16 * w, r.y0 + 0.52 * h),
                (r.x0 + 0.40 * w, r.y0 + 0.82 * h),
                (r.x0 + 0.86 * w, r.y0 + 0.18 * h),
            ]
            if rot or flip_h or flip_v:
                cx, cy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
                mat = _build_transform_matrix(fitz, rot, flip_h, flip_v)
                fpts = []
                for px, py in pts:
                    v = fitz.Point(px - cx, py - cy) * mat
                    fpts.append(fitz.Point(cx + v.x, cy + v.y))
            else:
                fpts = [fitz.Point(px, py) for px, py in pts]
            stroke_w = max(1.5, float(e.get("width", min(w, h) * 0.12)))
            color = e.get("color", (0, 0, 0))
            col = tuple(float(x) for x in color[:3]) if isinstance(color, (list, tuple)) and len(color) >= 3 else (0.0, 0.0, 0.0)
            shape = pg.new_shape()
            shape.draw_polyline(fpts)
            shape.finish(color=col, width=stroke_w, stroke_opacity=op, lineCap=1, lineJoin=1)
            shape.commit(overlay=overlay_flag)
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted checkmark on page {p_idx + 1}", flush=True)

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
            shape.finish(color=col, width=stroke_w, stroke_opacity=op, lineCap=1, lineJoin=1)
            shape.commit(overlay=overlay_flag)
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted crossmark on page {p_idx + 1}", flush=True)

        elif etype == "drawing":
            _insert_drawing_overlay(fitz, pg, e)

        elif etype == "text":
            font_req = str(e.get("font", "Helvetica") or "Helvetica")
            flags = int(e.get("flags", 0) or 0)
            is_bold = bool((flags & 16) or "bold" in font_req.lower())
            is_italic = bool((flags & 2) or "italic" in font_req.lower() or "oblique" in font_req.lower())

            ttf_path = find_system_font_file(font_req, bold=is_bold, italic=is_italic)

            if ttf_path and os.path.isfile(ttf_path):
                fontname = "user_font"
                fontfile_arg = ttf_path
                embedded_font = True
            else:
                fontfile_arg = None
                fname = font_req.lower()
                if "times" in fname or "serif" in fname or "roman" in fname:
                    fontname = "tiro"
                elif "mono" in fname or "courier" in fname or "consol" in fname:
                    fontname = "cour"
                else:
                    fontname = "helv"
                if is_bold:
                    if fontname == "helv": fontname = "hebo"
                    elif fontname == "tiro": fontname = "tibo"
                    elif fontname == "cour": fontname = "cobo"
                if is_italic:
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

            kw: dict = {
                "fontsize": e.get("size", 12),
                "color": color,
                "fontname": fontname,
                "fill_opacity": op,
                "overlay": overlay_flag,
            }
            if fontfile_arg:
                kw["fontfile"] = fontfile_arg

            if rot or flip_h or flip_v:
                rr = fitz.Rect(e["rect"]) if e.get("rect") else None
                if rr:
                    cx, cy = (rr.x0 + rr.x1) / 2.0, (rr.y0 + rr.y1) / 2.0
                else:
                    pt = e["point"]
                    cx, cy = float(pt.x), float(pt.y)
                kw["morph"] = (fitz.Point(cx, cy), _build_transform_matrix(fitz, rot, flip_h, flip_v))
            pg.insert_text(e["point"], e["text"], **kw)
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted text '{e.get('text', '')[:20]}' on page {p_idx + 1} with font '{font_req}'", flush=True)

        elif etype in ("image", "signature"):
            path = e.get("path", "")
            if not path or not os.path.isfile(path):
                continue
            stream_bytes = _prepare_image_bytes(
                fitz, path, rotation=rot, flip_h=flip_h, flip_v=flip_v, opacity=op
            )
            if stream_bytes is not None:
                pg.insert_image(e["rect"], stream=stream_bytes, overlay=overlay_flag, keep_proportion=False)
            else:
                pg.insert_image(e["rect"], filename=path, overlay=overlay_flag, keep_proportion=False)
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted image at {e.get('rect')} on page {p_idx + 1}", flush=True)

        elif etype == "highlight":
            rects = e.get("rects")
            stroke_color = e.get("color", (1, 1, 0))
            if rects:
                for r_item in rects:
                    a = pg.add_highlight_annot(fitz.Rect(r_item))
                    a.set_colors(stroke=stroke_color)
                    if op < 0.999:
                        a.update(opacity=op)
                    else:
                        a.update()
            elif "rect" in e:
                a = pg.add_highlight_annot(fitz.Rect(e["rect"]))
                a.set_colors(stroke=stroke_color)
                if op < 0.999:
                    a.update(opacity=op)
                else:
                    a.update()
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted highlight on page {p_idx + 1}", flush=True)

        elif etype == "note":
            pg.add_text_annot(e["point"], e["text"])
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted note on page {p_idx + 1}", flush=True)

        elif etype == "draw":
            stroke = [(float(x), float(y)) for x, y in e.get("points", [])]
            if len(stroke) >= 2:
                annot = pg.add_ink_annot([stroke])
                annot.set_colors(stroke=e.get("color", (1, 0, 0)))
                annot.set_border(width=max(1, int(e.get("width", 2))))
                if op < 0.999:
                    annot.update(opacity=op)
                else:
                    annot.update()
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Inserted ink stroke on page {p_idx + 1}", flush=True)

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
            print(f"[DEBUG] [apply_pending_edits] Phase 2: Reinserted text edit on page {p_idx + 1}", flush=True)

    result.embedded_font = embedded_font
    if embedded_font:
        try:
            doc.subset_fonts()
        except Exception:
            _log.exception("subset_fonts after text edit failed")
    return result
