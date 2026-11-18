# app/command_palette.py
"""PDF_Editor – Command Palette dialog: searchable quick-action launcher accessible via Alt+P."""

from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import ACCENT, BG_CARD, BG_INPUT, BORDER, TEXT_PRI, TEXT_SEC
from app.utils import is_dark


class _CommandItemWidget(QWidget):
    """Custom row widget displaying icon, title, category badge, and keyboard shortcut."""

    def __init__(
        self,
        name: str,
        category: str = "",
        shortcut: str = "",
        icon: QIcon | None = None,
        dark: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        fg = TEXT_PRI if dark else "#1A2B28"
        sec = TEXT_SEC if dark else "#6B7280"

        if icon and not icon.isNull():
            ico_lbl = QLabel()
            ico_lbl.setPixmap(icon.pixmap(18, 18))
            ico_lbl.setFixedSize(20, 20)
            layout.addWidget(ico_lbl)

        title_lbl = QLabel(name)
        title_lbl.setStyleSheet(f"color: {fg}; font-size: 10.5pt; font-weight: 500; background: transparent;")
        layout.addWidget(title_lbl, 1)

        if category:
            cat_lbl = QLabel(category.upper())
            cat_bg = "#264F78" if dark else "#D6E8FA"
            cat_fg = "#93C5FD" if dark else ACCENT
            cat_lbl.setStyleSheet(
                f"background: {cat_bg}; color: {cat_fg}; border-radius: 4px; "
                f"padding: 2px 6px; font-size: 8pt; font-weight: 700; letter-spacing: 0.5px;"
            )
            layout.addWidget(cat_lbl)

        if shortcut:
            sc_lbl = QLabel(shortcut)
            sc_lbl.setStyleSheet(
                f"color: {sec}; font-size: 9pt; font-family: monospace; "
                f"background: rgba(128, 128, 128, 0.15); border-radius: 3px; padding: 2px 5px;"
            )
            layout.addWidget(sc_lbl)


class CommandPalette(QDialog):
    """Searchable command palette dialog to find and execute application commands.
    Triggered via Alt+P or from the menu. Supports instant fuzzy/token filtering.
    """

    def __init__(self, commands: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.setMinimumSize(620, 440)
        self.resize(680, 480)

        self._dark = is_dark()
        self._commands = commands
        self._filtered_indices: list[int] = []

        self._setup_ui()
        self._apply_theme()
        self._populate_list("")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type a command (e.g. 'selection pane', 'edit', 'zoom', 'crop')...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.installEventFilter(self)
        layout.addWidget(self.search_input)

        self.results_list = QListWidget()
        self.results_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results_list.setUniformItemSizes(True)
        self.results_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.results_list, 1)

        hint_lbl = QLabel("Press Enter to execute, Up/Down to navigate, Escape to close.")
        hint_lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 8.5pt; padding: 2px 4px;")
        layout.addWidget(hint_lbl)

        self.search_input.setFocus()

    def _apply_theme(self):
        dark = self._dark
        bg_card = BG_CARD if dark else "#FFFFFF"
        bg_inp = BG_INPUT if dark else "#F8FAFC"
        brd = BORDER if dark else "#CBD5E1"
        fg = TEXT_PRI if dark else "#1A2B28"

        self.setStyleSheet(
            f"QDialog {{ background-color: {bg_card}; color: {fg}; border-radius: 8px; }}"
            f"QLineEdit {{ background-color: {bg_inp}; border: 1.5px solid {ACCENT}; "
            f"border-radius: 6px; padding: 8px 12px; font-size: 11pt; color: {fg}; }}"
            f"QListWidget {{ background-color: {bg_inp}; border: 1px solid {brd}; "
            f"border-radius: 6px; outline: none; padding: 4px; }}"
            f"QListWidget::item {{ border-radius: 5px; margin: 1px 2px; }}"
            f"QListWidget::item:selected {{ background-color: {'#264F78' if dark else '#D6E8FA'}; border: 1px solid {ACCENT}; }}"
            f"QListWidget::item:hover:!selected {{ background-color: {'#333333' if dark else '#F1F5F9'}; }}"
        )

    def _populate_list(self, query: str):
        self.results_list.clear()
        self._filtered_indices.clear()

        q_tokens = query.lower().strip().split()

        for idx, cmd in enumerate(self._commands):
            name = str(cmd.get("name", ""))
            category = str(cmd.get("category", ""))
            shortcut = str(cmd.get("shortcut", ""))
            full_searchable = f"{name} {category} {shortcut}".lower()

            if q_tokens:
                if not all(token in full_searchable for token in q_tokens):
                    continue

            self._filtered_indices.append(idx)
            item = QListWidgetItem(self.results_list)
            item.setSizeHint(QSize(0, 38))
            item.setData(Qt.ItemDataRole.UserRole, idx)

            row_widget = _CommandItemWidget(
                name=name,
                category=category,
                shortcut=shortcut,
                icon=cmd.get("icon"),
                dark=self._dark,
            )
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, row_widget)

        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _on_search_changed(self, text: str):
        self._populate_list(text)

    def _on_item_clicked(self, item: QListWidgetItem):
        self._execute_item(item)

    def _execute_item(self, item: QListWidgetItem | None):
        if item is None:
            return
        cmd_idx = item.data(Qt.ItemDataRole.UserRole)
        if cmd_idx is None or cmd_idx < 0 or cmd_idx >= len(self._commands):
            return

        cmd = self._commands[cmd_idx]
        action = cmd.get("action")
        name = cmd.get("name", "Unknown")

        print(f"[DEBUG CommandPalette] Executing command: '{name}'", flush=True)
        self.accept()

        try:
            if isinstance(action, QAction):
                action.trigger()
            elif callable(action):
                action()
        except Exception as exc:
            err = traceback.format_exc()
            print(f"[DEBUG CommandPalette] Error executing '{name}': {exc}\n{err}", file=sys.stderr, flush=True)
            QMessageBox.critical(self.parentWidget(), "Command Error", f"Failed to execute '{name}':\n{exc}")

    def eventFilter(self, watched, event):
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                cur = self.results_list.currentRow()
                if cur < self.results_list.count() - 1:
                    self.results_list.setCurrentRow(cur + 1)
                return True
            if key == Qt.Key.Key_Up:
                cur = self.results_list.currentRow()
                if cur > 0:
                    self.results_list.setCurrentRow(cur - 1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._execute_item(self.results_list.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)

