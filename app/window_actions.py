
# app/window_actions.py
"""PDF_Editor – WindowActionsMixin facade: navigation, themes, unsaved dirty indicators, presentation mode, and viewer UI interactions."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QWidget,
)
from shiboken6 import isValid

from app.constants import ACCENT, DESKTOP, OPEN_ALL_FILES_FILTER, TEXT_PRI, TEXT_SEC, _LP, _LQ
from app.i18n import t
from app.nav_config import NAV_ITEMS, _NAV_KEYS
from app.styles import STYLE, STYLE_LIGHT
from app.utils import SVG_MOON, SVG_SUN, _make_palette, show_error, svg_for_name, svg_icon
from app.viewer.presentation import PresentationWidget
from app.widgets import DropFileEdit, MultiDropWidget
from app.window_nav_page import WindowNavPageMixin
from app.window_recent_menu import WindowRecentMenuMixin
from app.window_tools_router import WindowToolsRouterMixin

if TYPE_CHECKING:
    from app.viewer.panel import PdfViewerPanel
    from app.window_tabs import _ViewerTabBar
    from app.workspace_bar import ElidedLabel, WorkspaceBar
    _Base = QMainWindow
else:
    _Base = object


def _has_dirty_user_pending(pending_list: list) -> bool:
    for e in pending_list or []:
        if not e.get("_existing") or e.get("_dirty") or e.get("_deleted") or e.get("type") == "delete_annot":
            return True
    return False


class WindowActionsMixin(WindowRecentMenuMixin, WindowNavPageMixin, WindowToolsRouterMixin, _Base):
    """Facade orchestrating zoom actions, theme switching, dirty indicators, presentation launch, and file drag-and-drop."""

    if TYPE_CHECKING:
        stack: QStackedWidget
        nav: QListWidget
        _tab_bar: _ViewerTabBar
        _viewers: list[PdfViewerPanel]
        _viewer: PdfViewerPanel | None
        _current_tool: int
        _saved_right_width: int
        _right_tool_container: QWidget
        _splitter: QSplitter
        _breadcrumb: ElidedLabel
        _undo_top_btn: QPushButton
        _redo_top_btn: QPushButton
        _tool_usage: dict[str, int]
        _pipeline_state: dict[int, dict[str, Any]]
        _zoom_widget: QWidget
        _zm_btn: QPushButton
        _zp_btn: QPushButton
        _z0_btn: QPushButton
        _lbl_zoom: QLabel
        _sidebar_collapsed: bool
        _sidebar: QWidget
        _brand_layout: QHBoxLayout
        _brand_text_w: QWidget
        _brand_title: QLabel
        _brand_sub: QLabel
        _nav_search: QLineEdit
        _footer_w: QWidget
        _sidebar_toggle_btn: QPushButton
        _pages_toggle_btn: QPushButton
        _right_pane_toggle_btn: QPushButton
        _tab_container: QWidget
        _page_nav_widget: QWidget
        _first_pg_btn: QPushButton
        _prev_pg_btn: QPushButton
        _next_pg_btn: QPushButton
        _last_pg_btn: QPushButton
        _page_input: QLineEdit
        _page_total_lbl: QLabel
        _lang_btn: QPushButton
        _toc_top_btn: QPushButton
        _night_top_btn: QPushButton
        _presentation: PresentationWidget | None
        _dark_mode: bool
        _qapp: QApplication
        _workspace_bar: WorkspaceBar
        _sb: QStatusBar
        _fullscreen: bool

        def _get_or_create_tool(self, index: int) -> QWidget | None: ...
        def _add_viewer_tab(self, path: str = ...) -> PdfViewerPanel: ...
        def _update_tab_bar_visibility(self) -> None: ...
        def _wait_for_workers_on_all_pages(self) -> None: ...
        def _wipe_all_pdf_passwords(self) -> None: ...

    def _edit_has_unsaved_for_path(self, path: str) -> bool:
        if not path:
            return False
        edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
        if edit_idx < 0 or edit_idx >= self.stack.count():
            return False
        edit_w = self.stack.widget(edit_idx)
        if edit_w is None or getattr(edit_w, "_is_lazy_placeholder", False):
            return False
        norm_p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        cur_edit_p = getattr(edit_w, "_doc_path", None)
        if cur_edit_p and os.path.normcase(os.path.normpath(os.path.abspath(cur_edit_p))) == norm_p:
            return bool(getattr(edit_w, "_user_pending", None))
        doc_states = getattr(edit_w, "_doc_states", None)
        if isinstance(doc_states, dict):
            for k, state in doc_states.items():
                if os.path.normcase(os.path.normpath(os.path.abspath(k))) == norm_p:
                    return _has_dirty_user_pending(state.get("pending", []))
        return False

    def _is_viewer_dirty(self, viewer) -> bool:
        if viewer is None:
            return False
        ps_map = getattr(self, "_pipeline_state", {})
        ps = ps_map.get(id(viewer)) if isinstance(ps_map, dict) else None
        if ps and ps.get("temp_path"):
            return True
        curr_p = viewer.current_path() if hasattr(viewer, "current_path") else ""
        orig_p = getattr(viewer, "_original_doc_path", "") or curr_p
        return bool(self._edit_has_unsaved_for_path(curr_p) or self._edit_has_unsaved_for_path(orig_p))

    def _sync_edit_dirty_indicator(self) -> None:
        if hasattr(self, "_tab_bar") and hasattr(self, "_viewers"):
            ps_map = getattr(self, "_pipeline_state", {})
            for idx, v in enumerate(self._viewers):
                curr_p = v.current_path() if hasattr(v, "current_path") else ""
                ps = ps_map.get(id(v)) if isinstance(ps_map, dict) else None
                disp_path = (ps.get("original_path") if ps else None) or getattr(v, "_original_doc_path", "") or curr_p
                if not disp_path:
                    continue
                base_name = os.path.basename(disp_path)
                dirty = self._is_viewer_dirty(v)
                self._tab_bar.setTabText(idx, f"* {base_name}" if dirty else base_name)
                self._tab_bar.setTabToolTip(idx, f"{disp_path} (unsaved changes)" if dirty else disp_path)

        active_v = getattr(self, "_viewer", None)
        active_dirty = False
        active_name = ""
        if active_v and active_v.current_path():
            ps_map = getattr(self, "_pipeline_state", {})
            ps = ps_map.get(id(active_v)) if isinstance(ps_map, dict) else None
            disp_path = (ps.get("original_path") if ps else None) or getattr(active_v, "_original_doc_path", "") or active_v.current_path()
            active_name = os.path.basename(disp_path)
            active_dirty = self._is_viewer_dirty(active_v)
        else:
            edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
            if edit_idx >= 0 and self._current_tool == edit_idx:
                edit_w = self.stack.widget(edit_idx)
                ep = getattr(edit_w, "_doc_path", None) if edit_w and not getattr(edit_w, "_is_lazy_placeholder", False) else None
                if ep:
                    active_name = os.path.basename(ep)
                    active_dirty = bool(getattr(edit_w, "_user_pending", None))

        if hasattr(self, "setWindowTitle"):
            if active_name:
                self.setWindowTitle(f"{t('app.name')} - {'* ' if active_dirty else ''}{active_name}")
            else:
                self.setWindowTitle(t("app.name"))
        self._update_breadcrumb()

    def _zoom_in_current(self):
        pres = getattr(self, "_presentation", None)
        if pres is not None and isValid(pres) and pres.isVisible():
            return
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                if getattr(canvas, "_selected_overlay_idx", -1) >= 0:
                    scale_fn = getattr(canvas, "scale_selected_overlay", None)
                    if callable(scale_fn):
                        scale_fn(1.10)
                        return
                zoom_in_fn = getattr(canvas, "zoom_in", None)
                if callable(zoom_in_fn):
                    zoom_in_fn()
                    return
        if self._viewer:
            canvas = getattr(self._viewer, "_canvas", None)
            if canvas is not None and hasattr(canvas, "zoom_in"):
                canvas.zoom_in()

    def _zoom_out_current(self):
        pres = getattr(self, "_presentation", None)
        if pres is not None and isValid(pres) and pres.isVisible():
            return
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                if getattr(canvas, "_selected_overlay_idx", -1) >= 0:
                    scale_fn = getattr(canvas, "scale_selected_overlay", None)
                    if callable(scale_fn):
                        scale_fn(0.90)
                        return
                zoom_out_fn = getattr(canvas, "zoom_out", None)
                if callable(zoom_out_fn):
                    zoom_out_fn()
                    return
        if self._viewer:
            canvas = getattr(self._viewer, "_canvas", None)
            if canvas is not None and hasattr(canvas, "zoom_out"):
                canvas.zoom_out()

    def _zoom_reset_current(self):
        pres = getattr(self, "_presentation", None)
        if pres is not None and isValid(pres) and pres.isVisible():
            return
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                reset_fn = getattr(canvas, "zoom_reset", None)
                if callable(reset_fn):
                    reset_fn()
                    return
        if self._viewer:
            canvas = getattr(self._viewer, "_canvas", None)
            if canvas is not None and hasattr(canvas, "zoom_reset"):
                canvas.zoom_reset()

    def _toggle_pages_sidebar(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            toggle_fn = getattr(edit_w, "toggle_pages_sidebar", None)
            if callable(toggle_fn):
                toggle_fn()
                return
        viewer = self._viewer
        if viewer:
            viewer._toggle_pages_sidebar()
            if self.sender() is getattr(self, "_toc_top_btn", None) and not viewer._pages_sidebar_collapsed:
                if viewer._toc_tree.topLevelItemCount() > 0:
                    viewer._sidebar_tabs.setCurrentIndex(viewer._toc_tab_idx)

    def _toggle_right_pane(self):
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            toggle_fn = getattr(edit_w, "toggle_controls", None)
            if callable(toggle_fn):
                toggle_fn()
            return
        if self._right_tool_container.isVisible():
            self._saved_right_width = max(260, self._right_tool_container.width())
            self._right_tool_container.setVisible(False)
            total = self._splitter.width()
            self._splitter.setSizes([total, 0])
        else:
            self._right_tool_container.setVisible(True)
            total = self._splitter.width()
            tool_w = max(280, getattr(self, "_saved_right_width", 380))
            self._splitter.setSizes([max(260, total - tool_w), tool_w])

    def _update_breadcrumb(self):
        full_path = ""
        is_dirty = False
        viewer = self._viewer
        if viewer and viewer.current_path():
            ps_map = getattr(self, "_pipeline_state", {})
            ps = ps_map.get(id(viewer)) if isinstance(ps_map, dict) else None
            if ps and ps.get("original_path"):
                raw_path = ps["original_path"]
                is_dirty = bool(ps.get("temp_path"))
            else:
                raw_path = getattr(viewer, "_original_doc_path", "") or viewer.current_path()
            full_path = os.path.normpath(raw_path) if raw_path else ""
            if self._is_viewer_dirty(viewer):
                is_dirty = True

        edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
        if getattr(self, "_current_tool", -1) == edit_idx and edit_idx >= 0:
            edit_w = self.stack.widget(edit_idx)
            if edit_w and not getattr(edit_w, "_is_lazy_placeholder", False):
                edit_path = getattr(edit_w, "_doc_path", None)
                if edit_path:
                    full_path = os.path.normpath(edit_path)
                    is_dirty = is_dirty or bool(getattr(edit_w, "_user_pending", None))

        prefix = "* " if is_dirty else ""
        if getattr(self, "_current_tool", -1) >= 0 and self._current_tool < len(NAV_ITEMS):
            tool_name = NAV_ITEMS[self._current_tool][0]
            if full_path:
                self._breadcrumb.setText(f"{prefix}{full_path}  >  {tool_name}")
            else:
                self._breadcrumb.setText(f"{prefix}{tool_name}")
        elif full_path:
            self._breadcrumb.setText(f"{prefix}{full_path}")
        else:
            self._breadcrumb.setText("")

        if hasattr(self._breadcrumb, "setCopyText"):
            self._breadcrumb.setCopyText(full_path)
        self._breadcrumb.setToolTip(f"{full_path}\n(Click to copy path)" if full_path else "")

    def _update_undo_redo_buttons(self):
        if not hasattr(self, "_undo_top_btn") or not hasattr(self, "_redo_top_btn"):
            return
        edit_idx = self._edit_tool_idx()
        crop_idx = self._crop_tool_idx()
        page_numbers_idx = self._page_numbers_tool_idx()
        viewer = self._viewer

        if getattr(self, "_current_tool", -1) == -1:
            self._undo_top_btn.setVisible(True)
            self._redo_top_btn.setVisible(True)
            can_u = viewer.can_undo() if viewer and hasattr(viewer, "can_undo") else False
            can_r = viewer.can_redo() if viewer and hasattr(viewer, "can_redo") else False
            self._undo_top_btn.setEnabled(can_u)
            self._redo_top_btn.setEnabled(can_r)
        elif self._current_tool == edit_idx:
            self._undo_top_btn.setVisible(True)
            self._redo_top_btn.setVisible(True)
            edit_w: Any = self._get_or_create_tool(edit_idx)
            user_pending = getattr(edit_w, "_user_pending", None)
            redo_stack = getattr(edit_w, "_redo_stack", None)
            self._undo_top_btn.setEnabled(bool(user_pending))
            self._redo_top_btn.setEnabled(bool(redo_stack))
        elif self._current_tool in (crop_idx, page_numbers_idx):
            self._undo_top_btn.setVisible(True)
            self._redo_top_btn.setVisible(True)
            tool_w: Any = self._get_or_create_tool(self._current_tool)
            btn_u = getattr(tool_w, "btn_undo", None)
            btn_r = getattr(tool_w, "btn_redo", None)
            self._undo_top_btn.setEnabled(btn_u.isEnabled() if btn_u else False)
            self._redo_top_btn.setEnabled(btn_r.isEnabled() if btn_r else False)
        else:
            self._undo_top_btn.setVisible(False)
            self._redo_top_btn.setVisible(False)

    def _handle_global_select_all(self):
        focus_w = self.focusWidget()
        if isinstance(focus_w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            focus_w.selectAll()
            return
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            canvas = getattr(edit_w, "_canvas", None)
            if canvas is not None:
                inline_edit = getattr(canvas, "_inline_edit", None)
                if inline_edit and inline_edit.isVisible():
                    inline_edit.selectAll()
                    return
        if self._viewer:
            self._viewer._select_all_text()

    def _setup_zoom_bar(self, active: bool, canvas=None):
        self._zoom_widget.setVisible(active)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for btn in (self._zm_btn, self._zp_btn, self._z0_btn):
                try:
                    btn.clicked.disconnect()
                except (RuntimeError, TypeError):
                    pass
        if canvas is None and hasattr(self, "_edit_tool_idx"):
            edit_idx = self._edit_tool_idx()
            if edit_idx >= 0 and self._current_tool == edit_idx:
                edit_w: Any = self._get_or_create_tool(edit_idx)
                canvas = getattr(edit_w, '_canvas', None)
        viewer = self._viewer
        if canvas is None and viewer:
            canvas = getattr(viewer, '_canvas', None)
        if canvas is None:
            return
        if active:
            self._zm_btn.clicked.connect(lambda: canvas.zoom_out())
            self._zp_btn.clicked.connect(lambda: canvas.zoom_in())
            self._z0_btn.clicked.connect(lambda: canvas.zoom_reset())
            canvas.zoom_changed.connect(lambda pct: self._lbl_zoom.setText(f"{pct}%"))
            self._lbl_zoom.setText(f"{round(canvas._zoom_factor * 100)}%")

    def _open_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("btn.open_pdf"),
            DESKTOP,
            OPEN_ALL_FILES_FILTER,
        )
        for path in paths:
            self._load_and_track(path)

    def _load_and_track(self, path: str):
        from app.window_tabs import WindowTabsMixin
        return WindowTabsMixin._load_and_track(cast(Any, self), path)

    def _on_second_instance(self, paths: list):
        _wlog = logging.getLogger(__name__)
        for path in paths:
            if isinstance(path, str) and os.path.isfile(path):
                try:
                    self._load_and_track(path)
                except Exception as exc:
                    _wlog.warning("second instance: failed to load %s: %s", path, exc)
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_status(self, msg: str):
        self._sb.showMessage(msg)

    def _set_status(self, msg: str):
        self._sb.showMessage(msg)

    def dragEnterEvent(self, e):
        if not e.mimeData().hasUrls():
            return
        for url in e.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isfile(local) or os.path.isdir(local):
                e.acceptProposedAction()
                return

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                scheme = url.scheme().lower() if url.isValid() else ""
                if scheme in ("http", "https", "ftp"):
                    QMessageBox.warning(self, t("msg.warning"), t("viewer.drop_url_not_supported"))
                    return
                continue
            if os.path.isdir(path):
                try:
                    entries = os.listdir(path)
                except OSError:
                    entries = []
                valid_files = sorted(os.path.join(path, f) for f in entries if os.path.isfile(os.path.join(path, f)))
                if len(valid_files) > 20:
                    reply = QMessageBox.question(
                        self,
                        t("msg.confirm"),
                        t("viewer.drop_many_pdfs_confirm", count=len(valid_files)),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        continue
                for fpath in valid_files:
                    self._load_and_track(fpath)
                continue
            if os.path.isfile(path):
                self._load_and_track(path)

    def _toggle_night_mode_top(self):
        viewer = self._viewer
        if viewer:
            active = self._night_top_btn.isChecked()
            canvas = getattr(viewer, "_canvas", None)
            if canvas:
                canvas.set_night_mode(active)
            bar_color = TEXT_PRI if self._dark_mode else _LP
            self._night_top_btn.setIcon(svg_icon(SVG_SUN if active else SVG_MOON, color=ACCENT if active else bar_color, size=15))

    def _refresh_viewer_top_buttons(self):
        try:
            v = self._viewer
            if v:
                self._toc_top_btn.setVisible(v._toc_tree.topLevelItemCount() > 0)
                canvas = getattr(v, "_canvas", None)
                is_night = bool(getattr(canvas, "_night_mode", False))
                self._night_top_btn.setChecked(is_night)
                bar_color = TEXT_PRI if self._dark_mode else _LP
                self._night_top_btn.setIcon(svg_icon(SVG_SUN if is_night else SVG_MOON, color=ACCENT if is_night else bar_color, size=15))
            else:
                self._toc_top_btn.setVisible(False)
                self._night_top_btn.setChecked(False)
        except Exception:
            self._toc_top_btn.setVisible(False)
            self._night_top_btn.setChecked(False)

    def _toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self._workspace_bar.setVisible(False)
            self._sidebar.setVisible(False)
            self._sb.setVisible(False)
            self.showFullScreen()
        else:
            self._workspace_bar.setVisible(True)
            if not self._sidebar_collapsed:
                self._sidebar.setVisible(True)
            self._sb.setVisible(True)
            self.showMaximized()

    def _start_presentation(self):
        edit_idx = self._edit_tool_idx()
        in_edit_mode = (self._current_tool == edit_idx)
        edit_w: Any = self._get_or_create_tool(edit_idx) if in_edit_mode else None

        viewer = self._viewer
        pres_path = ""
        pres_password = ""
        start_page = 0
        total_pages = 0
        temp_pres_path = ""

        if in_edit_mode and edit_w is not None and getattr(edit_w, "_doc_path", None):
            edit_canvas = getattr(edit_w, "_canvas", None)
            if edit_canvas is not None:
                if hasattr(edit_canvas, "_inline_edit") and edit_canvas._inline_edit.isVisible():
                    edit_canvas._commit_inline()

                pres_path = edit_w._doc_path
                pres_password = getattr(edit_w, "_pdf_password", "")
                total_pages = self._get_canvas_page_count(edit_canvas)
                sb_scroll = getattr(edit_w, "_canvas_scroll", None)
                sb = sb_scroll.verticalScrollBar() if sb_scroll else None
                start_page = edit_canvas.page_at_y(sb.value()) if total_pages > 0 and sb else 0

                user_pending = getattr(edit_w, "_user_pending", [])
                if user_pending and os.path.isfile(pres_path):
                    import fitz
                    from app.editor.apply_edits import apply_pending_edits

                    doc = None
                    try:
                        doc = fitz.open(pres_path)
                        if pres_password and doc.needs_pass:
                            doc.authenticate(pres_password)
                        pending_copy = [dict(e) for e in user_pending]
                        apply_pending_edits(doc, pending_copy)
                        fd, temp_pres_path = tempfile.mkstemp(prefix="PDF_Editor_pres_", suffix=".pdf")
                        os.close(fd)
                        doc.save(temp_pres_path, garbage=4, deflate=True)
                        pres_path = temp_pres_path
                        pres_password = ""
                    except Exception:
                        if temp_pres_path and os.path.isfile(temp_pres_path):
                            with contextlib.suppress(Exception):
                                os.unlink(temp_pres_path)
                        temp_pres_path = ""
                        pres_path = edit_w._doc_path
                    finally:
                        if doc is not None:
                            with contextlib.suppress(Exception):
                                doc.close()

        if not pres_path:
            if not viewer or not viewer._current_path:
                return
            canvas = getattr(viewer, "_canvas", None)
            if canvas is None:
                return
            sb_scroll = getattr(viewer, "_canvas_scroll", None)
            sb = sb_scroll.verticalScrollBar() if sb_scroll else None
            start_page = canvas.page_at_y(sb.value()) if canvas.page_count() > 0 and sb else 0
            total_pages = canvas.page_count()
            pres_path = viewer._current_path
            pres_password = getattr(viewer, "_pdf_password", "")
            edit_w = None

        try:
            pres = PresentationWidget(
                pres_path,
                pres_password,
                start_page,
                total_pages,
                dark_mode=self._dark_mode,
                viewer=viewer,
                edit_tab=edit_w,
                temp_source_path=temp_pres_path,
            )
        except Exception as e:
            if temp_pres_path and os.path.isfile(temp_pres_path):
                with contextlib.suppress(Exception):
                    os.unlink(temp_pres_path)
            show_error(self, e)
            return
        pres.destroyed.connect(lambda _=None, w=pres: setattr(self, "_presentation", None) if getattr(self, "_presentation", None) is w else None)
        self._presentation = pres
        self._presentation.show()

    def _toggle_sidebar(self):
        if not self._sidebar_collapsed and self._sidebar.width() > 60:
            self._sidebar_collapsed = False
            self._sidebar.setFixedWidth(52)
            self._brand_layout.setContentsMargins(0, 10, 0, 10)
            self._brand_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._brand_text_w.setVisible(False)
            self._brand_title.setVisible(False)
            self._brand_sub.setVisible(False)
            self._nav_search.setVisible(False)
            self._footer_w.setVisible(False)
            for r in range(self.nav.count()):
                it = self.nav.item(r)
                if it:
                    idx = it.data(Qt.ItemDataRole.UserRole)
                    if idx is not None and idx < 0:
                        it.setHidden(True)
            self._sidebar_toggle_btn.setIcon(self._workspace_bar._ico_bars)
            self.nav.updateGeometries()
            self.nav.viewport().update()
        elif not self._sidebar_collapsed:
            self._sidebar_collapsed = True
            self._sidebar.setVisible(False)
            self._sidebar_toggle_btn.setIcon(self._workspace_bar._ico_bars)
        else:
            self._sidebar_collapsed = False
            self._sidebar.setVisible(True)
            self._sidebar.setFixedWidth(228)
            self._brand_layout.setContentsMargins(12, 10, 10, 10)
            self._brand_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._brand_text_w.setVisible(True)
            self._brand_title.setVisible(True)
            self._brand_sub.setVisible(True)
            self._nav_search.setVisible(True)
            self._footer_w.setVisible(True)
            for r in range(self.nav.count()):
                it = self.nav.item(r)
                if it:
                    idx = it.data(Qt.ItemDataRole.UserRole)
                    if idx is not None and idx < 0:
                        it.setHidden(False)
            self._sidebar_toggle_btn.setIcon(self._workspace_bar._ico_bars)
            self.nav.updateGeometries()
            self.nav.viewport().update()
        QTimer.singleShot(50, self._relayout_viewer)

    def _relayout_viewer(self):
        for v in self._viewers:
            canvas = getattr(v, "_canvas", None)
            if canvas and getattr(canvas, "_doc", None):
                anchor = canvas._calc_anchor()
                canvas._layout_and_schedule(anchor=anchor)

        edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
        if self._current_tool == edit_idx:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            if edit_w and hasattr(edit_w, "_relayout_canvas"):
                edit_w._relayout_canvas()

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        self._apply_theme()
        from app.i18n import _update_config
        dark = self._dark_mode
        try:
            _update_config(lambda cfg: cfg.__setitem__("dark_mode", dark))
        except Exception:
            pass

    def _apply_theme(self):
        style = STYLE if self._dark_mode else STYLE_LIGHT
        nav_color = TEXT_SEC if self._dark_mode else _LQ
        self._qapp.setPalette(_make_palette(self._dark_mode))
        self._qapp.setStyleSheet(style)
        self._workspace_bar.update_theme(self._dark_mode, self._sidebar_collapsed)
        if hasattr(self, "_tab_bar") and hasattr(self._tab_bar, "update_theme"):
            self._tab_bar.update_theme(self._dark_mode)
        for r in range(self.nav.count()):
            it = self.nav.item(r)
            if it:
                tool_idx = it.data(Qt.ItemDataRole.UserRole)
                if tool_idx is not None and tool_idx >= 0:
                    _, icon_name, _ = _NAV_KEYS[tool_idx]
                    it.setIcon(svg_for_name(icon_name, color=nav_color, size=20))
                else:
                    it.setForeground(QColor(nav_color))
        self.nav.viewport().update()
        for v in self._viewers:
            v.update_theme(self._dark_mode)
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if w and not getattr(w, "_is_lazy_placeholder", False):
                fn = getattr(w, "update_theme", None)
                if callable(fn):
                    fn(self._dark_mode)
                for cls in (DropFileEdit, MultiDropWidget):
                    for child in w.findChildren(cls):
                        child_fn = getattr(child, "update_theme", None)
                        if callable(child_fn):
                            with contextlib.suppress(RuntimeError):
                                child_fn(self._dark_mode)
        pres = getattr(self, "_presentation", None)
        if pres is not None and isValid(pres):
            pres.update_theme(self._dark_mode)

