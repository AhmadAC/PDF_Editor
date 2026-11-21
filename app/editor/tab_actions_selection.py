# app/editor/tab_actions_selection.py
"""Selection editing, mouse point hits, stroke events, and text/highlight extraction for TabEditar."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from app.editor.canvas_overlay1 import snapshot_overlay
from app.editor.tab_constants import (
    _MODE_DRAW,
    _MODE_HIGHLIGHT,
    _MODE_HIGHLIGHT_RECT,
    _MODE_IMAGE,
    _MODE_NOTE,
    _MODE_REDACT,
    _MODE_SELECT,
    _MODE_SIGNATURE,
    _MODE_TEXT,
)
from app.i18n import t

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    _Base = QWidget
else:
    _Base = object


class TabSelectionActionsMixin(_Base):
    """Mixin providing rectangle, point, draw stroke, and selection manipulations."""

    if TYPE_CHECKING:
        _mode_idx: int
        _page_idx: int
        _canvas: PdfEditCanvas
        _draw_color_cb: Any
        _draw_width_slider: Any
        _draw_width_lbl: Any
        _hi_color: Any
        _img_drop: Any
        _signature_path: str | None
        _red_color: Any
        _text_submode: str
        _text_font: Any
        _text_size: Any
        _text_color: Any
        _btn_bold: Any
        _btn_italic: Any
        _last_selected_page: int
        _last_selected_rect: Any
        _last_selected_original_text: str
        _sel_result: Any

        def _add(self, edit: dict, *, _from_redo: bool = False) -> None: ...
        def _pick_image(self) -> None: ...
        def _pick_signature(self) -> None: ...
        def _update_nav(self) -> None: ...
        def _status(self, msg: str) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...
        def _current_text_effects(self) -> dict[str, Any]: ...
        def _on_overlay_committed(self, edit: dict) -> None: ...
        def _on_overlay_transformed(self, edit: dict, before_snap: dict) -> None: ...

    def _on_draw_color_changed(self, _color_tuple):
        self._canvas.set_draw_mode(
            self._mode_idx == _MODE_DRAW,
            color=self._draw_color_cb.color_tuple(),
            width=self._draw_width_slider.value(),
        )

    def _on_draw_width_changed(self, v: int):
        self._draw_width_lbl.setText(str(v))
        self._canvas.set_draw_mode(
            self._mode_idx == _MODE_DRAW,
            color=self._draw_color_cb.color_tuple(),
            width=v,
        )

    def _on_stroke(self, page_idx: int, pdf_points):
        self._page_idx = page_idx
        self._update_nav()
        col = getattr(self._canvas, "_draw_color", None)
        if col is None:
            col = self._draw_color_cb.color_tuple()
        self._add({
            "type": "draw",
            "page": page_idx,
            "points": pdf_points,
            "color": col,
            "width": self._draw_width_slider.value(),
        })
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _delete_selection(self):
        if self._canvas._selected_overlay_idx >= 0:
            self._canvas.delete_selected_overlay()
            return
        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        old_txt = self._sel_result.toPlainText().strip() if hasattr(self, "_sel_result") else ""
        edit = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
            "old_text": old_txt,
            "new_text": "",
            "size": max(8.0, rect.height * 0.8),
            "font_size": max(8.0, rect.height * 0.8),
            "color": (0, 0, 0),
            "font": "Helvetica",
            "flags": 0,
            "origin": [rect.x0, rect.y1],
            "_existing": True,
            "_dirty": True,
            "_orig_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        }
        self._add(edit)
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        if hasattr(self, "_sel_result"):
            self._sel_result.setPlainText("")
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Selected element deleted (Ctrl+Z to undo)")

    def _edit_selection(self):
        if hasattr(self, "_canvas") and self._canvas._selected_overlay_idx >= 0:
            ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                self._canvas._event_handler._begin_inline_edit_for_overlay(ov)
                return

        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        center = fitz.Point((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)
        hit = self._canvas.get_span_at(page_idx, center, max_dist=12.0)
        if hit:
            self._canvas.begin_inline_text_edit(hit, page_idx)
        else:
            txt = self._sel_result.toPlainText() if hasattr(self, "_sel_result") else ""
            span = {
                "text": txt,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "size": max(8.0, rect.height * 0.8),
                "font": "Helvetica",
                "color": 0,
                "flags": 0,
                "origin": [rect.x0, rect.y1],
            }
            self._canvas.begin_inline_text_edit(span, page_idx)

    def _apply_text_edit_from_selection(self):
        new_txt = self._sel_result.toPlainText() if hasattr(self, "_sel_result") else ""
        if hasattr(self, "_canvas") and self._canvas._selected_overlay_idx >= 0:
            ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                before_snap = snapshot_overlay(ov)
                was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
                ov["new_text"] = new_txt
                ov["text"] = new_txt
                ov.pop("runs", None)
                if was_untouched:
                    ov["_dirty"] = True
                    self._on_overlay_committed(ov)
                else:
                    self._on_overlay_transformed(ov, before_snap)
                self._canvas.overlay_changed.emit()
                self._canvas.update()
                if hasattr(self, "_sync_thumbnails"):
                    self._sync_thumbnails({ov.get("page", 0)})
                self._status("Updated text applied (Ctrl+Z to undo)")
                return

        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        old_txt = getattr(self, "_last_selected_original_text", "")
        edit = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
            "old_text": old_txt,
            "new_text": new_txt,
            "size": max(8.0, rect.height * 0.8),
            "font_size": max(8.0, rect.height * 0.8),
            "color": (0, 0, 0),
            "font": "Helvetica",
            "flags": 0,
            "origin": [rect.x0, rect.y1],
            "_existing": True,
            "_dirty": True,
            "_orig_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        }
        self._add(edit)
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Edited text applied (Ctrl+Z to undo)")

    def _redact_selection(self):
        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        self._add({
            "type": "redact",
            "page": page_idx,
            "rect": rect,
            "fill": (0, 0, 0),
        })
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        if hasattr(self, "_sel_result"):
            self._sel_result.setPlainText("")
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Selection redacted (Ctrl+Z to undo)")

    def _highlight_selection(self):
        page_idx = getattr(self, "_last_selected_page", -1)
        pdf_rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or pdf_rect is None:
            return
        doc = self._canvas._doc
        if not doc or not (0 <= page_idx < doc.page_count):
            return
        page = doc[page_idx]
        words = page.get_text("words", clip=pdf_rect)
        line_rects = []
        if words:
            lines_dict = {}
            for w in words:
                key = (w[5], w[6])
                lines_dict.setdefault(key, []).append(w)
            for line_words in lines_dict.values():
                lx0 = min(w[0] for w in line_words)
                ly0 = min(w[1] for w in line_words)
                lx1 = max(w[2] for w in line_words)
                ly1 = max(w[3] for w in line_words)
                line_rects.append(fitz.Rect(lx0, ly0, lx1, ly1))
        hi_color = getattr(self._canvas, "_highlight_color", None)
        if hi_color is None:
            hi_color = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)
        self._add({
            "type": "highlight",
            "page": page_idx,
            "rect": pdf_rect,
            "rects": line_rects if line_rects else None,
            "color": hi_color
        })
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Highlight added to selection")

    def _on_rect(self, page_idx: int, pdf_rect):
        self._page_idx = page_idx
        self._update_nav()
        mode = self._mode_idx

        if getattr(self._canvas, "_highlight_mode", False) or mode in (_MODE_HIGHLIGHT, _MODE_HIGHLIGHT_RECT):
            hi_color = getattr(self._canvas, "_highlight_color", None)
            if hi_color is None:
                hi_color = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)

            if mode == _MODE_HIGHLIGHT:
                doc = self._canvas._doc
                line_rects = []
                if doc and 0 <= page_idx < doc.page_count:
                    page = doc[page_idx]
                    words = page.get_text("words", clip=pdf_rect)
                    if words:
                        lines_dict = {}
                        for w in words:
                            key = (w[5], w[6])
                            lines_dict.setdefault(key, []).append(w)
                        for line_words in lines_dict.values():
                            lx0 = min(w[0] for w in line_words)
                            ly0 = min(w[1] for w in line_words)
                            lx1 = max(w[2] for w in line_words)
                            ly1 = max(w[3] for w in line_words)
                            line_rects.append(fitz.Rect(lx0, ly0, lx1, ly1))
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": pdf_rect,
                    "rects": line_rects if line_rects else None,
                    "color": hi_color
                })
            else:
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": pdf_rect,
                    "color": hi_color
                })
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({page_idx})
            self._status("Highlight added (Ctrl+Z to undo)")
            return

        if mode == _MODE_SELECT:
            doc = self._canvas._doc
            if not doc:
                return
            raw_text = doc[page_idx].get_text("text", clip=pdf_rect)
            text = raw_text.strip() if isinstance(raw_text, str) else str(raw_text).strip()
            self._sel_result.setPlainText(text)
            self._last_selected_page = page_idx
            self._last_selected_rect = pdf_rect
            self._last_selected_original_text = text
            if hasattr(self._canvas, "_active_sel_rect"):
                self._canvas._active_sel_rect = fitz.Rect(pdf_rect)
                self._canvas._active_sel_page = page_idx
                self._canvas.update()
            if text:
                clipboard = QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                self._status(t("edit.status.copied_clipboard", n=len(text)))
            else:
                self._status("Element selected (Press Del to delete or click Edit)")
            return

        if mode == _MODE_TEXT:
            submode = getattr(self, "_text_submode", "edit")
            if submode == "edit":
                doc = self._canvas._doc
                if not doc or not (0 <= page_idx < doc.page_count):
                    return
                page = doc[page_idx]
                matched_spans = []
                text_dict = page.get_text("dict", clip=pdf_rect)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if not span.get("text", "").strip():
                                continue
                            s_bbox = fitz.Rect(span["bbox"])
                            inter = s_bbox & pdf_rect
                            if not inter.is_empty and inter.width > 1 and inter.height > 1:
                                matched_spans.append(span)

                if not matched_spans:
                    self._status("No text found in selected area to edit.")
                    return

                if len(matched_spans) == 1:
                    target_span = dict(matched_spans[0])
                else:
                    first = matched_spans[0]
                    union_rect = fitz.Rect(first["bbox"])
                    for sp in matched_spans[1:]:
                        union_rect |= fitz.Rect(sp["bbox"])
                    combined_text = " ".join(
                        sp.get("text", "").strip() for sp in matched_spans if sp.get("text", "").strip()
                    )
                    target_span = dict(first)
                    target_span["bbox"] = [union_rect.x0, union_rect.y0, union_rect.x1, union_rect.y1]
                    target_span["text"] = combined_text

                self._canvas.begin_inline_text_edit(target_span, page_idx)
                return

            insert_pt = fitz.Point(pdf_rect.x0, pdf_rect.y0)
            size = float(self._text_size.value())
            color = self._text_color.color_tuple()
            font = self._text_font.currentText()
            bold = self._btn_bold.isChecked()
            italic = self._btn_italic.isChecked()
            effects = self._current_text_effects()
            self._canvas.begin_inline_text_insert(page_idx, insert_pt, size, color, font, bold, italic, effects=effects)
            self._canvas.update_active_text_format(font, size, color, bold, italic, effects=effects)
            return

        if mode == _MODE_NOTE:
            center = fitz.Point((pdf_rect.x0 + pdf_rect.x1) / 2, (pdf_rect.y0 + pdf_rect.y1) / 2)
            self._on_point(page_idx, center)
            return
        if mode == _MODE_REDACT:
            self._add({"type": "redact", "page": self._page_idx, "rect": pdf_rect, "fill": self._red_color.color_tuple()})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_IMAGE:
            img = self._img_drop.path()
            if not img or not os.path.isfile(img):
                self._pick_image()
                img = self._img_drop.path()
                if not img or not os.path.isfile(img):
                    return
            self._add({"type": "image", "page": self._page_idx, "rect": pdf_rect, "path": img})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_SIGNATURE:
            sig = self._signature_path
            if not sig or not os.path.isfile(sig):
                self._pick_signature()
                sig = self._signature_path
                if not sig or not os.path.isfile(sig):
                    return
            self._add({"type": "signature", "page": self._page_idx, "rect": pdf_rect, "path": sig})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})

    def _on_point(self, page_idx: int, pdf_pt):
        self._page_idx = page_idx
        self._update_nav()
        doc = self._canvas._doc
        if doc:
            page = doc[page_idx]
            for annot in page.annots() or []:
                annot_type = getattr(annot, "type", (None, None))
                if (isinstance(annot_type, (tuple, list)) and len(annot_type) >= 2 and (annot_type[0] == 0 or annot_type[1] == "Text")) or annot_type == 0:
                    ar = annot.rect
                    expanded = fitz.Rect(ar.x0 - 10, ar.y0 - 10, ar.x1 + 10, ar.y1 + 10)
                    if expanded.contains(fitz.Point(pdf_pt.x, pdf_pt.y)):
                        info = getattr(annot, "info", None) or {}
                        txt = info.get("content", "") if isinstance(info, dict) else ""
                        if txt:
                            from PySide6.QtWidgets import QMessageBox
                            QMessageBox.information(self, t("edit.note_popup"), txt)
                            return
        mode = self._mode_idx
        if mode == _MODE_SELECT:
            if 0 <= self._canvas._selected_overlay_idx < len(self._canvas._overlays):
                ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
                txt = (ov.get("new_text") or ov.get("text") or "").strip()
                bb = ov.get("bbox")
                rect = fitz.Rect(bb) if bb else ov.get("rect")
                self._sel_result.setPlainText(txt)
                self._last_selected_page = page_idx
                self._last_selected_rect = rect
                self._last_selected_original_text = txt
                if txt:
                    clipboard = QApplication.clipboard()
                    if clipboard:
                        clipboard.setText(txt)
                self._status(f"Selected: {txt[:30]}" if txt else "Element selected (Drag to move or Del to delete)")
                return

            hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=8.0)
            if hit:
                bb = fitz.Rect(hit["bbox"])
                txt = hit.get("text", "").strip()
                self._sel_result.setPlainText(txt)
                self._last_selected_page = page_idx
                self._last_selected_rect = bb
                self._last_selected_original_text = txt
                if hasattr(self._canvas, "_active_sel_rect"):
                    self._canvas._active_sel_rect = bb
                    self._canvas._active_sel_page = page_idx
                    self._canvas.update()
                if txt:
                    clipboard = QApplication.clipboard()
                    if clipboard:
                        clipboard.setText(txt)
                self._status(f"Selected: {txt[:30]}")
            else:
                if hasattr(self._canvas, "_active_sel_rect"):
                    self._canvas._active_sel_rect = None
                    self._canvas.update()
                self._last_selected_rect = None
        elif mode == _MODE_TEXT:
            submode = getattr(self, "_text_submode", "edit")
            if submode != "add":
                hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=6.0)
                if hit:
                    self._canvas.begin_inline_text_edit(hit, page_idx)
                    return
                self._status("Click or drag across existing text to edit it.")
                return

            size = float(self._text_size.value())
            color = self._text_color.color_tuple()
            font = self._text_font.currentText()
            bold = self._btn_bold.isChecked()
            italic = self._btn_italic.isChecked()
            effects = self._current_text_effects()
            self._canvas.begin_inline_text_insert(page_idx, pdf_pt, size, color, font, bold, italic, effects=effects)
            self._canvas.update_active_text_format(font, size, color, bold, italic, effects=effects)
        elif mode == _MODE_NOTE:
            from app.editor.dialogs import _NoteDialog
            dlg = _NoteDialog(self)
            if dlg.exec() != _NoteDialog.DialogCode.Accepted:
                return
            txt = dlg.edit.toPlainText().strip()
            if not txt:
                return
            self._add({"type": "note", "page": self._page_idx, "point": pdf_pt, "text": txt})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_HIGHLIGHT:
            hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=8.0)
            if hit:
                bb = fitz.Rect(hit["bbox"])
                hi_col = getattr(self._canvas, "_highlight_color", None)
                if hi_col is None:
                    hi_col = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": bb,
                    "color": hi_col
                })
                if hasattr(self, "_sync_thumbnails"):
                    self._sync_thumbnails({page_idx})
                self._status("Word highlighted")