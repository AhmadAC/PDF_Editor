# app/tools/merge.py
"""PDF_Editor – TabJuntar: merge PDFs tool with in-pane action button and SVG icons."""

import contextlib
import os
from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QPushButton, QMessageBox,
)
from pypdf import PdfReader, PdfWriter

from app.base import BasePage
from app.constants import TEXT_PRI, _LQ
from app.pdf_io import atomic_pdf_write
from app.i18n import t
from app.pdf_password import decrypt_pypdf
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CLOSE,
    SVG_MINUS,
    section,
    danger_btn,
    pick_pdfs,
    show_error,
    svg_icon,
    WrongPasswordError,
)
from app.widgets import DropFileEdit, MultiDropWidget


class TabJuntar(BasePage):
    def __init__(self, status_fn):
        super().__init__("fa5s.object-group", t("tool.merge.name"),
                         t("tool.merge.desc"),
                         t("tool.merge.btn"), status_fn)
        self._pwd_map: dict[str, str] = {}
        f = self._form

        grp = QGroupBox(t("tool.merge.list"))
        vl  = QVBoxLayout(grp)
        vl.setSpacing(8)
        self.drop_multi = MultiDropWidget(self._on_drop)
        self.drop_multi.btn.clicked.connect(self._add_files)
        vl.addWidget(self.drop_multi)
        self.lst = QListWidget()
        self.lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lst.setAlternatingRowColors(True)
        self.lst.setMinimumHeight(180)
        self.lst.setMinimumWidth(0)
        self.lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        vl.addWidget(self.lst)
        hb = QHBoxLayout()

        self._btn_up = QPushButton()
        self._btn_up.setIcon(svg_icon(SVG_ARROW_UP, color=TEXT_PRI, size=14))
        self._btn_up.setIconSize(QSize(14, 14))
        self._btn_up.setToolTip(t("btn.up"))
        self._btn_up.setFixedWidth(40)
        self._btn_up.clicked.connect(self._up)

        self._btn_dn = QPushButton()
        self._btn_dn.setIcon(svg_icon(SVG_ARROW_DOWN, color=TEXT_PRI, size=14))
        self._btn_dn.setIconSize(QSize(14, 14))
        self._btn_dn.setToolTip(t("btn.down"))
        self._btn_dn.setFixedWidth(40)
        self._btn_dn.clicked.connect(self._dn)

        self._btn_rem = danger_btn("")
        self._btn_rem.setIcon(svg_icon(SVG_MINUS, color="#FCA5A5", size=14))
        self._btn_rem.setIconSize(QSize(14, 14))
        self._btn_rem.setToolTip(t("btn.remove"))
        self._btn_rem.setFixedWidth(40)
        self._btn_rem.clicked.connect(self._remove)

        self._btn_clr = QPushButton()
        self._btn_clr.setIcon(svg_icon(SVG_CLOSE, color=TEXT_PRI, size=14))
        self._btn_clr.setIconSize(QSize(14, 14))
        self._btn_clr.setToolTip(t("btn.clear"))
        self._btn_clr.setFixedWidth(40)
        self._btn_clr.clicked.connect(self._clear_all)

        for btn in (self._btn_up, self._btn_dn, self._btn_rem, self._btn_clr):
            hb.addWidget(btn)
        hb.addStretch()
        vl.addLayout(hb)
        f.addWidget(grp)

        f.addWidget(section(t("tool.merge.output")))
        self.drop_out = DropFileEdit(save=True, default_name="merged.pdf")
        f.addWidget(self.drop_out)

        # In-pane action button placed directly inside the form layout
        btn_merge_row = QHBoxLayout()
        btn_merge_row.setContentsMargins(0, 10, 0, 0)
        btn_merge_row.addStretch()
        self.btn_merge = QPushButton(t("tool.merge.btn"))
        self.btn_merge.setObjectName("btn_action_small")
        self.btn_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_merge.setFixedHeight(32)
        self.btn_merge.setMinimumWidth(110)
        self.btn_merge.clicked.connect(self._run)
        btn_merge_row.addWidget(self.btn_merge)
        f.addLayout(btn_merge_row)
        self.action_btn = self.btn_merge

        f.addStretch()

        # Hide the bottom pinned banner
        self._action_bar.setVisible(False)

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        pri = TEXT_PRI if dark else _LQ
        self._btn_up.setIcon(svg_icon(SVG_ARROW_UP, color=pri, size=14))
        self._btn_dn.setIcon(svg_icon(SVG_ARROW_DOWN, color=pri, size=14))
        self._btn_rem.setIcon(svg_icon(SVG_MINUS, color="#FCA5A5" if dark else "#DC2626", size=14))
        self._btn_clr.setIcon(svg_icon(SVG_CLOSE, color=pri, size=14))

    def _add_path(self, p: str) -> bool:
        """Prompt for password if encrypted, store it, append to list."""
        if not self._maybe_prompt_password(p):
            return False
        self._pwd_map[p] = self._pdf_password
        self.lst.addItem(QListWidgetItem(p))
        if not self.drop_out.path():
            base, ext = os.path.splitext(p)
            self.drop_out.set_path(os.path.normpath(base + "_merged" + ext))
        return True

    def _on_drop(self, paths: list):
        for p in paths:
            self._add_path(p)

    def _add_files(self):
        for p in pick_pdfs(self):
            self._add_path(p)

    def _up(self):
        r = self.lst.currentRow()
        if r > 0:
            item = self.lst.takeItem(r)
            self.lst.insertItem(r - 1, item)
            self.lst.setCurrentRow(r - 1)

    def _dn(self):
        r = self.lst.currentRow()
        if r < self.lst.count() - 1:
            item = self.lst.takeItem(r)
            self.lst.insertItem(r + 1, item)
            self.lst.setCurrentRow(r + 1)

    def _remove(self):
        r = self.lst.currentRow()
        if r >= 0:
            item = self.lst.takeItem(r)
            self._pwd_map.pop(item.text(), None)

    def _clear_all(self):
        self.lst.clear()
        self._pwd_map.clear()

    def auto_load(self, path: str):
        if not path:
            return
        existing = [self.lst.item(i).text() for i in range(self.lst.count())]
        if path not in existing:
            self._add_path(path)

    def _run(self):
        paths = [self.lst.item(i).text() for i in range(self.lst.count())]
        if len(paths) < 2:
            QMessageBox.warning(self, t("msg.warning"), t("tool.merge.min2"))
            return
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            QMessageBox.critical(self, t("msg.error"),
                                 t("tool.merge.missing_files", files="\n".join(missing)))
            return

        out_path = self.drop_out.path()
        if not out_path:
            default_name = "merged.pdf"
            if paths:
                base, ext = os.path.splitext(os.path.basename(paths[0]))
                default_name = f"{base}_merged{ext}"
            start_dir = os.path.dirname(paths[0]) if paths else ""
            out_path = self._prompt_save_as(default_name, start_dir)
            if not out_path:
                return
            self.drop_out.set_path(out_path)
        out_path = os.path.normpath(out_path)

        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)

        try:
            w = PdfWriter()
            for p in paths:
                reader = PdfReader(p)
                if reader.is_encrypted:
                    pwd = self._pwd_map.get(p, "")
                    if pwd:
                        if decrypt_pypdf(reader, pwd) is None:
                            raise WrongPasswordError(t("tool.err.wrong_password"))
                for page in reader.pages:
                    w.add_page(page)

            if viewer and viewer.current_path() and os.path.abspath(viewer.current_path()) == os.path.abspath(out_path):
                viewer._canvas.close_doc()
                doc = getattr(viewer, "_fitz_doc", None)
                if doc is not None:
                    with contextlib.suppress(Exception):
                        if not getattr(doc, "is_closed", False):
                            doc.close()
                    viewer._fitz_doc = None
                viewer._thumbnails._stop_all_workers()

            atomic_pdf_write(w, out_path, sources=paths)

            self._status(t("tool.merge.status.done", name=os.path.basename(out_path)))
            msg = t("tool.merge.done", path=out_path)

            cleanup_fn = getattr(win, "_cleanup_pipeline", None)
            if callable(cleanup_fn) and viewer:
                cleanup_fn(id(viewer))

            if viewer:
                viewer.load(out_path)

            QMessageBox.information(self, t("msg.done"), msg)
        except Exception as e:
            show_error(self, e)