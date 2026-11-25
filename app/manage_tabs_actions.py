# app/manage_tabs_actions.py
"""PDF_Editor – Tab population, sorting, popup preview control, and tab manipulation mixin."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QModelIndex, QPoint, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QWidget,
)
from shiboken6 import isValid

from app.constants import _LQ, ACCENT, TEXT_PRI
from app.manage_tabs_preview import (
    ROLE_DISK_PATH,
    ROLE_DISPLAY_NAME,
    ROLE_IS_ACTIVE,
    ROLE_IS_DIRTY,
    ROLE_PAGE_RANGE,
    ROLE_THUMBNAIL,
    ROLE_VIEWER_ID,
    _natural_sort_key,
    _PagePreviewPopup,
    get_page1_thumbnail_and_count,
    render_high_res_page1,
)
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CHECK,
    SVG_EDIT,
    SVG_SORT,
    SVG_SWAP,
    svg_icon,
)
from app.window_tab_file_ops import get_tab_disk_path, rename_tab_file_on_disk

if TYPE_CHECKING:
    from app.window import MainWindow
    _Base = QDialog
else:
    _Base = object


class ManageTabsActionsMixin(_Base):
    """Mixin implementing tab listing, filtering, sorting, popup previews, and tab management actions."""

    if TYPE_CHECKING:
        win: MainWindow
        _dark: bool
        _viewer_by_id: dict[int, Any]
        _thumb_cache: dict[str, QPixmap]
        _current_sort_mode: str
        _sort_asc_next: bool
        _preview_popup: _PagePreviewPopup | None
        _last_popup_closed_ms: float
        _last_popup_closed_vid: int | None
        search_input: QLineEdit
        list_widget: QListWidget
        btn_top_sort: QPushButton
        btn_sort_toggle: QPushButton
        btn_jump: QPushButton
        btn_rename: QPushButton
        btn_duplicate: QPushButton
        btn_delete: QPushButton
        btn_move_up: QPushButton
        btn_move_down: QPushButton
        btn_move_top: QPushButton
        btn_move_bottom: QPushButton

        def _set_sort_toggle_state(self, asc_next: bool) -> None: ...

    def _on_preview_popup_closed(self, viewer_id: int | None) -> None:
        self._last_popup_closed_ms = time.monotonic() * 1000.0
        self._last_popup_closed_vid = viewer_id
        self._preview_popup = None

    def _dismiss_preview_if_open(self) -> bool:
        """Close the active preview popup if it is currently visible or was just auto-dismissed on this click."""
        if self._preview_popup is not None and isValid(self._preview_popup) and self._preview_popup.isVisible():
            self._preview_popup.close()
            self._preview_popup = None
            return True
        if (time.monotonic() * 1000.0 - self._last_popup_closed_ms) < 120.0:
            return True
        return False

    def _render_high_res_page1(self, v: Any, disk_path: str, max_w: int = 500, max_h: int = 680) -> QPixmap | None:
        return render_high_res_page1(self, v, disk_path, max_w=max_w, max_h=max_h)

    def _show_thumbnail_preview_popup(self, index: QModelIndex) -> None:
        """Display a high-definition Page 1 preview in the middle of the screen, or close if already open for it."""
        if not index.isValid():
            return

        v_id = index.data(ROLE_VIEWER_ID)
        if self._preview_popup is not None and isValid(self._preview_popup) and self._preview_popup.isVisible():
            prev_vid = self._preview_popup._viewer_id
            self._preview_popup.close()
            self._preview_popup = None
            if prev_vid == v_id:
                return

        display_name = str(index.data(ROLE_DISPLAY_NAME) or "")
        page_range_text = str(index.data(ROLE_PAGE_RANGE) or "")
        disk_path = str(index.data(ROLE_DISK_PATH) or "")
        v = self._viewer_by_id.get(v_id)

        high_res_pix = self._render_high_res_page1(v, disk_path)
        if high_res_pix is None:
            fallback = index.data(ROLE_THUMBNAIL)
            if isinstance(fallback, QPixmap) and not fallback.isNull():
                high_res_pix = fallback

        if high_res_pix is None or high_res_pix.isNull():
            return

        self._preview_popup = _PagePreviewPopup(
            title=display_name,
            page_range=page_range_text,
            pixmap=high_res_pix,
            viewer_id=v_id if isinstance(v_id, int) else None,
            dark=self._dark,
            parent=self,
        )
        self._preview_popup.show()

    def _get_page1_thumbnail_and_count(
        self, v: Any, disk_path: str
    ) -> tuple[QPixmap | None, int]:
        return get_page1_thumbnail_and_count(self, v, disk_path, self._thumb_cache)

    def _populate_tabs(self, preserve_selection: bool = True):
        selected_viewers = self._selected_viewers()

        self.list_widget.clear()
        self._viewer_by_id.clear()

        query = self.search_input.text().strip().lower()

        active_v = getattr(self.win, "_viewer", None)
        viewers = getattr(self.win, "_viewers", [])

        select_row = -1

        for i, v in enumerate(viewers):
            v_id = id(v)
            self._viewer_by_id[v_id] = v

            disk_p = get_tab_disk_path(self.win, v)
            tab_text = self.win._tab_bar.tabText(i)
            is_dirty = self.win._is_viewer_dirty(v)
            is_active = (v is active_v)

            base_name = os.path.basename(disk_p) if disk_p else tab_text
            display_name = f"* {base_name}" if is_dirty else base_name

            if query and (query not in display_name.lower() and query not in disk_p.lower()):
                continue

            thumb_pix, total_pages = self._get_page1_thumbnail_and_count(v, disk_p)

            if total_pages <= 0:
                page_range_str = "—"
            elif total_pages == 1:
                page_range_str = "1 page"
            else:
                page_range_str = f"1–{total_pages} pages"

            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 56))
            item.setData(ROLE_VIEWER_ID, v_id)
            item.setData(Qt.ItemDataRole.UserRole, v_id)
            item.setData(ROLE_DISPLAY_NAME, display_name)
            item.setData(ROLE_PAGE_RANGE, page_range_str)
            item.setData(ROLE_THUMBNAIL, thumb_pix)
            item.setData(ROLE_IS_ACTIVE, is_active)
            item.setData(ROLE_IS_DIRTY, is_dirty)
            item.setData(ROLE_DISK_PATH, disk_p)

            page_desc = f"{total_pages} page" if total_pages == 1 else f"{total_pages} pages"
            tooltip_lines = [
                display_name,
                f"Location: {disk_p or 'New unsaved document'}",
                f"Range: {page_range_str} ({page_desc})",
                f"Tab {i + 1}" + (" (Active)" if is_active else ""),
                "Click thumbnail to preview Page 1 (click again or outside to close)",
            ]
            item.setToolTip("\n".join(tooltip_lines))

            self.list_widget.addItem(item)
            row_idx = self.list_widget.count() - 1

            if preserve_selection and v in selected_viewers:
                item.setSelected(True)
            elif select_row < 0 and is_active:
                select_row = row_idx

        if not self.list_widget.selectedItems() and select_row >= 0:
            self.list_widget.setCurrentRow(select_row)

        self._update_button_states()

    def _on_search_changed(self, _text: str):
        self._populate_tabs(preserve_selection=True)

    def _update_button_states(self):
        sel_items = self.list_widget.selectedItems()
        n_sel = len(sel_items)
        has_sel = (n_sel > 0)
        is_single = (n_sel == 1)

        self.btn_jump.setEnabled(has_sel)
        self.btn_rename.setEnabled(is_single)
        self.btn_duplicate.setEnabled(is_single)
        self.btn_delete.setEnabled(has_sel)

        can_move = False
        if is_single:
            raw = sel_items[0].data(ROLE_VIEWER_ID)
            v = self._viewer_by_id.get(raw) if isinstance(raw, int) else raw
            if v in self.win._viewers:
                cur_pos = self.win._viewers.index(v)
                total = len(self.win._viewers)
                self.btn_move_up.setEnabled(cur_pos > 0)
                self.btn_move_top.setEnabled(cur_pos > 0)
                self.btn_move_down.setEnabled(cur_pos < total - 1)
                self.btn_move_bottom.setEnabled(cur_pos < total - 1)
                can_move = True

        if not can_move:
            self.btn_move_up.setEnabled(False)
            self.btn_move_down.setEnabled(False)
            self.btn_move_top.setEnabled(False)
            self.btn_move_bottom.setEnabled(False)

    def _selected_viewers(self) -> list[Any]:
        res: list[Any] = []
        for item in self.list_widget.selectedItems():
            raw = item.data(ROLE_VIEWER_ID)
            v = self._viewer_by_id.get(raw) if isinstance(raw, int) else raw
            if v is None:
                raw_user = item.data(Qt.ItemDataRole.UserRole)
                v = self._viewer_by_id.get(raw_user) if isinstance(raw_user, int) else raw_user
            if v in self.win._viewers:
                res.append(v)
        return res

    def _jump_to_selected(self):
        viewers = self._selected_viewers()
        if not viewers:
            item = self.list_widget.currentItem()
            if item:
                raw = item.data(ROLE_VIEWER_ID)
                v = self._viewer_by_id.get(raw) if isinstance(raw, int) else raw
                if v is None:
                    raw_user = item.data(Qt.ItemDataRole.UserRole)
                    v = self._viewer_by_id.get(raw_user) if isinstance(raw_user, int) else raw_user
                if v in self.win._viewers:
                    viewers = [v]
        if not viewers:
            return

        target_v = viewers[0]
        idx = self.win._viewers.index(target_v)
        self.win._tab_bar.setCurrentIndex(idx)
        self.win._viewer_stack.setCurrentIndex(idx)
        self.accept()

    def _on_item_double_clicked(self, _item: QListWidgetItem):
        self._jump_to_selected()

    def _on_list_dropped(self):
        """Handle drag-and-drop manual reordering in the list."""
        new_order: list[int] = []
        for i in range(self.list_widget.count()):
            raw = self.list_widget.item(i).data(ROLE_VIEWER_ID)
            v = self._viewer_by_id.get(raw) if isinstance(raw, int) else raw
            if v is None:
                raw_user = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
                v = self._viewer_by_id.get(raw_user) if isinstance(raw_user, int) else raw_user
            if v in self.win._viewers:
                new_order.append(self.win._viewers.index(v))
        if len(new_order) == len(self.win._viewers):
            self.win.reorder_tabs(new_order)
            self._populate_tabs(preserve_selection=True)

    def _sort_tabs(self, mode: str, only_selected: bool = False) -> None:
        """Sort all tabs or selected tabs using natural numerical sorting, page count, or modified status."""
        total_viewers = len(self.win._viewers)
        if total_viewers <= 1:
            return

        selected_viewers = self._selected_viewers()
        is_subset = bool(only_selected and len(selected_viewers) > 1 and len(selected_viewers) < total_viewers)

        if is_subset:
            target_indices = sorted(self.win._viewers.index(v) for v in selected_viewers if v in self.win._viewers)
        else:
            target_indices = list(range(total_viewers))

        items_to_sort: list[dict[str, Any]] = []
        for idx in target_indices:
            v = self.win._viewers[idx]
            disk_p = get_tab_disk_path(self.win, v)
            tab_text = self.win._tab_bar.tabText(idx)
            raw_name = os.path.basename(disk_p) if disk_p else tab_text
            clean_name = raw_name.lstrip("* ").strip()
            _, total_pages = self._get_page1_thumbnail_and_count(v, disk_p)
            is_dirty = self.win._is_viewer_dirty(v)
            items_to_sort.append({
                "orig_idx": idx,
                "viewer": v,
                "clean_name": clean_name,
                "natural_key": _natural_sort_key(clean_name),
                "total_pages": total_pages,
                "is_dirty": is_dirty,
            })

        if mode == "name_asc":
            items_to_sort.sort(key=lambda x: x["natural_key"])
        elif mode == "name_desc":
            items_to_sort.sort(key=lambda x: x["natural_key"], reverse=True)
        elif mode == "pages_asc":
            items_to_sort.sort(key=lambda x: (x["total_pages"], x["natural_key"]))
        elif mode == "pages_desc":
            items_to_sort.sort(key=lambda x: (-x["total_pages"], x["natural_key"]))
        elif mode == "dirty_first":
            items_to_sort.sort(key=lambda x: (0 if x["is_dirty"] else 1, x["natural_key"]))
        elif mode == "reverse":
            items_to_sort.reverse()
        else:
            return

        self._current_sort_mode = mode

        if hasattr(self, "_set_sort_toggle_state"):
            if mode == "name_asc":
                self._set_sort_toggle_state(asc_next=False)
            elif mode == "name_desc":
                self._set_sort_toggle_state(asc_next=True)

        new_order = list(range(total_viewers))
        for slot_pos, item_info in zip(target_indices, items_to_sort):
            new_order[slot_pos] = item_info["orig_idx"]

        if new_order != list(range(total_viewers)):
            self.win.reorder_tabs(new_order)
            self._populate_tabs(preserve_selection=True)

        mode_labels = {
            "name_asc": "A → Z",
            "name_desc": "Z → A",
            "pages_asc": "Page count (fewest first)",
            "pages_desc": "Page count (most first)",
            "dirty_first": "Unsaved first (*)",
            "reverse": "Reversed",
        }
        lbl = mode_labels.get(mode, mode)
        scope_lbl = "selected" if is_subset else "all"
        msg = f"Sorted {scope_lbl} sheets: {lbl}"
        if hasattr(self.win, "_set_status"):
            self.win._set_status(msg)
        elif hasattr(self.win, "_sb"):
            self.win._sb.showMessage(msg)

    def _show_sort_menu(self, anchor_widget: QWidget | None = None) -> None:
        """Display a popup menu with all sorting options (A-Z, Z-A, page count, unsaved first, reverse)."""
        menu = QMenu(self)
        dark = self._dark
        icon_color = TEXT_PRI if dark else _LQ

        sel_count = len(self._selected_viewers())
        has_multi = sel_count > 1

        def _add_sort_action(label: str, mode: str, icon_svg: str, only_sel: bool = False):
            is_active = (self._current_sort_mode == mode and not only_sel)
            act = menu.addAction(svg_icon(icon_svg, color=icon_color, size=15), label)
            if is_active:
                act.setIcon(svg_icon(SVG_CHECK, color=ACCENT, size=15))
            act.triggered.connect(lambda _=False, m=mode, sel=only_sel: self._sort_tabs(m, only_selected=sel))
            return act

        _add_sort_action("Sort by Name (A → Z)", "name_asc", SVG_SORT)
        _add_sort_action("Sort by Name (Z → A)", "name_desc", SVG_SORT)
        menu.addSeparator()

        _add_sort_action("Sort by Page Count (Fewest to Most)", "pages_asc", SVG_ARROW_UP)
        _add_sort_action("Sort by Page Count (Most to Fewest)", "pages_desc", SVG_ARROW_DOWN)
        menu.addSeparator()

        _add_sort_action("Sort Unsaved / Modified Tabs First (*)", "dirty_first", SVG_EDIT)
        _add_sort_action("Reverse Current Tab Sequence", "reverse", SVG_SWAP)

        if has_multi:
            menu.addSeparator()
            act_sel_hdr = menu.addAction(f"Selected Only ({sel_count} sheets):")
            act_sel_hdr.setEnabled(False)
            _add_sort_action("Sort Selected Sheets (A → Z)", "name_asc", SVG_SORT, only_sel=True)
            _add_sort_action("Sort Selected Sheets (Z → A)", "name_desc", SVG_SORT, only_sel=True)

        target = anchor_widget or self.btn_top_sort
        menu.exec(target.mapToGlobal(QPoint(0, target.height() + 2)))

    def _rename_selected(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        tab_idx = self.win._viewers.index(v)
        rename_tab_file_on_disk(self.win, tab_idx, v)
        self._populate_tabs(preserve_selection=True)

    def _duplicate_selected(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        path = v.current_path() or getattr(v, "_original_doc_path", "")
        if path and os.path.isfile(path):
            self.win._load_and_track(path)
            self._populate_tabs(preserve_selection=True)

    def _delete_selected(self):
        viewers = self._selected_viewers()
        if not viewers:
            return
        indices = sorted([self.win._viewers.index(v) for v in viewers], reverse=True)
        for idx in indices:
            self.win._close_tab(idx)
        self._populate_tabs(preserve_selection=False)

    def _move_up(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        cur_idx = self.win._viewers.index(v)
        if cur_idx > 0:
            self.win.reorder_tab(cur_idx, cur_idx - 1)
            self._populate_tabs(preserve_selection=True)

    def _move_down(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        cur_idx = self.win._viewers.index(v)
        if cur_idx < len(self.win._viewers) - 1:
            self.win.reorder_tab(cur_idx, cur_idx + 1)
            self._populate_tabs(preserve_selection=True)

    def _move_to_top(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        cur_idx = self.win._viewers.index(v)
        if cur_idx > 0:
            self.win.reorder_tab(cur_idx, 0)
            self._populate_tabs(preserve_selection=True)

    def _move_to_bottom(self):
        viewers = self._selected_viewers()
        if len(viewers) != 1:
            return
        v = viewers[0]
        cur_idx = self.win._viewers.index(v)
        total = len(self.win._viewers)
        if cur_idx < total - 1:
            self.win.reorder_tab(cur_idx, total - 1)
            self._populate_tabs(preserve_selection=True)

    def _add_sheet(self):
        self.win._open_pdf()
        self._populate_tabs(preserve_selection=True)