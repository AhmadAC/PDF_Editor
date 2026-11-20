
# app/manage_tabs_dialog.py
"""PDF_Editor – Manage & Switch Tabs dialog matching LMS sheet management layout and functionality."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import ACCENT, BG_CARD, BG_INPUT, BORDER, TEXT_PRI, TEXT_SEC
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_TO_BOTTOM,
    SVG_ARROW_TO_TOP,
    SVG_ARROW_UP,
    SVG_CLONE,
    SVG_EDIT,
    SVG_FILE_PDF,
    SVG_PLUS,
    SVG_SEARCH,
    SVG_TRASH,
    is_dark,
    svg_icon,
)
from app.window_tab_file_ops import get_tab_disk_path, rename_tab_file_on_disk

if TYPE_CHECKING:
    from app.window import MainWindow


class _ManageTabsListWidget(QListWidget):
    """QListWidget supporting internal drag-and-drop reordering with signal emission."""

    def __init__(self, dialog: ManageTabsDialog, parent=None) -> None:
        super().__init__(parent)
        self._dialog = dialog
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        self._dialog._on_list_dropped()


class ManageTabsDialog(QDialog):
    """Dialog to search, filter, reorder, rename, duplicate, delete/close, and jump between open tabs."""

    def __init__(self, main_window: MainWindow) -> None:
        super().__init__(main_window)
        self.win = main_window
        self.setWindowTitle("Manage & Switch Tabs — PDF_Editor")
        self.setModal(True)
        self.setMinimumSize(780, 520)
        self.resize(840, 560)

        self._dark = is_dark()
        self._setup_ui()
        self._apply_theme()
        self._populate_tabs()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        # ── Search Input ───────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter sheets by name...")
        self.search_input.setFixedHeight(38)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.installEventFilter(self)
        search_row.addWidget(self.search_input, 1)
        root.addLayout(search_row)

        # ── Body: List on left, Action buttons on right ────────────────────
        body_h = QHBoxLayout()
        body_h.setSpacing(12)

        self.list_widget = _ManageTabsListWidget(self, self)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.itemSelectionChanged.connect(self._update_button_states)
        body_h.addWidget(self.list_widget, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        btn_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        dark = self._dark
        ico_col = TEXT_PRI if dark else "#1A2B28"
        red_col = "#EF4444" if dark else "#DC2626"

        def _make_action_btn(text: str, svg_src: str, col: str = ico_col, obj_name: str = ""):
            b = QPushButton(text)
            b.setIcon(svg_icon(svg_src, color=col, size=15))
            b.setIconSize(QSize(15, 15))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(34)
            b.setMinimumWidth(160)
            if obj_name:
                b.setObjectName(obj_name)
            return b

        self.btn_rename = _make_action_btn("Rename / Bulk...", SVG_EDIT)
        self.btn_rename.clicked.connect(self._rename_selected)
        btn_col.addWidget(self.btn_rename)

        self.btn_duplicate = _make_action_btn("Duplicate", SVG_CLONE)
        self.btn_duplicate.clicked.connect(self._duplicate_selected)
        btn_col.addWidget(self.btn_duplicate)

        self.btn_delete = _make_action_btn("Delete", SVG_TRASH, col=red_col, obj_name="btn_delete_action")
        self.btn_delete.setToolTip("Close selected tab(s)")
        self.btn_delete.clicked.connect(self._delete_selected)
        btn_col.addWidget(self.btn_delete)

        btn_col.addSpacing(6)

        self.btn_move_up = _make_action_btn("Move Up", SVG_ARROW_UP)
        self.btn_move_up.clicked.connect(self._move_up)
        btn_col.addWidget(self.btn_move_up)

        self.btn_move_down = _make_action_btn("Move Down", SVG_ARROW_DOWN)
        self.btn_move_down.clicked.connect(self._move_down)
        btn_col.addWidget(self.btn_move_down)

        self.btn_move_top = _make_action_btn("Move to Top", SVG_ARROW_TO_TOP)
        self.btn_move_top.clicked.connect(self._move_to_top)
        btn_col.addWidget(self.btn_move_top)

        self.btn_move_bottom = _make_action_btn("Move to Bottom", SVG_ARROW_TO_BOTTOM)
        self.btn_move_bottom.clicked.connect(self._move_to_bottom)
        btn_col.addWidget(self.btn_move_bottom)

        btn_col.addSpacing(6)

        self.btn_add = _make_action_btn("+ Add Sheet", SVG_PLUS)
        self.btn_add.setToolTip("Open new PDF document in a tab")
        self.btn_add.clicked.connect(self._add_sheet)
        btn_col.addWidget(self.btn_add)

        btn_col.addStretch(1)
        body_h.addLayout(btn_col)
        root.addLayout(body_h, 1)

        # ── Bottom row: Tip on left, Select / Jump button on right ─────────
        bottom_h = QHBoxLayout()
        bottom_h.setContentsMargins(0, 4, 0, 0)
        bottom_h.setSpacing(10)

        self.tip_lbl = QLabel(
            "Tip: Ctrl/Shift+Click to multi-select. Drag items to reorder. Double-click or press Enter to jump to sheet."
        )
        self.tip_lbl.setWordWrap(True)
        self.tip_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9.5pt;")
        bottom_h.addWidget(self.tip_lbl, 1)

        self.btn_jump = QPushButton("Select / Jump")
        self.btn_jump.setObjectName("btn_jump")
        self.btn_jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_jump.setFixedHeight(38)
        self.btn_jump.setMinimumWidth(140)
        self.btn_jump.clicked.connect(self._jump_to_selected)
        bottom_h.addWidget(self.btn_jump)

        root.addLayout(bottom_h)
        self.search_input.setFocus()

    def _apply_theme(self):
        dark = self._dark
        bg_card = BG_CARD if dark else "#FFFFFF"
        bg_inp = BG_INPUT if dark else "#F8FAFC"
        brd = BORDER if dark else "#CBD5E1"
        fg = TEXT_PRI if dark else "#1A2B28"
        del_fg = "#EF4444" if dark else "#DC2626"
        del_hover_bg = "rgba(239, 68, 68, 0.16)" if dark else "rgba(220, 38, 38, 0.12)"

        self.setStyleSheet(
            f"QDialog {{ background-color: {bg_card}; color: {fg}; border-radius: 8px; }}"
            f"QLineEdit {{ background-color: {bg_inp}; border: 1.5px solid {ACCENT}; "
            f"border-radius: 6px; padding: 6px 12px; font-size: 11pt; color: {fg}; }}"
            f"QListWidget {{ background-color: {bg_inp}; border: 1px solid {brd}; "
            f"border-radius: 6px; outline: none; padding: 4px; font-size: 11pt; color: {fg}; }}"
            f"QListWidget::item {{ padding: 8px 12px; margin: 1px 2px; border-radius: 5px; min-height: 32px; }}"
            f"QListWidget::item:selected {{ background-color: {'#264F78' if dark else '#D6E8FA'}; border: 1px solid {ACCENT}; color: #FFFFFF; font-weight: 600; }}"
            f"QListWidget::item:hover:!selected {{ background-color: {'#333333' if dark else '#F1F5F9'}; }}"
            f"QPushButton {{ background-color: {'#383A40' if dark else '#FFFFFF'}; border: 1px solid {brd}; "
            f"border-radius: 6px; padding: 6px 14px; font-size: 10pt; font-weight: 500; color: {fg}; text-align: left; }}"
            f"QPushButton:hover:enabled {{ background-color: {'#4F5254' if dark else '#F1F5F9'}; border-color: {ACCENT}; }}"
            f"QPushButton:disabled {{ color: {'#6B7280' if dark else '#9CA3AF'}; background-color: {'#2A2A2A' if dark else '#F1F5F9'}; }}"
            f"QPushButton#btn_delete_action {{ color: {del_fg}; font-weight: 600; }}"
            f"QPushButton#btn_delete_action:hover:enabled {{ background-color: {del_hover_bg}; border-color: {del_fg}; }}"
            f"QPushButton#btn_jump {{ background-color: #10B981; color: #FFFFFF; border: none; "
            f"border-radius: 6px; font-size: 11pt; font-weight: 700; text-align: center; }}"
            f"QPushButton#btn_jump:hover {{ background-color: #059669; }}"
            f"QPushButton#btn_jump:pressed {{ background-color: #047857; }}"
            f"QPushButton#btn_jump:disabled {{ background-color: {'#374151' if dark else '#D1D5DB'}; color: {'#9CA3AF' if dark else '#9CA3AF'}; }}"
        )

    def _populate_tabs(self, preserve_selection: bool = True):
        selected_viewers = [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).isSelected()
        ]

        self.list_widget.clear()
        query = self.search_input.text().strip().lower()

        active_v = getattr(self.win, "_viewer", None)
        viewers = getattr(self.win, "_viewers", [])

        select_row = -1
        dark = self._dark

        for i, v in enumerate(viewers):
            disk_p = get_tab_disk_path(self.win, v)
            tab_text = self.win._tab_bar.tabText(i)
            is_dirty = self.win._is_viewer_dirty(v)
            is_active = (v is active_v)

            base_name = os.path.basename(disk_p) if disk_p else tab_text
            display_name = f"* {base_name}" if is_dirty else base_name

            if query and (query not in display_name.lower() and query not in disk_p.lower()):
                continue

            item = QListWidgetItem()
            item.setText(display_name)
            item.setToolTip(f"{disk_p}\n(Tab {i + 1})" if disk_p else f"Tab {i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, v)

            if is_active:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                item.setIcon(svg_icon(SVG_FILE_PDF, color=ACCENT, size=18))
            else:
                item.setIcon(svg_icon(SVG_FILE_PDF, color=TEXT_SEC if dark else "#6B7280", size=18))

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
            v = sel_items[0].data(Qt.ItemDataRole.UserRole)
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
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.list_widget.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole) in self.win._viewers
        ]

    def _jump_to_selected(self):
        viewers = self._selected_viewers()
        if not viewers:
            item = self.list_widget.currentItem()
            if item:
                v = item.data(Qt.ItemDataRole.UserRole)
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
            v = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            if v in self.win._viewers:
                new_order.append(self.win._viewers.index(v))
        if len(new_order) == len(self.win._viewers):
            self.win.reorder_tabs(new_order)
            self._populate_tabs(preserve_selection=True)

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

    def eventFilter(self, watched, event):
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                cur = self.list_widget.currentRow()
                if cur < self.list_widget.count() - 1:
                    self.list_widget.setCurrentRow(cur + 1)
                return True
            if key == Qt.Key.Key_Up:
                cur = self.list_widget.currentRow()
                if cur > 0:
                    self.list_widget.setCurrentRow(cur - 1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._jump_to_selected()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)
