# app/editor/tab.py
"""PDF_Editor – TabEditar facade: visual PDF editor tab with Fill & Sign, media manipulation, and typography styling."""

from __future__ import annotations

import os
from typing import Callable

import fitz
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QWidget,
)

from app.editor.canvas import PdfEditCanvas
from app.editor.tab_actions import TabActionsMixin
from app.editor.tab_constants import (
    _DRAW_COLORS_KEYS,
    _DRAW_COLORS_VALS,
    _HI_COLORS_KEYS,
    _HI_COLORS_VALS,
    _MAX_PENDING,
    _MAX_REDO,
    _MODE_FORMS,
    _MODE_KEYS,
    _MODE_TEXT,
    _RED_FILLS_KEYS,
    _RED_FILLS_VALS,
)
from app.editor.tab_document import TabDocumentMixin
from app.editor.tab_history import TabHistoryManager
from app.editor.tab_nav import TabNavMixin
from app.editor.tab_operations import (
    apply_form_fields_and_save,
    apply_visual_edits_and_save,
    get_fitz_permissions,
    prompt_encryption_choice,
)
from app.editor.tab_ui import setup_editor_ui, update_tab_theme
from app.i18n import t
from app.utils import _paint_bg
from app.viewer.thumbnails import ThumbnailPanel
from app.widgets import ColorPickerButton, DropFileEdit, FocusComboBox, FocusSpinBox


class TabEditar(TabNavMixin, TabDocumentMixin, TabActionsMixin, QWidget):
    """Visual editor facade: click/drag directly on the rendered PDF."""

    _MAX_REDO = _MAX_REDO
    _MAX_PENDING = _MAX_PENDING
    _HI_COLORS_KEYS = _HI_COLORS_KEYS
    _HI_COLORS_VALS = _HI_COLORS_VALS
    _RED_FILLS_KEYS = _RED_FILLS_KEYS
    _RED_FILLS_VALS = _RED_FILLS_VALS
    _MODE_KEYS = _MODE_KEYS
    _DRAW_COLORS_KEYS = _DRAW_COLORS_KEYS
    _DRAW_COLORS_VALS = _DRAW_COLORS_VALS

    _canvas: PdfEditCanvas
    _canvas_scroll: QScrollArea
    _ctrl_scroll: QScrollArea
    _grp_file: QGroupBox
    _drop_in: DropFileEdit
    _lbl_info: QLabel
    _btn_prev: QPushButton
    _lbl_page: QLabel
    _btn_next: QPushButton
    _mode_btns: list[QPushButton]
    _mode_btn_idx: dict[int, int]
    _opt_stack: QStackedWidget
    _hint_labels: list[QLabel]
    _pending_list: QListWidget
    _btn_undo: QPushButton
    _btn_redo: QPushButton
    _grp_save: QGroupBox
    _drop_out: DropFileEdit
    _action_bar: QWidget
    action_btn: QPushButton

    _thumbnails: ThumbnailPanel
    _toc_tree: QTreeWidget
    _sidebar_tabs: QTabWidget
    _sidebar_panel: QWidget
    _edit_splitter: QSplitter
    _pages_sidebar_collapsed: bool
    _saved_sidebar_width: int
    _toc_tab_idx: int
    _pages_tab_idx: int

    _btn_text_add: QPushButton
    _btn_text_edit: QPushButton
    _red_color: ColorPickerButton
    _text_font: FocusComboBox
    _text_size: FocusSpinBox
    _btn_bold: QPushButton
    _btn_italic: QPushButton
    _text_color: ColorPickerButton
    _btn_del_text: QPushButton
    _text_hint: QLabel
    _img_drop: DropFileEdit
    _hi_color: ColorPickerButton
    _hi_rect_color: ColorPickerButton
    _note_txt: QTextEdit
    _form_table: QTableWidget
    _form_status: QLabel
    _sig_preview: QLabel
    _sig_choose: QPushButton
    _btn_stamp_check: QPushButton
    _btn_stamp_cross: QPushButton
    _stamp_size: FocusSpinBox
    _stamp_color: ColorPickerButton
    _draw_color_cb: ColorPickerButton
    _draw_width_slider: QSlider
    _draw_width_lbl: QLabel
    _sel_result: QTextEdit
    _btn_copy: QPushButton
    _btn_edit_sel: QPushButton
    _btn_apply_sel_edit: QPushButton
    _btn_delete_sel: QPushButton
    _btn_highlight: QPushButton
    _btn_redact_sel: QPushButton

    @property
    def _HI_COLORS(self):
        return {t(k): v for k, v in zip(self._HI_COLORS_KEYS, self._HI_COLORS_VALS)}

    @property
    def _RED_FILLS(self):
        return {t(k): v for k, v in zip(self._RED_FILLS_KEYS, self._RED_FILLS_VALS)}

    @property
    def _MODE_DEFS(self):
        return [(t(k), icon) for k, icon in self._MODE_KEYS]

    @property
    def _DRAW_COLORS(self):
        return {t(k): v for k, v in zip(self._DRAW_COLORS_KEYS, self._DRAW_COLORS_VALS)}

    @property
    def _user_pending(self) -> list:
        return [
            e for e in self._pending
            if not e.get("_existing") or e.get("_deleted") or e.get("type") in (
                "delete_annot", "text_edit", "image", "signature", "check", "cross"
            )
        ]

    def __init__(self, status_fn: Callable[[str], None]):
        super().__init__()
        self._status = status_fn
        self._pending: list[dict] = []
        self._redo_stack: list[dict] = []
        self._doc_path: str | None = None
        self._pdf_password: str = ""
        self._mode_idx: int = _MODE_TEXT
        self._text_submode: str = "edit"
        self._dark_mode: bool = True
        self._signature_path: str | None = None
        self._page_idx: int = 0
        self._last_selected_page: int = -1
        self._last_selected_rect: fitz.Rect | None = None
        self._last_selected_original_text: str = ""

        self._doc_states: dict[str, dict] = {}
        self._history = TabHistoryManager(self)
        setup_editor_ui(self)

        self._canvas.rect_selected.connect(self._on_rect)
        self._canvas.point_clicked.connect(self._on_point)
        self._canvas.stroke_finished.connect(self._on_stroke)
        self._canvas.note_deleted.connect(self._on_note_deleted)
        self._canvas.text_edit_committed.connect(self._on_text_edit_committed)
        self._canvas.text_inserted.connect(self._on_text_edit_committed)
        self._canvas.text_edit_started.connect(self._on_text_edit_started)
        self._canvas.signature_added.connect(self._on_signature_added)
        self._canvas.stamp_added.connect(self._on_stamp_added)
        self._canvas.overlay_changed.connect(self.update)
        self._canvas.overlay_deleted.connect(self._on_overlay_deleted)
        self._canvas.zoom_changed.connect(lambda _: self._update_page_nav_and_thumbnails())
        if hasattr(self._canvas, "delete_selection_requested"):
            self._canvas.delete_selection_requested.connect(self._delete_selection)
        if hasattr(self._canvas, "edit_selection_requested"):
            self._canvas.edit_selection_requested.connect(self._edit_selection)
        if hasattr(self._canvas, "highlight_selection_requested"):
            self._canvas.highlight_selection_requested.connect(self._highlight_selection)
        if hasattr(self._canvas, "redact_selection_requested"):
            self._canvas.redact_selection_requested.connect(self._redact_selection)

        self._set_text_submode("edit")
        self._on_mode_btn(self._mode_btns[_MODE_TEXT])
        self._update_nav()

    def set_compact_mode(self, active: bool, path: str = "") -> None:
        if active and path:
            self.auto_load(path)
        self._grp_file.setVisible(not active)
        self._grp_save.setVisible(not active)

    def update_theme(self, dark: bool) -> None:
        update_tab_theme(self, dark)
        self._update_text_submode_styles()
        if hasattr(self, "_thumbnails"):
            self._thumbnails.update_theme(dark)

    def paintEvent(self, event):
        _paint_bg(self)

    def _add(self, edit: dict, *, _from_redo: bool = False):
        self._history.add(edit, _from_redo=_from_redo)

    def _undo(self):
        self._history.undo()

    def _redo(self):
        self._history.redo()

    def _clear_pending(self):
        self._history.clear()

    def _prompt_encryption_choice(self) -> str | None:
        return prompt_encryption_choice(self)

    @staticmethod
    def _fitz_permissions_of(doc) -> int:
        return get_fitz_permissions(doc)

    def _apply_forms(self, out: str):
        apply_form_fields_and_save(self, out)

    def _run(self):
        if not self._doc_path or not os.path.isfile(self._doc_path):
            QMessageBox.warning(self, t("msg.warning"), t("msg.open_pdf_first"))
            return
        out = self._drop_out.path()
        if not out:
            base, ext = os.path.splitext(os.path.basename(self._doc_path))
            suggested = os.path.join(os.path.dirname(self._doc_path), base + "_edited" + ext)
            from PySide6.QtWidgets import QFileDialog
            out, _ = QFileDialog.getSaveFileName(self, t("btn.choose"), suggested, t("file_filter.pdf"))
            if not out:
                return
            self._drop_out.set_path(out)

        if self._mode_idx == _MODE_FORMS:
            if self._user_pending:
                reply = QMessageBox.question(
                    self, t("msg.warning"),
                    t("editor.forms.has_pending"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._apply_forms(out)
            return

        if not self._user_pending:
            QMessageBox.warning(self, t("msg.warning"), t("msg.no_pending"))
            return

        apply_visual_edits_and_save(self, out)

    def closeEvent(self, event):
        if hasattr(self, "_thumbnails"):
            self._thumbnails.clear()
        super().closeEvent(event)