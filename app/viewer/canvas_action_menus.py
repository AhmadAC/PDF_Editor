# app/viewer/canvas_action_menus.py
"""PDF_Editor – Context menus, note interaction, comment deletion, and rich highlight/color tools with SVG icons."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Callable

import fitz
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QConicalGradient, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QStyle,
)

from app.constants import ACCENT, TEXT_SEC
from app.i18n import t
from app.utils import (
    SVG_CHECK,
    SVG_COPY,
    SVG_EDIT,
    SVG_GROUP,
    SVG_HIGHLIGHTER,
    SVG_PEN,
    SVG_PLUS_CIRCLE,
    SVG_SIGNATURE,
    SVG_TRASH,
    SVG_VECTOR_SQUARE,
    svg_icon,
)
from app.viewer.canvas_worker import _NOTE_ICON_SIZE

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)

PRESET_COLORS = [
    ("#FFFF00", "Yellow", (1.0, 1.0, 0.0)),
    ("#10B981", "Green", (0.06, 0.72, 0.50)),
    ("#EF4444", "Red", (0.94, 0.27, 0.27)),
    ("#3B82F6", "Blue", (0.23, 0.51, 0.96)),
    ("#F472B6", "Pink", (0.95, 0.45, 0.71)),
    ("#F97316", "Orange", (0.97, 0.45, 0.09)),
    ("#8B5CF6", "Purple", (0.55, 0.36, 0.96)),
]


def make_circular_color_icon(color_hex: str, size: int = 16) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor(color_hex))
    p.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pix)


def make_rainbow_wheel_icon(size: int = 16) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    grad = QConicalGradient(size / 2.0, size / 2.0, 0)
    grad.setColorAt(0.00, QColor("#EF4444"))
    grad.setColorAt(0.17, QColor("#F97316"))
    grad.setColorAt(0.33, QColor("#FBBF24"))
    grad.setColorAt(0.50, QColor("#10B981"))
    grad.setColorAt(0.67, QColor("#06B6D4"))
    grad.setColorAt(0.83, QColor("#3B82F6"))
    grad.setColorAt(1.00, QColor("#EF4444"))
    p.setBrush(grad)
    p.setPen(QPen(QColor(0, 0, 0, 80), 1.0))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pix)


class _ScrollableMenuStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class CanvasActionMenuBuilder:
    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas
        self._menu_style: _ScrollableMenuStyle | None = None
        self._current_hi_color: tuple[float, float, float] = (1.0, 1.0, 0.0)

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
                hit_r = QRect(px - margin, py - margin, _NOTE_ICON_SIZE + margin * 2, _NOTE_ICON_SIZE + margin * 2)
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
            c, t("msg.confirm"), t("viewer.confirm_delete_comment"),
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
                    fd, backup_path = tempfile.mkstemp(prefix=".PDF_Editor_backup_", suffix=".pdf.bak", dir=src_dir)
                    os.close(fd)
                    shutil.copy2(c._path, backup_path)
                except Exception:
                    if backup_path:
                        with contextlib.suppress(Exception):
                            os.unlink(backup_path)
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

    def _apply_highlight_to_selection(self, color_tuple: tuple[float, float, float]) -> None:
        c = self.canvas
        if not c._sel_rects or not c._doc or not c._path:
            return

        z = c._zoom or 1.0
        page_rects: dict[int, list[fitz.Rect]] = {}
        for sr in c._sel_rects:
            p_idx = c.page_at_y(sr.center().y())
            if 0 <= p_idx < len(c._entries):
                entry = c._entries[p_idx]
                x_off = c.page_x_offset(entry)
                pdf_r = fitz.Rect(
                    (sr.x() - x_off) / z,
                    (sr.y() - entry.y_off) / z,
                    (sr.right() - x_off) / z,
                    (sr.bottom() - entry.y_off) / z,
                )
                if not pdf_r.is_empty and pdf_r.width > 0.5 and pdf_r.height > 0.5:
                    page_rects.setdefault(p_idx, []).append(pdf_r)

        if not page_rects:
            return

        try:
            doc = fitz.open(c._path)
            if c._password and doc.needs_pass:
                doc.authenticate(c._password)

            first_p = min(page_rects.keys())
            for p_idx, r_list in page_rects.items():
                if 0 <= p_idx < doc.page_count:
                    page = doc[p_idx]
                    for r in r_list:
                        annot = page.add_highlight_annot(r)
                        annot.set_colors(stroke=color_tuple)
                        annot.update()

            parent = c.parentWidget()
            while parent and not hasattr(parent, "_save_and_reload"):
                parent = parent.parentWidget()
            if parent and hasattr(parent, "_save_and_reload"):
                parent._save_and_reload(doc, target_page=first_p)
            c.clear_active_selection()
        except Exception as exc:
            QMessageBox.warning(c, t("msg.warning"), f"Could not highlight text: {exc}")

    def _pick_custom_color(self) -> tuple[float, float, float] | None:
        curr = QColor(
            int(self._current_hi_color[0] * 255),
            int(self._current_hi_color[1] * 255),
            int(self._current_hi_color[2] * 255),
        )
        col = QColorDialog.getColor(curr, self.canvas, "Select Highlight Color")
        if col.isValid():
            chosen = (col.redF(), col.greenF(), col.blueF())
            self._current_hi_color = chosen
            return chosen
        return None

    def _route_to_edit_tool(self, mode: str, pos: QPoint | None = None) -> None:
        c = self.canvas
        win = c.window()
        if hasattr(win, "_open_tool_by_name"):
            win._open_tool_by_name(t("nav.edit"))
            edit_idx = win._edit_tool_idx() if hasattr(win, "_edit_tool_idx") else -1
            if edit_idx >= 0 and hasattr(win, "stack"):
                edit_w = win.stack.widget(edit_idx)
                if edit_w:
                    ctrl_scroll = getattr(edit_w, "_ctrl_scroll", None)
                    if ctrl_scroll is not None:
                        ctrl_scroll.setVisible(False)
                    if hasattr(edit_w, "_relayout_canvas"):
                        edit_w._relayout_canvas()

                    if hasattr(edit_w, "_sync_canvas_color"):
                        edit_w._sync_canvas_color(self._current_hi_color)

                    edit_canvas = getattr(edit_w, "_canvas", None)
                    if mode == "add" and hasattr(edit_w, "_set_mode_by_idx"):
                        edit_w._set_mode_by_idx(1)
                        edit_w._set_text_submode("add")
                    elif mode == "edit" and hasattr(edit_w, "_set_mode_by_idx"):
                        edit_w._set_mode_by_idx(1)
                        edit_w._set_text_submode("edit")
                    elif mode == "draw" and hasattr(edit_w, "_set_mode_by_idx"):
                        edit_w._set_mode_by_idx(8)
                        if edit_canvas:
                            edit_canvas.set_draw_mode(True, color=self._current_hi_color)
                    elif mode == "highlight_rect" and hasattr(edit_w, "_set_mode_by_idx"):
                        edit_w._set_mode_by_idx(4)
                        if edit_canvas:
                            edit_canvas.set_highlight_mode(True, self._current_hi_color, Qt.CursorShape.CrossCursor)
                    elif mode == "highlight_text" and hasattr(edit_w, "_set_mode_by_idx"):
                        edit_w._set_mode_by_idx(3)
                        if edit_canvas:
                            edit_canvas.set_highlight_mode(True, self._current_hi_color, Qt.CursorShape.IBeamCursor)

    def show_context_menu(
        self,
        e,
        select_all_fn: Callable[[], None],
        is_sig_inside_fn: Callable[[QPoint], bool],
    ) -> None:
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
            act_apply = menu.addAction(svg_icon(SVG_CHECK, color=ACCENT, size=15), t("btn.apply"))
            act_apply.triggered.connect(lambda _=False: c.commit_active_signature())
            act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), t("btn.delete"))
            act_del.triggered.connect(lambda _=False: c.delete_active_signature())
            menu.exec(e.globalPos())
            return

        hit = self.note_icon_at(pos)
        if hit is not None:
            menu = QMenu(c)
            self._menu_style = _ScrollableMenuStyle(menu.style())
            menu.setStyle(self._menu_style)
            menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
            delete_action = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), t("viewer.delete_comment"))
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
                svg_icon(SVG_COPY, color=ACCENT, size=15),
                t("viewer.copy_chars", n=len(c._sel_text)),
            )
            act_copy.triggered.connect(lambda _=False: QApplication.clipboard().setText(c._sel_text))

            act_hi_sel = menu.addAction(
                svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15),
                t("tool.text.highlight", default="Highlight Selection"),
            )
            act_hi_sel.triggered.connect(lambda _=False: self._apply_highlight_to_selection(self._current_hi_color))
            menu.addSeparator()

        act_sel_all = menu.addAction(
            svg_icon(SVG_GROUP, color=TEXT_SEC, size=15),
            t("Select All Text", default="Select All Text\tCtrl+A"),
        )
        act_sel_all.triggered.connect(lambda _=False: select_all_fn())
        menu.addSeparator()

        act_add_text = menu.addAction(
            svg_icon(SVG_PLUS_CIRCLE, color=ACCENT, size=15),
            "Add Text",
        )
        act_add_text.triggered.connect(lambda _=False: self._route_to_edit_tool("add", pos))

        act_edit_text = menu.addAction(
            svg_icon(SVG_EDIT, color=ACCENT, size=15),
            "Edit Text",
        )
        act_edit_text.triggered.connect(lambda _=False: self._route_to_edit_tool("edit", pos))
        menu.addSeparator()

        act_draw = menu.addAction(
            svg_icon(SVG_PEN, color=ACCENT, size=15),
            "Draw (Freehand Pen)",
        )
        act_draw.triggered.connect(lambda _=False: self._route_to_edit_tool("draw", pos))

        act_draw_hi_rect = menu.addAction(
            svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=15),
            "Draw Highlight (Rectangle)",
        )
        act_draw_hi_rect.triggered.connect(lambda _=False: c.set_rect_highlight_mode(True, self._current_hi_color))

        act_hi_text = menu.addAction(
            svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15),
            "Highlight Text",
        )
        act_hi_text.triggered.connect(lambda _=False: self._route_to_edit_tool("highlight_text", pos))
        menu.addSeparator()

        color_menu = menu.addMenu(make_rainbow_wheel_icon(18), "Highlight & Pen Color")
        act_custom = color_menu.addAction(make_rainbow_wheel_icon(16), "Custom Color...")

        def _on_custom_chosen():
            chosen = self._pick_custom_color()
            if chosen:
                if c._rect_hi_mode:
                    c.set_rect_highlight_mode(True, chosen)
                if c._sel_text:
                    self._apply_highlight_to_selection(chosen)

        act_custom.triggered.connect(lambda _=False: _on_custom_chosen())
        color_menu.addSeparator()

        for hex_code, label, rgb_tuple in PRESET_COLORS:
            act_color = color_menu.addAction(make_circular_color_icon(hex_code, 16), label)

            def _make_color_handler(ct=rgb_tuple):
                return lambda _=False: (
                    setattr(self, "_current_hi_color", ct),
                    c.set_rect_highlight_mode(True, ct) if getattr(c, "_rect_hi_mode", False) else None,
                    self._apply_highlight_to_selection(ct) if c._sel_text else None,
                )

            act_color.triggered.connect(_make_color_handler(rgb_tuple))

        menu.addSeparator()
        act_sig = menu.addAction(svg_icon(SVG_SIGNATURE, color=ACCENT, size=15), t("viewer.add_signature"))
        act_sig.triggered.connect(lambda _=False: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())