# app/editor/canvas_painter1.py
"""Canvas painter: renders pages, overlays, vector drawings, opacity, rotation, flips, check/cross stamps, text, notes, and strokes."""

from __future__ import annotations

import os
import fitz
from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QPainterPath

from app.constants import ACCENT, TEXT_SEC
from app.i18n import t
from app.editor.canvas_overlay1 import overlay_pdf_rect, rects_close
from app.editor.canvas_render1 import _NOTE_ICON_SIZE, _load_overlay_pixmap


def _draw_selection_box(p: QPainter, qr: QRect, handle_size: int = 8, draw_handles: bool = True, is_locked: bool = False) -> None:
    if is_locked:
        p.setPen(QPen(QColor("#F59E0B"), 1.8, Qt.PenStyle.DashDotLine))
    else:
        p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(qr)

    if not draw_handles or is_locked:
        if is_locked:
            p.setPen(QColor("#F59E0B"))
            f = QFont()
            f.setPointSize(8)
            f.setBold(True)
            p.setFont(f)
            p.drawText(qr.right() - 42, qr.top() - 4, "[LOCKED]")
        return

    hs = handle_size
    p.setBrush(QColor("#FFFFFF"))
    p.setPen(QPen(QColor(ACCENT), 1.5))
    for hpt in (
        QPoint(qr.left(), qr.top()),
        QPoint(qr.right(), qr.top()),
        QPoint(qr.left(), qr.bottom()),
        QPoint(qr.right(), qr.bottom()),
    ):
        p.drawRect(QRect(hpt.x() - hs // 2, hpt.y() - hs // 2, hs, hs))


def _map_pt(x: float, y: float, orig_r: fitz.Rect, new_r: fitz.Rect, z: float, yo: int) -> QPointF:
    sx = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
    sy = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
    nx = new_r.x0 + (x - orig_r.x0) * sx
    ny = new_r.y0 + (y - orig_r.y0) * sy
    return QPointF(nx * z, yo + ny * z)


def _apply_center_transform(p: QPainter, cx: float, cy: float, rotation: int, flip_h: bool, flip_v: bool) -> None:
    p.translate(cx, cy)
    if flip_h or flip_v:
        p.scale(-1.0 if flip_h else 1.0, -1.0 if flip_v else 1.0)
    if rotation % 360:
        p.rotate(float(rotation % 360))


class CanvasPainter:
    @staticmethod
    def paint(canvas, painter: QPainter):
        p = painter
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(canvas.rect(), QColor(canvas._bg_color))

        if not canvas._page_pixmaps:
            p.setPen(QColor(TEXT_SEC))
            f = QFont(); f.setPointSize(11); p.setFont(f)
            p.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, t("edit.open_prompt"))
            return

        # 1. Render pages (already includes live Phase-1 removals and _z_back items)
        for i, qpix in enumerate(canvas._page_pixmaps):
            yo, pw, ph = canvas._page_offsets[i]
            page_r = QRect(0, yo, pw, ph)
            p.fillRect(page_r, QColor("#FFFFFF"))

            if qpix is not None and not qpix.isNull():
                p.save()
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                p.drawPixmap(page_r, qpix)
                p.restore()
            elif i < len(canvas._page_prev_pixmaps) and canvas._page_prev_pixmaps[i] is not None and not canvas._page_prev_pixmaps[i].isNull():
                p.save()
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                p.drawPixmap(page_r, canvas._page_prev_pixmaps[i])
                p.restore()
            else:
                p.setPen(QColor(TEXT_SEC))
                f = QFont(); f.setPointSize(9); p.setFont(f)
                p.drawText(page_r, Qt.AlignmentFlag.AlignCenter, f"Page {i+1}")

            p.setPen(QColor("#E0E0E0"))
            p.drawRect(QRect(0, yo, pw - 1, ph - 1))

        # 2. Render active overlays
        z = canvas._zoom
        is_actively_dragging = bool(
            getattr(canvas, "_moving_overlay", False)
            or getattr(canvas, "_drag_handle", 0) != getattr(canvas, "HANDLE_NONE", 0)
        )

        for ov_idx, e in enumerate(canvas._overlays):
            if e.get("_deleted"):
                continue
            if not e.get("_visible", True):
                continue

            pg = e.get("page", 0)
            if pg >= len(canvas._page_offsets):
                continue
            yo = canvas._page_offsets[pg][0]
            etype = e["type"]
            is_selected = (ov_idx == canvas._selected_overlay_idx)
            is_locked = bool(e.get("_locked", False))
            op = max(0.0, min(1.0, float(e.get("opacity", 1.0))))
            rot = int(e.get("rotation", 0)) % 360
            flip_h = bool(e.get("flip_h", False))
            flip_v = bool(e.get("flip_v", False))
            is_z_back = bool(e.get("_z_back", False))

            skip_body_for_z_back = bool(is_z_back and not (is_selected and is_actively_dragging))

            if etype == "redact":
                r = e["rect"]; fill = e["fill"]
                qr = QRect(int(r.x0*z), yo+int(r.y0*z), max(1,int(r.width*z)), max(1,int(r.height*z)))
                if not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    p.fillRect(qr, QColor(int(fill[0]*255), int(fill[1]*255), int(fill[2]*255), 210))
                    p.setPen(QPen(QColor("#EF4444"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(qr)
                    p.restore()
                if is_selected:
                    _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "check":
                r = e["rect"]
                sx0 = r.x0 * z
                sy0 = yo + r.y0 * z
                sw = max(4.0, r.width * z)
                sh = max(4.0, r.height * z)
                if not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    if rot or flip_h or flip_v:
                        _apply_center_transform(p, sx0 + sw / 2.0, sy0 + sh / 2.0, rot, flip_h, flip_v)
                        bx, by = -sw / 2.0, -sh / 2.0
                    else:
                        bx, by = sx0, sy0
                    c = e.get("color", (0, 0, 0))
                    col = QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255))
                    stroke_w = max(1.5, float(e.get("width", min(sw, sh) * 0.12)) * z)
                    pen = QPen(col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p1 = QPointF(bx + 0.16 * sw, by + 0.52 * sh)
                    p2 = QPointF(bx + 0.40 * sw, by + 0.82 * sh)
                    p3 = QPointF(bx + 0.86 * sw, by + 0.18 * sh)
                    check_path = QPainterPath()
                    check_path.moveTo(p1)
                    check_path.lineTo(p2)
                    check_path.lineTo(p3)
                    p.drawPath(check_path)
                    p.restore()
                if is_selected:
                    _draw_selection_box(p, QRect(int(sx0) - 2, int(sy0) - 2, int(sw) + 4, int(sh) + 4), handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "cross":
                r = e["rect"]
                sx0 = r.x0 * z
                sy0 = yo + r.y0 * z
                sw = max(4.0, r.width * z)
                sh = max(4.0, r.height * z)
                if not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    if rot or flip_h or flip_v:
                        _apply_center_transform(p, sx0 + sw / 2.0, sy0 + sh / 2.0, rot, flip_h, flip_v)
                        bx, by = -sw / 2.0, -sh / 2.0
                    else:
                        bx, by = sx0, sy0
                    c = e.get("color", (0, 0, 0))
                    col = QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255))
                    stroke_w = max(1.5, float(e.get("width", min(sw, sh) * 0.12)) * z)
                    pen = QPen(col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawLine(QPointF(bx + 0.20 * sw, by + 0.20 * sh), QPointF(bx + 0.80 * sw, by + 0.80 * sh))
                    p.drawLine(QPointF(bx + 0.80 * sw, by + 0.20 * sh), QPointF(bx + 0.20 * sw, by + 0.80 * sh))
                    p.restore()
                if is_selected:
                    _draw_selection_box(p, QRect(int(sx0) - 2, int(sy0) - 2, int(sw) + 4, int(sh) + 4), handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "drawing":
                new_r = fitz.Rect(e["rect"])
                items_raw = e.get("_items_rect") or e.get("_orig_rect")
                orig_r = fitz.Rect(items_raw) if items_raw else fitz.Rect(new_r)

                is_untouched_existing = bool(
                    e.get("_existing")
                    and not e.get("_dirty")
                    and rects_close(new_r, orig_r, tol=0.25)
                    and op >= 0.999
                )
                if not is_untouched_existing and not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    qpath = QPainterPath()
                    if e.get("even_odd"):
                        qpath.setFillRule(Qt.FillRule.OddEvenFill)
                    else:
                        qpath.setFillRule(Qt.FillRule.WindingFill)

                    for item in e.get("items", []):
                        op_code = item[0]
                        if op_code == "l":
                            p1 = _map_pt(item[1][0], item[1][1], orig_r, new_r, z, yo)
                            p2 = _map_pt(item[2][0], item[2][1], orig_r, new_r, z, yo)
                            qpath.moveTo(p1)
                            qpath.lineTo(p2)
                        elif op_code == "re":
                            rx0, ry0, rx1, ry1 = item[1]
                            tl = _map_pt(rx0, ry0, orig_r, new_r, z, yo)
                            br = _map_pt(rx1, ry1, orig_r, new_r, z, yo)
                            qpath.addRect(tl.x(), tl.y(), max(0.5, br.x() - tl.x()), max(0.5, br.y() - tl.y()))
                        elif op_code == "qu":
                            pts = [_map_pt(pt[0], pt[1], orig_r, new_r, z, yo) for pt in item[1]]
                            qpath.moveTo(pts[0])
                            for pt in pts[1:]:
                                qpath.lineTo(pt)
                            qpath.closeSubpath()
                        elif op_code == "c":
                            p1 = _map_pt(item[1][0], item[1][1], orig_r, new_r, z, yo)
                            p2 = _map_pt(item[2][0], item[2][1], orig_r, new_r, z, yo)
                            p3 = _map_pt(item[3][0], item[3][1], orig_r, new_r, z, yo)
                            p4 = _map_pt(item[4][0], item[4][1], orig_r, new_r, z, yo)
                            qpath.moveTo(p1)
                            qpath.cubicTo(p2, p3, p4)

                    if e.get("closePath"):
                        qpath.closeSubpath()

                    fill_col = e.get("fill")
                    if fill_col:
                        fa = e.get("fill_opacity")
                        alpha = int(round(max(0.0, min(1.0, float(fa if fa is not None else 1.0))) * 255))
                        p.setBrush(QColor(int(fill_col[0] * 255), int(fill_col[1] * 255), int(fill_col[2] * 255), alpha))
                    else:
                        p.setBrush(Qt.BrushStyle.NoBrush)

                    stroke_col = e.get("color")
                    if stroke_col:
                        sa = e.get("stroke_opacity")
                        alpha = int(round(max(0.0, min(1.0, float(sa if sa is not None else 1.0))) * 255))
                        sx_s = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
                        sy_s = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
                        w_val = max(0.75, float(e.get("width", 1.0)) * ((sx_s + sy_s) * 0.5) * z)
                        pen = QPen(
                            QColor(int(stroke_col[0] * 255), int(stroke_col[1] * 255), int(stroke_col[2] * 255), alpha),
                            w_val,
                        )
                        p.setPen(pen)
                    else:
                        p.setPen(Qt.PenStyle.NoPen)

                    p.drawPath(qpath)
                    p.restore()

                if is_selected:
                    pad = 4 if (new_r.width < 4 or new_r.height < 4) else 0
                    qr = QRect(
                        int(new_r.x0 * z) - pad,
                        yo + int(new_r.y0 * z) - pad,
                        max(1, int(new_r.width * z)) + pad * 2,
                        max(1, int(new_r.height * z)) + pad * 2,
                    )
                    _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "highlight":
                c = e.get("color", (1, 1, 0))
                col = QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255), int(round(120 * op)))
                rects = e.get("rects")
                if rects:
                    if not skip_body_for_z_back:
                        for r_item in rects:
                            qr = QRect(int(r_item.x0*z), yo+int(r_item.y0*z), max(1,int(r_item.width*z)), max(1,int(r_item.height*z)))
                            p.fillRect(qr, col)
                    if is_selected:
                        ur = overlay_pdf_rect(e, zoom=z)
                        if ur:
                            uqr = QRect(int(ur.x0*z), yo+int(ur.y0*z), max(1,int(ur.width*z)), max(1,int(ur.height*z)))
                            _draw_selection_box(p, uqr, handle_size=8, draw_handles=False, is_locked=is_locked)
                elif "rect" in e:
                    r = e["rect"]
                    qr = QRect(int(r.x0*z), yo+int(r.y0*z), max(1,int(r.width*z)), max(1,int(r.height*z)))
                    if not skip_body_for_z_back:
                        p.fillRect(qr, col)
                    if is_selected:
                        _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "text":
                pt = e["point"]; c = e["color"]
                r = overlay_pdf_rect(e, zoom=z)
                if not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    p.setPen(QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255)))
                    fname = e.get("font", "Helvetica")
                    f2 = QFont(fname)
                    f2.setPointSizeF(max(4.0, float(e.get("size", 12)) * z * 0.75))
                    flags = int(e.get("flags", 0) or 0)
                    if (flags & 16) or "bold" in fname.lower():
                        f2.setBold(True)
                    if (flags & 2) or "italic" in fname.lower() or "oblique" in fname.lower():
                        f2.setItalic(True)
                    p.setFont(f2)
                    if (rot or flip_h or flip_v) and r is not None:
                        cx = (r.x0 + r.x1) * 0.5 * z
                        cy = yo + (r.y0 + r.y1) * 0.5 * z
                        _apply_center_transform(p, cx, cy, rot, flip_h, flip_v)
                        p.drawText(QPointF(pt.x * z - cx, (yo + pt.y * z) - cy), e["text"])
                    else:
                        p.drawText(int(pt.x*z), yo+int(pt.y*z), e["text"])
                    p.restore()

                if is_selected and r:
                    qr = QRect(int(r.x0*z) - 2, yo+int(r.y0*z) - 2, max(1,int(r.width*z)) + 4, max(1,int(r.height*z)) + 4)
                    _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype in ("image", "signature"):
                r = e["rect"]
                qr = QRect(int(r.x0*z), yo+int(r.y0*z), max(1,int(r.width*z)), max(1,int(r.height*z)))
                orig_raw = e.get("_orig_rect") or e.get("_annot_bbox")
                is_untouched_existing = bool(
                    e.get("_existing")
                    and not e.get("_dirty")
                    and orig_raw
                    and rects_close(r, orig_raw, tol=0.25)
                    and op >= 0.999
                    and rot == 0
                    and not flip_h
                    and not flip_v
                )

                if not is_untouched_existing and not skip_body_for_z_back:
                    path = e["path"]
                    try:
                        mtime = os.path.getmtime(path)
                        img_px = _load_overlay_pixmap(path, mtime)
                    except OSError:
                        img_px = QPixmap(path)
                    if not img_px.isNull():
                        p.save()
                        p.setOpacity(op)
                        if rot or flip_h or flip_v:
                            cx = qr.x() + qr.width() / 2.0
                            cy = qr.y() + qr.height() / 2.0
                            _apply_center_transform(p, cx, cy, rot, flip_h, flip_v)
                            dw = float(qr.height()) if rot in (90, 270) else float(qr.width())
                            dh = float(qr.width()) if rot in (90, 270) else float(qr.height())
                            p.drawPixmap(QRectF(-dw / 2.0, -dh / 2.0, dw, dh), img_px, QRectF(img_px.rect()))
                        else:
                            p.drawPixmap(qr, img_px)
                        p.restore()

                if is_selected:
                    _draw_selection_box(p, qr, handle_size=10, draw_handles=True, is_locked=is_locked)

            elif etype == "text_edit":
                r = e["bbox"]
                qr = QRect(int(r[0]*z) - 1, yo+int(r[1]*z) - 1,
                           max(1, int((r[2]-r[0])*z)) + 2, max(1, int((r[3]-r[1])*z)) + 2)
                orig_raw = e.get("_orig_rect")
                is_untouched_existing = bool(
                    e.get("_existing")
                    and not e.get("_dirty")
                    and orig_raw
                    and rects_close(r, orig_raw, tol=0.25)
                    and op >= 0.999
                    and rot == 0
                    and not flip_h
                    and not flip_v
                )

                new_txt = e.get("new_text", "")
                if new_txt and not is_untouched_existing and not skip_body_for_z_back:
                    p.save()
                    p.setOpacity(op)
                    c = e.get("color", 0)
                    if isinstance(c, int):
                        col = QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
                    elif isinstance(c, (list, tuple)) and len(c) >= 3:
                        col = QColor(int(c[0]*255), int(c[1]*255), int(c[2]*255))
                    else:
                        col = QColor("#000000")
                    p.setPen(col)

                    fname = e.get("font", "Helvetica")
                    fnt = QFont(fname)
                    sz = float(e.get("font_size", e.get("size", 12)))
                    fnt.setPointSizeF(max(4.0, sz * z * 0.75))
                    flags = int(e.get("flags", 0) or 0)
                    if (flags & 16) or "bold" in fname.lower():
                        fnt.setBold(True)
                    if (flags & 2) or "italic" in fname.lower() or "oblique" in fname.lower():
                        fnt.setItalic(True)
                    p.setFont(fnt)

                    origin = e.get("origin", [r[0], r[3]])
                    baseline_x = origin[0] * z
                    baseline_y = yo + origin[1] * z
                    if rot or flip_h or flip_v:
                        cx = (r[0] + r[2]) * 0.5 * z
                        cy = yo + (r[1] + r[3]) * 0.5 * z
                        _apply_center_transform(p, cx, cy, rot, flip_h, flip_v)
                        p.drawText(QPointF(baseline_x - cx, baseline_y - cy), new_txt)
                    else:
                        p.drawText(int(baseline_x), int(baseline_y), new_txt)
                    p.restore()

                if is_selected:
                    _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

            elif etype == "note":
                pt = e["point"]
                px, py = int(pt.x*z), yo+int(pt.y*z)
                note_idx = ov_idx
                icon_r = QRect(px, py - _NOTE_ICON_SIZE, _NOTE_ICON_SIZE, _NOTE_ICON_SIZE)
                p.setBrush(QColor("#FBBF24")); p.setPen(QPen(QColor("#D97706"), 1))
                p.drawRoundedRect(icon_r, 4, 4)
                fi = QFont(); fi.setPointSize(10); fi.setBold(True); p.setFont(fi)
                p.setPen(QColor("#1C1917")); p.drawText(icon_r, Qt.AlignmentFlag.AlignCenter, "N")
                if is_selected:
                    _draw_selection_box(p, icon_r.adjusted(-2, -2, 2, 2), handle_size=6, draw_handles=False, is_locked=is_locked)
                if canvas._open_note == note_idx:
                    bx = px + _NOTE_ICON_SIZE + 6
                    by = py - _NOTE_ICON_SIZE - 4
                    ft = QFont(); ft.setPointSize(9); p.setFont(ft)
                    fm = p.fontMetrics()
                    lines = e["text"].split("\n")
                    tw = max(fm.horizontalAdvance(ln) for ln in lines) + 20
                    th = fm.height() * len(lines) + 16
                    bw, bh = max(120, min(tw, 260)), max(32, th)
                    br = QRect(bx, by, bw, bh)
                    p.setBrush(QColor(0,0,0,30)); p.setPen(Qt.PenStyle.NoPen)
                    p.drawRoundedRect(QRect(bx+2, by+2, bw, bh), 6, 6)
                    p.setBrush(QColor("#FFFDF5")); p.setPen(QPen(QColor("#D97706"), 1))
                    p.drawRoundedRect(br, 6, 6)
                    p.setPen(QColor("#000000"))
                    p.drawText(QRect(bx+10, by+8, bw-20, bh-16),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                               e["text"])

            elif etype == "draw":
                pts = e.get("points", [])
                if len(pts) >= 2:
                    if not skip_body_for_z_back:
                        p.save()
                        p.setOpacity(op)
                        col = e.get("color", (1, 0, 0))
                        w = max(1, int(e.get("width", 2)))
                        pen = QPen(QColor(int(col[0]*255), int(col[1]*255), int(col[2]*255)),
                                   max(1, int(w * z)))
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                        p.setPen(pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)

                        path = QPainterPath()
                        p0 = (pts[0][0] * z, yo + pts[0][1] * z)
                        path.moveTo(p0[0], p0[1])
                        if len(pts) == 2:
                            p1 = (pts[1][0] * z, yo + pts[1][1] * z)
                            path.lineTo(p1[0], p1[1])
                        else:
                            for i in range(1, len(pts) - 1):
                                cur = (pts[i][0] * z, yo + pts[i][1] * z)
                                nxt = (pts[i + 1][0] * z, yo + pts[i + 1][1] * z)
                                mid_x = (cur[0] + nxt[0]) / 2.0
                                mid_y = (cur[1] + nxt[1]) / 2.0
                                path.quadTo(cur[0], cur[1], mid_x, mid_y)
                            p_last = (pts[-1][0] * z, yo + pts[-1][1] * z)
                            path.lineTo(p_last[0], p_last[1])
                        p.drawPath(path)
                        p.restore()

                    if is_selected:
                        r = overlay_pdf_rect(e, zoom=z)
                        if r:
                            qr = QRect(int(r.x0*z), yo+int(r.y0*z), max(1,int(r.width*z)), max(1,int(r.height*z)))
                            _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=is_locked)

        # 3. Interactive signature follow preview
        if canvas._placing_signature and canvas._sig_cursor_pos and canvas._placing_sig_pixmap:
            pos = canvas._sig_cursor_pos
            pix = canvas._placing_sig_pixmap
            if not pix.isNull():
                aspect = (pix.height() / pix.width()) if pix.width() > 0 else 0.35
                pw = max(40, int(160 * z))
                ph = max(20, int(pw * aspect))
                preview_r = QRect(pos.x() - pw//2, pos.y() - ph//2, pw, ph)
                p.save()
                p.setOpacity(0.75)
                p.drawPixmap(preview_r, pix)
                p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(preview_r)
                p.restore()

        # 4. Interactive check/cross vector stamp placement follow preview
        if getattr(canvas, "_placing_stamp", False) and getattr(canvas, "_stamp_cursor_pos", None):
            pos = canvas._stamp_cursor_pos
            st_size = getattr(canvas, "_stamp_size", 20.0) * z
            sw = max(8, int(st_size))
            sh = sw
            sx0 = pos.x() - sw // 2
            sy0 = pos.y() - sh // 2
            c_val = getattr(canvas, "_stamp_color", (0, 0, 0))
            col = QColor(int(c_val[0]*255), int(c_val[1]*255), int(c_val[2]*255))
            stroke_w = max(1.5, sw * 0.12)
            pen = QPen(col, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.save()
            p.setOpacity(0.85)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            st_type = getattr(canvas, "_placing_stamp_type", "check")
            if st_type == "check":
                p1 = QPointF(sx0 + 0.16 * sw, sy0 + 0.52 * sh)
                p2 = QPointF(sx0 + 0.40 * sw, sy0 + 0.82 * sh)
                p3 = QPointF(sx0 + 0.86 * sw, sy0 + 0.18 * sh)
                cp = QPainterPath()
                cp.moveTo(p1); cp.lineTo(p2); cp.lineTo(p3)
                p.drawPath(cp)
            else:
                p.drawLine(QPointF(sx0 + 0.20 * sw, sy0 + 0.20 * sh), QPointF(sx0 + 0.80 * sw, sy0 + 0.80 * sh))
                p.drawLine(QPointF(sx0 + 0.80 * sw, sy0 + 0.20 * sh), QPointF(sx0 + 0.20 * sw, sy0 + 0.80 * sh))
            p.setPen(QPen(QColor(ACCENT), 1.0, Qt.PenStyle.DashLine))
            p.drawRect(QRect(sx0 - 2, sy0 - 2, sw + 4, sh + 4))
            p.restore()

        # 5. Interactive smoothed freehand stroke preview
        if canvas._draw_mode and canvas._current_stroke and len(canvas._current_stroke) >= 2:
            col = canvas._draw_color
            pen = QPen(QColor(int(col[0]*255), int(col[1]*255), int(col[2]*255)),
                       max(1, int(canvas._draw_width * canvas._zoom)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            pts = canvas._current_stroke
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            if len(pts) == 2:
                path.lineTo(pts[1][0], pts[1][1])
            else:
                for i in range(1, len(pts) - 1):
                    mid_x = (pts[i][0] + pts[i + 1][0]) / 2.0
                    mid_y = (pts[i][1] + pts[i + 1][1]) / 2.0
                    path.quadTo(pts[i][0], pts[i][1], mid_x, mid_y)
                path.lineTo(pts[-1][0], pts[-1][1])
            p.drawPath(path)

        # 6. Drag selection rectangle
        if canvas._drag_rect:
            if canvas._select_mode:
                p.setPen(QPen(QColor("#3B82F6"), 2, Qt.PenStyle.SolidLine))
                p.setBrush(QColor(59, 130, 246, 50))
                p.drawRect(canvas._drag_rect)
            elif getattr(canvas, "_highlight_mode", False):
                hi_c = getattr(canvas, "_highlight_color", (1.0, 1.0, 0.0))
                col_border = QColor(int(hi_c[0]*255), int(hi_c[1]*255), int(hi_c[2]*255), 230)
                col_fill = QColor(int(hi_c[0]*255), int(hi_c[1]*255), int(hi_c[2]*255), 90)
                p.setPen(QPen(col_border, 1.5, Qt.PenStyle.SolidLine))
                p.setBrush(col_fill)
                p.drawRect(canvas._drag_rect)
            else:
                p.setPen(QPen(QColor("#EF4444"), 2, Qt.PenStyle.DashLine))
                p.setBrush(QColor(239, 68, 68, 50))
                p.drawRect(canvas._drag_rect)

        # 7. Active persistent selection rectangle in Select mode
        if getattr(canvas, "_select_mode", False) and getattr(canvas, "_active_sel_rect", None) and not canvas._drag_rect:
            sel_r = canvas._active_sel_rect
            sel_pg = getattr(canvas, "_active_sel_page", 0)
            if 0 <= sel_pg < len(canvas._page_offsets):
                yo = canvas._page_offsets[sel_pg][0]
                sx0 = int(sel_r.x0 * z)
                sy0 = yo + int(sel_r.y0 * z)
                sw = max(1, int(sel_r.width * z))
                sh = max(1, int(sel_r.height * z))
                qr = QRect(sx0, sy0, sw, sh)
                p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(QColor(59, 130, 246, 35))
                p.drawRect(qr)
                _draw_selection_box(p, qr, handle_size=8, draw_handles=True, is_locked=False)