# app/tools/_import_gui.py
"""PDF_Editor – GUI bindings for the multi-format PDF import tab with SVG icons."""

import contextlib
import logging
import os
from typing import Any

import fitz
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton
)

from app.base import BasePage
from app.pdf_io import atomic_pdf_write
from app.i18n import t
from app.utils import (
    SVG_MINUS,
    SVG_PLUS,
    section,
    danger_btn,
    result_label_style,
    svg_for_name,
    svg_icon,
)
from app.constants import DESKTOP, TEXT_SEC, _LQ
from app.widgets import DropFileEdit
from app.tools._import_base import IMAGE_EXTS, DOC_EXTS, PPT_EXTS, EXCEL_EXTS, EBOOK_EXTS, _ALL_SUPPORTED_FILTER, _NoContent
from app.tools._import_core import convert_file_to_pdf

_log = logging.getLogger(__name__)


class _ImportFileList(QListWidget):
    """File list supporting drag & drop of arbitrary files directly into the list."""

    def __init__(self, on_drop_callback, parent=None):
        super().__init__(parent)
        self._on_drop_callback = on_drop_callback
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [
                os.path.abspath(u.toLocalFile())
                for u in event.mimeData().urls()
                if u.toLocalFile() and os.path.isfile(u.toLocalFile())
            ]
            if paths:
                self._on_drop_callback(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for item in self.selectedItems():
                row = self.row(item)
                self.takeItem(row)
            event.accept()
            return
        super().keyPressEvent(event)


class TabImport(BasePage):
    def __init__(self, status_fn):
        super().__init__(
            "fa5s.file-import",
            t("tool.import.name"),
            t("tool.import.desc"),
            t("tool.import.btn"),
            status_fn,
        )
        f = self._form

        f.addWidget(section(t("tool.import.source_file")))

        self._file_list = _ImportFileList(self._on_files_dropped, self)
        self._file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_list.setMinimumHeight(180)
        f.addWidget(self._file_list)

        file_btns = QHBoxLayout()
        self._add_btn = QPushButton(t("tool.import.add_files"))
        self._add_btn.setIcon(svg_icon(SVG_PLUS, color=TEXT_SEC, size=14))
        self._add_btn.setIconSize(QSize(14, 14))
        self._add_btn.clicked.connect(self._pick_files)
        file_btns.addWidget(self._add_btn)

        self._remove_selected_btn = QPushButton(t("btn.remove"))
        self._remove_selected_btn.setIcon(svg_icon(SVG_MINUS, color=TEXT_SEC, size=14))
        self._remove_selected_btn.setIconSize(QSize(14, 14))
        self._remove_selected_btn.clicked.connect(self._remove_selected_files)
        file_btns.addWidget(self._remove_selected_btn)

        self._clear_btn = danger_btn(t("btn.clear"))
        self._clear_btn.clicked.connect(self._file_list.clear)
        file_btns.addWidget(self._clear_btn)
        file_btns.addStretch()
        f.addLayout(file_btns)

        f.addWidget(section(t("tool.import.output")))
        self.drop_out = DropFileEdit(save=True, default_name="output.pdf")
        f.addWidget(self.drop_out)

        self.lbl_result = QLabel("")
        self.lbl_result.setStyleSheet(result_label_style())
        f.addWidget(self.lbl_result)
        f.addStretch()

    def set_compact_mode(self, active: bool, path: str = "") -> None:
        pass

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        col = TEXT_SEC if dark else _LQ
        try:
            self.lbl_result.setStyleSheet(result_label_style(dark))
            self._add_btn.setIcon(svg_icon(SVG_PLUS, color=col, size=14))
            self._remove_selected_btn.setIcon(svg_icon(SVG_MINUS, color=col, size=14))
            for i in range(self._file_list.count()):
                item = self._file_list.item(i)
                if item is not None:
                    p = item.data(Qt.ItemDataRole.UserRole) or ""
                    ext = os.path.splitext(p)[1].lower()
                    item.setIcon(svg_for_name(self._icon_for_ext(ext), color=col, size=15))
        except RuntimeError:
            pass

    @staticmethod
    def _icon_for_ext(ext: str) -> str:
        ext = ext.lower()
        if ext in IMAGE_EXTS:
            return "fa5s.image"
        if ext in DOC_EXTS:
            return "fa5s.file-word"
        if ext in PPT_EXTS:
            return "fa5s.file-powerpoint"
        if ext in EXCEL_EXTS:
            return "fa5s.file-excel"
        if ext in (".html", ".htm"):
            return "fa5s.file-code"
        if ext in EBOOK_EXTS:
            return "fa5s.book"
        if ext in (".pdf",):
            return "fa5s.file-pdf"
        return "fa5s.file-alt"

    def _add_file_path(self, p: str):
        if not p or not os.path.isfile(p):
            return
        p = os.path.abspath(p)
        existing = [
            self._file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._file_list.count())
        ]
        if p in existing:
            return

        ext = os.path.splitext(p)[1].lower()
        icon_name = self._icon_for_ext(ext)
        item = QListWidgetItem(svg_for_name(icon_name, color=TEXT_SEC, size=15), os.path.basename(p))
        item.setData(Qt.ItemDataRole.UserRole, p)
        item.setToolTip(p)
        self._file_list.addItem(item)

        if not self.drop_out.path():
            base = os.path.splitext(p)[0]
            self.drop_out.set_path(base + ".pdf")

    def _on_files_dropped(self, paths: list[str]):
        for p in paths:
            self._add_file_path(p)

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("tool.import.add_files"),
            DESKTOP,
            _ALL_SUPPORTED_FILTER,
        )
        for p in paths:
            self._add_file_path(p)

    def _remove_selected_files(self):
        for item in self._file_list.selectedItems():
            row = self._file_list.row(item)
            self._file_list.takeItem(row)

    def _get_files(self) -> list[str]:
        return [
            self._file_list.item(i).data(Qt.ItemDataRole.UserRole)
            or self._file_list.item(i).text()
            for i in range(self._file_list.count())
        ]

    def _run(self):
        files = self._get_files()
        if not files:
            QMessageBox.warning(self, t("msg.warning"), t("tool.import.select_file"))
            return

        default_name = "output.pdf"
        if files:
            base, _ = os.path.splitext(os.path.basename(files[0]))
            default_name = f"{base}.pdf"
        start_dir = os.path.dirname(files[0]) if files else ""
        out = self._prompt_save_as(default_name, start_dir)
        if not out:
            return
        self.drop_out.set_path(out)

        self.lbl_result.setText("")
        n = len(files)

        def do_work(worker):
            doc = fitz.open()
            skipped = 0
            try:
                for i, file_path in enumerate(files):
                    if worker.is_cancelled():
                        return None
                    worker.progress.emit(i + 1, f"{i + 1}/{n}: {os.path.basename(file_path)}")
                    try:
                        part_pdf = convert_file_to_pdf(file_path)
                        part_doc = fitz.open(part_pdf)
                        doc.insert_pdf(part_doc)
                        with contextlib.suppress(Exception):
                            if not getattr(part_doc, "is_closed", False):
                                part_doc.close()
                        if part_pdf != file_path and os.path.isfile(part_pdf):
                            with contextlib.suppress(Exception):
                                os.unlink(part_pdf)
                    except Exception as exc_part:
                        _log.warning("Failed to convert %s: %s", file_path, exc_part)
                        skipped += 1

                if worker.is_cancelled():
                    return None
                if doc.page_count == 0:
                    return _NoContent(skipped)

                win: Any = self.window()
                viewer = getattr(win, "_viewer", None)
                if (
                    viewer
                    and viewer.current_path()
                    and os.path.abspath(viewer.current_path()) == os.path.abspath(out)
                ):
                    viewer._canvas.close_doc()
                    if viewer._fitz_doc:
                        with contextlib.suppress(Exception):
                            viewer._fitz_doc.close()
                        viewer._fitz_doc = None
                    viewer._thumbnails._stop_all_workers()

                atomic_pdf_write(doc, out, close_writer=True)
            finally:
                with contextlib.suppress(Exception):
                    if not getattr(doc, "is_closed", False):
                        doc.close()
            return out

        self._run_background(
            do_work,
            total=max(n, 1),
            label=t("tool.import.converting"),
            on_done=lambda r: self._on_result(r, out),
        )

    def _on_result(self, result, out_path: str):
        if isinstance(result, _NoContent):
            if result.skipped:
                self._status(t("tool.import.skipped_images", n=result.skipped))
            else:
                self._status(t("tool.import.no_content"))
            QMessageBox.warning(self, t("msg.warning"), t("tool.import.no_content"))
            return
        self._done(out_path)

    def _done(self, out_path: str):
        self.lbl_result.setText(f"  -> {os.path.basename(out_path)}")
        self._status(t("tool.import.status.done", path=out_path))

        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)
        cleanup_fn = getattr(win, "_cleanup_pipeline", None)
        if callable(cleanup_fn) and viewer:
            cleanup_fn(id(viewer))

        if viewer:
            viewer.load(out_path)

        QMessageBox.information(self, t("msg.done"), t("tool.import.done", path=out_path))