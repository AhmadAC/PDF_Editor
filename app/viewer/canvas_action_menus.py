"""PDF_Editor – Context menus, note interaction, and comment deletion logic."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Callable

import fitz
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QProxyStyle, QStyle
import qtawesome as qta

from app.constants import ACCENT, TEXT_SEC
from app.i18n import t
from app.viewer.canvas_worker import _NOTE_ICON_SIZE

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


class _ScrollableMenuStyle(QProxyStyle):
    """Enforce single-column scrollable QMenu across all platforms."""
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class CanvasActionMenuBuilder:
    """Builds and executes context menus for signatures, comments, and text."""

    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas
        self._menu_style: _ScrollableMenuStyle | None = None

    def note_icon_at(self, pos: QPoint) -> tuple[int, int] | None:
        c = self.canvas
        z = c._zoom
        margin = 8
        for page_idx, entry in enumerate(c._entries):
            annots = getattr(entry, "annots", None)
            if not annots:
                continue
            if pos.y() < entry.y_off - margin or pos.y() > entry.y_off + entry.h + margin:
                continue
            x_off = c.page_x_offset(entry)
            for annot_idx, (rect, _txt) in enumerate(annots):
                px = x_off + int(round(rect.x0 * z))
                py = entry.y_off + int(round(rect.y0 * z))
                hit_r = QRect(px - margin, py - margin,
                              _NOTE_ICON_SIZE + margin * 2, _NOTE_ICON_SIZE + margin * 2)
                if hit_r.contains(pos):
                    return (page_idx, annot_idx)
        return None

    def _delete_comment_at(self, hit: tuple[int, int]) -> None:
        c = self.canvas
        page_idx, annot_idx = hit
        entry = c._entries[page_idx]
        annots = getattr(entry, "annots", None)
        if not annots or annot_idx >= len(annots):
            return

        rect, txt = annots[annot_idx]
        reply = QMessageBox.question(
            c, t("msg.confirm"),
            t("viewer.confirm_delete_comment"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if c._doc:
            backup_path = None
            if c._path and os.path.isfile(c._path):
                try:
                    src_dir = os.path.dirname(c._path) or "."
                    fd, backup_path = tempfile.mkstemp(
                        prefix=".PDF_Editor_backup_",
                        suffix=".pdf.bak",
                        dir=src_dir,
                    )
                    os.close(fd)
                    shutil.copy2(c._path, backup_path)
                except Exception:
                    if backup_path:
                        with contextlib.suppress(Exception):
                            os.unlink(backup_path)
                        backup_path = None
                    return

            target_annot = None
            try:
                page = c._doc[page_idx]
                for annot in page.annots() or []:
                    if annot.type[0] != _PDF_ANNOT_TEXT and annot.type[1] != "Text":
                        continue
                    content = annot.info.get("content", "") or ""
                    if content.strip() != txt.strip():
                        continue
                    ar = annot.rect
                    if abs(ar.x0 - rect.x0) < 1 and abs(ar.y0 - rect.y0) < 1:
                        target_annot = annot
                        break
                if target_annot is None:
                    for annot in page.annots() or []:
                        if annot.type[0] != _PDF_ANNOT_TEXT and annot.type[1] != "Text":
                            continue
                        content = annot.info.get("content", "") or ""
                        if content.strip() == txt.strip():
                            target_annot = annot
                            break
                if target_annot is not None:
                    page.delete_annot(target_annot)
                else:
                    if backup_path:
                        with contextlib.suppress(Exception):
                            os.unlink(backup_path)
                    QMessageBox.warning(c, t("msg.warning"), t("viewer.delete_no_match"))
                    return
            except Exception:
                if backup_path:
                    with contextlib.suppress(Exception):
                        os.unlink(backup_path)
                return

            if c._path:
                c._prepare_for_save()
                try:
                    c._doc.saveIncr()
                except Exception:
                    if backup_path:
                        with contextlib.suppress(Exception):
                            shutil.move(backup_path, c._path)
                        backup_path = None
                    new_doc = c._reopen_document()
                    if new_doc is not None:
                        c._schedule_visible()
                    return
            if backup_path:
                with contextlib.suppress(Exception):
                    os.unlink(backup_path)

        annots.pop(annot_idx)
        if c._open_note is not None:
            open_page, open_idx = c._open_note
            if open_page == page_idx:
                if open_idx == annot_idx:
                    c._open_note = None
                elif open_idx > annot_idx:
                    c._open_note = (open_page, open_idx - 1)
        c._schedule_visible()
        c.update()

    def show_context_menu(self, e, select_all_fn: Callable[[], None], is_sig_inside_fn: Callable[[QPoint], bool]) -> None:
        c = self.canvas
        pos = e.pos()

        if c._placing_signature:
            c.cancel_signature_placement()
            return

        sig = c._active_sig
        if sig is not None and is_sig_inside_fn(pos):
            menu = QMenu(c)
            self._menu_style = _ScrollableMenuStyle(menu.style())
            menu.setStyle(self._menu_style)
            menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
            act_apply = menu.addAction(qta.icon("fa5s.check", color=ACCENT), t("btn.apply"))
            act_apply.triggered.connect(c.commit_active_signature)
            act_del = menu.addAction(qta.icon("fa5s.trash-alt", color="#EF4444"), t("btn.delete"))
            act_del.triggered.connect(c.delete_active_signature)
            menu.exec(e.globalPos())
            return

        hit = self.note_icon_at(pos)
        if hit is not None:
            menu = QMenu(c)
            self._menu_style = _ScrollableMenuStyle(menu.style())
            menu.setStyle(self._menu_style)
            menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
            delete_action = menu.addAction(t("viewer.delete_comment"))
            action = menu.exec(e.globalPos())
            if action == delete_action:
                self._delete_comment_at(hit)
            return

        menu = QMenu(c)
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        if c._sel_text:
            act_copy = menu.addAction(
                qta.icon("fa5s.copy", color=TEXT_SEC),
                t("viewer.copy_chars", n=len(c._sel_text)),
            )
            act_copy.triggered.connect(lambda: QApplication.clipboard().setText(c._sel_text))
            menu.addSeparator()

        act_sel_all = menu.addAction(
            qta.icon("fa5s.object-group", color=TEXT_SEC),
            t("Select All Text", default="Select All\tCtrl+A"),
        )
        act_sel_all.triggered.connect(select_all_fn)
        menu.addSeparator()

        act_sig = menu.addAction(
            qta.icon("fa5s.signature", color=ACCENT),
            t("viewer.add_signature"),
        )
        act_sig.triggered.connect(lambda: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())