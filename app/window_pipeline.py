# app/window_pipeline.py
"""PDF_Editor – Pipeline processing, Save As, Save As Copy, and security wiping mixin."""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import traceback
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.base import BasePage
from app.i18n import add_recent_file, t

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QMainWindow, QWidget, QStackedWidget, QStatusBar,
    )
    from app.viewer.panel import PdfViewerPanel
    from app.window_tabs import _ViewerTabBar
    _Base = QMainWindow
else:
    _Base = object

_log = logging.getLogger(__name__)


class WindowPipelineMixin(_Base):
    """Mixin for managing pipeline processing, Save As, Save As Copy, undo/redo, and worker lifecycles."""

    if TYPE_CHECKING:
        stack: QStackedWidget
        _viewers: list[PdfViewerPanel]
        _viewer: PdfViewerPanel | None
        _viewer_stack: QStackedWidget
        _tab_bar: _ViewerTabBar
        _current_tool: int
        _pipeline_state: dict[int, dict[str, Any]]
        _sb: QStatusBar

        def _get_or_create_tool(self, index: int) -> QWidget | None: ...
        def _edit_tool_idx(self) -> int: ...
        def _crop_tool_idx(self) -> int: ...
        def _rotate_tool_idx(self) -> int: ...
        def _page_numbers_tool_idx(self) -> int: ...
        def _update_breadcrumb(self) -> None: ...
        def _update_tab_bar_visibility(self) -> None: ...

    def _handle_global_undo(self):
        edit_idx = self._edit_tool_idx()
        crop_idx = self._crop_tool_idx()
        rotate_idx = self._rotate_tool_idx()
        page_numbers_idx = self._page_numbers_tool_idx()

        target_idx = -1
        if self._current_tool == edit_idx:
            target_idx = edit_idx
        elif self._current_tool == crop_idx:
            target_idx = crop_idx
        elif self._current_tool == rotate_idx:
            target_idx = rotate_idx
        elif self._current_tool == page_numbers_idx:
            target_idx = page_numbers_idx

        if target_idx >= 0:
            w = self._get_or_create_tool(target_idx)
            fn = getattr(w, "_undo", None)
            if callable(fn):
                fn()
        elif self._current_tool == -1 and self._viewer:
            self._viewer.undo()

    def _handle_global_redo(self):
        edit_idx = self._edit_tool_idx()
        crop_idx = self._crop_tool_idx()
        rotate_idx = self._rotate_tool_idx()
        page_numbers_idx = self._page_numbers_tool_idx()

        target_idx = -1
        if self._current_tool == edit_idx:
            target_idx = edit_idx
        elif self._current_tool == crop_idx:
            target_idx = crop_idx
        elif self._current_tool == rotate_idx:
            target_idx = rotate_idx
        elif self._current_tool == page_numbers_idx:
            target_idx = page_numbers_idx

        if target_idx >= 0:
            w = self._get_or_create_tool(target_idx)
            fn = getattr(w, "_redo", None)
            if callable(fn):
                fn()
        elif self._current_tool == -1 and self._viewer:
            self._viewer.redo()

    def _on_pipeline_done(self, temp_path: str):
        viewer = self._viewer
        if not viewer:
            return
        vid = id(viewer)
        ps = self._pipeline_state.get(vid)
        if ps is None:
            orig_p = getattr(viewer, "_original_doc_path", "") or viewer.current_path()
            ps = {"original_path": orig_p, "temp_path": None}
            self._pipeline_state[vid] = ps
        ps["temp_path"] = temp_path
        viewer.load(temp_path, _is_history_step=True)
        idx = self._viewer_stack.currentIndex()
        orig_name = os.path.basename(ps["original_path"])
        self._tab_bar.setTabText(idx, f"* {orig_name}")
        self._tab_bar.setTabToolTip(idx, f"{ps['original_path']} (modified)")
        self._sb.showMessage(t("pipeline.applied"))
        if self._current_tool >= 0:
            w = self._get_or_create_tool(self._current_tool)
            fn = getattr(w, "auto_load", None)
            if callable(fn):
                for attr in ("drop_in", "drop_out"):
                    drop = getattr(w, attr, None)
                    if drop:
                        drop.clear()
                fn(temp_path)

    def _atomic_copy_file(self, src_path: str, dest_path: str) -> None:
        for v in self._viewers:
            if v.current_path() and os.path.abspath(v.current_path()) == os.path.abspath(dest_path):
                v._canvas.close_doc()
                if v._fitz_doc:
                    with contextlib.suppress(Exception):
                        v._fitz_doc.close()
                    v._fitz_doc = None
                v._thumbnails._stop_all_workers()

        try:
            dst_dir = os.path.dirname(dest_path) or "."
            fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=dst_dir)
            os.close(fd)
            try:
                shutil.copyfile(src_path, tmp)
                os.replace(tmp, dest_path)
            except Exception:
                with contextlib.suppress(Exception):
                    os.unlink(tmp)
                raise
        except OSError:
            shutil.copy2(src_path, dest_path)

    def _save_pipeline(self):
        viewer = self._viewer
        if not viewer:
            return
        vid = id(viewer)
        ps = self._pipeline_state.get(vid)
        if not ps or not ps.get("temp_path"):
            return
        orig = ps["original_path"]
        base, ext = os.path.splitext(os.path.basename(orig))
        default_name = f"{base}_edited.pdf" if ext.lower() == ".pdf" else f"{base}.pdf"
        suggested = os.path.join(os.path.dirname(orig), default_name)
        path, _ = QFileDialog.getSaveFileName(self, t("btn.choose"), suggested, t("file_filter.pdf"))
        if not path:
            return
        try:
            if os.path.lexists(path) and os.path.realpath(path) != os.path.abspath(path):
                _log.warning("Pipeline save destination is a symlink: %s -> %s", path, os.path.realpath(path))
        except Exception:
            pass

        self._atomic_copy_file(ps["temp_path"], path)
        self._cleanup_pipeline(vid)
        viewer.load(path)
        idx = self._viewer_stack.currentIndex()
        self._tab_bar.setTabText(idx, os.path.basename(path))
        self._tab_bar.setTabToolTip(idx, path)
        add_recent_file(path)
        self._sb.showMessage(t("pipeline.saved"))

    def _save_as_current_doc(self):
        """Handle Ctrl+Shift+S (Save As) across Edit Mode and Normal Viewer."""
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            doc_path = getattr(edit_w, "_doc_path", None)
            if not doc_path or not os.path.isfile(doc_path):
                QMessageBox.warning(self, t("msg.warning"), t("msg.open_pdf_first"))
                return
            base, ext = os.path.splitext(os.path.basename(doc_path))
            suggested = os.path.join(os.path.dirname(doc_path), f"{base}_edited{ext or '.pdf'}")
            out, _ = QFileDialog.getSaveFileName(self, t("widget.save_as"), suggested, t("file_filter.pdf"))
            if not out:
                return
            if getattr(edit_w, "_user_pending", None):
                edit_w._drop_out.set_path(out)
                from app.editor.tab_operations import apply_visual_edits_and_save
                apply_visual_edits_and_save(edit_w, out)
            else:
                self._atomic_copy_file(doc_path, out)
                edit_w._load_pdf(out)
                self._sb.showMessage(f"Saved as {os.path.basename(out)}")
            return

        viewer = self._viewer
        if not viewer or not viewer.current_path():
            self._sb.showMessage("No open PDF to save.")
            return

        vid = id(viewer)
        ps = self._pipeline_state.get(vid)
        src_path = ps["temp_path"] if (ps and ps.get("temp_path") and os.path.isfile(ps["temp_path"])) else viewer.current_path()
        orig = (ps.get("original_path") if ps else None) or getattr(viewer, "_original_doc_path", "") or viewer.current_path()

        base, ext = os.path.splitext(os.path.basename(orig))
        default_name = f"{base}_copy.pdf" if ext.lower() == ".pdf" else f"{base}.pdf"
        suggested = os.path.join(os.path.dirname(orig), default_name)
        out, _ = QFileDialog.getSaveFileName(self, t("widget.save_as"), suggested, t("file_filter.pdf"))
        if not out:
            return

        try:
            self._atomic_copy_file(src_path, out)
            self._cleanup_pipeline(vid)
            viewer.load(out)
            idx = self._viewers.index(viewer) if viewer in self._viewers else self._viewer_stack.currentIndex()
            self._tab_bar.setTabText(idx, os.path.basename(out))
            self._tab_bar.setTabToolTip(idx, out)
            add_recent_file(out)
            self._update_tab_bar_visibility()
            self._update_breadcrumb()
            self._sb.showMessage(f"Saved as {os.path.basename(out)}")
        except Exception as exc:
            _log.debug("Save As error: %s", traceback.format_exc())
            QMessageBox.critical(self, t("msg.error"), f"Could not save file:\n{exc}")

    def _save_as_copy_for_viewer(self, viewer: PdfViewerPanel | None = None):
        """Save a copy of the specified tab's PDF (including unsaved pipeline edits) without altering the open tab."""
        target_v = viewer or self._viewer
        if not target_v or not target_v.current_path():
            return

        vid = id(target_v)
        ps = self._pipeline_state.get(vid)
        src_path = ps["temp_path"] if (ps and ps.get("temp_path") and os.path.isfile(ps["temp_path"])) else target_v.current_path()
        orig = (ps.get("original_path") if ps else None) or getattr(target_v, "_original_doc_path", "") or target_v.current_path()

        base, ext = os.path.splitext(os.path.basename(orig))
        default_name = f"{base}_copy.pdf" if ext.lower() == ".pdf" else f"{base}.pdf"
        suggested = os.path.join(os.path.dirname(orig), default_name)
        out, _ = QFileDialog.getSaveFileName(self, "Save As Copy", suggested, t("file_filter.pdf"))
        if not out:
            return

        try:
            self._atomic_copy_file(src_path, out)
            add_recent_file(out)
            self._sb.showMessage(f"Saved copy -> {os.path.basename(out)}")
        except Exception as exc:
            _log.debug("Save As Copy error: %s", traceback.format_exc())
            QMessageBox.critical(self, t("msg.error"), f"Could not save copy:\n{exc}")

    def _cleanup_pipeline(self, viewer_id: int):
        ps = self._pipeline_state.pop(viewer_id, None)
        if not ps:
            return
        temp_path = ps.get("temp_path")
        if temp_path and os.path.isfile(temp_path):
            with contextlib.suppress(Exception):
                os.unlink(temp_path)
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if w and not getattr(w, "_is_lazy_placeholder", False) and isinstance(w, BasePage):
                w.cleanup_pipeline()

    def _viewer_has_unsaved(self, viewer=None) -> bool:
        v = viewer or self._viewer
        if not v:
            return False
        ps = self._pipeline_state.get(id(v))
        return bool(ps and ps.get("temp_path"))

    def _save_current_tool(self):
        viewer = self._viewer
        if not viewer:
            return
        vid = id(viewer)
        ps = self._pipeline_state.get(vid)
        if ps and ps.get("temp_path"):
            self._save_pipeline()
        elif self._current_tool >= 0:
            w = self._get_or_create_tool(self._current_tool)
            fn = getattr(w, "_run", None)
            if callable(fn):
                fn()

    @staticmethod
    def _wipe_password_holder(holder) -> None:
        with contextlib.suppress(Exception):
            clear_fn = getattr(holder, "_clear_pdf_password", None)
            if callable(clear_fn):
                clear_fn()

    def _wait_for_workers_on_all_pages(self) -> None:
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if page is not None and not getattr(page, "_is_lazy_placeholder", False):
                with contextlib.suppress(Exception):
                    wait_fn = getattr(page, "wait_for_workers", None)
                    if callable(wait_fn):
                        wait_fn()

    def _wipe_all_pdf_passwords(self) -> None:
        holders: list[Any] = [
            self.stack.widget(i)
            for i in range(self.stack.count())
            if self.stack.widget(i) is not None and not getattr(self.stack.widget(i), "_is_lazy_placeholder", False)
        ]
        holders += list(self._viewers)
        for holder in holders:
            self._wipe_password_holder(holder)