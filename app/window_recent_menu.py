# app/window_recent_menu.py
"""PDF_Editor – Recent files context menu, persistent popups, and history actions mixin."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QStyle,
)
import qtawesome as qta
from shiboken6 import isValid

from app.constants import ACCENT, TEXT_SEC, _LQ
from app.i18n import get_recent_files, t
from app.utils import reveal_file, success_color

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
        menu.setMaximumWidth(380)

        hdr_text = t("viewer.recent")
        if hdr_text == "viewer.recent":
            hdr_text = "Recent files"
        hdr_action = menu.addAction(qta.icon("fa5s.history", color=ACCENT), hdr_text)
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
                    open_paths.add(os.path.normcase(os.path.abspath(orig_p)))
                if curr_p:
                    open_paths.add(os.path.normcase(os.path.abspath(curr_p)))

            name_counts: dict[str, int] = {}
            for rp in recents:
                bn = os.path.basename(rp)
                name_counts[bn] = name_counts.get(bn, 0) + 1

            for rp in recents:
                fname = os.path.basename(rp)
                if len(fname) > 42:
                    disp_name = fname[:22] + "…" + fname[-17:]
                else:
                    disp_name = fname

                if name_counts.get(fname, 0) > 1:
                    parent_dir = os.path.basename(os.path.dirname(rp))
                    if parent_dir:
                        disp_name = f"{disp_name}  ({parent_dir})"

                norm_rp = os.path.normcase(os.path.abspath(rp))
                is_open = norm_rp in open_paths
                if is_open:
                    ico = qta.icon("fa5s.check", color=opened_color)
                elif rp.lower().endswith(".pdf"):
                    ico = qta.icon("fa5s.file-pdf", color=icon_color)
                else:
                    ico = qta.icon("fa5s.file-alt", color=icon_color)

                act = menu.addAction(ico, disp_name)
                act.setToolTip(rp)
                act.setProperty("keep_menu_open", True)
                act.triggered.connect(
                    lambda _checked=False, p=rp, a=act, m=menu:
                    self._open_recent_from_history_menu(p, a, m)
                )

        cursor_pos = QCursor.pos()
        if self.frameGeometry().contains(cursor_pos):
            popup_pos = cursor_pos
        else:
            area = getattr(self, "_content_area", self)
            popup_pos = area.mapToGlobal(area.rect().center())

        menu.exec(popup_pos)

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
                action.setIcon(qta.icon("fa5s.check", color=opened_color))
            self._set_status(f"✔ {os.path.basename(path)}")

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