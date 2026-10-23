# app/viewer/panel_search_print.py
"""PDF_Editor – High-speed custom print dialog, accounting integration, and in-document search."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from app.viewer.print_dialog import _PdfPrintDialog

if TYPE_CHECKING:
    import fitz
    from PySide6.QtWidgets import QLineEdit, QLabel, QScrollArea
    from app.viewer.canvas_1 import _SelectCanvas
    _Base = QWidget
else:
    _Base = object


class PanelSearchPrintMixin(_Base):
    """Mixin for in-document text search bar and high-speed custom print dialog."""

    if TYPE_CHECKING:
        _current_path: str
        _pdf_password: str
        _fitz_doc: fitz.Document | None
        _search_bar: QWidget
        _search_input: QLineEdit
        _search_lbl: QLabel
        _search_results: list[tuple[int, list]]
        _search_current: int
        _search_debounce: QTimer
        _pending_search_query: str
        _canvas: _SelectCanvas
        _canvas_scroll: QScrollArea

    def _toggle_search(self):
        if self._search_bar.isVisible():
            self._close_search()
        else:
            self._search_bar.setVisible(True)
            self._search_input.setFocus()
            self._search_input.selectAll()

    def _close_search(self):
        self._search_bar.setVisible(False)
        self._search_results.clear()
        self._search_current = -1
        self._search_lbl.setText("")
        self._canvas.set_search_highlights([])
        self._canvas.update()

    def _on_search_text_changed(self, text: str):
        query = text.strip()
        if not query:
            self._search_debounce.stop()
            self._pending_search_query = ""
            self._search_results.clear()
            self._search_current = -1
            self._search_lbl.setText("")
            self._canvas.set_search_highlights([])
            self._canvas.update()
            return
        self._pending_search_query = query
        self._search_debounce.start()

    def _run_pending_search(self):
        query = self._pending_search_query
        if query:
            self._do_search(query)

    def _do_search(self, query: str):
        doc = self._fitz_doc
        if doc is None or getattr(doc, "is_closed", False):
            return
        results = []
        try:
            for page_idx in range(doc.page_count):
                page = doc[page_idx]
                rects = page.search_for(query)
                if rects:
                    results.append((page_idx, rects))
        except (RuntimeError, ValueError):
            return
        self._search_results = results
        total = sum(len(rects) for _, rects in results)
        if total == 0:
            self._search_lbl.setText("0 / 0")
            self._search_current = -1
            self._canvas.set_search_highlights([])
            self._canvas.update()
            return
        self._search_current = 0
        self._update_search_highlight()

    def _search_next(self):
        if not self._search_results:
            text = self._search_input.text().strip()
            if text:
                self._do_search(text)
            return
        results = self._search_results
        total = sum(len(rects) for _, rects in results) if results else 0
        if total == 0:
            return
        self._search_current = (self._search_current + 1) % total
        self._update_search_highlight()

    def _search_prev(self):
        if not self._search_results:
            return
        total = sum(len(rects) for _, rects in self._search_results)
        if total == 0:
            return
        self._search_current = (self._search_current - 1) % total
        self._update_search_highlight()

    def _update_search_highlight(self):
        total = sum(len(rects) for _, rects in self._search_results)
        self._search_lbl.setText(f"{self._search_current + 1} / {total}")
        all_highlights = []
        flat_idx = 0
        current_page = 0
        for page_idx, rects in self._search_results:
            for rect in rects:
                all_highlights.append((page_idx, rect))
                if flat_idx == self._search_current:
                    current_page = page_idx
                flat_idx += 1
        self._canvas.set_search_highlights(all_highlights, self._search_current)
        if current_page < len(self._canvas._entries):
            entry = self._canvas._entries[current_page]
            _, cur_rect = all_highlights[self._search_current]
            z = self._canvas._zoom
            y_target = entry.y_off + int(cur_rect.y0 * z) - 100
            sb = self._canvas_scroll.verticalScrollBar()
            if sb:
                sb.setValue(max(0, y_target))
        self._canvas.update()

    def _reset_search_state(self):
        self._search_debounce.stop()
        self._pending_search_query = ""
        self._close_search()

    def _cancel_print_job(self):
        pass

    def _print_pdf(self, page_indices: list[int] | None = None):
        """Invoke the high-speed custom PDF_Editor Print Dialog."""
        doc = self._fitz_doc
        if doc is None or getattr(doc, "is_closed", False):
            return
        if not self._current_path or not os.path.isfile(self._current_path):
            return

        dlg = _PdfPrintDialog(
            doc_path=self._current_path,
            password=getattr(self, "_pdf_password", ""),
            initial_pages=page_indices,
            parent=self,
        )
        dlg.exec()