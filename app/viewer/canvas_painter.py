# app/viewer/canvas_painter.py
"""PDF_Editor – CanvasPainter for _SelectCanvas: renders pages, highlights, notes, crop/number previews, and signatures."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath, QPaintEvent

from app.constants import ACCENT, TEXT_SEC
from app.i18n import t
from app.viewer.canvas_worker import _NOTE_ICON_SIZE


class CanvasPainter:
    """Stateless painter for the continuous-scroll PDF viewer canvas."""

    @staticmethod
    def paint(canvas, event: QPaintEvent | QPainter | None = None) -> None:
        if isinstance(event, QPainter):
            p = event
            owns_painter = False
            clip_rect = None
        else:
            p = QPainter(canvas)
            owns_painter = True
            clip_rect = event.rect() if event is not None and hasattr(event, "rect") else None

        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.fillRect(canvas.rect(), QColor(canvas._bg_color))

            entries = getattr(canvas, "_entries", None)
            if not entries:
                p.setPen(QColor(TEXT_SEC))
                f = QFont()
                f.setPointSize(11)
                p.setFont(f)
                p.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, t("viewer.drag_hint"))
                return

            z = float(getattr(canvas, "_zoom", 1.0) or 1.0)
            night = bool(getattr(canvas, "_night_mode", False))
            page_bg = QColor("#1E1E1E") if night else QColor("#FFFFFF")
            border_col = QColor("#333333") if night else QColor("#D8D8D8")

            # 1. Render visible pages
            for i, entry in enumerate(entries):
                x_off = canvas.page_x_offset(entry)
                page_r = QRect(x_off, entry.y_off, entry.w, entry.h)
                if clip_rect is not None and not page_r.intersects(clip_rect):
                    continue

                p.fillRect(page_r, page_bg)

                qpix = getattr(entry, "pixmap", None)
                prev_pix = getattr(entry, "prev_pixmap", None)
                if qpix is not None and not qpix.isNull():
                    p.save()
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    p.drawPixmap(page_r, qpix)
                    p.restore()
                elif prev_pix is not None and not prev_pix.isNull():
                    p.save()
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    p.drawPixmap(page_r, prev_pix)
                    p.restore()
                else:
                    p.setPen(QColor(TEXT_SEC))
                    f = QFont()
                    f.setPointSize(9)
                    p.setFont(f)
                    p.drawText(page_r, Qt.AlignmentFlag.AlignCenter, f"⏳ {i + 1}")

                p.setPen(border_col)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRect(page_r.x(), page_r.y(), max(0, page_r.width() - 1), max(0, page_r.height() - 1)))

            # 2. Render search highlights
            highlights = getattr(canvas, "_search_highlights", None)
            if highlights:
                cur_hi = int(getattr(canvas, "_search_current", -1))
                use_order = getattr(canvas, "_page_order", None) is not None
                src_map = {e.src_page: e for e in entries} if use_order else None

                for flat_idx, (page_idx, rect) in enumerate(highlights):
                    if src_map is not None:
                        entry = src_map.get(page_idx)
                    elif 0 <= page_idx < len(entries):
                        entry = entries[page_idx]
                    else:
                        entry = None
                    if entry is None:
                        continue

                    x_off = canvas.page_x_offset(entry)
                    if hasattr(rect, "x0"):
                        rx0, ry0, rw, rh = rect.x0, rect.y0, rect.width, rect.height
                    else:
                        rx0, ry0 = rect[0], rect[1]
                        rw, rh = rect[2] - rect[0], rect[3] - rect[1]

                    qr = QRect(
                        x_off + int(round(rx0 * z)),
                        entry.y_off + int(round(ry0 * z)),
                        max(2, int(round(rw * z))),
                        max(2, int(round(rh * z))),
                    )
                    if clip_rect is not None and not qr.intersects(clip_rect):
                        continue

                    if flat_idx == cur_hi:
                        p.fillRect(qr, QColor(255, 140, 0, 140))
                        p.setPen(QPen(QColor(255, 100, 0), 1.5))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(qr)
                    else:
                        p.fillRect(qr, QColor(255, 235, 59, 110))

            # 3. Render selected text highlights
            sel_rects = getattr(canvas, "_sel_rects", None)
            if sel_rects:
                sel_col = QColor(59, 130, 246, 85)
                for sr in sel_rects:
                    if clip_rect is not None and not sr.intersects(clip_rect):
                        continue
                    p.fillRect(sr, sel_col)

            # 4. Render sticky note icons and active comment bubble
            open_note = getattr(canvas, "_open_note", None)
            open_note_info = None
            for page_idx, entry in enumerate(entries):
                annots = getattr(entry, "annots", None)
                if not annots:
                    continue
                x_off = canvas.page_x_offset(entry)
                for annot_idx, (rect, txt) in enumerate(annots):
                    px = x_off + int(round(rect.x0 * z))
                    py = entry.y_off + int(round(rect.y0 * z))
                    icon_r = QRect(px, py, _NOTE_ICON_SIZE, _NOTE_ICON_SIZE)
                    if clip_rect is None or icon_r.intersects(clip_rect):
                        p.setBrush(QColor("#FBBF24"))
                        p.setPen(QPen(QColor("#D97706"), 1))
                        p.drawRoundedRect(icon_r, 4, 4)
                        fi = QFont()
                        fi.setPointSize(10)
                        fi.setBold(True)
                        p.setFont(fi)
                        p.setPen(QColor("#1C1917"))
                        p.drawText(icon_r, Qt.AlignmentFlag.AlignCenter, "\u270e")
                    if open_note == (page_idx, annot_idx) and txt:
                        open_note_info = (px, py, txt)

            if open_note_info is not None:
                px, py, txt = open_note_info
                ft = QFont()
                ft.setPointSize(9)
                p.setFont(ft)
                fm = p.fontMetrics()
                lines = txt.split("\n")
                tw = max((fm.horizontalAdvance(ln) for ln in lines), default=80) + 20
                th = fm.height() * max(1, len(lines)) + 16
                bw = max(120, min(tw, 260))
                bh = max(32, th)
                bx = px + _NOTE_ICON_SIZE + 6
                if bx + bw > canvas.width() - 8:
                    bx = max(8, px - bw - 6)
                by = max(4, py - 4)
                br = QRect(bx, by, bw, bh)
                p.setBrush(QColor(0, 0, 0, 35))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRect(bx + 2, by + 2, bw, bh), 6, 6)
                p.setBrush(QColor("#FFFDF5"))
                p.setPen(QPen(QColor("#D97706"), 1))
                p.drawRoundedRect(br, 6, 6)
                p.setPen(QColor("#000000"))
                p.drawText(
                    QRect(bx + 10, by + 8, bw - 20, bh - 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                    txt,
                )

            # 5. Render crop margin preview
            crop_preview = getattr(canvas, "_crop_preview", None)
            if crop_preview:
                margins = crop_preview.get("margins", (0, 0, 0, 0))
                targets = crop_preview.get("targets", set())
                top_m, bot_m, left_m, right_m = margins
                if top_m > 0 or bot_m > 0 or left_m > 0 or right_m > 0:
                    t_px = int(round(top_m * z))
                    b_px = int(round(bot_m * z))
                    l_px = int(round(left_m * z))
                    r_px = int(round(right_m * z))
                    for page_idx, entry in enumerate(entries):
                        if page_idx not in targets and entry.src_page not in targets:
                            continue
                        x_off = canvas.page_x_offset(entry)
                        page_r = QRect(x_off, entry.y_off, entry.w, entry.h)
                        if clip_rect is not None and not page_r.intersects(clip_rect):
                            continue
                        keep_w = max(1, entry.w - l_px - r_px)
                        keep_h = max(1, entry.h - t_px - b_px)
                        keep_r = QRect(x_off + l_px, entry.y_off + t_px, keep_w, keep_h).intersected(page_r)

                        outer = QPainterPath()
                        outer.addRect(float(page_r.x()), float(page_r.y()), float(page_r.width()), float(page_r.height()))
                        inner = QPainterPath()
                        inner.addRect(float(keep_r.x()), float(keep_r.y()), float(keep_r.width()), float(keep_r.height()))
                        mask_path = outer.subtracted(inner)
                        p.fillPath(mask_path, QColor(0, 0, 0, 100))

                        p.setPen(QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(keep_r)

            # 6. Render interactive crop rubber-band drag
            if getattr(canvas, "_crop_mode", False):
                c_start = getattr(canvas, "_crop_drag_start", None)
                c_cur = getattr(canvas, "_crop_drag_cur", None)
                if c_start is not None and c_cur is not None:
                    drag_r = QRect(c_start, c_cur).normalized()
                    p.setPen(QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine))
                    p.setBrush(QColor(0, 120, 212, 45))
                    p.drawRect(drag_r)

            # 7. Render interactive drawn highlight rectangle drag preview
            if getattr(canvas, "_rect_hi_mode", False):
                hi_start = getattr(canvas, "_rect_hi_drag_start", None)
                hi_cur = getattr(canvas, "_rect_hi_drag_cur", None)
                if hi_start is not None and hi_cur is not None:
                    drag_r = QRect(hi_start, hi_cur).normalized()
                    hi_c = getattr(canvas, "_rect_hi_color", (1.0, 1.0, 0.0))
                    r_c = int(hi_c[0] * 255)
                    g_c = int(hi_c[1] * 255)
                    b_c = int(hi_c[2] * 255)
                    p.setPen(QPen(QColor(r_c, g_c, b_c, 230), 1.5, Qt.PenStyle.SolidLine))
                    p.setBrush(QColor(r_c, g_c, b_c, 90))
                    p.drawRect(drag_r)

            # 8. Render live page-numbers preview
            num_preview = getattr(canvas, "_numbers_preview", None)
            if num_preview:
                pos_code = num_preview.get("pos_code", "bc")
                font_size = float(num_preview.get("font_size", 10))
                targets = num_preview.get("targets", {})
                margin = max(18.0, font_size + 8.0)
                f_num = QFont("Helvetica")
                f_num.setPixelSize(max(6, int(round(font_size * z))))
                p.setFont(f_num)
                fm_num = p.fontMetrics()

                for page_idx, label in targets.items():
                    if not (0 <= page_idx < len(entries)):
                        continue
                    entry = entries[page_idx]
                    x_off = canvas.page_x_offset(entry)
                    page_r = QRect(x_off, entry.y_off, entry.w, entry.h)
                    if clip_rect is not None and not page_r.intersects(clip_rect):
                        continue

                    pw_pt = entry.w / z
                    ph_pt = entry.h / z
                    tw_px = fm_num.horizontalAdvance(label)
                    th_px = fm_num.height()
                    margin_px = margin * z

                    if pos_code[1] == "l":
                        tx = x_off + int(round(margin_px))
                    elif pos_code[1] == "c":
                        tx = x_off + int(round((entry.w - tw_px) / 2.0))
                    else:
                        tx = x_off + int(round(entry.w - margin_px - tw_px))

                    if pos_code[0] == "t":
                        ty = entry.y_off + int(round(margin_px - fm_num.ascent() * 0.5))
                    else:
                        ty = entry.y_off + int(round((ph_pt - margin + font_size * 0.3) * z)) - fm_num.ascent()

                    pill_r = QRect(tx - 6, ty - 2, tw_px + 12, th_px + 4)
                    p.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DashLine))
                    p.setBrush(QColor(255, 255, 255, 210) if not night else QColor(30, 30, 30, 210))
                    p.drawRoundedRect(pill_r, 4, 4)

                    p.setPen(QColor("#000000") if not night else QColor("#FFFFFF"))
                    p.drawText(pill_r, Qt.AlignmentFlag.AlignCenter, label)

            # 9. Render placed active signature with resize handles
            active_sig = getattr(canvas, "_active_sig", None)
            if active_sig is not None:
                sig_pg = int(active_sig.get("page", -1))
                if 0 <= sig_pg < len(entries):
                    entry = entries[sig_pg]
                    x_off = canvas.page_x_offset(entry)
                    r = active_sig["rect"]
                    sx0 = x_off + int(round(r.x0 * z))
                    sy0 = entry.y_off + int(round(r.y0 * z))
                    sx1 = x_off + int(round(r.x1 * z))
                    sy1 = entry.y_off + int(round(r.y1 * z))
                    qr = QRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0))

                    sig_pix = active_sig.get("pixmap")
                    if sig_pix is not None and not sig_pix.isNull():
                        p.drawPixmap(qr, sig_pix)

                    p.setPen(QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(qr)

                    hs = 10
                    p.setBrush(QColor("#FFFFFF"))
                    p.setPen(QPen(QColor(ACCENT), 2))
                    for hpt in (
                        QPoint(sx0, sy0),
                        QPoint(sx1, sy0),
                        QPoint(sx0, sy1),
                        QPoint(sx1, sy1),
                    ):
                        p.drawRect(QRect(hpt.x() - hs // 2, hpt.y() - hs // 2, hs, hs))

            # 10. Render cursor-follow signature placement preview
            if (
                getattr(canvas, "_placing_signature", False)
                and getattr(canvas, "_sig_cursor_pos", None) is not None
                and getattr(canvas, "_placing_sig_pixmap", None) is not None
            ):
                pos = canvas._sig_cursor_pos
                pix = canvas._placing_sig_pixmap
                if not pix.isNull():
                    aspect = (pix.height() / pix.width()) if pix.width() > 0 else 0.35
                    pw = max(40, int(round(180.0 * z)))
                    ph = max(20, int(round(pw * aspect)))
                    preview_r = QRect(pos.x() - pw // 2, pos.y() - ph // 2, pw, ph)
                    p.save()
                    p.setOpacity(0.75)
                    p.drawPixmap(preview_r, pix)
                    p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(preview_r)
                    p.restore()
        finally:
            if owns_painter:
                p.end()