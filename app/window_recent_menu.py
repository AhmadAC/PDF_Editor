# app/window_recent_menu.py
"""PDF_Editor – Searchable recent files history popup menu with real-time multi-token filtering, persistent multi-tab opening, and SVG icons."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from shiboken6 import isValid

from app.constants import ACCENT, TEXT_PRI, TEXT_SEC, _LQ
from app.i18n import get_recent_files, t
from app.utils import (
    SVG_CHECK,
    SVG_CLOSE,
    SVG_COPY,
    SVG_FILE_ALT,
    SVG_FILE_PDF,
    SVG_FOLDER,
    SVG_SEARCH,
    SVG_TRASH,
    reveal_file,
    success_color,
    svg_icon,
)

if TYPE_CHECKING:
    _Base = QMainWindow
else:
    _Base = object


class _HistoryMenuStyle(QProxyStyle):
    """Enforce single-column scrollable QMenu across all platforms."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class _HistorySearchFilter(QObject):
    """Event filter routing navigation and activation keys in the search input."""

    def __init__(
        self,
        search_input: QLineEdit,
        list_widget: QListWidget,
        menu: QMenu,
        on_return_cb,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.search_input = search_input
        self.list_widget = list_widget
        self.menu = menu
        self.on_return_cb = on_return_cb

    def eventFilter(self, watched, event):
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()

            if key == Qt.Key.Key_Down:
                curr = self.list_widget.currentRow()
                for next_r in range(curr + 1, self.list_widget.count()):
                    it = self.list_widget.item(next_r)
                    if not it.isHidden() and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                        self.list_widget.setCurrentRow(next_r)
                        return True
                return True

            if key == Qt.Key.Key_Up:
                curr = self.list_widget.currentRow()
                for prev_r in range(curr - 1, -1, -1):
                    it = self.list_widget.item(prev_r)
                    if not it.isHidden() and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                        self.list_widget.setCurrentRow(prev_r)
                        return True
                return True

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.on_return_cb()
                return True

            if key == Qt.Key.Key_Escape or (
                (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_H
            ):
                if self.menu is not None and isValid(self.menu):
                    self.menu.close()
                return True

        return super().eventFilter(watched, event)


class _PersistentHistoryMenu(QMenu):
    """Context menu for recent file history that stays open when selecting items,
    allowing multiple files to be opened in succession in tabs, and forwards
    printable keypresses directly into the search bar."""

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.pos())
        if action is not None and action.isEnabled():
            if isinstance(action, QWidgetAction):
                super().mouseReleaseEvent(event)
                return

            action_text = action.text().lower()
            if "clear" in action_text or "close" in action_text:
                super().mouseReleaseEvent(event)
                return

            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        focus_w = QApplication.focusWidget()

        if isinstance(focus_w, QLineEdit) and self.isAncestorOf(focus_w):
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                super().keyPressEvent(event)
                return
            elif event.key() == Qt.Key.Key_Escape:
                self.close()
                event.accept()
                return
            focus_w.event(event)
            event.accept()
            return

        search_edit = self.findChild(QLineEdit)
        if (
            search_edit is not None
            and event.text()
            and event.text().isprintable()
            and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
        ):
            search_edit.setFocus()
            search_edit.event(event)
            event.accept()
            return

        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and event.key() == Qt.Key.Key_H:
            self.close()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return

        super().keyPressEvent(event)


class WindowRecentMenuMixin(_Base):
    """Mixin encapsulating recent files menu display, real-time search, and history management."""

    if TYPE_CHECKING:
        _dark_mode: bool
        _viewers: list[Any]
        _content_area: Any
        _open_pdf_btn: QPushButton

        def _set_status(self, msg: str) -> None: ...
        def _load_and_track(self, path: str) -> Any: ...

    def _show_recent_files_menu(self):
        recents = get_recent_files()
        dark = getattr(self, "_dark_mode", True)
        icon_color = TEXT_SEC if dark else _LQ
        opened_color = success_color(dark)

        menu = _PersistentHistoryMenu(self)
        style = _HistoryMenuStyle(menu.style())
        style.setParent(menu)
        menu.setStyle(style)
        menu.setToolTipsVisible(True)

        bg_card = "#262626" if dark else "#FFFFFF"
        bg_inp = "#1E1E1E" if dark else "#F8FAFC"
        brd = "#444444" if dark else "#CBD5E1"
        fg = "#F0F0F0" if dark else "#1A2B28"
        sec_fg = "#9CA3AF" if dark else "#6B7280"
        hover_bg = "#333333" if dark else "#E0E7FF"
        sb_handle = "#555555" if dark else "#CBD5E1"
        sb_hover = "#777777" if dark else "#94A3B8"

        menu.setStyleSheet(
            f"QMenu {{ background: {bg_card}; color: {fg}; border: 1px solid {brd}; border-radius: 8px; padding: 4px; menu-scrollable: 1; }}"
            f"QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: 4px; margin: 2px 4px; background: transparent; color: {fg}; }}"
            f"QMenu::item:selected {{ background: {ACCENT}; color: #FFFFFF; }}"
            f"QMenu::separator {{ height: 1px; background: {brd}; margin: 4px 6px; }}"
        )

        container = QWidget(menu)
        container.setObjectName("recent_history_container")
        container_lay = QVBoxLayout(container)
        container_lay.setContentsMargins(6, 6, 6, 6)
        container_lay.setSpacing(6)
        container.setStyleSheet(f"background-color: {bg_card}; border: none;")

        # Search Bar with "Filter history..." placeholder
        search_input = QLineEdit(container)
        search_input.setObjectName("recent_search_input")
        search_input.setFixedHeight(34)
        search_input.setPlaceholderText("Filter history...")
        search_input.setClearButtonEnabled(True)
        search_input.addAction(
            svg_icon(SVG_SEARCH, color=sec_fg, size=15),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        search_input.setStyleSheet(
            f"QLineEdit#recent_search_input {{"
            f"  background: {bg_inp};"
            f"  border: 1.5px solid {ACCENT};"
            f"  border-radius: 6px;"
            f"  color: {fg};"
            f"  padding: 4px 8px 4px 6px;"
            f"  font-size: 10pt;"
            f"}}"
            f"QLineEdit#recent_search_input:focus {{"
            f"  border: 1.5px solid {ACCENT};"
            f"}}"
        )
        container_lay.addWidget(search_input)

        # History List Widget
        list_w = QListWidget(container)
        list_w.setObjectName("recent_history_list")
        list_w.setMouseTracking(True)
        list_w.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        list_w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        list_w.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        list_w.setIconSize(QSize(15, 15))
        list_w.setUniformItemSizes(True)
        list_w.setStyleSheet(
            f"QListWidget#recent_history_list {{"
            f"  background: transparent; border: none; outline: none;"
            f"  color: {fg}; font-size: 10pt; padding: 0px;"
            f"}}"
            f"QListWidget#recent_history_list::item {{"
            f"  padding: 6px 8px; margin: 1px 2px; border-radius: 4px;"
            f"}}"
            f"QListWidget#recent_history_list::item:hover {{"
            f"  background: {hover_bg}; color: {fg};"
            f"}}"
            f"QListWidget#recent_history_list::item:selected {{"
            f"  background: {ACCENT}; color: #FFFFFF;"
            f"}}"
            f"QListWidget#recent_history_list::item:disabled {{"
            f"  color: {sec_fg};"
            f"}}"
            f"QListWidget#recent_history_list QScrollBar:vertical {{"
            f"  width: 6px; background: transparent; margin: 2px 0;"
            f"}}"
            f"QListWidget#recent_history_list QScrollBar::handle:vertical {{"
            f"  background: {sb_handle}; border-radius: 3px; min-height: 24px;"
            f"}}"
            f"QListWidget#recent_history_list QScrollBar::handle:vertical:hover {{"
            f"  background: {sb_hover};"
            f"}}"
            f"QListWidget#recent_history_list QScrollBar::add-line:vertical,"
            f"QListWidget#recent_history_list QScrollBar::sub-line:vertical {{"
            f"  height: 0px;"
            f"}}"
        )

        open_paths: set[str] = set()
        for v in getattr(self, "_viewers", []):
            orig_p = getattr(v, "_original_doc_path", "")
            curr_p = v.current_path() if hasattr(v, "current_path") else ""
            if orig_p:
                open_paths.add(os.path.normcase(os.path.normpath(os.path.abspath(orig_p))))
            if curr_p:
                open_paths.add(os.path.normcase(os.path.normpath(os.path.abspath(curr_p))))

        row_h = 32
        fm = list_w.fontMetrics()
        max_item_w = 420

        for i, raw_entry in enumerate(recents):
            norm_rp = os.path.normpath(os.path.abspath(raw_entry))
            norm_slash = norm_rp.replace("\\", "/")
            dirname = os.path.basename(os.path.dirname(norm_slash))
            filename = os.path.basename(norm_slash)
            display_name = f"{i + 1}. {dirname}/{filename}" if dirname else f"{i + 1}. {filename}"

            is_open = os.path.normcase(norm_rp) in open_paths

            if is_open:
                ico = svg_icon(SVG_CHECK, color=opened_color, size=15)
            elif norm_rp.lower().endswith(".pdf"):
                ico = svg_icon(SVG_FILE_PDF, color=icon_color, size=15)
            else:
                ico = svg_icon(SVG_FILE_ALT, color=icon_color, size=15)

            item = QListWidgetItem(ico, display_name)
            item.setData(Qt.ItemDataRole.UserRole, norm_rp)
            item.setToolTip(norm_rp)
            item.setSizeHint(QSize(0, row_h))

            if is_open:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

            list_w.addItem(item)

            calc_w = fm.horizontalAdvance(display_name) + 64
            if calc_w > max_item_w:
                max_item_w = calc_w

        max_item_w = min(max_item_w, 750)
        list_w.setMinimumWidth(max_item_w)
        container.setMinimumWidth(max_item_w + 14)

        no_matches_item = QListWidgetItem("No matching files in history")
        no_matches_item.setFlags(Qt.ItemFlag.NoItemFlags)
        no_matches_item.setForeground(QColor(sec_fg))
        no_matches_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        no_matches_item.setSizeHint(QSize(max_item_w, row_h))
        no_matches_item.setHidden(bool(recents))
        list_w.addItem(no_matches_item)

        visible_rows = min(max(len(recents), 1), 12)
        list_h = visible_rows * row_h + 8
        list_w.setFixedHeight(list_h)
        container_lay.addWidget(list_w)

        def _on_item_clicked(it: QListWidgetItem):
            if not it or not (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                return
            file_entry = str(it.data(Qt.ItemDataRole.UserRole) or "")
            if not file_entry:
                return
            self._open_recent_from_history_item(file_entry, it, menu)

        list_w.itemClicked.connect(_on_item_clicked)
        list_w.itemActivated.connect(_on_item_clicked)

        def filter_history(text: str):
            query = text.strip().lower()
            tokens = query.split()
            first_visible = None

            for idx in range(list_w.count()):
                item = list_w.item(idx)
                if item == no_matches_item:
                    continue
                raw_path = str(item.data(Qt.ItemDataRole.UserRole) or "").lower()
                disp_text = item.text().lower()
                clean_disp = re.sub(r"^\d+\.\s*", "", disp_text)

                if not tokens:
                    matches = True
                else:
                    matches = all(t in clean_disp or t in raw_path or t in disp_text for t in tokens)

                item.setHidden(not matches)
                if matches and first_visible is None and (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                    first_visible = item

            if first_visible:
                list_w.setCurrentItem(first_visible)
            else:
                list_w.setCurrentRow(-1)

        search_input.textChanged.connect(filter_history)

        def on_search_return():
            item = list_w.currentItem()
            if not item or item.isHidden() or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                for idx in range(list_w.count()):
                    it = list_w.item(idx)
                    if not it.isHidden() and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                        item = it
                        break
            if item and (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                _on_item_clicked(item)

        search_filter = _HistorySearchFilter(search_input, list_w, menu, on_search_return, container)
        search_input.installEventFilter(search_filter)

        list_w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _on_item_context_menu(pt: QPoint):
            it = list_w.itemAt(pt)
            if it and it != no_matches_item:
                file_p = str(it.data(Qt.ItemDataRole.UserRole) or "")
                if file_p:
                    self._show_history_item_context_menu(list_w.mapToGlobal(pt), file_p, it, menu)

        list_w.customContextMenuRequested.connect(_on_item_context_menu)

        widget_action = QWidgetAction(menu)
        widget_action.setDefaultWidget(container)
        menu.addAction(widget_action)

        menu.addSeparator()

        act_clear = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Clear History")

        def _do_clear():
            reply = QMessageBox.question(
                self,
                t("msg.confirm"),
                "Are you sure you want to clear your recent files history?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._clear_recent()
                for idx in range(list_w.count()):
                    it = list_w.item(idx)
                    if it != no_matches_item:
                        it.setHidden(True)
                no_matches_item.setText("No recent files")
                no_matches_item.setHidden(False)
                search_input.clear()

        act_clear.triggered.connect(_do_clear)

        act_close = menu.addAction(svg_icon(SVG_CLOSE, color=icon_color, size=15), "Close")
        act_close.triggered.connect(menu.close)

        menu.adjustSize()
        menu_sz = menu.sizeHint()

        cursor_pos = QCursor.pos()
        if hasattr(self, "_open_pdf_btn") and self._open_pdf_btn.underMouse():
            popup_pos = self._open_pdf_btn.mapToGlobal(QPoint(0, self._open_pdf_btn.height() + 2))
        elif self.frameGeometry().contains(cursor_pos):
            popup_pos = QPoint(cursor_pos)
        else:
            area = getattr(self, "_content_area", self)
            popup_pos = area.mapToGlobal(area.rect().center())

        screen = QApplication.screenAt(popup_pos) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_x = avail.right() - menu_sz.width() - 6
            max_y = avail.bottom() - menu_sz.height() - 6
            popup_pos.setX(max(avail.left() + 6, min(popup_pos.x(), max_x)))
            popup_pos.setY(max(avail.top() + 6, min(popup_pos.y(), max_y)))

        QTimer.singleShot(25, search_input.setFocus)
        menu.exec(popup_pos)

    def _show_history_item_context_menu(
        self,
        global_pos: QPoint,
        path: str,
        item: QListWidgetItem,
        parent_menu: QMenu,
    ):
        dark = getattr(self, "_dark_mode", True)
        icon_color = TEXT_PRI if dark else _LQ

        item_menu = QMenu(self)
        item_menu.setStyle(parent_menu.style())

        act_open = item_menu.addAction(
            svg_icon(SVG_FILE_PDF, color=icon_color, size=15),
            "Open in Tab",
        )
        act_open.triggered.connect(lambda: self._open_recent_from_history_item(path, item, parent_menu))

        item_menu.addSeparator()

        if sys.platform == "win32":
            reveal_label = "Reveal in File Explorer"
        elif sys.platform == "darwin":
            reveal_label = "Reveal in Finder"
        elif shutil.which("dolphin"):
            reveal_label = "Reveal in Dolphin"
        else:
            reveal_label = "Reveal in File Manager"

        act_reveal = item_menu.addAction(
            svg_icon(SVG_FOLDER, color=icon_color, size=15),
            reveal_label,
        )
        act_reveal.triggered.connect(lambda: reveal_file(path))

        act_copy = item_menu.addAction(
            svg_icon(SVG_COPY, color=icon_color, size=15),
            "Copy Full Path",
        )
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))

        item_menu.addSeparator()

        act_remove = item_menu.addAction(
            svg_icon(SVG_TRASH, color="#EF4444", size=15),
            "Remove from Recent",
        )

        def _remove_this():
            self._remove_single_recent(path)
            item.setHidden(True)
            self._set_status(f"Removed from recent: {os.path.basename(path)}")

        act_remove.triggered.connect(_remove_this)
        item_menu.exec(global_pos)

    def _remove_single_recent(self, path: str) -> None:
        from app.i18n import _update_config
        normed = os.path.normcase(os.path.normpath(os.path.abspath(path)))

        def _mutate(cfg: dict) -> None:
            recents = cfg.get("recent_files", [])
            if not isinstance(recents, list):
                recents = []
            cfg["recent_files"] = [
                r for r in recents
                if isinstance(r, str) and os.path.normcase(os.path.normpath(os.path.abspath(r))) != normed
            ]

        try:
            _update_config(_mutate)
        except Exception:
            pass

        for v in getattr(self, "_viewers", []):
            ref = getattr(v, "_refresh_recents", None)
            if callable(ref):
                with contextlib.suppress(Exception):
                    ref()

    def _open_recent_from_history_item(
        self,
        path: str,
        item: QListWidgetItem | None = None,
        menu: QMenu | None = None,
    ):
        norm_p = os.path.normpath(path)
        if not os.path.isfile(norm_p):
            if os.path.isfile(norm_p + ".pdf"):
                norm_p = norm_p + ".pdf"
        if not os.path.isfile(norm_p):
            if item is not None and isValid(item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._set_status(f"File not found: {path}")
            QMessageBox.warning(
                self,
                t("msg.warning"),
                f"The file '{os.path.basename(path)}' could not be found at:\n{path}",
            )
            return

        needs_dialog = False
        if norm_p.lower().endswith(".pdf"):
            try:
                import fitz
                with fitz.open(norm_p) as _probe:
                    needs_dialog = bool(_probe.needs_pass)
            except Exception:
                needs_dialog = True
        if needs_dialog and menu is not None and isValid(menu):
            menu.close()

        viewer = self._load_and_track(norm_p)
        if viewer is not None:
            opened_color = success_color(getattr(self, "_dark_mode", True))
            if item is not None and isValid(item):
                item.setIcon(svg_icon(SVG_CHECK, color=opened_color, size=15))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._set_status(f"Opened {os.path.basename(norm_p)}")

    def _open_recent_from_history_menu(self, path: str, action: QAction, menu: QMenu):
        if not os.path.isfile(path):
            if isValid(action):
                action.setEnabled(False)
            self._set_status(f"File not found: {path}")
            return

        needs_dialog = False
        if path.lower().endswith(".pdf"):
            try:
                import fitz
                with fitz.open(path) as _probe:
                    needs_dialog = bool(_probe.needs_pass)
            except Exception:
                needs_dialog = True
        if needs_dialog and isValid(menu):
            menu.close()

        viewer = self._load_and_track(path)
        if viewer is not None:
            opened_color = success_color(getattr(self, "_dark_mode", True))
            if isValid(action):
                action.setIcon(svg_icon(SVG_CHECK, color=opened_color, size=15))
            self._set_status(f"Opened {os.path.basename(path)}")

    def _clear_recent(self):
        from app.i18n import _update_config
        try:
            _update_config(lambda cfg: cfg.__setitem__("recent_files", []))
        except Exception:
            pass
        for v in getattr(self, "_viewers", []):
            refresh = getattr(v, "_refresh_recents", None)
            if callable(refresh):
                with contextlib.suppress(Exception):
                    refresh()
        self._set_status("Recent files history cleared")