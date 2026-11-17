# app/window_recent_menu.py
"""PDF_Editor – Recent files context menu, scrollable history popup, and history actions mixin using SVG icons."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProxyStyle,
    QStyle,
    QWidgetAction,
)
from shiboken6 import isValid

from app.constants import ACCENT, TEXT_SEC, _LQ
from app.i18n import get_recent_files, t
from app.utils import (
    SVG_CHECK,
    SVG_FILE_ALT,
    SVG_FILE_PDF,
    SVG_HISTORY,
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


class _HistoryListKeyFilter(QObject):
    """Handle Escape, Ctrl+H, and Enter/Return inside the scrollable history list."""

    def __init__(self, menu: QMenu, list_widget: QListWidget, activate_cb, parent=None) -> None:
        super().__init__(parent)
        self._menu = menu
        self._list = list_widget
        self._activate_cb = activate_cb

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key.Key_Escape or (
                (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_H
            ):
                self._menu.close()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._list.currentItem()
                if item is not None:
                    self._activate_cb(item)
                return True
        return super().eventFilter(obj, event)


class _PersistentHistoryMenu(QMenu):
    """Context menu for recent file history that stays open when selecting items
    until the user clicks outside the menu or presses Escape/Ctrl+H."""

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        if self.rect().contains(pos):
            action = self.actionAt(pos)
            if e.button() == Qt.MouseButton.LeftButton:
                if (
                    action is not None
                    and action.isEnabled()
                    and not action.isSeparator()
                    and action.menu() is None
                    and action.property("keep_menu_open")
                ):
                    action.trigger()
                    e.accept()
                    return
            if (
                action is None
                or not action.isEnabled()
                or action.isSeparator()
                or action.property("keep_menu_open")
            ):
                e.accept()
                return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if (e.modifiers() & Qt.KeyboardModifier.ControlModifier) and e.key() == Qt.Key.Key_H:
            self.close()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            action = self.activeAction()
            if (
                action is not None
                and action.isEnabled()
                and not action.isSeparator()
                and action.menu() is None
                and action.property("keep_menu_open")
            ):
                action.trigger()
                e.accept()
                return
        super().keyPressEvent(e)


class WindowRecentMenuMixin(_Base):
    """Mixin encapsulating recent files menu display, selection, and history clearing."""

    if TYPE_CHECKING:
        _dark_mode: bool
        _viewers: list[Any]
        _content_area: Any

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
        menu.setMaximumWidth(400)

        hdr_text = t("viewer.recent")
        if hdr_text == "viewer.recent":
            hdr_text = "Recent files"
        hdr_action = menu.addAction(svg_icon(SVG_HISTORY, color=ACCENT, size=15), hdr_text)
        hdr_action.setEnabled(False)
        menu.addSeparator()

        if not recents:
            empty_text = t("recent.empty")
            if empty_text == "recent.empty":
                empty_text = "No recent files"
            empty_action = menu.addAction(empty_text)
            empty_action.setEnabled(False)
        else:
            open_paths: set[str] = set()
            for v in getattr(self, "_viewers", []):
                orig_p = getattr(v, "_original_doc_path", "")
                curr_p = v.current_path() if hasattr(v, "current_path") else ""
                if orig_p:
                    open_paths.add(os.path.normcase(os.path.normpath(os.path.abspath(orig_p))))
                if curr_p:
                    open_paths.add(os.path.normcase(os.path.normpath(os.path.abspath(curr_p))))

            name_counts: dict[str, int] = {}
            for rp in recents:
                bn = os.path.basename(rp)
                name_counts[bn] = name_counts.get(bn, 0) + 1

            list_w = QListWidget(menu)
            list_w.setObjectName("recent_history_list")
            list_w.setMouseTracking(True)
            list_w.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            list_w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            list_w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            list_w.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            list_w.setIconSize(QSize(15, 15))
            list_w.setUniformItemSizes(True)

            fg = "#F0F0F0" if dark else "#1A2B28"
            hover_bg = "#333333" if dark else "#E0E7FF"
            sb_handle = "#555555" if dark else "#CBD5E1"
            sb_hover = "#777777" if dark else "#94A3B8"
            list_w.setStyleSheet(
                f"QListWidget#recent_history_list {{"
                f"  background: transparent; border: none; outline: none;"
                f"  color: {fg}; font-size: 10.5pt; padding: 0px;"
                f"}}"
                f"QListWidget#recent_history_list::item {{"
                f"  padding: 6px 10px; margin: 1px 4px; border-radius: 4px;"
                f"}}"
                f"QListWidget#recent_history_list::item:hover {{"
                f"  background: {hover_bg}; color: {fg};"
                f"}}"
                f"QListWidget#recent_history_list::item:selected {{"
                f"  background: {ACCENT}; color: #FFFFFF;"
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

            row_h = 32
            for rp in recents:
                norm_rp = os.path.normpath(os.path.abspath(rp))
                fname = os.path.basename(norm_rp)
                if len(fname) > 42:
                    disp_name = fname[:22] + "..." + fname[-17:]
                else:
                    disp_name = fname

                if name_counts.get(fname, 0) > 1:
                    parent_dir = os.path.basename(os.path.dirname(norm_rp))
                    if parent_dir:
                        disp_name = f"{disp_name}  ({parent_dir})"

                is_open = os.path.normcase(norm_rp) in open_paths
                if is_open:
                    ico = svg_icon(SVG_CHECK, color=opened_color, size=15)
                elif norm_rp.lower().endswith(".pdf"):
                    ico = svg_icon(SVG_FILE_PDF, color=icon_color, size=15)
                else:
                    ico = svg_icon(SVG_FILE_ALT, color=icon_color, size=15)

                item = QListWidgetItem(ico, disp_name)
                item.setData(Qt.ItemDataRole.UserRole, norm_rp)
                item.setToolTip(norm_rp)
                item.setSizeHint(QSize(340, row_h))
                list_w.addItem(item)

            visible_rows = min(len(recents), 10)
            list_h = visible_rows * row_h + 6
            list_w.setFixedSize(368, list_h)

            def _on_item_clicked(it: QListWidgetItem):
                if not it or not (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                    return
                rp_val = it.data(Qt.ItemDataRole.UserRole)
                if rp_val:
                    self._open_recent_from_history_item(rp_val, it, menu)

            list_w.itemClicked.connect(_on_item_clicked)
            key_flt = _HistoryListKeyFilter(menu, list_w, _on_item_clicked, list_w)
            list_w.installEventFilter(key_flt)

            wa = QWidgetAction(menu)
            wa.setDefaultWidget(list_w)
            menu.addAction(wa)
            list_w.setFocus()

        menu.adjustSize()
        menu_sz = menu.sizeHint()
        cursor_pos = QCursor.pos()
        if self.frameGeometry().contains(cursor_pos):
            popup_pos = QPoint(cursor_pos)
        else:
            area = getattr(self, "_content_area", self)
            popup_pos = area.mapToGlobal(area.rect().center())

        screen = QApplication.screenAt(popup_pos) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_x = avail.right() - menu_sz.width() - 4
            max_y = avail.bottom() - menu_sz.height() - 4
            popup_pos.setX(max(avail.left() + 4, min(popup_pos.x(), max_x)))
            popup_pos.setY(max(avail.top() + 4, min(popup_pos.y(), max_y)))

        menu.exec(popup_pos)

    def _open_recent_from_history_item(self, path: str, item: QListWidgetItem, menu: QMenu):
        lw = item.listWidget() if item is not None else None
        if not os.path.isfile(path):
            if lw is not None and isValid(lw):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
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
            if lw is not None and isValid(lw):
                item.setIcon(svg_icon(SVG_CHECK, color=opened_color, size=15))
            self._set_status(f"Opened {os.path.basename(path)}")

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