
# app/window_tabs.py
"""PDF_Editor – Tab management, unsaved edit checks, and multi-viewer window mixin."""

from __future__ import annotations

import os
import shutil
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabBar,
)

from app.constants import ACCENT, TEXT_PRI, _LQ
from app.i18n import add_recent_file, t
from app.utils import (
    SVG_CLOSE,
    SVG_COPY,
    SVG_FOLDER,
    SVG_MANAGE_TABS,
    SVG_PASTE,
    SVG_SAVE,
    SVG_TRASH,
    reveal_file,
    show_error,
    svg_icon,
)
from app.viewer.panel import PdfViewerPanel
from app.tools.import_pdf import convert_file_to_pdf
from app.window_tab_file_ops import (
    delete_tab_file_from_disk,
    get_tab_disk_path,
    rename_tab_file_on_disk,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import (
        QMainWindow,
        QStackedWidget,
        QStatusBar,
        QWidget,
    )
    _Base = QMainWindow
else:
    _Base = object

__all__ = ["_ViewerTabBar", "WindowTabsMixin"]

_TAB_CLOSE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">
<path d="M5.75 5.75l6.5 6.5M12.25 5.75l-6.5 6.5" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/>
</svg>"""

_SVG_RENAME = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>"""


def _render_close_svg_pixmap(color: str, size: int = 18, dpr: float = 1.0) -> QPixmap:
    svg_bytes = _TAB_CLOSE_SVG.format(color=color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    px = max(1, int(round(size * dpr)))
    pix = QPixmap(px, px)
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(dpr)
    if renderer.isValid():
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
        painter.end()
    return pix


class _ViewerTabBar(QTabBar):
    """Custom tab bar supporting middle-click to close, left-click on active tab to toggle thumbnails, Ctrl+V paste pages, and double-click to open."""

    active_tab_clicked = Signal()

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        btn = QPushButton(self)
        btn.setObjectName("tab_close_btn")
        btn.setFixedSize(18, 18)
        btn.setIconSize(QSize(18, 18))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(t("btn.close") if t("btn.close") != "btn.close" else "Close Tab")
        self._update_close_btn_icon(btn)
        btn.clicked.connect(lambda _=False, b=btn: self._on_close_btn_clicked(b))
        self.setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)

    def _update_close_btn_icon(self, btn: QPushButton, dark: bool | None = None) -> None:
        if dark is None:
            win = self.window()
            dark = getattr(win, "_dark_mode", True) if win else True
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        if dpr <= 0:
            dpr = 1.0
        normal_color = "#9CA3AF" if dark else "#6B7280"
        active_color = "#FFFFFF" if dark else "#1F2937"
        ico = QIcon()
        ico.addPixmap(_render_close_svg_pixmap(normal_color, 18, dpr), QIcon.Mode.Normal)
        ico.addPixmap(_render_close_svg_pixmap(active_color, 18, dpr), QIcon.Mode.Active)
        ico.addPixmap(_render_close_svg_pixmap(active_color, 18, dpr), QIcon.Mode.Selected)
        btn.setIcon(ico)

    def _on_close_btn_clicked(self, btn: QPushButton) -> None:
        for i in range(self.count()):
            if self.tabButton(i, QTabBar.ButtonPosition.RightSide) is btn:
                self.tabCloseRequested.emit(i)
                return

    def update_theme(self, dark: bool) -> None:
        for i in range(self.count()):
            btn = self.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if isinstance(btn, QPushButton):
                self._update_close_btn_icon(btn, dark=dark)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and event.key() == Qt.Key.Key_V:
            from app.viewer.panel_page_ops import has_cut_pages
            if has_cut_pages():
                win: Any = self.window()
                viewer = getattr(win, "_viewer", None)
                if viewer and viewer.current_path() and hasattr(viewer, "_paste_pages"):
                    pages = viewer._thumbnails.selected_pages() if hasattr(viewer, "_thumbnails") else [0]
                    viewer._paste_pages(pages)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()

        if event.button() == Qt.MouseButton.MiddleButton:
            idx = self.tabAt(pos)
            if idx >= 0:
                self.tabCloseRequested.emit(idx)
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.tabAt(pos)
            if idx >= 0 and idx == self.currentIndex():
                is_close_click = False
                for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
                    btn = self.tabButton(idx, side)
                    if btn and btn.isVisible() and btn.geometry().contains(pos):
                        is_close_click = True
                        break
                if not is_close_click:
                    self.active_tab_clicked.emit()

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        idx = self.tabAt(pos)
        if idx < 0:
            parent = self.window()
            open_fn = getattr(parent, "_open_pdf", None)
            if callable(open_fn):
                open_fn()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class WindowTabsMixin(_Base):
    """Mixin for MainWindow managing viewer tabs, document routing, and tab lifecycle."""

    if TYPE_CHECKING:
        _viewers: list[PdfViewerPanel]
        _viewer_stack: QStackedWidget
        _tab_bar: _ViewerTabBar
        stack: QStackedWidget
        nav: Any
        _right_tool_container: QWidget
        _right_pane_toggle_btn: QPushButton
        _tab_container: QWidget
        _current_tool: int
        _dark_mode: bool
        _sb: QStatusBar
        _pipeline_state: dict[int, dict[str, Any]]

        def _set_status(self, msg: str) -> None: ...
        def _handle_global_undo(self) -> None: ...
        def _handle_global_redo(self) -> None: ...
        def _update_page_nav(self) -> None: ...
        def _update_breadcrumb(self) -> None: ...
        def _viewer_has_unsaved(self, viewer: Any = ...) -> bool: ...
        def _edit_has_unsaved_for_path(self, path: str) -> bool: ...
        def _sync_edit_dirty_indicator(self) -> None: ...
        def _save_pipeline(self, prompt_dialog: bool = ...) -> None: ...
        def _save_as_copy_for_viewer(self, viewer: Any = ...) -> None: ...
        def _cleanup_pipeline(self, viewer_id: int) -> None: ...
        def _crop_tool_idx(self) -> int: ...
        def _edit_tool_idx(self) -> int: ...
        def _setup_zoom_bar(self, active: bool, canvas: Any = ...) -> None: ...
        def _toggle_pages_sidebar(self) -> None: ...
        def _refresh_viewer_top_buttons(self) -> None: ...

    @property
    def _viewer(self) -> PdfViewerPanel | None:
        if hasattr(self, "_viewers") and self._viewers:
            idx = self._viewer_stack.currentIndex()
            if 0 <= idx < len(self._viewers):
                return self._viewers[idx]
            return self._viewers[0]
        return None

    def _show_manage_tabs_dialog(self) -> None:
        """Open the Manage & Switch Tabs dialog."""
        from app.manage_tabs_dialog import ManageTabsDialog
        dlg = ManageTabsDialog(cast(Any, self))
        dlg.exec()

    def reorder_tab(self, from_idx: int, to_idx: int) -> None:
        """Move a single tab smoothly between two indices without reloading document bytes."""
        if from_idx == to_idx:
            return
        if not (0 <= from_idx < len(self._viewers)) or not (0 <= to_idx < len(self._viewers)):
            return

        cur_idx = self._tab_bar.currentIndex()
        cur_viewer = self._viewers[cur_idx] if 0 <= cur_idx < len(self._viewers) else None

        self._tab_bar.blockSignals(True)
        self._tab_bar.moveTab(from_idx, to_idx)
        self._tab_bar.blockSignals(False)

        v = self._viewers.pop(from_idx)
        self._viewers.insert(to_idx, v)

        self._viewer_stack.removeWidget(v)
        self._viewer_stack.insertWidget(to_idx, v)

        if cur_viewer in self._viewers:
            new_active = self._viewers.index(cur_viewer)
            self._tab_bar.setCurrentIndex(new_active)
            self._viewer_stack.setCurrentIndex(new_active)

    def reorder_tabs(self, new_order: list[int]) -> None:
        """Reorder all open tabs based on an updated list of indices."""
        if len(new_order) != len(self._viewers):
            return

        cur_idx = self._tab_bar.currentIndex()
        cur_viewer = self._viewers[cur_idx] if 0 <= cur_idx < len(self._viewers) else None

        reordered_viewers = [self._viewers[i] for i in new_order]
        reordered_texts = [self._tab_bar.tabText(i) for i in new_order]
        reordered_tips = [self._tab_bar.tabToolTip(i) for i in new_order]

        self._tab_bar.blockSignals(True)
        for i in range(len(new_order)):
            self._tab_bar.setTabText(i, reordered_texts[i])
            self._tab_bar.setTabToolTip(i, reordered_tips[i])
        self._tab_bar.blockSignals(False)

        self._viewers = reordered_viewers
        for i, v in enumerate(self._viewers):
            self._viewer_stack.removeWidget(v)
            self._viewer_stack.insertWidget(i, v)

        if cur_viewer in self._viewers:
            new_active = self._viewers.index(cur_viewer)
            self._tab_bar.setCurrentIndex(new_active)
            self._viewer_stack.setCurrentIndex(new_active)

    def _on_active_tab_clicked(self) -> None:
        toggle_fn = getattr(self, "_toggle_pages_sidebar", None)
        if callable(toggle_fn):
            toggle_fn()

    def _add_viewer_tab(self, path: str = "") -> PdfViewerPanel:
        viewer = PdfViewerPanel()

        viewer.crop_selected.connect(self._on_canvas_crop_selected)
        viewer.crop_applied.connect(self._on_canvas_crop_applied)
        viewer.crop_undo_requested.connect(self._handle_global_undo)
        viewer.crop_redo_requested.connect(self._handle_global_redo)
        if hasattr(viewer, "page_action_requested"):
            viewer.page_action_requested.connect(self._on_thumbnail_action_requested)

        viewer._canvas_scroll.verticalScrollBar().valueChanged.connect(
            lambda _: self._update_page_nav()
        )
        viewer._canvas.zoom_changed.connect(lambda _: self._update_page_nav())

        self._viewers.append(viewer)
        self._viewer_stack.addWidget(viewer)

        tab_title = os.path.basename(path) if path else t("viewer.title")
        tab_idx = self._tab_bar.addTab(tab_title)
        self._tab_bar.setTabToolTip(tab_idx, path or "")
        self._tab_bar.setCurrentIndex(tab_idx)
        self._viewer_stack.setCurrentIndex(tab_idx)

        if path:
            viewer.load(path)

        self._update_tab_bar_visibility()
        if hasattr(self, "_update_page_nav"):
            self._update_page_nav()
        if hasattr(self, "_update_breadcrumb"):
            self._update_breadcrumb()
        return viewer

    def _update_tab_bar_visibility(self) -> None:
        has_multiple = len(self._viewers) > 1
        has_open_doc = any(bool(v.current_path()) for v in self._viewers)
        self._tab_bar.setVisible(has_multiple or has_open_doc)
        if hasattr(self, "_sync_edit_dirty_indicator"):
            self._sync_edit_dirty_indicator()
        elif hasattr(self, "_update_breadcrumb"):
            self._update_breadcrumb()

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._viewers)):
            return
        viewer = self._viewers[index]

        closed_path = viewer.current_path()
        orig_path = getattr(viewer, "_original_doc_path", "")
        pipe_unsaved = hasattr(self, "_viewer_has_unsaved") and self._viewer_has_unsaved(viewer)
        edit_unsaved = hasattr(self, "_edit_has_unsaved_for_path") and (
            self._edit_has_unsaved_for_path(closed_path) or self._edit_has_unsaved_for_path(orig_path)
        )

        if pipe_unsaved or edit_unsaved:
            ans = QMessageBox.question(
                self,
                t("msg.warning"),
                t("pipeline.unsaved_prompt"),
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Save:
                if pipe_unsaved:
                    self._save_pipeline()
                if edit_unsaved:
                    edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
                    edit_w: Any = self.stack.widget(edit_idx) if (0 <= edit_idx < self.stack.count()) else None
                    if edit_w is not None and not getattr(edit_w, "_is_lazy_placeholder", False):
                        target_p = closed_path or orig_path
                        if target_p and getattr(edit_w, "_doc_path", None) != target_p:
                            edit_w.auto_load(target_p)
                        run_fn = getattr(edit_w, "_run", None)
                        if callable(run_fn):
                            run_fn()

        closed_norm = {os.path.normcase(os.path.normpath(os.path.abspath(p))) for p in (closed_path, orig_path) if p}

        edit_idx = self._edit_tool_idx() if hasattr(self, "_edit_tool_idx") else -1
        edit_w = self.stack.widget(edit_idx) if (0 <= edit_idx < self.stack.count()) else None
        if edit_w is not None and not getattr(edit_w, "_is_lazy_placeholder", False):
            doc_states = getattr(edit_w, "_doc_states", None)
            if isinstance(doc_states, dict):
                for p_norm in closed_norm:
                    doc_states.pop(p_norm, None)
            cur_edit_path = getattr(edit_w, "_doc_path", None)
            if cur_edit_path and os.path.normcase(os.path.normpath(os.path.abspath(cur_edit_path))) in closed_norm:
                close_fn = getattr(edit_w, "_close_pdf", None)
                if callable(close_fn):
                    close_fn()

        if hasattr(self, "_cleanup_pipeline"):
            self._cleanup_pipeline(id(viewer))

        viewer.close_doc()
        self._viewer_stack.removeWidget(viewer)
        self._viewers.pop(index)

        self._tab_bar.blockSignals(True)
        self._tab_bar.removeTab(index)
        self._tab_bar.blockSignals(False)
        viewer.deleteLater()

        if not self._viewers:
            self._add_viewer_tab()
        else:
            new_idx = min(index, len(self._viewers) - 1)
            self._tab_bar.setCurrentIndex(new_idx)
            self._viewer_stack.setCurrentIndex(new_idx)

        new_v = self._viewer
        new_path = new_v.current_path() if new_v else ""

        if not new_path:
            if getattr(self, "_current_tool", -1) == edit_idx and edit_idx >= 0:
                if hasattr(self, "nav"):
                    self.nav.clearSelection()
                self._current_tool = -1
                self.stack.setVisible(False)
                if hasattr(self, "_right_tool_container"):
                    self._right_tool_container.setVisible(False)
                if hasattr(self, "_right_pane_toggle_btn"):
                    self._right_pane_toggle_btn.setVisible(False)
                if hasattr(self, "_tab_container"):
                    self._tab_container.setVisible(True)
                if hasattr(self, "_setup_zoom_bar") and new_v:
                    self._setup_zoom_bar(True, canvas=new_v._canvas)
            if hasattr(self, "setWindowTitle"):
                self.setWindowTitle(t("app.name"))
        else:
            if edit_w is not None and not getattr(edit_w, "_is_lazy_placeholder", False) and getattr(self, "_current_tool", -1) == edit_idx:
                auto_fn = getattr(edit_w, "auto_load", None)
                if callable(auto_fn):
                    auto_fn(new_path)
            if hasattr(self, "setWindowTitle") and new_v:
                doc_name = os.path.basename(getattr(new_v, "_original_doc_path", "") or new_path)
                self.setWindowTitle(f"{t('app.name')} - {doc_name}")

        self._update_tab_bar_visibility()
        if hasattr(self, "_update_page_nav"):
            self._update_page_nav()
        if hasattr(self, "_sync_edit_dirty_indicator"):
            self._sync_edit_dirty_indicator()
        elif hasattr(self, "_update_breadcrumb"):
            self._update_breadcrumb()
        if hasattr(self, "_refresh_viewer_top_buttons"):
            self._refresh_viewer_top_buttons()

    def _close_current_tab(self) -> None:
        if hasattr(self, "_viewer_stack"):
            self._close_tab(self._viewer_stack.currentIndex())

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < len(self._viewers):
            self._viewer_stack.setCurrentIndex(index)
            viewer = self._viewers[index]
            self._update_tab_bar_visibility()
            if hasattr(self, "_update_page_nav"):
                self._update_page_nav()

            if getattr(self, "_current_tool", -1) >= 0:
                tool_w = self.stack.widget(self._current_tool)
                cur_path = viewer.current_path()
                fn = getattr(tool_w, "auto_load", None)
                if cur_path and callable(fn):
                    fn(cur_path)
            elif hasattr(self, "_setup_zoom_bar"):
                self._setup_zoom_bar(True, canvas=viewer._canvas)

            if hasattr(self, "_sync_edit_dirty_indicator"):
                self._sync_edit_dirty_indicator()

    def _on_tab_context_menu(self, point: QPoint) -> None:
        tab_idx = self._tab_bar.tabAt(point)
        if tab_idx < 0:
            return
        viewer = self._viewers[tab_idx]
        curr_path = viewer.current_path()
        disk_path = get_tab_disk_path(self, viewer)
        has_disk_file = bool(disk_path and os.path.isfile(disk_path))
        dark = getattr(self, "_dark_mode", True)
        icon_color = TEXT_PRI if dark else _LQ

        from app.viewer.panel_page_ops import has_cut_pages

        menu = QMenu(self)

        act_manage = menu.addAction(
            svg_icon(SVG_MANAGE_TABS, color=ACCENT, size=15),
            "Manage & Switch Tabs...\tCtrl+Shift+T",
        )
        menu.addSeparator()

        act_paste_pages = None
        if curr_path and os.path.exists(curr_path) and has_cut_pages():
            act_paste_pages = menu.addAction(
                svg_icon(SVG_PASTE, color=ACCENT, size=15),
                "Paste Cut Pages Here\tCtrl+V",
            )
            menu.addSeparator()

        act_save_copy = menu.addAction(svg_icon(SVG_SAVE, color=ACCENT, size=15), "Save As Copy...")
        act_save_copy.setEnabled(bool(curr_path and os.path.exists(curr_path)))

        act_rename = menu.addAction(svg_icon(_SVG_RENAME, color=icon_color, size=15), "Rename File on Disk...")
        act_rename.setEnabled(bool(disk_path))
        menu.addSeparator()

        act_close = menu.addAction(svg_icon(SVG_CLOSE, color="#EF4444", size=15), "Close Tab")
        act_close_others = menu.addAction(svg_icon(SVG_CLOSE, color=icon_color, size=15), "Close All Other Tabs")
        act_close_others.setEnabled(len(self._viewers) > 1)
        menu.addSeparator()

        act_reveal = None
        act_copy_path = None
        act_delete_disk = None
        if disk_path:
            if sys.platform == "win32":
                reveal_label = "Reveal in File Explorer"
            elif sys.platform == "darwin":
                reveal_label = "Reveal in Finder"
            elif shutil.which("dolphin"):
                reveal_label = "Reveal in Dolphin"
            else:
                reveal_label = "Reveal in File Manager"

            if has_disk_file:
                act_reveal = menu.addAction(svg_icon(SVG_FOLDER, color=icon_color, size=15), reveal_label)

            act_copy_path = menu.addAction(svg_icon(SVG_COPY, color=icon_color, size=15), "Copy Full Path")
            menu.addSeparator()
            if has_disk_file:
                act_delete_disk = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Delete From Disk")

        chosen = menu.exec(self._tab_bar.mapToGlobal(point))
        if chosen == act_manage:
            self._show_manage_tabs_dialog()
        elif act_paste_pages and chosen == act_paste_pages:
            self._tab_bar.setCurrentIndex(tab_idx)
            self._viewer_stack.setCurrentIndex(tab_idx)
            pages = viewer._thumbnails.selected_pages() if hasattr(viewer, "_thumbnails") else [0]
            viewer._paste_pages(pages)
        elif chosen == act_save_copy:
            self._save_as_copy_for_viewer(viewer)
        elif chosen == act_rename:
            rename_tab_file_on_disk(self, tab_idx, viewer)
        elif chosen == act_close:
            self._close_tab(tab_idx)
        elif chosen == act_close_others:
            for i in range(len(self._viewers) - 1, -1, -1):
                if i != tab_idx:
                    self._close_tab(i)
        elif act_reveal and chosen == act_reveal:
            reveal_file(os.path.normpath(disk_path))
        elif act_copy_path and chosen == act_copy_path:
            QApplication.clipboard().setText(os.path.normpath(disk_path))
        elif act_delete_disk and chosen == act_delete_disk:
            delete_tab_file_from_disk(self, tab_idx, viewer)

    def _load_and_track(self, path: str) -> PdfViewerPanel | None:
        if not path or not os.path.isfile(path):
            return None
        norm_path = os.path.normpath(os.path.abspath(path))

        for i, v in enumerate(self._viewers):
            orig_p = getattr(v, "_original_doc_path", "")
            curr_p = v.current_path()
            if (orig_p and os.path.normcase(os.path.normpath(os.path.abspath(orig_p))) == os.path.normcase(norm_path)) or (curr_p and os.path.normcase(os.path.normpath(os.path.abspath(curr_p))) == os.path.normcase(norm_path)):
                self._tab_bar.setCurrentIndex(i)
                self._viewer_stack.setCurrentIndex(i)
                add_recent_file(norm_path)
                self._update_tab_bar_visibility()
                if hasattr(self, "_update_page_nav"):
                    self._update_page_nav()
                if getattr(self, "_current_tool", -1) >= 0:
                    tool_w = self.stack.widget(self._current_tool)
                    fn = getattr(tool_w, "auto_load", None)
                    if callable(fn):
                        fn(curr_p or orig_p)
                if hasattr(self, "_sync_edit_dirty_indicator"):
                    self._sync_edit_dirty_indicator()
                return v

        is_pdf = norm_path.lower().endswith(".pdf")
        if not is_pdf:
            try:
                with open(norm_path, "rb") as f:
                    if b"%PDF-" in f.read(1024):
                        is_pdf = True
            except Exception:
                is_pdf = False

        temp_pdf = None
        target_load_path = norm_path

        if not is_pdf:
            try:
                if hasattr(self, "_set_status"):
                    self._set_status(f"Converting {os.path.basename(norm_path)} to PDF...")
                temp_pdf = convert_file_to_pdf(norm_path)
                target_load_path = os.path.normpath(temp_pdf)
            except Exception as exc:
                show_error(self, exc)
                return None

        cur_v = self._viewer
        if cur_v and not cur_v.current_path():
            target_v = cur_v
            idx = self._viewer_stack.currentIndex()
        else:
            target_v = self._add_viewer_tab()
            idx = len(self._viewers) - 1

        target_v._original_doc_path = norm_path
        if temp_pdf:
            target_v._history_temp_files.add(target_load_path)
            vid = id(target_v)
            ps = getattr(self, "_pipeline_state", None)
            if ps is not None:
                ps[vid] = {"original_path": norm_path, "temp_path": target_load_path}

        target_v.load(target_load_path, _is_history_step=bool(temp_pdf))
        target_v._original_doc_path = norm_path
        add_recent_file(norm_path)

        tab_title = f"● {os.path.basename(norm_path)}" if temp_pdf else os.path.basename(norm_path)
        tooltip = f"{norm_path} (Converted to PDF)" if temp_pdf else norm_path

        self._tab_bar.setTabText(idx, tab_title)
        self._tab_bar.setTabToolTip(idx, tooltip)
        self._update_tab_bar_visibility()
        if hasattr(self, "_update_page_nav"):
            self._update_page_nav()

        if getattr(self, "_current_tool", -1) >= 0:
            tool_w = self.stack.widget(self._current_tool)
            fn = getattr(tool_w, "auto_load", None)
            if callable(fn):
                fn(target_load_path)

        if hasattr(self, "_sync_edit_dirty_indicator"):
            self._sync_edit_dirty_indicator()

        if temp_pdf and hasattr(self, "_set_status"):
            self._set_status(f"Converted {os.path.basename(norm_path)} to PDF (Ctrl+S to save as PDF)")
        return target_v

    def _on_second_instance(self, paths: list[str]) -> None:
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive
        )
        self.show()
        self.raise_()
        self.activateWindow()
        for p in paths:
            if os.path.isfile(p):
                self._load_and_track(os.path.normpath(p))

    def _on_canvas_crop_selected(self, page_idx: int, rect: tuple) -> None:
        crop_idx = self._crop_tool_idx() if hasattr(self, "_crop_tool_idx") else -1
        if crop_idx >= 0:
            crop_w = self.stack.widget(crop_idx)
            fn = getattr(crop_w, "on_canvas_crop_selected", None)
            if callable(fn):
                fn(page_idx, rect)

    def _on_canvas_crop_applied(self) -> None:
        crop_idx = self._crop_tool_idx() if hasattr(self, "_crop_tool_idx") else -1
        if crop_idx >= 0:
            crop_w = self.stack.widget(crop_idx)
            fn = getattr(crop_w, "apply_crop_preview", None)
            if callable(fn):
                fn()

    def _on_thumbnail_action_requested(self, action: str, pages_arg: object) -> None:
        if self._viewer and hasattr(self._viewer, "_on_thumbnail_action"):
            self._viewer._on_thumbnail_action(action, pages_arg)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local and (os.path.isfile(local) or os.path.isdir(local)):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        files = [
            os.path.normpath(u.toLocalFile())
            for u in urls
            if u.toLocalFile() and os.path.isfile(u.toLocalFile())
        ]
        if files:
            for p in files:
                self._load_and_track(p)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
