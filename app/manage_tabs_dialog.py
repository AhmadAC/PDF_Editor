# app/manage_tabs_dialog.py
"""PDF_Editor – ManageTabsDialog facade composing tab preview, custom list, and tab management actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from shiboken6 import isValid

from app.constants import ACCENT, BG_CARD, BG_INPUT, BORDER, TEXT_PRI, TEXT_SEC
from app.manage_tabs_actions import ManageTabsActionsMixin
from app.manage_tabs_list import _ManageTabItemDelegate, _ManageTabsListWidget
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
)
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_TO_BOTTOM,
    SVG_ARROW_TO_TOP,
    SVG_ARROW_UP,
    SVG_CLONE,
    SVG_EDIT,
    SVG_PLUS,
    SVG_SORT,
    SVG_TRASH,
    is_dark,
    svg_icon,
)

if TYPE_CHECKING:
    from app.window import MainWindow

__all__ = [
    "ManageTabsDialog",
    "ROLE_DISK_PATH",
    "ROLE_DISPLAY_NAME",
    "ROLE_IS_ACTIVE",
    "ROLE_IS_DIRTY",
    "ROLE_PAGE_RANGE",
    "ROLE_THUMBNAIL",
    "ROLE_VIEWER_ID",
    "_ManageTabItemDelegate",
    "_ManageTabsListWidget",
    "_PagePreviewPopup",
    "_natural_sort_key",
]


class ManageTabsDialog(ManageTabsActionsMixin, QDialog):
    """Facade dialog to search, filter, reorder, rename, duplicate, delete/close, sort, and jump between open tabs."""

    def __init__(self, main_window: MainWindow) -> None:
        super().__init__(main_window)
        self.win = main_window
        self.setWindowTitle("Manage & Switch Tabs — PDF_Editor")
        self.setModal(True)
        self.setMinimumSize(820, 560)
        self.resize(880, 600)

        self._dark = is_dark()
        self._viewer_by_id: dict[int, Any] = {}
        self._thumb_cache: dict[str, QPixmap] = {}
        self._current_sort_mode: str = ""
        self._sort_asc_next: bool = True
        self._preview_popup: _PagePreviewPopup | None = None
        self._last_popup_closed_ms: float = 0.0
        self._last_popup_closed_vid: int | None = None

        self._setup_ui()
        self._apply_theme()
        self._populate_tabs()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        dark = self._dark
        ico_col = TEXT_PRI if dark else "#1A2B28"
        red_col = "#EF4444" if dark else "#DC2626"

        # ── Search Input & Top Sort Button ─────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter sheets by name...")
        self.search_input.setFixedHeight(38)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.installEventFilter(self)
        search_row.addWidget(self.search_input, 1)

        self.btn_top_sort = QPushButton("Sort Sheets ▾")
        self.btn_top_sort.setIcon(svg_icon(SVG_SORT, color=ico_col, size=15))
        self.btn_top_sort.setIconSize(QSize(15, 15))
        self.btn_top_sort.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_top_sort.setFixedHeight(38)
        self.btn_top_sort.setToolTip("Sort sheets (A-Z, Z-A, page count, unsaved)")
        self.btn_top_sort.clicked.connect(lambda: self._show_sort_menu(self.btn_top_sort))
        search_row.addWidget(self.btn_top_sort)

        root.addLayout(search_row)

        # ── Body: List on left, Action buttons on right ────────────────────
        body_h = QHBoxLayout()
        body_h.setSpacing(12)

        self.list_widget = _ManageTabsListWidget(self, self)
        self._delegate = _ManageTabItemDelegate(self, self.list_widget)
        self.list_widget.setItemDelegate(self._delegate)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.itemSelectionChanged.connect(self._update_button_states)
        body_h.addWidget(self.list_widget, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        btn_col.setAlignment(Qt.AlignmentFlag.AlignTop)

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

        btn_col.addSpacing(4)

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

        btn_col.addSpacing(4)

        self.btn_sort_toggle = _make_action_btn("Sort (A → Z)", SVG_SORT)
        self.btn_sort_toggle.setToolTip("Sort all sheets alphabetically (A → Z / Z → A toggle)")
        self.btn_sort_toggle.clicked.connect(self._toggle_sort_name)
        btn_col.addWidget(self.btn_sort_toggle)

        self.btn_sort_menu = _make_action_btn("Sort Options ▾", SVG_SORT)
        self.btn_sort_menu.setToolTip("Additional sorting options: Page count, unsaved first, reverse")
        self.btn_sort_menu.clicked.connect(lambda: self._show_sort_menu(self.btn_sort_menu))
        btn_col.addWidget(self.btn_sort_menu)

        btn_col.addSpacing(4)

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
            "Tip: Click any Page 1 thumbnail to expand (click again or outside to close). Double-click or press Enter to jump."
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

    def _set_sort_toggle_state(self, asc_next: bool) -> None:
        self._sort_asc_next = asc_next
        if asc_next:
            self.btn_sort_toggle.setText("Sort (A → Z)")
            self.btn_sort_toggle.setToolTip("Sort all sheets alphabetically A to Z (click to toggle)")
        else:
            self.btn_sort_toggle.setText("Sort (Z → A)")
            self.btn_sort_toggle.setToolTip("Sort all sheets alphabetically Z to A (click to toggle)")

    def _toggle_sort_name(self) -> None:
        if getattr(self, "_sort_asc_next", True):
            self._sort_tabs("name_asc")
            self._set_sort_toggle_state(asc_next=False)
        else:
            self._sort_tabs("name_desc")
            self._set_sort_toggle_state(asc_next=True)

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
            f"QListWidget::item {{ padding: 0px; margin: 2px 2px; border-radius: 5px; min-height: 56px; }}"
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

    def closeEvent(self, event):
        if self._preview_popup is not None and isValid(self._preview_popup):
            self._preview_popup.close()
            self._preview_popup = None
        super().closeEvent(event)

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