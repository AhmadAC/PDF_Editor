
# app/editor/canvas_text1.py
"""Inline text editor controller for Foxit-style in-place editing & insertion with per-selection color, multi-line expansion, and installed system font support."""

from __future__ import annotations

import fitz
from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QTextEdit

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
    """Inline editor supporting per-selection rich text colors, multi-line Enter expansion, and QLineEdit-compatible helpers."""

    returnPressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_color = QColor(0, 0, 0)
        self._multiline_enter = False
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(False)
        self.document().setDocumentMargin(2.0)
        self.setViewportMargins(2, 1, 2, 1)
        self.viewport().setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.setPalette(pal)

    def set_multiline_enter(self, enabled: bool) -> None:
        self._multiline_enter = bool(enabled)

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
            fmt.setForeground(_color_value_to_qcolor(run.get("color", base_color)))
            if "bold" in run:
                fmt.setFontWeight(QFont.Weight.Bold if run["bold"] else QFont.Weight.Normal)
            if "italic" in run:
                fmt.setFontItalic(bool(run["italic"]))
            cur.insertText(r_txt, fmt)
        self.blockSignals(False)

    def apply_uniform_style(self, base_font: QFont, base_color: QColor, preserve_runs: bool = False) -> None:
        self.setFont(base_font)
        self.document().setDefaultFont(base_font)
        self.set_base_text_color(base_color)
        if not preserve_runs:
            fmt = QTextCharFormat()
            fmt.setForeground(base_color)
            fmt.setFontWeight(QFont.Weight.Bold if base_font.bold() else QFont.Weight.Normal)
            fmt.setFontItalic(base_font.italic())
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
                return
            e.accept()
            self.returnPressed.emit()
            return
        super().keyPressEvent(e)


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

    @staticmethod
    def _resolve_font_family(fname: str) -> str:
        return _resolve_font_family(fname)

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
        self.target_overlay = None
        self.page_idx = page_idx
        self.original_text = ""
        self.insert_point = (float(pdf_point.x), float(pdf_point.y))
        self.insert_size = float(size)
        self.insert_color = tuple(color)
        self.insert_font = font or "Helvetica"
        self.insert_flags = (16 if bold else 0) | (2 if italic else 0)
        self.edit.set_multiline_enter(True)
        self.edit.blockSignals(True)
        self.edit.setText("")
        self.edit.blockSignals(False)
        self._style_inline_insert(preserve_runs=False)
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

    def update_format(self, font: str, size: float, color: tuple, bold: bool, italic: bool):
        flags = (16 if bold else 0) | (2 if italic else 0)
        c = self.canvas
        if self.mode == "edit" and self.span is not None:
            prev_color = _color_value_to_tuple(self.span.get("color", 0))
            self.span["font"] = font
            self.span["size"] = size
            self.span["font_size"] = size
            self.span["flags"] = flags
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
            self._style_inline_insert(preserve_runs=True)
            if _color_value_to_tuple(color) != prev_color:
                self.apply_color(color)
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
                if ov.get("runs"):
                    ov["runs"] = [
                        dict(r, bold=bold, italic=italic, color=color)
                        for r in ov["runs"]
                    ]
                c.overlay_changed.emit()
                c.update()
            elif ov.get("type") == "text":
                ov["font"] = font
                ov["size"] = size
                ov["color"] = color
                ov["flags"] = flags
                if ov.get("runs"):
                    ov["runs"] = [
                        dict(r, bold=bold, italic=italic, color=color)
                        for r in ov["runs"]
                    ]
                c.overlay_changed.emit()
                c.update()

    def _style_inline_insert(self, preserve_runs: bool = True):
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

        qcol = _color_value_to_qcolor(self.insert_color)
        self.edit.setStyleSheet(
            f"QTextEdit, QTextEdit > QWidget {{ "
            f"  background: transparent; background-color: transparent; "
            f"  border: 1.5px solid {ACCENT}; border-radius: 2px; padding: 0px; "
            f"}} "
            f"QTextEdit > QWidget {{ border: none; }}"
        )
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent)
        self.edit.setPalette(pal)
        self.edit.apply_uniform_style(fnt, qcol, preserve_runs=preserve_runs)

    def _style_inline_edit(self, span: dict, preserve_runs: bool = True):
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

        qcol = _color_value_to_qcolor(span.get("color", 0))
        is_added_overlay = bool(self.target_overlay and self.target_overlay.get("type") == "text")
        bg_style = "transparent" if is_added_overlay else "#FFFFFF"
        self.edit.setStyleSheet(
            f"QTextEdit, QTextEdit > QWidget {{ background: {bg_style}; background-color: {bg_style}; border: 1.5px solid {ACCENT};"
            f" border-radius: 2px; padding: 0px; }}"
            f"QTextEdit > QWidget {{ border: none; }}"
        )
        pal = self.edit.palette()
        pal.setColor(QPalette.ColorRole.Base, Qt.GlobalColor.transparent if is_added_overlay else QColor("#FFFFFF"))
        self.edit.setPalette(pal)
        self.edit.apply_uniform_style(fnt, qcol, preserve_runs=preserve_runs)

    def reposition(self):
        c = self.canvas
        if self.mode is None or self.page_idx < 0 or self.page_idx >= len(c._page_offsets):
            return
        z = c._zoom or 1.0
        yo = c._page_offsets[self.page_idx][0]
        if self.mode == "edit" and self.span is not None:
            bb = self.span["bbox"]
            sz = float(self.span.get("font_size") or self.span.get("size") or 12.0)
            px_size = max(6, int(round(sz * z)))
            origin = self.span.get("origin")
            fm = self.edit.fontMetrics()
            txt = self.edit.toPlainText()
            lines = txt.split("\n") if txt else [""]
            max_line_w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
            doc_w = self.edit.document().idealWidth()
            content_w = max(max_line_w, doc_w)
            cursor_extra = max(24, int(px_size * 2))
            orig_span_w = int((bb[2] - bb[0]) * z)
            w = max(orig_span_w + 14, int(content_w + cursor_extra + 16), 64)
            h = max(int((bb[3] - bb[1]) * z) + 6, int(fm.lineSpacing() * len(lines) + 8), 24)
            if origin:
                baseline_y = yo + int(round(origin[1] * z))
                ascent = fm.ascent()
                if len(lines) > 1:
                    top_y = baseline_y - ascent - 3
                else:
                    top_y = baseline_y - ascent - max(2, (h - fm.height()) // 2)
                x = int(round(origin[0] * z)) - 4
                y = top_y
            else:
                x = int(round(bb[0] * z)) - 4
                y = yo + int(round(bb[1] * z)) - 3
            need_w = max(c.width(), x + w + 32)
            need_h = max(c.height(), y + h + 32)
            if need_w > c.width() or need_h > c.height():
                c.setFixedSize(need_w, need_h)
            self.edit.setGeometry(x, y, w, h)
            self.edit.horizontalScrollBar().setValue(0)
            self.edit.verticalScrollBar().setValue(0)
        elif self.mode == "insert" and self.insert_point is not None:
            px, py = self.insert_point
            size = self.insert_size
            px_size = max(6, int(round(size * z)))
            fm = self.edit.fontMetrics()
            txt = self.edit.toPlainText()
            lines = txt.split("\n") if txt else [""]
            max_line_w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
            doc_w = self.edit.document().idealWidth()
            content_w = max(max_line_w, doc_w)
            cursor_extra = max(24, int(px_size * 2))
            w = max(100, int(content_w + cursor_extra + 16))
            h = max(int(px_size * 1.35) + 6, int(fm.lineSpacing() * len(lines) + 8), 24)
            x = int(round(px * z)) - 4
            y = yo + int(round(py * z)) - 3
            need_w = max(c.width(), x + w + 32)
            need_h = max(c.height(), y + h + 32)
            if need_w > c.width() or need_h > c.height():
                c.setFixedSize(need_w, need_h)
            self.edit.setGeometry(x, y, w, h)
            self.edit.horizontalScrollBar().setValue(0)
            self.edit.verticalScrollBar().setValue(0)

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
        edit_x = self.edit.x()
        edit_y = self.edit.y()

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
                if runs_to_store:
                    target_ov["runs"] = runs_to_store
                else:
                    target_ov.pop("runs", None)
                if target_ov.get("type") == "text_edit" and font_name != orig_font:
                    target_ov["_font_changed"] = True

                lines = new_text.split("\n") or [""]
                est_w = max(20.0, max((fm.horizontalAdvance(ln) for ln in lines), default=20.0) / z)
                est_h = max(12.0, (fm.lineSpacing() * len(lines)) / z)
                if target_ov.get("type") == "text_edit" and "bbox" in target_ov:
                    ob = target_ov["bbox"]
                    target_ov["bbox"] = [ob[0], ob[1], max(ob[2], ob[0] + est_w), max(ob[3], ob[1] + est_h)]
                elif target_ov.get("type") == "text" and "rect" in target_ov and target_ov["rect"] is not None:
                    orr = fitz.Rect(target_ov["rect"])
                    target_ov["rect"] = fitz.Rect(orr.x0, orr.y0, max(orr.x1, orr.x0 + est_w), max(orr.y1, orr.y0 + est_h))

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
                "_font_changed": bool(font_name != orig_font),
            }
            if runs_to_store:
                edit["runs"] = runs_to_store
            self.canvas.text_edit_committed.emit(page_idx, edit)
        elif mode == "insert" and ipoint is not None:
            if not new_text.strip():
                return
            yo = self.canvas._page_offsets[page_idx][0] if page_idx < len(self.canvas._page_offsets) else 0
            lines = new_text.split("\n") or [""]
            screen_x = edit_x + 4
            top_screen_y = edit_y + 3
            screen_y = top_screen_y + fm.ascent()
            pdf_baseline_x = screen_x / z
            pdf_baseline_y = (screen_y - yo) / z

            est_w = max(20.0, max((fm.horizontalAdvance(ln) for ln in lines), default=20.0) / z)
            total_h_px = max(float(fm.height()), fm.lineSpacing() * len(lines))
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
            }
            if runs_to_store:
                edit["runs"] = runs_to_store
            self.canvas.text_inserted.emit(page_idx, edit)

    def cancel(self):
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
