
# app/window_tools_router.py
"""PDF_Editor – Tool activation, sidebar navigation dispatch, and preview synchronizations mixin."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QWidget,
)

from app.i18n import t
from app.nav_config import NAV_ITEMS, _NAV_KEYS

if TYPE_CHECKING:
    from app.viewer.panel import PdfViewerPanel
    _Base = QMainWindow
else:
    _Base = object


class WindowToolsRouterMixin(_Base):
    """Mixin dispatching tool activation, mode switching, and tool-to-viewer event routing."""

    if TYPE_CHECKING:
        stack: QStackedWidget
        nav: QListWidget
        _current_tool: int
        _right_tool_container: QWidget
        _right_pane_toggle_btn: QPushButton
        _pages_toggle_btn: QPushButton
        _tab_container: QWidget
        _splitter: QSplitter
        _saved_right_width: int
        _tool_usage: dict[str, int]
        _viewer: PdfViewerPanel | None

        def _get_or_create_tool(self, index: int) -> QWidget | None: ...
        def _setup_zoom_bar(self, active: bool, canvas: Any = ...) -> None: ...
        def _update_breadcrumb(self) -> None: ...
        def _update_undo_redo_buttons(self) -> None: ...

    def _tool_idx_for_key(self, target_key: str) -> int:
        for idx, (key, _, _) in enumerate(_NAV_KEYS):
            if key == target_key:
                return idx
        return -1

    def _edit_tool_idx(self) -> int:
        return self._tool_idx_for_key("nav.edit")

    def _rotate_tool_idx(self) -> int:
        return self._tool_idx_for_key("nav.rotate")

    def _crop_tool_idx(self) -> int:
        return self._tool_idx_for_key("nav.crop")

    def _reorder_tool_idx(self) -> int:
        return self._tool_idx_for_key("nav.reorder")

    def _page_numbers_tool_idx(self) -> int:
        return self._tool_idx_for_key("nav.page_numbers")

    def _open_tool_by_name(self, tool_name: str):
        for i, (name, _, _) in enumerate(NAV_ITEMS):
            if name == tool_name or t(name) == tool_name:
                for r in range(self.nav.count()):
                    item = self.nav.item(r)
                    if item and item.data(Qt.ItemDataRole.UserRole) == i:
                        self.nav.setCurrentRow(r)
                        self._on_nav_clicked(item)
                        return
                self._activate_tool(i)
                return

    def _activate_tool(self, tool_idx: int):
        for r in range(self.nav.count()):
            it = self.nav.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) == tool_idx:
                self.nav.setCurrentRow(r)
                self._on_nav_clicked(it)
                return

    def _try_auto_load(self, index: int):
        nav_key = _NAV_KEYS[index][0]
        self._tool_usage[nav_key] = self._tool_usage.get(nav_key, 0) + 1
        with contextlib.suppress(Exception):
            from app.i18n import _update_config
            usage = dict(self._tool_usage)
            _update_config(lambda cfg: cfg.__setitem__("tool_usage", usage))
        widget: Any = self._get_or_create_tool(index)
        if widget is None:
            return
        viewer = self._viewer
        path = viewer.current_path() if viewer else ""
        if path:
            viewer_pwd = getattr(viewer, "_pdf_password", "")
            if hasattr(widget, "_pdf_password"):
                setattr(widget, "_pdf_password", viewer_pwd)
            fn = getattr(widget, "auto_load", None)
            if callable(fn):
                fn(path)

        compact_fn = getattr(widget, "set_compact_mode", None)
        if callable(compact_fn):
            compact_fn(bool(path), path or "")

    def _filter_nav(self, text: str):
        q = text.lower().strip()
        visible_groups = set()
        for r in range(self.nav.count()):
            it = self.nav.item(r)
            if it:
                tool_idx = it.data(Qt.ItemDataRole.UserRole)
                if tool_idx is not None and tool_idx >= 0:
                    match = not q or q in it.text().lower()
                    it.setHidden(not match)
                    if match:
                        visible_groups.add(r)
        for r in range(self.nav.count()):
            it = self.nav.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) == -1:
                has_visible = False
                for r2 in range(r + 1, self.nav.count()):
                    it2 = self.nav.item(r2)
                    if it2 and it2.data(Qt.ItemDataRole.UserRole) == -1:
                        break
                    if it2 and not it2.isHidden():
                        has_visible = True
                        break
                it.setHidden(not has_visible)

    def _on_nav_clicked(self, item: QListWidgetItem):
        row = item.data(Qt.ItemDataRole.UserRole)
        if row is None or row < 0:
            self.nav.clearSelection()
            return
        edit_idx = self._edit_tool_idx()
        rotate_idx = self._rotate_tool_idx()
        crop_idx = self._crop_tool_idx()
        reorder_idx = self._reorder_tool_idx()
        page_numbers_idx = self._page_numbers_tool_idx()
        viewer = self._viewer

        if row == self._current_tool:
            self.nav.clearSelection()
            self._current_tool = -1
            self.stack.setVisible(False)
            self._right_tool_container.setVisible(False)
            self._right_pane_toggle_btn.setVisible(False)
            self._pages_toggle_btn.setVisible(True)
            self._tab_container.setVisible(True)
            self._update_breadcrumb()
            self._setup_zoom_bar(True, canvas=viewer._canvas if viewer else None)
            if viewer:
                viewer.set_page_rotations({})
                viewer.set_crop_mode(False)
                viewer.set_crop_preview(None)
                viewer.set_numbers_preview(None)
                viewer.set_page_crops({})
                viewer.set_page_order(None)
        else:
            self._setup_zoom_bar(False)
            self._current_tool = row
            tool_widget: Any = self._get_or_create_tool(row)
            self.stack.setCurrentIndex(row)
            self._right_tool_container.setVisible(True)
            self.stack.setVisible(True)
            self._right_pane_toggle_btn.setVisible(True)

            if row == edit_idx:
                self.stack.setMinimumWidth(0)
                self.stack.setMaximumWidth(16777215)
                self._right_tool_container.setMinimumWidth(0)
                self._right_tool_container.setMaximumWidth(16777215)
                self._tab_container.setVisible(False)
                self._pages_toggle_btn.setVisible(True)
                self._setup_zoom_bar(True)
                ctrl_scroll = getattr(tool_widget, "_ctrl_scroll", None)
                if ctrl_scroll is not None:
                    ctrl_scroll.setVisible(True)
                if viewer:
                    viewer.set_page_rotations({})
                    viewer.set_crop_mode(False)
                    viewer.set_crop_preview(None)
                    viewer.set_numbers_preview(None)
                    viewer.set_page_crops({})
                    viewer.set_page_order(None)
            else:
                self._pages_toggle_btn.setVisible(True)
                self.stack.setMinimumWidth(260)
                self.stack.setMaximumWidth(600)
                self._right_tool_container.setMinimumWidth(260)
                self._right_tool_container.setMaximumWidth(600)
                self._tab_container.setVisible(True)
                total = self._splitter.width()
                tool_w = max(280, min(420, total // 3))
                self._splitter.setSizes([max(260, total - tool_w), tool_w])

                if row == rotate_idx:
                    rot_w = tool_widget
                    rotations = getattr(rot_w, "_rotations", None)
                    if rotations is not None and viewer:
                        viewer.set_page_rotations(rotations)
                elif viewer:
                    viewer.set_page_rotations({})

                if row == crop_idx:
                    crop_w = tool_widget
                    btn_draw = getattr(crop_w, "btn_draw_crop", None)
                    if btn_draw is not None:
                        active = btn_draw.isChecked()
                        if viewer:
                            viewer.set_crop_mode(active)
                            emit_fn = getattr(crop_w, "_emit_preview", None)
                            if callable(emit_fn):
                                emit_fn()
                            applied_crops = getattr(crop_w, "_applied_crops", None)
                            if applied_crops is not None:
                                viewer.set_page_crops(applied_crops)
                elif viewer:
                    viewer.set_crop_mode(False)
                    viewer.set_crop_preview(None)
                    viewer.set_page_crops({})

                if row == page_numbers_idx:
                    pn_w = tool_widget
                    emit_fn = getattr(pn_w, "_emit_preview", None)
                    if callable(emit_fn):
                        emit_fn()
                    update_fn = getattr(pn_w, "_update_undo_redo_state", None)
                    if callable(update_fn):
                        update_fn()
                elif viewer:
                    viewer.set_numbers_preview(None)

                if row == reorder_idx:
                    reorder_w = tool_widget
                    get_order_fn = getattr(reorder_w, "get_order", None)
                    if callable(get_order_fn):
                        order = get_order_fn()
                        if order and viewer:
                            viewer.set_page_order(cast(list[int] | None, order))
                elif viewer:
                    viewer.set_page_order(None)

            self._update_breadcrumb()
            self._try_auto_load(row)

        self._update_undo_redo_buttons()

    def _on_rotations_changed(self, rotations: dict[int, int]):
        if self._viewer:
            self._viewer.set_page_rotations(rotations)

    def _on_crop_changed(self, crop_data: dict | None):
        if self._viewer:
            self._viewer.set_crop_preview(crop_data)

    def _on_crops_changed(self, crops: dict[int, tuple[float, float, float, float]]):
        if self._viewer:
            self._viewer.set_page_crops(crops)

    def _on_crop_mode_toggled(self, active: bool):
        if self._viewer:
            self._viewer.set_crop_mode(active)

    def _on_order_changed(self, order: list[int] | None):
        if self._viewer:
            self._viewer.set_page_order(order)

    def _on_numbers_preview_changed(self, preview_data: dict | None):
        if self._viewer:
            self._viewer.set_numbers_preview(preview_data)


