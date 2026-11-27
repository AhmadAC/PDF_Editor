
# app/viewer/panel_history.py
"""PDF_Editor – Panel history, undo/redo state management, reset, and temp pipeline persistence."""
from __future__ import annotations

import contextlib
import os
import tempfile
from typing import TYPE_CHECKING, Any

from app.utils import show_error
from app.pdf_io import atomic_pdf_write
from app.i18n import t

if TYPE_CHECKING:
    import fitz
    from PySide6.QtWidgets import QScrollArea, QTabWidget
    from app.viewer.canvas_1 import _SelectCanvas
    from app.viewer.thumbnails import ThumbnailPanel


class PanelHistoryMixin:
    """Mixin providing undo/redo history and pipeline modification saving."""

    if TYPE_CHECKING:
        _original_doc_path: str
        _current_path: str
        _pdf_password: str
        _undo_stack: list[dict[str, Any]]
        _redo_stack: list[dict[str, Any]]
        _history_temp_files: set[str]
        _canvas_scroll: QScrollArea
        _canvas: _SelectCanvas
        _thumbnails: ThumbnailPanel
        _sidebar_tabs: QTabWidget
        _fitz_doc: fitz.Document | None

        def window(self) -> Any: ...
        def load(
            self,
            path: str,
            target_page: int = 0,
            target_scroll: int = -1,
            selected_pages: list[int] | None = None,
            active_sidebar_tab: Any = None,
            _is_history_step: bool = False,
        ) -> None: ...
        def set_page_rotations(self, rotations: dict[int, int]) -> None: ...
        def set_page_crops(self, crops: dict[int, tuple[float, float, float, float]]) -> None: ...
        def set_page_order(self, order: list[int] | None) -> None: ...

    def _cleanup_history_files(self):
        for path in getattr(self, "_history_temp_files", set()):
            if path and os.path.isfile(path) and path != getattr(self, "_original_doc_path", ""):
                with contextlib.suppress(Exception):
                    os.unlink(path)
        self._history_temp_files = set()

    def can_undo(self) -> bool:
        return len(getattr(self, "_undo_stack", [])) > 0

    def can_redo(self) -> bool:
        return len(getattr(self, "_redo_stack", [])) > 0

    def reset_pdf(self) -> bool:
        """Reset the PDF document in viewer to its original state (reverting edits, rotations, crops, order),
        without a confirmation dialog, recordable in undo stack so Ctrl+Z restores everything."""
        orig_path = getattr(self, "_original_doc_path", "") or getattr(self, "_current_path", "")
        if not orig_path or not os.path.isfile(orig_path):
            return False

        has_pipeline = False
        win: Any = self.window()
        vid = id(self)
        if win and hasattr(win, "_pipeline_state"):
            ps = win._pipeline_state.get(vid)
            if ps and ps.get("temp_path"):
                has_pipeline = True

        has_rotations = bool(getattr(self._canvas, "_page_rotations", None))
        has_crops = bool(getattr(self._canvas, "_page_crops", None))
        has_order = bool(getattr(self._canvas, "_page_order", None))
        has_sig = bool(getattr(self._canvas, "_active_sig", None))
        has_undo = bool(getattr(self, "_undo_stack", None))

        if not has_pipeline and not has_rotations and not has_crops and not has_order and not has_sig and not has_undo and (self._current_path == orig_path):
            if win and hasattr(win, "_set_status"):
                win._set_status("Document is already in original state")
            return False

        scroll_val = self._canvas_scroll.verticalScrollBar().value() if hasattr(self, "_canvas_scroll") else 0
        viewed_page = self._canvas.page_at_y(scroll_val) if (hasattr(self, "_canvas") and self._canvas.page_count() > 0) else 0
        cur_selected = self._thumbnails.selected_pages() if hasattr(self, "_thumbnails") else [viewed_page]

        cur_state = {
            "path": self._current_path,
            "target_page": viewed_page,
            "scroll_val": scroll_val,
            "selected_pages": cur_selected,
            "is_temp": bool(self._current_path != orig_path),
            "rotations": dict(getattr(self._canvas, "_page_rotations", {})),
            "crops": dict(getattr(self._canvas, "_page_crops", {})),
            "order": list(self._canvas._page_order) if getattr(self._canvas, "_page_order", None) else None,
            "kind": "reset_pdf",
        }
        self._undo_stack.append(cur_state)
        self._redo_stack.clear()

        # Reset canvas states
        self._canvas.set_page_rotations({})
        self._canvas.set_page_crops({})
        self._canvas.set_page_order(None)
        self._canvas.cancel_signature_placement()
        self._canvas._active_sig = None

        if win and hasattr(win, "_pipeline_state"):
            win._pipeline_state.pop(vid, None)

        self.load(orig_path, target_page=viewed_page, target_scroll=scroll_val, selected_pages=cur_selected, _is_history_step=True)
        self._original_doc_path = orig_path

        base_name = os.path.basename(orig_path)
        if win and hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
            for idx, v in enumerate(win._viewers):
                if v is self:
                    win._tab_bar.setTabText(idx, base_name)
                    win._tab_bar.setTabToolTip(idx, orig_path)
                    break
        if win and hasattr(win, "setWindowTitle"):
            win.setWindowTitle(f"{t('app.name')} - {base_name}")
        if win and hasattr(win, "_sync_edit_dirty_indicator"):
            win._sync_edit_dirty_indicator()
        if win and hasattr(win, "_set_status"):
            win._set_status("PDF reset to original (Ctrl+Z to undo)")
        return True

    def undo(self):
        if not getattr(self, "_undo_stack", None):
            win: Any = self.window()
            if hasattr(win, "_set_status"):
                win._set_status("Nothing to undo")
            return

        scroll_val = self._canvas_scroll.verticalScrollBar().value()
        viewed_page = self._canvas.page_at_y(scroll_val) if self._canvas.page_count() > 0 else 0
        selected_pages = self._thumbnails.selected_pages() if hasattr(self, "_thumbnails") else [0]
        cur_state = {
            "path": self._current_path,
            "target_page": viewed_page,
            "scroll_val": scroll_val,
            "selected_pages": selected_pages,
            "is_temp": bool(self._current_path != self._original_doc_path),
            "rotations": dict(getattr(self._canvas, "_page_rotations", {})),
            "crops": dict(getattr(self._canvas, "_page_crops", {})),
            "order": list(self._canvas._page_order) if getattr(self._canvas, "_page_order", None) else None,
        }
        self._redo_stack.append(cur_state)

        target_state = self._undo_stack.pop()
        target_path = target_state["path"]
        current_tab_widget = self._sidebar_tabs.currentWidget()

        self._canvas.close_doc()
        doc = getattr(self, "_fitz_doc", None)
        if doc is not None:
            with contextlib.suppress(Exception):
                if not getattr(doc, "is_closed", False):
                    doc.close()
            self._fitz_doc = None
        self._thumbnails._stop_all_workers()

        if "rotations" in target_state:
            self.set_page_rotations(target_state["rotations"])
        if "crops" in target_state:
            self.set_page_crops(target_state["crops"])
        if "order" in target_state:
            self.set_page_order(target_state["order"])

        self.load(
            target_path,
            target_page=target_state.get("target_page", 0),
            target_scroll=target_state.get("scroll_val", -1),
            selected_pages=target_state.get("selected_pages"),
            active_sidebar_tab=current_tab_widget,
            _is_history_step=True,
        )

        win: Any = self.window()
        vid = id(self)
        if target_path == self._original_doc_path:
            if hasattr(win, "_pipeline_state"):
                win._pipeline_state.pop(vid, None)
            if hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
                for idx, v in enumerate(win._viewers):
                    if v is self:
                        orig_name = os.path.basename(self._original_doc_path)
                        win._tab_bar.setTabText(idx, orig_name)
                        win._tab_bar.setTabToolTip(idx, self._original_doc_path)
                        break
            if hasattr(win, "_set_status"):
                win._set_status("Undo (Original document state)")
            if hasattr(win, "setWindowTitle"):
                orig_name = os.path.basename(self._original_doc_path)
                win.setWindowTitle(f"{t('app.name')} - {orig_name}")
        else:
            if hasattr(win, "_pipeline_state"):
                win._pipeline_state[vid] = {
                    "original_path": self._original_doc_path,
                    "temp_path": target_path,
                }
            if hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
                for idx, v in enumerate(win._viewers):
                    if v is self:
                        orig_name = os.path.basename(self._original_doc_path)
                        win._tab_bar.setTabText(idx, f"* {orig_name}")
                        win._tab_bar.setTabToolTip(idx, f"{self._original_doc_path} (modified)")
                        break
            if hasattr(win, "_set_status"):
                win._set_status("Undo")
            if hasattr(win, "setWindowTitle"):
                orig_name = os.path.basename(self._original_doc_path)
                win.setWindowTitle(f"{t('app.name')} - * {orig_name}")

        if hasattr(win, "_update_tab_bar_visibility"):
            win._update_tab_bar_visibility()
        if hasattr(win, "_update_breadcrumb"):
            win._update_breadcrumb()
        if hasattr(win, "_update_page_nav"):
            win._update_page_nav()

    def redo(self):
        if not getattr(self, "_redo_stack", None):
            win: Any = self.window()
            if hasattr(win, "_set_status"):
                win._set_status("Nothing to redo")
            return

        scroll_val = self._canvas_scroll.verticalScrollBar().value()
        viewed_page = self._canvas.page_at_y(scroll_val) if self._canvas.page_count() > 0 else 0
        selected_pages = self._thumbnails.selected_pages() if hasattr(self, "_thumbnails") else [0]
        cur_state = {
            "path": self._current_path,
            "target_page": viewed_page,
            "scroll_val": scroll_val,
            "selected_pages": selected_pages,
            "is_temp": bool(self._current_path != self._original_doc_path),
            "rotations": dict(getattr(self._canvas, "_page_rotations", {})),
            "crops": dict(getattr(self._canvas, "_page_crops", {})),
            "order": list(self._canvas._page_order) if getattr(self._canvas, "_page_order", None) else None,
        }
        self._undo_stack.append(cur_state)

        target_state = self._redo_stack.pop()
        target_path = target_state["path"]
        current_tab_widget = self._sidebar_tabs.currentWidget()

        self._canvas.close_doc()
        doc = getattr(self, "_fitz_doc", None)
        if doc is not None:
            with contextlib.suppress(Exception):
                if not getattr(doc, "is_closed", False):
                    doc.close()
            self._fitz_doc = None
        self._thumbnails._stop_all_workers()

        if "rotations" in target_state:
            self.set_page_rotations(target_state["rotations"])
        if "crops" in target_state:
            self.set_page_crops(target_state["crops"])
        if "order" in target_state:
            self.set_page_order(target_state["order"])

        self.load(
            target_path,
            target_page=target_state.get("target_page", 0),
            target_scroll=target_state.get("scroll_val", -1),
            selected_pages=target_state.get("selected_pages"),
            active_sidebar_tab=current_tab_widget,
            _is_history_step=True,
        )

        win: Any = self.window()
        vid = id(self)
        if target_path == self._original_doc_path:
            if hasattr(win, "_pipeline_state"):
                win._pipeline_state.pop(vid, None)
            if hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
                for idx, v in enumerate(win._viewers):
                    if v is self:
                        orig_name = os.path.basename(self._original_doc_path)
                        win._tab_bar.setTabText(idx, orig_name)
                        win._tab_bar.setTabToolTip(idx, self._original_doc_path)
                        break
            if hasattr(win, "setWindowTitle"):
                orig_name = os.path.basename(self._original_doc_path)
                win.setWindowTitle(f"{t('app.name')} - {orig_name}")
        else:
            if hasattr(win, "_pipeline_state"):
                win._pipeline_state[vid] = {
                    "original_path": self._original_doc_path,
                    "temp_path": target_path,
                }
            if hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
                for idx, v in enumerate(win._viewers):
                    if v is self:
                        orig_name = os.path.basename(self._original_doc_path)
                        win._tab_bar.setTabText(idx, f"* {orig_name}")
                        win._tab_bar.setTabToolTip(idx, f"{self._original_doc_path} (modified)")
                        break
            if hasattr(win, "setWindowTitle"):
                orig_name = os.path.basename(self._original_doc_path)
                win.setWindowTitle(f"{t('app.name')} - * {orig_name}")

        if hasattr(win, "_set_status"):
            win._set_status("Redo")
        if hasattr(win, "_update_tab_bar_visibility"):
            win._update_tab_bar_visibility()
        if hasattr(win, "_update_breadcrumb"):
            win._update_breadcrumb()
        if hasattr(win, "_update_page_nav"):
            win._update_page_nav()

    def _save_and_reload(self, doc_to_save, target_page: int | None = None, selected_pages: list[int] | None = None, scroll_to_target: bool = False):
        """Persist modifications to a temporary pipeline file and reload live viewer without modifying original on disk."""
        try:
            win: Any = self.window()
            vid = id(self)
            ps = getattr(win, "_pipeline_state", None)

            orig_path = getattr(self, "_original_doc_path", "") or getattr(self, "_current_path", "")
            orig_path = os.path.normpath(orig_path) if orig_path else ""

            scroll_val = self._canvas_scroll.verticalScrollBar().value()
            viewed_page = self._canvas.page_at_y(scroll_val) if self._canvas.page_count() > 0 else 0
            cur_selected = self._thumbnails.selected_pages() if hasattr(self, "_thumbnails") else [0]
            prev_state = {
                "path": self._current_path,
                "target_page": viewed_page,
                "scroll_val": scroll_val,
                "selected_pages": cur_selected,
                "is_temp": bool(self._current_path != orig_path),
                "rotations": dict(getattr(self._canvas, "_page_rotations", {})),
                "crops": dict(getattr(self._canvas, "_page_crops", {})),
                "order": list(self._canvas._page_order) if getattr(self._canvas, "_page_order", None) else None,
            }
            self._undo_stack.append(prev_state)
            if len(self._undo_stack) > 30:
                old_state = self._undo_stack.pop(0)
                if old_state.get("is_temp") and old_state.get("path") != self._current_path:
                    with contextlib.suppress(Exception):
                        os.unlink(old_state["path"])

            for red_s in self._redo_stack:
                if red_s.get("is_temp") and red_s.get("path") != self._current_path:
                    with contextlib.suppress(Exception):
                        os.unlink(red_s["path"])
            self._redo_stack.clear()

            if target_page is not None:
                viewed_page = target_page

            if selected_pages is None and hasattr(self, "_thumbnails"):
                selected_pages = self._thumbnails.selected_pages()

            current_tab_widget = self._sidebar_tabs.currentWidget()

            thumb_scroll_val = 0
            thumbnails = getattr(self, "_thumbnails", None)
            view = getattr(thumbnails, "_view", None) if thumbnails else None
            if view is not None:
                sb = view.verticalScrollBar()
                if sb:
                    thumb_scroll_val = sb.value()

            self._canvas.close_doc()
            doc = getattr(self, "_fitz_doc", None)
            if doc is not None:
                with contextlib.suppress(Exception):
                    if not getattr(doc, "is_closed", False):
                        doc.close()
                self._fitz_doc = None
            self._thumbnails._stop_all_workers()

            fd, temp_path = tempfile.mkstemp(prefix="PDF_Editor_modified_", suffix=".pdf")
            os.close(fd)
            temp_path = os.path.normpath(temp_path)
            self._history_temp_files.add(temp_path)

            atomic_pdf_write(
                doc_to_save, temp_path,
                save_opts={"garbage": 4, "deflate": True},
                close_writer=True,
            )

            if ps is not None:
                ps[vid] = {
                    "original_path": orig_path,
                    "temp_path": temp_path,
                }

            reload_scroll = -1 if scroll_to_target else scroll_val
            self.load(
                temp_path,
                target_page=viewed_page,
                target_scroll=reload_scroll,
                selected_pages=selected_pages,
                active_sidebar_tab=current_tab_widget,
                _is_history_step=True,
            )

            if win and hasattr(win, "_tab_bar") and hasattr(win, "_viewers"):
                for idx, v in enumerate(win._viewers):
                    if v is self:
                        orig_name = os.path.basename(orig_path)
                        win._tab_bar.setTabText(idx, f"* {orig_name}")
                        win._tab_bar.setTabToolTip(idx, f"{orig_path} (modified)")
                        break

            if win and hasattr(win, "_update_tab_bar_visibility"):
                win._update_tab_bar_visibility()
            if win and hasattr(win, "_update_breadcrumb"):
                win._update_breadcrumb()
            if win and hasattr(win, "setWindowTitle"):
                orig_name = os.path.basename(orig_path)
                win.setWindowTitle(f"{t('app.name')} - * {orig_name}")

            if not scroll_to_target and thumb_scroll_val > 0:
                thumbnails = getattr(self, "_thumbnails", None)
                view = getattr(thumbnails, "_view", None) if thumbnails else None
                if view is not None:
                    sb = view.verticalScrollBar()
                    if sb:
                        sb.setValue(min(thumb_scroll_val, sb.maximum()))

            if hasattr(win, "_update_page_nav"):
                win._update_page_nav()
            if hasattr(win, "_set_status"):
                win._set_status("Document modified (Press Ctrl+S to save changes)")
        except Exception as exc:
            show_error(self, exc)


