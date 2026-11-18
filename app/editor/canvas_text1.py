

# app/editor/canvas_text1.py

"""Inline text editor controller for Foxit-style in-place editing & insertion with installed system font support."""

from __future__ import annotations

import fitz
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QFont, QFontMetrics, QFontDatabase
from PySide6.QtWidgets import QLineEdit

from app.constants import ACCENT


def _resolve_font_family(fname: str) -> str:
    """Resolve font name to an installed system font family or suitable fallback."""
    if not fname:
        return "Arial"
    try:
        fams = QFontDatabase.families()
        if fname in fams:
            return fname
        fn = fname.strip().lower()
        for fam in fams:
            if fam.lower() == fn:
                return fam
    except Exception:
        pass

    fn = fname.strip().lower()
    if "times" in fn or "serif" in fn or "roman" in fn:
        return "Times New Roman"
    if "mono" in fn or "courier" in fn or "consol" in fn:
        return "Courier New"
    if "segoe" in fn:
        return "Segoe UI"
    if "georgia" in fn:
        return "Georgia"
    if "calibri" in fn:
        return "Calibri"
    if "verdana" in fn:
        return "Verdana"
    if "trebuchet" in fn:
        return "Trebuchet MS"
    if "comic" in fn:
        return "Comic Sans MS"
    if "arial" in fn or "helvetica" in fn:
        return "Arial"
    return fname


class CanvasInlineTextManager:
    def __init__(self, canvas):
        self.canvas = canvas
        self.edit = QLineEdit(canvas)
        self.edit.hide()
        self.edit.installEventFilter(canvas)
        self.edit.returnPressed.connect(self.commit)
        self.edit.textChanged.connect(lambda _: self.reposition() if self.mode == "insert" else None)

        self.mode: str | None = None
        self.span: dict | None = None
        self.page_idx: int = -1
        self.original_text: str = ""
        self.insert_point: tuple[float, float] | None = None
        self.insert_size: float = 12.0
        self.insert_color: tuple = (0.0, 0.0, 0.0)
        self.insert_font: str = "Helvetica"
        self.insert_flags: int = 0

    @staticmethod
    def _resolve_font_family(fname: str) -> str:
        return _resolve_font_family(fname)

    def begin_edit(self, span: dict, page_idx: int, target_overlay: dict | None = None):
        if self.edit.isVisible():
            self.commit()
        self.mode = "edit"
        self.span = dict(span)
        self.page_idx = page_idx
        self.original_text = span.get("text", "")
        self.edit.setText(self.original_text)

        bb = span["bbox"]
        visual_size = float(span.get("size") or span.get("font_size") or 0)
        if visual_size <= 0:
            visual_size = max(6.0, float(bb[3] - bb[1]) * 0.75)
        font_name = span.get("font", "Helvetica")
        flags = int(span.get("flags", 0) or 0)
        c = span.get("color", 0)
        if isinstance(c, int):
            color = (((c >> 16) & 0xFF) / 255.0, ((c >> 8) & 0xFF) / 255.0, (c & 0xFF) / 255.0)
        elif isinstance(c, (list, tuple)) and len(c) >= 3:
            color = (float(c[0]), float(c[1]), float(c[2]))
        else:
            color = (0.0, 0.0, 0.0)

        self._style_inline_edit(self.span)
        self.reposition()
        self.edit.show()
        self.edit.raise_()
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.edit.selectAll()

        self.canvas.text_edit_started.emit({
            "font": font_name,
            "size": visual_size,
            "color": color,
            "bold": bool(flags & 16),
            "italic": bool(flags & 2),
            "text": self.original_text,
        })

    def begin_insert(self, page_idx: int, pdf_point, size: float, color: tuple, font: str = "", bold: bool = False, italic: bool = False):
        if self.edit.isVisible():
            self.commit()
        self.mode = "insert"
        self.span = None
        self.page_idx = page_idx
        self.original_text = ""
        self.insert_point = (float(pdf_point.x), float(pdf_point.y))
        self.insert_size = float(size)
        self.insert_color = tuple(color)
        self.insert_font = font or "Helvetica"
        self.insert_flags = (16 if bold else 0) | (2 if italic else 0)
        self.edit.setText("")
        self._style_inline_insert()
        self.reposition()
        self.edit.show()
        self.edit.raise_()
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)

        self.canvas.text_edit_started.emit({
            "font": self.insert_font,
            "size": self.insert_size,
            "color": self.insert_color,
            "bold": bold,
            "italic": italic,
            "text": "",
        })

    def update_format(self, font: str, size: float, color: tuple, bold: bool, italic: bool):
        flags = (16 if bold else 0) | (2 if italic else 0)
        c = self.canvas
        if self.mode == "edit" and self.span is not None:
            self.span["font"] = font
            self.span["size"] = size
            self.span["font_size"] = size
            self.span["color"] = color
            self.span["flags"] = flags
            self._style_inline_edit(self.span)
            self.reposition()
            c.update()
        elif self.mode == "insert":
            self.insert_font = font
            self.insert_size = size
            self.insert_color = color
            self.insert_flags = flags
            self._style_inline_insert()
            self.reposition()
            c.update()
        elif 0 <= c._selected_overlay_idx < len(c._overlays):
            ov = c._overlays[c._selected_overlay_idx]
            if ov.get("type") == "text_edit":
                ov["font"] = font
                ov["font_size"] = size
                ov["size"] = size
                ov["color"] = color
                ov["flags"] = flags
                ov["_font_changed"] = True
                c.overlay_changed.emit()
                c.update()
            elif ov.get("type") == "text":
                ov["font"] = font
                ov["size"] = size
                ov["color"] = color
                ov["flags"] = flags
                c.overlay_changed.emit()
                c.update()

    def _style_inline_insert(self):
        family = _resolve_font_family(self.insert_font)
        fnt = QFont(family)
        z = self.canvas._zoom or 1.0
        px_size = max(6, int(round(self.insert_size * z)))
        fnt.setPixelSize(px_size)
        bold = bool(self.insert_flags & 16)
        italic = bool(self.insert_flags & 2)
        if bold:
            fnt.setBold(True)
        if italic:
            fnt.setItalic(True)
        self.edit.setFont(fnt)

        c = self.insert_color
        r, g, b = int(c[0]*255), int(c[1]*255), int(c[2]*255)
        weight_str = "bold" if bold else "normal"
        style_str = "italic" if italic else "normal"
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: #FFFFFF; color: #{r:02x}{g:02x}{b:02x};"
            f" font-family: '{family}', Arial, sans-serif; font-size: {px_size}px;"
            f" font-weight: {weight_str}; font-style: {style_str};"
            f" border: 1.5px solid {ACCENT}; border-radius: 2px; padding: 0px 2px; }}"
        )

    def _style_inline_edit(self, span: dict):
        bb = span["bbox"]
        sz = float(span.get("font_size") or span.get("size") or 0)
        if sz <= 0:
            sz = max(6.0, float(bb[3] - bb[1]) * 0.75)
        family = _resolve_font_family(span.get("font", ""))
        fnt = QFont(family)
        z = self.canvas._zoom or 1.0
        px_size = max(6, int(round(sz * z)))
        fnt.setPixelSize(px_size)
        flags = int(span.get("flags", 0) or 0)
        fname = span.get("font", "").lower()
        bold = bool((flags & 16) or "bold" in fname)
        italic = bool((flags & 2) or "italic" in fname or "oblique" in fname)
        if bold:
            fnt.setBold(True)
        if italic:
            fnt.setItalic(True)
        self.edit.setFont(fnt)

        c = span.get("color", 0)
        if isinstance(c, int):
            r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
        elif isinstance(c, (list, tuple)) and len(c) >= 3:
            r, g, b = int(c[0]*255), int(c[1]*255), int(c[2]*255)
        else:
            r, g, b = 0, 0, 0
        weight_str = "bold" if bold else "normal"
        style_str = "italic" if italic else "normal"
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: #FFFFFF; color: #{r:02x}{g:02x}{b:02x};"
            f" font-family: '{family}', Arial, sans-serif; font-size: {px_size}px;"
            f" font-weight: {weight_str}; font-style: {style_str};"
            f" border: 1.5px solid {ACCENT}; border-radius: 2px; padding: 0px 2px; }}"
        )

    def reposition(self):
        c = self.canvas
        if self.mode is None or self.page_idx < 0 or self.page_idx >= len(c._page_offsets):
            return
        z = c._zoom or 1.0
        yo = c._page_offsets[self.page_idx][0]
        if self.mode == "edit" and self.span is not None:
            self._style_inline_edit(self.span)
            bb = self.span["bbox"]
            sz = float(self.span.get("font_size") or self.span.get("size") or 12.0)
            px_size = max(6, int(round(sz * z)))
            origin = self.span.get("origin")
            fm = self.edit.fontMetrics()
            txt = self.edit.text()
            txt_w = fm.horizontalAdvance(txt) if txt else 0
            w = max(int((bb[2] - bb[0]) * z) + 12, int(txt_w + px_size * 2), 60)
            h = max(int((bb[3] - bb[1]) * z) + 4, int(px_size * 1.35) + 4, 22)
            if origin:
                baseline_y = yo + int(round(origin[1] * z))
                ascent = fm.ascent()
                top_y = baseline_y - ascent - (h - fm.height()) // 2
                x = int(round(origin[0] * z)) - 3
                y = top_y
            else:
                x = int(round(bb[0] * z)) - 3
                y = yo + int(round(bb[1] * z)) - 2
            self.edit.setGeometry(x, y, w, h)
        elif self.mode == "insert" and self.insert_point is not None:
            self._style_inline_insert()
            px, py = self.insert_point
            size = self.insert_size
            px_size = max(6, int(round(size * z)))
            fm = self.edit.fontMetrics()
            txt = self.edit.text()
            txt_w = fm.horizontalAdvance(txt) if txt else 0
            w = max(100, int(txt_w + px_size * 2))
            h = max(int(px_size * 1.35) + 4, 22)
            x = int(round(px * z)) - 3
            y = yo + int(round(py * z)) - 2
            self.edit.setGeometry(x, y, w, h)

    def commit(self):
        if self.mode is None or not self.edit.isVisible():
            return
        new_text = self.edit.text()
        mode = self.mode
        page_idx = self.page_idx
        span = self.span
        ipoint = self.insert_point
        isize = self.insert_size
        icolor = self.insert_color
        ifont = self.insert_font
        iflags = self.insert_flags
        original = self.original_text

        self.edit.hide()
        self.mode = None
        self.span = None
        self.page_idx = -1
        self.insert_point = None
        self.original_text = ""

        if mode == "edit" and span is not None:
            if new_text == original:
                return
            bb = span["bbox"]
            span_size = float(span.get("font_size") or span.get("size") or 0)
            if span_size <= 0:
                span_size = max(6.0, float(bb[3] - bb[1]) * 0.75)
            font_name = span.get("font", "Helvetica")
            edit = {
                "type": "text_edit",
                "page": page_idx,
                "bbox": list(bb),
                "old_text": span.get("text", ""),
                "new_text": new_text,
                "size": span_size,
                "font_size": span_size,
                "color": span.get("color", 0),
                "font": font_name,
                "flags": int(span.get("flags", 0) or 0),
                "ascender": float(span.get("ascender") or 0.8),
                "descender": float(span.get("descender") or -0.2),
                "origin": list(span.get("origin", (bb[0], bb[3]))),
                "_font_changed": bool(font_name != span.get("font", "")),
            }
            self.canvas.text_edit_committed.emit(page_idx, edit)
        elif mode == "insert" and ipoint is not None:
            if not new_text.strip():
                return
            z = self.canvas._zoom or 1.0
            yo = self.canvas._page_offsets[page_idx][0] if page_idx < len(self.canvas._page_offsets) else 0
            fm = self.edit.fontMetrics()
            screen_x = self.edit.x() + 3
            screen_y = self.edit.y() + (self.edit.height() - fm.height()) // 2 + fm.ascent()
            pdf_baseline_x = screen_x / z
            pdf_baseline_y = (screen_y - yo) / z

            char_w = isize * 0.55
            est_w = max(20.0, len(new_text) * char_w)
            pdf_top_y = (self.edit.y() + (self.edit.height() - fm.height()) // 2 - yo) / z
            pdf_bottom_y = pdf_top_y + fm.height() / z
            rect = fitz.Rect(pdf_baseline_x, pdf_top_y, pdf_baseline_x + est_w, pdf_bottom_y)
            edit = {
                "type": "text",
                "page": page_idx,
                "point": fitz.Point(pdf_baseline_x, pdf_baseline_y),
                "rect": rect,
                "text": new_text,
                "size": isize,
                "color": icolor,
                "font": ifont,
                "flags": iflags,
            }
            self.canvas.text_inserted.emit(page_idx, edit)

    def cancel(self):
        self.edit.hide()
        self.mode = None
        self.span = None
        self.page_idx = -1
        self.insert_point = None
        self.original_text = ""

    def handle_event_filter(self, obj, event) -> bool:
        if obj is self.edit:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self.cancel()
                    return True
                if event.key() == Qt.Key.Key_Tab:
                    self.commit()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                self.commit()
                return False
        return False

