

# app/window_nav_page.py
"""PDF_Editor – Window page navigation, top bar page controls, and language selector mixin."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QProcess, QProcessEnvironment
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QWidget,
)

from app.i18n import get_language, set_language, t

if TYPE_CHECKING:
    from app.viewer.panel import PdfViewerPanel
    _Base = QMainWindow
else:
    _Base = object


class WindowNavPageMixin(_Base):
    """Mixin managing page indexing, navigation buttons, and language switching."""

    if TYPE_CHECKING:
        _current_tool: int
        _page_nav_widget: QWidget
        _page_input: QLineEdit
        _page_total_lbl: QLabel
        _first_pg_btn: QPushButton
        _prev_pg_btn: QPushButton
        _next_pg_btn: QPushButton
        _last_pg_btn: QPushButton
        _lang_btn: QPushButton
        _viewer: PdfViewerPanel | None

        def _edit_tool_idx(self) -> int: ...
        def _get_or_create_tool(self, index: int) -> QWidget | None: ...
        def _update_undo_redo_buttons(self) -> None: ...
        def _wait_for_workers_on_all_pages(self) -> None: ...
        def _wipe_all_pdf_passwords(self) -> None: ...

    @staticmethod
    def _get_canvas_page_count(canvas: Any) -> int:
        fn = getattr(canvas, "page_count", None)
        if callable(fn):
            try:
                return int(cast(Any, fn)())
            except Exception:
                return 0
        return 0

    def _update_page_nav(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                n = self._get_canvas_page_count(canvas)
                if n > 0:
                    self._page_nav_widget.setVisible(True)
                    idx = int(cast(Any, getattr(edit_w, "_page_idx", 0) or 0))
                    self._page_input.setText(str(idx + 1))
                    self._page_total_lbl.setText(f"/ {n}")
                    self._first_pg_btn.setEnabled(idx > 0)
                    self._prev_pg_btn.setEnabled(idx > 0)
                    self._next_pg_btn.setEnabled(idx < n - 1)
                    self._last_pg_btn.setEnabled(idx < n - 1)
                    self._update_undo_redo_buttons()
                    return

        viewer = self._viewer
        if not viewer:
            self._page_nav_widget.setVisible(False)
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            self._page_nav_widget.setVisible(False)
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            self._page_nav_widget.setVisible(False)
            return
        self._page_nav_widget.setVisible(True)
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        sb_val = sb.value() if sb else 0
        idx = int(canvas.page_at_y(sb_val))
        total = int(len(entries))
        self._page_input.setText(str(idx + 1))
        self._page_total_lbl.setText(f"/ {total}")
        self._first_pg_btn.setEnabled(idx > 0)
        self._prev_pg_btn.setEnabled(idx > 0)
        self._next_pg_btn.setEnabled(idx < total - 1)
        self._last_pg_btn.setEnabled(idx < total - 1)
        self._update_undo_redo_buttons()

    def _goto_first_page(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            scroll_fn = getattr(edit_w, "_scroll_to", None)
            if callable(scroll_fn):
                scroll_fn(0)
                return
        viewer = self._viewer
        if not viewer:
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            return
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        if sb:
            sb.setValue(canvas.scroll_to_page(0))

    def _goto_prev_page(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            prev_fn = getattr(edit_w, "_prev_page", None)
            if callable(prev_fn):
                prev_fn()
                return
        viewer = self._viewer
        if not viewer:
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            return
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        if sb:
            idx = canvas.page_at_y(sb.value())
            if idx > 0:
                sb.setValue(canvas.scroll_to_page(idx - 1))

    def _goto_next_page(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            next_fn = getattr(edit_w, "_next_page", None)
            if callable(next_fn):
                next_fn()
                return
        viewer = self._viewer
        if not viewer:
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            return
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        if sb:
            idx = canvas.page_at_y(sb.value())
            if idx < len(entries) - 1:
                sb.setValue(canvas.scroll_to_page(idx + 1))

    def _goto_last_page(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                n = self._get_canvas_page_count(canvas)
                if n > 0:
                    scroll_fn = getattr(edit_w, "_scroll_to", None)
                    if callable(scroll_fn):
                        scroll_fn(n - 1)
                return
        viewer = self._viewer
        if not viewer:
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            return
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        if sb:
            sb.setValue(canvas.scroll_to_page(len(entries) - 1))

    def _goto_input_page(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                n = self._get_canvas_page_count(canvas)
                try:
                    p = int(self._page_input.text())
                except ValueError:
                    return
                p = max(1, min(p, n))
                scroll_fn = getattr(edit_w, "_scroll_to", None)
                if callable(scroll_fn):
                    scroll_fn(p - 1)
                return
        viewer = self._viewer
        if not viewer:
            return
        canvas = getattr(viewer, "_canvas", None)
        if canvas is None:
            return
        entries = getattr(canvas, "_entries", None)
        if not entries:
            return
        try:
            page_num = int(self._page_input.text())
        except ValueError:
            return
        page_num = max(1, min(page_num, len(entries)))
        self._page_input.setText(str(page_num))
        sb_scroll = getattr(viewer, "_canvas_scroll", None)
        sb = sb_scroll.verticalScrollBar() if sb_scroll else None
        if sb:
            sb.setValue(canvas.scroll_to_page(page_num - 1))

    def _show_language_menu(self):
        _langs = [
            ("en", "English"), ("pt", "Português"), ("es", "Español"), ("fr", "Français"),
            ("de", "Deutsch"), ("zh", "中文"), ("it", "Italiano"), ("nl", "Nederlands")
        ]
        menu = QMenu(self)
        current = get_language()
        for code, name in _langs:
            action = menu.addAction(f"  {'● ' if code == current else '  '}{name}")
            action.triggered.connect(lambda checked=False, c=code, n=name: self._set_language(c, n))
        menu.exec(self._lang_btn.mapToGlobal(self._lang_btn.rect().bottomLeft()))

    def _set_language(self, code: str, name: str):
        if code == get_language():
            return
        ans = QMessageBox.question(
            self, t("lang.selector"), t("lang.restart", lang=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        set_language(code)
        _labels = {"en": "EN", "pt": "PT", "es": "ES", "fr": "FR", "de": "DE", "zh": "ZH", "it": "IT", "nl": "NL"}
        self._lang_btn.setText(_labels.get(code, "EN"))
        self._restart_app()

    def _restart_app(self):
        self._wait_for_workers_on_all_pages()
        self._wipe_all_pdf_passwords()
        pdf_args = [a for a in sys.argv[1:] if os.path.isfile(a)]
        if getattr(sys, "frozen", False):
            program = sys.executable
            args = pdf_args
            cwd = os.path.dirname(sys.executable) or os.getcwd()
        else:
            script = os.path.abspath(sys.argv[0])
            program = sys.executable
            args = [script] + pdf_args
            cwd = os.path.dirname(script) or os.getcwd()
        proc = QProcess()
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(cwd)
        env = QProcessEnvironment.systemEnvironment()
        for _k in list(env.keys()):
            if _k.startswith("_PYI_") or _k.startswith("_MEIPASS"):
                env.remove(_k)
        proc.setProcessEnvironment(env)
        proc.startDetached()
        app = QApplication.instance()
        if app is not None:
            app.exit(0)


