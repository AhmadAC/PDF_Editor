# app/editor/canvas_text1.py
"""Inline text editor controller for Foxit-style in-place editing & insertion with per-selection color, live shadow/outline preview, downward multi-line expansion, and pixel-locked typography."""

from __future__ import annotations

import math
import fitz
from PySide6.QtCore import Qt, QEvent, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QTextEdit
from shiboken6 import isValid

from app.constants import ACCENT

_EFFECT_KEYS = (
    "shadow_enabled",
    "shadow_angle",
    "shadow_opacity",
    "shadow_distance",
    "shadow_thickness",
    "shadow_color",
    "outline_enabled",
    "outline_thickness",
    "outline_color",
)


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


def build_canvas_qfont(
    device,
    font_name: str,
    pt_size: float,
    zoom: float,
    bold: bool = False,
    italic: bool = False,
) -> QFont:
    """Build the exact QFont shared by both _InlineRichEdit and CanvasPainter."""
    resolved_fam = _resolve_font_family(font_name)
    fnt = QFont(resolved_fam)
    dpi_y = (
        float(device.logicalDpiY())
        if (device is not None and hasattr(device, "logicalDpiY") and device.logicalDpiY() > 0)
        else 96.0
    )
    dpi_scale = 72.0 / dpi_y
    fnt.setPointSizeF(max(4.0, float(pt_size) * float(zoom or 1.0) * dpi_scale))
    fn_low = (font_name or "").lower()
    if bold or "bold" in fn_low:
        fnt.setBold(True)
    if italic or "italic" in fn_low or "oblique" in fn_low:
        fnt.setItalic(True)
    return fnt


def _find_tab_editar(canvas):
    p = canvas.parentWidget() if hasattr(canvas, "parentWidget") else None
    while p is not None:
        if hasattr(p, "_set_mode_by_idx") and hasattr(p, "_set_text_submode"):
            return p
        p = p.parentWidget()
    return None


def _color_value_to_qcolor(c) -> QColor:
    if isinstance(c, QColor):
        return QColor(c)
    if isinstance(c, int):
        return QColor((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return QColor(
            max(0, min(255, int(round(float(c[0]) * 255)))),
            max(0, min(255, int(round(float(c[1]) * 255)))),
            max(0, min(255, int(round(float(c[2]) * 255)))),
        )
    return QColor(0, 0, 0)


def _color_value_to_tuple(c) -> tuple[float, float, float]:
    qc = _color_value_to_qcolor(c)
    return (round(qc.redF(), 4), round(qc.greenF(), 4), round(qc.blueF(), 4))


class _InlineRichEdit(QTextEdit):
    """Inline editor supporting per-selection rich text colors, downward multi-line Enter expansion, and exact font sync."""

    returnPressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inline_pdf_text_edit")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._base_color = QColor(0, 0, 0)
        self._multiline_enter = False
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(False)
        self.document().setDocumentMargin(2.0)
        self.setViewportMargins(0, 0, 0, 0)
        self.viewport().setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.setPalette(pal)

    def set_multiline_enter(self, enabled: bool) -> None:
        self._multiline_enter = bool(enabled)

    def reset_scroll_offsets(self) -> None:
        hsb = self.horizontalScrollBar()
        vsb = self.verticalScrollBar()
        if hsb:
            hsb.setValue(0)
        if vsb:
            vsb.setValue(0)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, txt: str) -> None:
        self.setPlainText(txt or "")

    def set_base_text_color(self, col: QColor) -> None:
        self._base_color = QColor(col)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, self._base_color)
        self.setPalette(pal)

    def hasSelectedText(self) -> bool:
        return self.textCursor().hasSelection()

    def selectedText(self) -> str:
        return self.textCursor().selectedText()

    def selectionStart(self) -> int:
        c = self.textCursor()
        return min(c.anchor(), c.position()) if c.hasSelection() else -1

    def selectionLength(self) -> int:
        c = self.textCursor()
        return abs(c.position() - c.anchor()) if c.hasSelection() else 0

    def setSelection(self, start: int, length: int) -> None:
        c = self.textCursor()
        doc_len = max(0, self.document().characterCount() - 1)
        s = max(0, min(start, doc_len))
        e = max(0, min(start + length, doc_len))
        c.setPosition(s)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)

    def cursorPosition(self) -> int:
        return self.textCursor().position()

    def setCursorPosition(self, pos: int) -> None:
        c = self.textCursor()
        doc_len = max(0, self.document().characterCount() - 1)
        c.setPosition(max(0, min(pos, doc_len)))
        self.setTextCursor(c)

    def load_runs(
        self,
        runs: list[dict],
        base_font: QFont,
        base_color: QColor,
    ) -> None:
        self.blockSignals(True)
        self.clear()
        self.setFont(base_font)
        self.document().setDefaultFont(base_font)
        self.set_base_text_color(base_color)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.Start)
        for run in runs:
            r_txt = run.get("text", "")
            if not r_txt:
                continue
            fmt = QTextCharFormat()
            fmt.setFontFamily(base_font.family())
            fmt.setFontPointSize(base_font.pointSizeF())
            fmt.setForeground(_color_value_to_qcolor(run.get("color", base_color)))
            is_b = bool(run.get("bold", base_font.bold()))
            is_i = bool(run.get("italic", base_font.italic()))
            fmt.setFontWeight(QFont.Weight.Bold if is_b else QFont.Weight.Normal)
            fmt.setFontItalic(is_i)
            cur.insertText(r_txt, fmt)
        self.blockSignals(False)

    def apply_uniform_style(self, base_font: QFont, base_color: QColor, preserve_runs: bool = False) -> None:
        self.setFont(base_font)
        self.document().setDefaultFont(base_font)
        self.set_base_text_color(base_color)
        fmt = QTextCharFormat()
        fmt.setFontFamily(base_font.family())
        fmt.setFontPointSize(base_font.pointSizeF())
        fmt.setFontWeight(QFont.Weight.Bold if base_font.bold() else QFont.Weight.Normal)
        fmt.setFontItalic(base_font.italic())
        if not preserve_runs:
            fmt.setForeground(base_color)
        c_all = QTextCursor(self.document())
        c_all.select(QTextCursor.SelectionType.Document)
        c_all.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    def extract_runs(
        self,
        default_color: tuple[float, float, float],
        default_bold: bool,
        default_italic: bool,
    ) -> list[dict]:
        cur = QTextCursor(self.document())
        cur.setPosition(0)
        runs: list[dict] = []
        while not cur.atEnd():
            cur.clearSelection()
            if not cur.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor):
                break
            ch = cur.selectedText().replace("\u2029", "\n")
            if not ch:
                break
            cf = cur.charFormat()
            if cf.hasProperty(QTextFormat.Property.ForegroundBrush) and cf.foreground().style() != Qt.BrushStyle.NoBrush:
                qc = cf.foreground().color()
                col = (round(qc.redF(), 4), round(qc.greenF(), 4), round(qc.blueF(), 4))
            else:
                col = default_color
            if cf.hasProperty(QTextFormat.Property.FontWeight):
                bold = cf.fontWeight() >= QFont.Weight.Bold
            else:
                bold = default_bold
            if cf.hasProperty(QTextFormat.Property.FontItalic):
                italic = cf.fontItalic()
            else:
                italic = default_italic

            if runs and runs[-1]["color"] == col and runs[-1]["bold"] == bold and runs[-1]["italic"] == italic:
                runs[-1]["text"] += ch
            else:
                runs.append({"text": ch, "color": col, "bold": bold, "italic": italic})
        return runs

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = e.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                e.accept()
                self.returnPressed.emit()
                return
            if self._multiline_enter or (mods & Qt.KeyboardModifier.ShiftModifier):
                super().keyPressEvent(e)
                self.reset_scroll_offsets()
                QTimer.singleShot(0, lambda: self.reset_scroll_offsets() if isValid(self) and self.isVisible() else None)
                return
            e.accept()
            self.returnPressed.emit()
            return
        super().keyPressEvent(e)
        self.reset_scroll_offsets()


class CanvasInlineTextManager:
    def __init__(self, canvas):
        self.canvas = canvas
        self.edit = _InlineRichEdit(canvas)
        self.edit.hide()
        self.edit.installEventFilter(canvas)
        self.edit.returnPressed.connect(self.commit)
        self.edit.textChanged.connect(lambda *args: self.reposition() if self.mode in ("insert", "edit") else None)

        self.mode: str | None = None
        self.span: dict | None = None
        self.target_overlay: dict | None = None
        self.page_idx: int = -1
        self.original_text: str = ""
        self.insert_point: tuple[float, float] | None = None
        self.insert_size: float = 12.0
        self.insert_color: tuple = (0.0, 0.0, 0.0)
        self.insert_font: str = "Helvetica"
        self.insert_flags: int = 0
        self.insert_effects: dict = {}

    @staticmethod
    def _resolve_font_family(fname: str) -> str:
        return _resolve_font_family(fname)

    def _line0_ascent(self) -> float:
        try:
            blk = self.edit.document().begin()
            lay = blk.layout()
            if lay is not None and lay.lineCount() > 0:
                asc = float(lay.lineAt(0).ascent())
                if asc > 0:
                    return asc
        except Exception:
            pass
        return float(self.edit.fontMetrics().ascent())

    def _line_step_px(self) -> float:
        try:
            doc = self.edit.document()
            if doc.blockCount() >= 2:
                b0 = doc.begin()
                b1 = b0.next()
                p0 = doc.documentLayout().blockBoundingRect(b0).top()
                p1 = doc.documentLayout().blockBoundingRect(b1).top()
                diff = float(p1 - p0)
                if diff > 1.0:
                    return diff
        except Exception:
            pass
        return float(self.edit.fontMetrics().lineSpacing())

    def _text_origin_offset_in_edit(self) -> tuple[int, int]:
        """Pixel offset from (edit.x(), edit.y()) to the top-left of Line 0 text."""
        doc_m = int(round(self.edit.document().documentMargin()))
        vp_x = self.edit.viewport().x() if self.edit.isVisible() else 1
        vp_y = self.edit.viewport().y() if self.edit.isVisible() else 1
        return (vp_x + doc_m, vp_y + doc_m)

    def _apply_edit_stylesheet(self, fnt: QFont, bg_style: str) -> None:
        safe_fam = fnt.family().replace('"', "")
        pt_val = max(4.0, fnt.pointSizeF())
        weight_css = "bold" if fnt.bold() else "normal"
        style_css = "italic" if fnt.italic() else "normal"
        self.edit.setStyleSheet(
            f"QTextEdit#inline_pdf_text_edit {{ "
            f"  background: {bg_style}; background-color: {bg_style}; "
            f"  border: 1px solid {ACCENT}; border-radius: 2px; "
            f"  padding: 0px; margin: 0px; "
            f"  font-family: \"{safe_fam}\"; font-size: {pt_val:.2f}pt; "
            f"  font-weight: {weight_css}; font-style: {style_css}; "
            f"}} "
            f"QTextEdit#inline_pdf_text_edit > QWidget {{ "
            f"  background: {bg_style}; background-color: {bg_style}; "
            f"  border: none; padding: 0px; margin: 0px; "
            f"}}"
        )

    def _apply_viewport_effects(self, effects: dict | None) -> None:
        vp = self.edit.viewport()
        if not effects or not effects.get("shadow_enabled"):
            vp.setGraphicsEffect(None)  # type: ignore[arg-type]
            return
        z = self.canvas._zoom or 1.0
        rad = math.radians(float(effects.get("shadow_angle", 45.0)))
        dist = float(effects.get("shadow_distance", 3.0)) * z
        dx = dist * math.cos(rad)
        dy = dist * math.sin(rad)
        sh_col = _color_value_to_qcolor(effects.get("shadow_color", (0.0, 0.0, 0.0)))
        sh_op = max(0.05, min(1.0, float(effects.get("shadow_opacity", 0.5))))
        sh_col.setAlphaF(sh_op)
        blur = max(0.0, float(effects.get("shadow_thickness", 0.0)) * 2.0 * z)
        eff = QGraphicsDropShadowEffect(vp)
        eff.setOffset(dx, dy)
        eff.setBlurRadius(blur)
        eff.setColor(sh_col)
        vp.setGraphicsEffect(eff)

    def begin_edit(self, span: dict, page_idx: int, target_overlay: dict | None = None):
        if self.edit.isVisible():
            self.commit()
        self.mode = "edit"
        self.span = dict(span)
        self.target_overlay = target_overlay
        self.page_idx = page_idx
        self.original_text = span.get("text", "")
        is_added_overlay = bool(target_overlay and target_overlay.get("type") == "text")
        self.edit.set_multiline_enter(is_added_overlay)

        bb = span["bbox"]
        visual_size = float(span.get("size") or span.get("font_size") or 0)
        if visual_size <= 0:
            visual_size = max(6.0, float(bb[3] - bb[1]) * 0.75)
        font_name = span.get("font", "Helvetica")
        flags = int(span.get("flags", 0) or 0)
        color = _color_value_to_tuple(span.get("color", 0))

        self.span["_orig_color"] = color
        self.span["_orig_font"] = font_name
        self.span["_orig_size"] = visual_size
        self.span["_orig_flags"] = flags
        self.span["_orig_runs"] = [dict(r) for r in span["runs"]] if span.get("runs") else None
        self.span["_orig_effects"] = {k: span[k] for k in _EFFECT_KEYS if k in span}

        self._style_inline_edit(self.span, preserve_runs=True)

        runs = span.get("runs")
        if runs:
            fnt = self.edit.font()
            self.edit.load_runs(runs, fnt, _color_value_to_qcolor(color))
        else:
            self.edit.blockSignals(True)
            self.edit.setText(self.original_text)
            self.edit.blockSignals(False)
            self.edit.apply_uniform_style(self.edit.font(), _color_value_to_qcolor(color), preserve_runs=False)

        self.reposition()
        self.edit.show()
        self.edit.raise_()
        self.reposition()
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.edit.selectAll()

        started_fmt: dict = {
            "font": font_name,
            "size": visual_size,
            "color": color,
            "bold": bool(flags & 16),
            "italic": bool(flags & 2),
            "text": self.original_text,
        }
        for k in _EFFECT_KEYS:
            if k in self.span:
                started_fmt[k] = self.span[k]
        self.canvas.text_edit_started.emit(started_fmt)

    def begin_insert(
        self,
        page_idx: int,
        pdf_point,
        size: float,
        color: tuple,
        font: str = "",
        bold: bool = False,
        italic: bool = False,
        effects: dict | None = None,
    ):
        if self.edit.isVisible():
            self.commit()
        self.mode = "insert"
        self.span = None
        self.target_overlay = None
        self.page_idx = page_idx
        self.original_text = ""
        self.insert_point = (float(pdf_point.x), float(pdf_point.y))
        self.insert_size = float(size)
        self.insert_color = tuple(color)
        self.insert_font = font or "Helvetica"
        self.insert_flags = (16 if bold else 0) | (2 if italic else 0)
        self.insert_effects = dict(effects or {})
        self.edit.set_multiline_enter(True)
        self.edit.blockSignals(True)
        self.edit.setText("")
        self.edit.blockSignals(False)
        self._style_inline_insert(preserve_runs=False)
        self.reposition()
        self.edit.show()
        self.edit.raise_()
        self.reposition()
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)

        started_fmt: dict = {
            "font": self.insert_font,
            "size": self.insert_size,
            "color": self.insert_color,
            "bold": bold,
            "italic": italic,
            "text": "",
        }
        started_fmt.update(self.insert_effects)
        self.canvas.text_edit_started.emit(started_fmt)

    def apply_color(self, color: tuple[float, float, float]) -> None:
        """Apply color to the selected substring inside the inline editor, or the whole text box if nothing is selected."""
        if not self.edit.isVisible():
            return
        qcol = _color_value_to_qcolor(color)
        fmt = QTextCharFormat()
        fmt.setForeground(qcol)

        cursor = self.edit.textCursor()
        if cursor.hasSelection():
            sel_start = cursor.selectionStart()
            sel_end = cursor.selectionEnd()
            cursor.mergeCharFormat(fmt)
            self.edit.setTextCursor(cursor)
            self.edit.setSelection(sel_start, sel_end - sel_start)
            doc_len = len(self.edit.toPlainText())
            if sel_start == 0 and sel_end >= doc_len:
                if self.mode == "edit" and self.span is not None:
                    self.span["color"] = color
                elif self.mode == "insert":
                    self.insert_color = color
                self.edit.set_base_text_color(qcol)
        else:
            c_all = QTextCursor(self.edit.document())
            c_all.select(QTextCursor.SelectionType.Document)
            c_all.mergeCharFormat(fmt)
            self.edit.mergeCurrentCharFormat(fmt)
            self.edit.set_base_text_color(qcol)
            if self.mode == "edit" and self.span is not None:
                self.span["color"] = color
            elif self.mode == "insert":
                self.insert_color = color
        self.canvas.update()

    def update_format(
        self,
        font: str,
        size: float,
        color: tuple,
        bold: bool,
        italic: bool,
        effects: dict | None = None,
    ):
        flags = (16 if bold else 0) | (2 if italic else 0)
        c = self.canvas
        if self.mode == "edit" and self.span is not None:
            prev_color = _color_value_to_tuple(self.span.get("color", 0))
            self.span["font"] = font
            self.span["size"] = size
            self.span["font_size"] = size
            self.span["flags"] = flags
            if effects is not None:
                for k in _EFFECT_KEYS:
                    if k in effects:
                        self.span[k] = effects[k]
                    else:
                        self.span.pop(k, None)
            self._style_inline_edit(self.span, preserve_runs=True)
            if _color_value_to_tuple(color) != prev_color:
                self.apply_color(color)
            self.reposition()
            c.update()
        elif self.mode == "insert":
            prev_color = _color_value_to_tuple(self.insert_color)
            self.insert_font = font
            self.insert_size = size
            self.insert_flags = flags
            if effects is not None:
                self.insert_effects = dict(effects)
            self._style_inline_insert(preserve_runs=True)
            if _color_value_to_tuple(color) != prev_color:
                self.apply_color(color)
            self.reposition()
            c.update()
        elif 0 <= c._selected_overlay_idx < len(c._overlays):
            ov = c._overlays[c._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                ov["font"] = font
                ov["size"] = size
                if ov.get("type") == "text_edit":
                    ov["font_size"] = size
                    ov["_font_changed"] = True
                ov["color"] = color
                ov["flags"] = flags
                if effects is not None:
                    for k in _EFFECT_KEYS:
                        if k in effects:
                            ov[k] = effects[k]
                        else:
                            ov.pop(k, None)
                if ov.get("runs"):
                    ov["runs"] = [
                        dict(r, bold=bold, italic=italic, color=color)
                        for r in ov["runs"]
                    ]
                c.overlay_changed.emit()
                c.update()

    def _style_inline_insert(self, preserve_runs: bool = True):
        z = self.canvas._zoom or 1.0
        bold = bool(self.insert_flags & 16)
        italic = bool(self.insert_flags & 2)
        fnt = build_canvas_qfont(self.canvas, self.insert_font, self.insert_size, z, bold, italic)
        qcol = _color_value_to_qcolor(self.insert_color)

        self._apply_edit_stylesheet(fnt, "transparent")
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.edit.setPalette(pal)
        self.edit.apply_uniform_style(fnt, qcol, preserve_runs=preserve_runs)
        self._apply_viewport_effects(self.insert_effects)

    def _style_inline_edit(self, span: dict, preserve_runs: bool = True):
        bb = span["bbox"]
        sz = float(span.get("font_size") or span.get("size") or 0)
        if sz <= 0:
            sz = max(6.0, float(bb[3] - bb[1]) * 0.75)
        z = self.canvas._zoom or 1.0
        flags = int(span.get("flags", 0) or 0)
        fname = span.get("font", "")
        bold = bool((flags & 16) or "bold" in fname.lower())
        italic = bool((flags & 2) or "italic" in fname.lower() or "oblique" in fname.lower())
        fnt = build_canvas_qfont(self.canvas, fname, sz, z, bold, italic)

        qcol = _color_value_to_qcolor(span.get("color", 0))
        is_added_overlay = bool(self.target_overlay and self.target_overlay.get("type") == "text")
        bg_style = "transparent" if is_added_overlay else "#FFFFFF"
        self._apply_edit_stylesheet(fnt, bg_style)
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent if is_added_overlay else QColor("#FFFFFF"))
        self.edit.setPalette(pal)
        self.edit.apply_uniform_style(fnt, qcol, preserve_runs=preserve_runs)
        self._apply_viewport_effects(span)

    def reposition(self):
        c = self.canvas
        if self.mode is None or self.page_idx < 0 or self.page_idx >= len(c._page_offsets):
            return
        z = c._zoom or 1.0
        yo = c._page_offsets[self.page_idx][0]
        self.edit.document().setTextWidth(-1)
        doc_sz = self.edit.document().documentLayout().documentSize()
        pad_x, pad_y = self._text_origin_offset_in_edit()
        frame_w = (self.edit.width() - self.edit.viewport().width()) if self.edit.isVisible() else 2
        frame_h = (self.edit.height() - self.edit.viewport().height()) if self.edit.isVisible() else 2
        frame_w = max(2, frame_w)
        frame_h = max(2, frame_h)

        fm = self.edit.fontMetrics()
        txt = self.edit.toPlainText()
        lines = txt.split("\n") if txt else [""]
        max_line_w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
        content_w = max(max_line_w, float(doc_sz.width()))

        if self.mode == "edit" and self.span is not None:
            bb = self.span["bbox"]
            sz = float(self.span.get("font_size") or self.span.get("size") or 12.0)
            px_size = max(6.0, sz * z)
            origin = self.span.get("origin")
            cursor_extra = max(24, int(round(px_size * 1.8)))
            orig_span_w = int((bb[2] - bb[0]) * z)
            w = max(orig_span_w + 14, int(math.ceil(content_w + cursor_extra + frame_w + 8)), 64)
            content_h = max(
                float(doc_sz.height()),
                fm.lineSpacing() * len(lines) + 2.0 * self.edit.document().documentMargin(),
            )
            h = max(int((bb[3] - bb[1]) * z) + 6, int(math.ceil(content_h + frame_h + 6)), 24)
            if origin:
                baseline_y = yo + origin[1] * z
                ascent = self._line0_ascent()
                x = int(round(origin[0] * z)) - pad_x
                y = int(round(baseline_y - ascent)) - pad_y
            else:
                x = int(round(bb[0] * z)) - pad_x
                y = yo + int(round(bb[1] * z)) - pad_y
            need_w = max(c.width(), x + w + 32)
            need_h = max(c.height(), y + h + 32)
            if need_w > c.width() or need_h > c.height():
                c.setFixedSize(need_w, need_h)
            self.edit.setGeometry(x, y, w, h)
            self.edit.reset_scroll_offsets()
        elif self.mode == "insert" and self.insert_point is not None:
            px, py = self.insert_point
            size = self.insert_size
            px_size = max(6.0, size * z)
            cursor_extra = max(24, int(round(px_size * 1.8)))
            w = max(100, int(math.ceil(content_w + cursor_extra + frame_w + 8)))
            content_h = max(
                float(doc_sz.height()),
                fm.lineSpacing() * len(lines) + 2.0 * self.edit.document().documentMargin(),
            )
            h = max(24, int(math.ceil(content_h + frame_h + 6)))
            x = int(round(px * z)) - pad_x
            y = yo + int(round(py * z)) - pad_y
            need_w = max(c.width(), x + w + 32)
            need_h = max(c.height(), y + h + 32)
            if need_w > c.width() or need_h > c.height():
                c.setFixedSize(need_w, need_h)
            self.edit.setGeometry(x, y, w, h)
            self.edit.reset_scroll_offsets()

    def commit(self):
        if self.mode is None or not self.edit.isVisible():
            return
        new_text = self.edit.toPlainText()
        mode = self.mode
        page_idx = self.page_idx
        span = self.span
        target_ov = self.target_overlay
        ipoint = self.insert_point
        isize = self.insert_size
        icolor = self.insert_color
        ifont = self.insert_font
        iflags = self.insert_flags
        ieffects = dict(self.insert_effects)
        original = self.original_text

        base_color_tuple = (
            _color_value_to_tuple(span.get("color", 0))
            if (mode == "edit" and span is not None)
            else _color_value_to_tuple(icolor)
        )
        base_flags = (
            int(span.get("flags", 0) or 0)
            if (mode == "edit" and span is not None)
            else int(iflags)
        )
        raw_runs = self.edit.extract_runs(
            default_color=base_color_tuple,
            default_bold=bool(base_flags & 16),
            default_italic=bool(base_flags & 2),
        )
        if len(raw_runs) > 1:
            runs_to_store: list[dict] | None = raw_runs
            effective_color = raw_runs[0]["color"]
        elif len(raw_runs) == 1:
            runs_to_store = None
            effective_color = raw_runs[0]["color"]
        else:
            runs_to_store = None
            effective_color = base_color_tuple

        z = self.canvas._zoom or 1.0
        fm = self.edit.fontMetrics()
        pad_x, pad_y = self._text_origin_offset_in_edit()
        line0_ascent = self._line0_ascent()
        line_step_px = self._line_step_px()
        edit_x = self.edit.x()
        edit_y = self.edit.y()

        self.edit.viewport().setGraphicsEffect(None)  # type: ignore[arg-type]
        self.edit.hide()
        self.mode = None
        self.span = None
        self.target_overlay = None
        self.page_idx = -1
        self.insert_point = None
        self.original_text = ""

        if mode == "edit" and span is not None:
            orig_col = span.get("_orig_color", base_color_tuple)
            orig_font = span.get("_orig_font", span.get("font", "Helvetica"))
            orig_size = float(span.get("_orig_size") or span.get("font_size") or span.get("size") or 12.0)
            orig_flags = int(span.get("_orig_flags", base_flags) or 0)
            orig_runs = span.get("_orig_runs")
            orig_effects = span.get("_orig_effects", {})
            cur_effects = {k: span[k] for k in _EFFECT_KEYS if k in span}

            bb = span["bbox"]
            span_size = float(span.get("font_size") or span.get("size") or 0)
            if span_size <= 0:
                span_size = max(6.0, float(bb[3] - bb[1]) * 0.75)
            font_name = span.get("font", "Helvetica")

            changed = (
                new_text != original
                or effective_color != orig_col
                or runs_to_store != orig_runs
                or font_name != orig_font
                or abs(span_size - orig_size) > 0.05
                or base_flags != orig_flags
                or cur_effects != orig_effects
            )
            if not changed:
                return

            if target_ov is not None and target_ov in self.canvas._overlays:
                from app.editor.canvas_overlay1 import snapshot_overlay
                if target_ov.get("type") == "text" and not new_text.strip():
                    idx = self.canvas._overlays.index(target_ov)
                    self.canvas._selected_overlay_idx = idx
                    self.canvas.delete_selected_overlay()
                    return

                before_snap = snapshot_overlay(target_ov)
                was_untouched = bool(target_ov.get("_existing") and not target_ov.get("_dirty"))
                target_ov["new_text"] = new_text
                target_ov["text"] = new_text
                target_ov["color"] = effective_color
                target_ov["font"] = font_name
                target_ov["size"] = span_size
                target_ov["font_size"] = span_size
                target_ov["flags"] = base_flags
                target_ov["line_step"] = float(line_step_px / z)
                for k in _EFFECT_KEYS:
                    if k in cur_effects:
                        target_ov[k] = cur_effects[k]
                    else:
                        target_ov.pop(k, None)
                if runs_to_store:
                    target_ov["runs"] = runs_to_store
                else:
                    target_ov.pop("runs", None)
                if target_ov.get("type") == "text_edit" and font_name != orig_font:
                    target_ov["_font_changed"] = True

                lines = new_text.split("\n") or [""]
                est_w = max(20.0, max((fm.horizontalAdvance(ln) for ln in lines), default=20.0) / z)
                est_h = max(12.0, (line_step_px * len(lines)) / z)
                yo = self.canvas._page_offsets[page_idx][0] if page_idx < len(self.canvas._page_offsets) else 0
                screen_x = edit_x + pad_x
                top_screen_y = edit_y + pad_y
                baseline_screen_y = top_screen_y + line0_ascent

                if target_ov.get("type") == "text_edit" and "bbox" in target_ov:
                    ob = target_ov["bbox"]
                    target_ov["bbox"] = [ob[0], ob[1], max(ob[2], ob[0] + est_w), max(ob[3], ob[1] + est_h)]
                elif target_ov.get("type") == "text":
                    pdf_bx = screen_x / z
                    pdf_by = (baseline_screen_y - yo) / z
                    pdf_ty = (top_screen_y - yo) / z
                    target_ov["point"] = fitz.Point(pdf_bx, pdf_by)
                    target_ov["rect"] = fitz.Rect(pdf_bx, pdf_ty, pdf_bx + est_w, pdf_ty + est_h)

                if was_untouched:
                    target_ov["_dirty"] = True
                    self.canvas.overlay_committed.emit(target_ov)
                else:
                    self.canvas.overlay_transformed.emit(target_ov, before_snap)
                if was_untouched or target_ov.get("_z_back"):
                    self.canvas.refresh_pages({page_idx})
                self.canvas.overlay_changed.emit()
                self.canvas.update()
                return

            edit = {
                "type": "text_edit",
                "page": page_idx,
                "bbox": list(bb),
                "old_text": span.get("text", ""),
                "new_text": new_text,
                "size": span_size,
                "font_size": span_size,
                "color": effective_color,
                "font": font_name,
                "flags": base_flags,
                "ascender": float(span.get("ascender") or 0.8),
                "descender": float(span.get("descender") or -0.2),
                "origin": list(span.get("origin", (bb[0], bb[3]))),
                "line_step": float(line_step_px / z),
                "_font_changed": bool(font_name != orig_font),
            }
            for k in _EFFECT_KEYS:
                if k in cur_effects:
                    edit[k] = cur_effects[k]
            if runs_to_store:
                edit["runs"] = runs_to_store
            self.canvas.text_edit_committed.emit(page_idx, edit)
        elif mode == "insert" and ipoint is not None:
            if not new_text.strip():
                return
            yo = self.canvas._page_offsets[page_idx][0] if page_idx < len(self.canvas._page_offsets) else 0
            lines = new_text.split("\n") or [""]
            screen_x = edit_x + pad_x
            top_screen_y = edit_y + pad_y
            baseline_screen_y = top_screen_y + line0_ascent
            pdf_baseline_x = screen_x / z
            pdf_baseline_y = (baseline_screen_y - yo) / z

            est_w = max(20.0, max((fm.horizontalAdvance(ln) for ln in lines), default=20.0) / z)
            total_h_px = max(float(fm.height()), line_step_px * len(lines))
            pdf_top_y = (top_screen_y - yo) / z
            pdf_bottom_y = pdf_top_y + total_h_px / z
            rect = fitz.Rect(pdf_baseline_x, pdf_top_y, pdf_baseline_x + est_w, pdf_bottom_y)
            edit = {
                "type": "text",
                "page": page_idx,
                "point": fitz.Point(pdf_baseline_x, pdf_baseline_y),
                "rect": rect,
                "text": new_text,
                "size": isize,
                "color": effective_color,
                "font": ifont,
                "flags": iflags,
                "line_step": float(line_step_px / z),
            }
            for k in _EFFECT_KEYS:
                if k in ieffects:
                    edit[k] = ieffects[k]
            if runs_to_store:
                edit["runs"] = runs_to_store
            self.canvas.text_inserted.emit(page_idx, edit)

    def cancel(self):
        self.edit.viewport().setGraphicsEffect(None)  # type: ignore[arg-type]
        self.edit.hide()
        self.mode = None
        self.span = None
        self.target_overlay = None
        self.page_idx = -1
        self.insert_point = None
        self.original_text = ""

    def handle_event_filter(self, obj, event) -> bool:
        if obj is self.edit:
            etype = event.type()
            if etype == QEvent.Type.ShortcutOverride:
                mods = event.modifiers()
                if (mods & Qt.KeyboardModifier.ControlModifier) and not (
                    mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
                ):
                    if Qt.Key.Key_0 <= event.key() <= Qt.Key.Key_9:
                        event.accept()
                        return True
            elif etype == QEvent.Type.KeyPress:
                mods = event.modifiers()
                if (mods & Qt.KeyboardModifier.ControlModifier) and not (
                    mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
                ):
                    key = event.key()
                    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                        digit = key - Qt.Key.Key_0
                        tab = _find_tab_editar(self.canvas)
                        if tab and hasattr(tab, "_apply_color_shortcut"):
                            if tab._apply_color_shortcut(digit):
                                event.accept()
                                return True
                if event.key() == Qt.Key.Key_Escape:
                    self.cancel()
                    return True
                if event.key() == Qt.Key.Key_Tab:
                    self.commit()
                    return True
            elif etype == QEvent.Type.FocusOut:
                self.commit()
                return False
        return False