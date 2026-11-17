# app/tools/watermark.py
"""PDF_Editor – TabMarcaDagua: stamp PDF A onto PDF B with independent opacities, open-tab selectors, omitted-page retention, and new-tab output."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import tempfile
import traceback
from typing import Any

import fitz
from PIL import Image, ImageChops
from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
)

from app.base import BasePage
from app.constants import (
    ACCENT,
    BG_CARD,
    BG_INPUT,
    BORDER,
    DESKTOP,
    TEXT_PRI,
    TEXT_SEC,
    _LC,
    _LI,
    _LO,
    _LP,
    _LQ,
)
from app.i18n import add_recent_file, t
from app.pdf_io import atomic_pdf_write
from app.pdf_password import authenticate_fitz
from app.utils import (
    SVG_CHECK,
    SVG_CLOSE,
    SVG_FILE_PDF,
    SVG_FOLDER,
    SVG_LIGHTBULB,
    SVG_STAMP,
    SVG_TAB,
    WrongPasswordError,
    is_dark,
    open_folder,
    parse_pages,
    reveal_file,
    show_error,
    svg_icon,
    svg_pixmap,
)
from app.widgets import DropFileEdit, FocusComboBox, FocusSpinBox

_log = logging.getLogger(__name__)


class _OpenTabComboBox(FocusComboBox):
    """Combo box that refreshes its open-tab entries right before opening its popup."""

    def __init__(self, refresh_fn=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self.setMinimumWidth(0)
        self.setMinimumContentsLength(8)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def showPopup(self):
        if callable(self._refresh_fn):
            self._refresh_fn()
        super().showPopup()


class _OpacitySpinBox(FocusSpinBox):
    """Spinbox allowing effortless click-and-type editing of 0–100% opacity values."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(100)
        self.setSuffix("%")
        self.setFixedWidth(78)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setKeyboardTracking(True)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)

    def mousePressEvent(self, event):
        had_focus = self.hasFocus() or (self.lineEdit() and self.lineEdit().hasFocus())
        super().mousePressEvent(event)
        if not had_focus:
            QTimer.singleShot(0, self.selectAll)


class _PdfSourceCard(QFrame):
    """Compact card combining an open-tab dropdown, Open... file picker, drag-and-drop, and page info."""

    file_picked = Signal(str)
    tab_selected = Signal(object)
    cleared = Signal()

    def __init__(self, title_text: str, placeholder_text: str, refresh_fn, parent=None):
        super().__init__(parent)
        self.setObjectName("wm_source_card")
        self.setAcceptDrops(True)
        self.setMinimumWidth(0)
        self._path_value: str = ""
        self._display_name: str = ""
        self._placeholder = placeholder_text

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self._lbl_title = QLabel(title_text)
        self._lbl_title.setObjectName("wm_card_title")
        self._lbl_info = QLabel("")
        self._lbl_info.setObjectName("wm_card_info")
        self._lbl_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top.addWidget(self._lbl_title)
        top.addStretch(1)
        top.addWidget(self._lbl_info)
        v.addLayout(top)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)

        self.cmb_tab = _OpenTabComboBox(refresh_fn=refresh_fn)
        self.cmb_tab.currentIndexChanged.connect(self._on_combo_changed)
        row.addWidget(self.cmb_tab, 1)

        self.btn_clear = QPushButton()
        self.btn_clear.setObjectName("wm_clear_btn")
        self.btn_clear.setFixedSize(24, 24)
        self.btn_clear.setIconSize(QSize(12, 12))
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip(t("btn.clear"))
        self.btn_clear.setVisible(False)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        row.addWidget(self.btn_clear)

        self.btn_open = QPushButton(t("widget.open"))
        self.btn_open.setObjectName("wm_open_btn")
        self.btn_open.setIconSize(QSize(14, 14))
        self.btn_open.setFixedHeight(26)
        self.btn_open.setMinimumWidth(72)
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.clicked.connect(self._browse_file)
        row.addWidget(self.btn_open)

        v.addLayout(row)
        self.update_theme(is_dark())

    def path(self) -> str:
        return self._path_value

    def set_path(self, path: str, display_name: str = "") -> None:
        self._path_value = os.path.abspath(os.path.normpath(path)) if path else ""
        self._display_name = display_name or (os.path.basename(self._path_value) if self._path_value else "")
        self.btn_clear.setVisible(bool(self._path_value))
        self.cmb_tab.setToolTip(self._path_value or self._placeholder)

    def set_info(self, text: str) -> None:
        self._lbl_info.setText(text)

    def populate_tabs(self, entries: list[tuple[str, str, str, str]]) -> None:
        """Populate open tabs while preserving a custom disk selection if not in open tabs."""
        dark = is_dark()
        tab_ico = svg_icon(SVG_TAB, color=ACCENT, size=14)
        pdf_ico = svg_icon(SVG_FILE_PDF, color=ACCENT, size=14)

        self.cmb_tab.blockSignals(True)
        self.cmb_tab.clear()
        self.cmb_tab.addItem(self._placeholder, None)

        matched_idx = 0
        cur_abs = os.path.abspath(self._path_value) if self._path_value else ""
        for idx, (label, curr_p, orig_p, pwd) in enumerate(entries, start=1):
            self.cmb_tab.addItem(tab_ico, label, ("tab", curr_p, orig_p, pwd))
            self.cmb_tab.setItemData(idx, orig_p, Qt.ItemDataRole.ToolTipRole)
            if cur_abs and (
                os.path.abspath(curr_p) == cur_abs or os.path.abspath(orig_p) == cur_abs
            ):
                matched_idx = idx

        if cur_abs and matched_idx == 0:
            disp = self._display_name or os.path.basename(cur_abs)
            self.cmb_tab.addItem(pdf_ico, disp, ("file", cur_abs, cur_abs, ""))
            idx = self.cmb_tab.count() - 1
            self.cmb_tab.setItemData(idx, cur_abs, Qt.ItemDataRole.ToolTipRole)
            matched_idx = idx

        self.cmb_tab.setCurrentIndex(matched_idx)
        self.cmb_tab.blockSignals(False)
        self.btn_clear.setVisible(bool(self._path_value))
        self.update_theme(dark)

    def _on_combo_changed(self, idx: int) -> None:
        data = self.cmb_tab.itemData(idx)
        if not data:
            if idx == 0 and self._path_value:
                self._on_clear_clicked()
            return
        kind, curr_p, orig_p, pwd = data
        if kind == "tab":
            self.tab_selected.emit((curr_p, orig_p, pwd))

    def _on_clear_clicked(self) -> None:
        self._path_value = ""
        self._display_name = ""
        self._lbl_info.setText("")
        self.btn_clear.setVisible(False)
        self.cmb_tab.blockSignals(True)
        if self.cmb_tab.count() > 0:
            self.cmb_tab.setCurrentIndex(0)
        self.cmb_tab.blockSignals(False)
        self.cleared.emit()

    def _browse_file(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, t("btn.open_pdf"), DESKTOP, t("file_filter.pdf"))
        if p:
            self.file_picked.emit(os.path.abspath(p))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(".pdf") and os.path.isfile(p):
                self.file_picked.emit(os.path.abspath(p))
                event.acceptProposedAction()
                return

    def update_theme(self, dark: bool) -> None:
        bg = BG_CARD if dark else _LC
        brd = BORDER if dark else _LO
        pri = TEXT_PRI if dark else _LP
        sec = TEXT_SEC if dark else _LQ
        inp = BG_INPUT if dark else _LI

        self.setStyleSheet(
            f"#wm_source_card {{ background: {bg}; border: 1px solid {brd}; border-radius: 8px; }}"
            f"#wm_card_title {{ color: {pri}; font-size: 9.5pt; font-weight: 700; background: transparent; }}"
            f"#wm_card_info {{ color: {sec}; font-size: 9pt; background: transparent; }}"
            f"#wm_source_card QComboBox {{ background: {inp}; border: 1px solid {brd}; border-radius: 5px; padding: 2px 8px; min-height: 20px; font-size: 9.5pt; }}"
            f"#wm_clear_btn {{ background: transparent; border: none; border-radius: 4px; padding: 0; }}"
            f"#wm_clear_btn:hover {{ background: rgba(239, 68, 68, 0.16); }}"
        )
        self.btn_open.setIcon(svg_icon(SVG_FOLDER, color=pri, size=14))
        self.btn_clear.setIcon(svg_icon(SVG_CLOSE, color="#EF4444" if dark else "#DC2626", size=12))


class TabMarcaDagua(BasePage):
    def __init__(self, status_fn):
        super().__init__(
            "fa5s.stamp",
            t("tool.watermark.name"),
            t("tool.watermark.desc"),
            t("tool.watermark.btn"),
            status_fn,
        )
        self._pipeline_supported = False
        self._base_orig_path: str = ""
        self._wm_orig_path: str = ""
        self._wm_password: str = ""
        self._generated_outputs: set[str] = set()
        self._wm_tmp_dirs: list[str] = []
        self._last_output_temp: str = ""
        self._last_suggested_path: str = ""
        self._last_saved_path: str = ""
        self._last_output_viewer: Any = None

        # Compact header so the entire watermark panel fits comfortably without clipping
        self._header.setFixedHeight(44)
        hdr_lay = self._header.layout()
        if hdr_lay is not None:
            hdr_lay.setContentsMargins(14, 4, 14, 4)

        # Hide scrollbars completely while preserving wheel scroll if shrunk very small
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical, QScrollBar:horizontal {"
            " width: 0px; height: 0px; max-width: 0px; max-height: 0px;"
            " background: transparent; border: none; }"
        )

        # Never use the floating bottom overlay bar; button & green box live inside the window
        self._action_bar.setVisible(False)

        f = self._form
        f.setContentsMargins(12, 6, 12, 8)
        f.setSpacing(6)

        # ── 1. Base PDF Card (Open tab OR Open...) ────────────────────────
        self.base_card = _PdfSourceCard(
            "Base PDF (Document)",
            "Select open tab or drop PDF...",
            refresh_fn=self._refresh_open_tabs,
        )
        self.base_card.tab_selected.connect(self._on_base_tab_selected)
        self.base_card.file_picked.connect(self._on_base_file_picked)
        self.base_card.cleared.connect(self._on_base_cleared)
        f.addWidget(self.base_card)

        # ── 2. Stamp PDF Card (Open tab OR Open...) ───────────────────────
        self.wm_card = _PdfSourceCard(
            "Stamp PDF (Watermark)",
            "Select open tab or drop PDF...",
            refresh_fn=self._refresh_open_tabs,
        )
        self.wm_card.tab_selected.connect(self._on_wm_tab_selected)
        self.wm_card.file_picked.connect(self._on_wm_file_picked)
        self.wm_card.cleared.connect(self._on_wm_cleared)
        f.addWidget(self.wm_card)

        # ── 3. Options Card (Plain frame — NO "Options" tab or label) ─────
        self._options_card = QFrame()
        self._options_card.setObjectName("wm_options_card")
        self._options_card.setMinimumWidth(0)
        form = QFormLayout(self._options_card)
        form.setContentsMargins(10, 8, 10, 8)
        form.setVerticalSpacing(5)
        form.setHorizontalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.edit_pages = QLineEdit()
        self.edit_pages.setMinimumWidth(0)
        self.edit_pages.setPlaceholderText(t("tool.watermark.pages_hint"))
        form.addRow(t("tool.watermark.pages_label"), self.edit_pages)

        self.chk_include_omitted_wm = QCheckBox("Copy omitted watermark pages (without base)")
        self.chk_include_omitted_wm.setChecked(False)
        self.chk_include_omitted_wm.setToolTip(
            "When only specific pages are selected, copies the remaining omitted watermark PDF pages to the output without applying the base page"
        )
        form.addRow("", self.chk_include_omitted_wm)

        self.chk_match_pages = QCheckBox("Match && auto-duplicate pages")
        self.chk_match_pages.setChecked(True)
        self.chk_match_pages.setToolTip(
            "Automatically duplicates a 1-page background PDF to match all pages of a multi-page stamp PDF"
        )
        self.chk_match_pages.toggled.connect(self._on_match_toggled)
        form.addRow("", self.chk_match_pages)

        self.spin_wm_page = FocusSpinBox()
        self.spin_wm_page.setMinimumWidth(0)
        self.spin_wm_page.setRange(1, 9999)
        self.spin_wm_page.setValue(1)
        self.spin_wm_page.setEnabled(False)
        self.spin_wm_page.setToolTip("Single page number from Stamp PDF to repeat across pages")
        form.addRow("Stamp page:", self.spin_wm_page)

        self.cmb_layer = FocusComboBox()
        self.cmb_layer.setMinimumWidth(0)
        full = [t("tool.watermark.above"), t("tool.watermark.below")]
        short = [s.split("(")[0].strip() for s in full]
        self.cmb_layer.addItems(short)
        for i, full_text in enumerate(full):
            self.cmb_layer.setItemData(i, full_text, Qt.ItemDataRole.ToolTipRole)
        self.cmb_layer.setMinimumContentsLength(8)
        form.addRow(t("tool.watermark.position_label"), self.cmb_layer)

        # Base PDF opacity: slider + clickable/typeable spinbox
        base_op_row = QHBoxLayout()
        base_op_row.setContentsMargins(0, 0, 0, 0)
        base_op_row.setSpacing(8)
        self.slider_base_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_base_opacity.setMinimumWidth(60)
        self.slider_base_opacity.setRange(0, 100)
        self.slider_base_opacity.setValue(100)
        self.spin_base_opacity = _OpacitySpinBox()
        self.slider_base_opacity.valueChanged.connect(self._sync_base_opacity_from_slider)
        self.spin_base_opacity.valueChanged.connect(self._sync_base_opacity_from_spin)
        base_op_row.addWidget(self.slider_base_opacity, 1)
        base_op_row.addWidget(self.spin_base_opacity)
        form.addRow("Base opacity:", base_op_row)

        # Stamp PDF opacity: slider + clickable/typeable spinbox
        op_row = QHBoxLayout()
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.setSpacing(8)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setMinimumWidth(60)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setValue(100)
        self.spin_opacity = _OpacitySpinBox()
        self.slider_opacity.valueChanged.connect(self._sync_stamp_opacity_from_slider)
        self.spin_opacity.valueChanged.connect(self._sync_stamp_opacity_from_spin)
        op_row.addWidget(self.slider_opacity, 1)
        op_row.addWidget(self.spin_opacity)
        form.addRow("Stamp opacity:", op_row)

        self.chk_transparent_white = QCheckBox("Make white background transparent")
        self.chk_transparent_white.setChecked(True)
        self.chk_transparent_white.setToolTip(
            "Removes solid white page background from the overlay PDF so underlying content shows through"
        )
        form.addRow("", self.chk_transparent_white)

        tip_row = QHBoxLayout()
        tip_row.setContentsMargins(0, 1, 0, 0)
        tip_row.setSpacing(6)
        self._lbl_tip_icon = QLabel()
        self._lbl_tip_icon.setFixedSize(14, 14)
        self._lbl_tip = QLabel("Adjust Base opacity to fade the base PDF, or Stamp opacity to fade the overlay PDF.")
        self._lbl_tip.setWordWrap(True)
        tip_row.addWidget(self._lbl_tip_icon, 0, Qt.AlignmentFlag.AlignTop)
        tip_row.addWidget(self._lbl_tip, 1)
        form.addRow("", tip_row)

        f.addWidget(self._options_card)

        # ── 4. In-Window Green Result Box (Not an overlay) ─────────────────
        self._result_box = QFrame()
        self._result_box.setObjectName("wm_result_box")
        self._result_box.setVisible(False)
        rv = QVBoxLayout(self._result_box)
        rv.setContentsMargins(10, 7, 10, 7)
        rv.setSpacing(5)

        r_top = QHBoxLayout()
        r_top.setContentsMargins(0, 0, 0, 0)
        r_top.setSpacing(8)

        self._lbl_res_icon = QLabel()
        self._lbl_res_icon.setFixedSize(16, 16)
        r_top.addWidget(self._lbl_res_icon, 0, Qt.AlignmentFlag.AlignTop)

        self._lbl_res_msg = QLabel("")
        self._lbl_res_msg.setWordWrap(True)
        self._lbl_res_msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        r_top.addWidget(self._lbl_res_msg, 1)

        self._btn_res_close = QPushButton()
        self._btn_res_close.setObjectName("wm_res_close")
        self._btn_res_close.setFixedSize(20, 20)
        self._btn_res_close.setIconSize(QSize(12, 12))
        self._btn_res_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_res_close.clicked.connect(lambda: self._result_box.setVisible(False))
        r_top.addWidget(self._btn_res_close, 0, Qt.AlignmentFlag.AlignTop)
        rv.addLayout(r_top)

        r_btns = QHBoxLayout()
        r_btns.setContentsMargins(0, 0, 0, 0)
        r_btns.setSpacing(6)
        r_btns.addStretch(1)

        self._btn_res_save = QPushButton(t("widget.save_as"))
        self._btn_res_save.setObjectName("wm_res_save")
        self._btn_res_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_res_save.clicked.connect(self._save_last_output_as)
        r_btns.addWidget(self._btn_res_save)

        self._btn_res_folder = QPushButton(t("toast.open_folder"))
        self._btn_res_folder.setObjectName("wm_res_action")
        self._btn_res_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_res_folder.clicked.connect(self._open_last_output_folder)
        r_btns.addWidget(self._btn_res_folder)
        rv.addLayout(r_btns)

        f.addWidget(self._result_box)

        # ── 5. In-Window Apply Watermark Button (Always visible inside window) ─
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.addStretch(1)
        self.btn_apply = QPushButton(t("tool.watermark.btn"))
        self.btn_apply.setObjectName("btn_action_small")
        self.btn_apply.setIcon(svg_icon(SVG_STAMP, color="#FFFFFF", size=15))
        self.btn_apply.setIconSize(QSize(15, 15))
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.setFixedHeight(32)
        self.btn_apply.setMinimumWidth(136)
        self.btn_apply.clicked.connect(self._run)
        btn_row.addWidget(self.btn_apply)
        f.addLayout(btn_row)

        self.action_btn = self.btn_apply

        # Hidden compatibility widgets kept for external callers/auto_load contracts
        self.drop_in = DropFileEdit()
        self.drop_in._skip_window_track = True  # type: ignore[attr-defined]
        self.drop_in.setVisible(False)
        self.drop_wm = DropFileEdit()
        self.drop_wm._skip_window_track = True  # type: ignore[attr-defined]
        self.drop_wm.setVisible(False)
        self.drop_out = DropFileEdit("watermarked.pdf", save=True, default_name="watermarked.pdf")
        self.drop_out.setVisible(False)
        f.addStretch(1)

        self._compact_hidden = []
        self._apply_local_theme(is_dark())
        self._refresh_open_tabs()

    def _sync_base_opacity_from_slider(self, value: int) -> None:
        self.spin_base_opacity.blockSignals(True)
        self.spin_base_opacity.setValue(value)
        self.spin_base_opacity.blockSignals(False)

    def _sync_base_opacity_from_spin(self, value: int) -> None:
        self.slider_base_opacity.blockSignals(True)
        self.slider_base_opacity.setValue(value)
        self.slider_base_opacity.blockSignals(False)

    def _sync_stamp_opacity_from_slider(self, value: int) -> None:
        self.spin_opacity.blockSignals(True)
        self.spin_opacity.setValue(value)
        self.spin_opacity.blockSignals(False)

    def _sync_stamp_opacity_from_spin(self, value: int) -> None:
        self.slider_opacity.blockSignals(True)
        self.slider_opacity.setValue(value)
        self.slider_opacity.blockSignals(False)

    def _apply_local_theme(self, dark: bool) -> None:
        bg_card = BG_CARD if dark else _LC
        bg_inp = BG_INPUT if dark else _LI
        brd = BORDER if dark else _LO
        sec = TEXT_SEC if dark else _LQ
        pri = TEXT_PRI if dark else _LP
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0

        self._options_card.setStyleSheet(
            f"#wm_options_card {{ background: {bg_card}; border: 1px solid {brd}; border-radius: 8px; }}"
            f"#wm_options_card QLineEdit, #wm_options_card QComboBox, #wm_options_card QSpinBox {{"
            f" background: {bg_inp}; border: 1px solid {brd}; border-radius: 5px;"
            f" color: {pri}; padding: 2px 6px; min-height: 20px; font-size: 9.5pt; }}"
            f"#wm_options_card QLineEdit:focus, #wm_options_card QSpinBox:focus, #wm_options_card QComboBox:focus {{"
            f" border: 1px solid {ACCENT}; }}"
            f"#wm_options_card QCheckBox {{ font-size: 9.5pt; }}"
        )
        self._lbl_tip_icon.setPixmap(svg_pixmap(SVG_LIGHTBULB, color=ACCENT, size=14, dpr=dpr))
        self._lbl_tip.setStyleSheet(f"color: {sec}; font-size: 8.5pt; background: transparent;")

        res_bg = "#132E25" if dark else "#ECFDF5"
        res_brd = "#10B981" if dark else "#059669"
        res_txt = "#E2F8F0" if dark else "#064E3B"
        sec_btn_bg = "rgba(16, 185, 129, 0.14)" if dark else "rgba(5, 150, 105, 0.10)"
        sec_btn_hover = "rgba(16, 185, 129, 0.28)" if dark else "rgba(5, 150, 105, 0.22)"
        sec_btn_fg = "#A7F3D0" if dark else "#065F46"

        self._result_box.setStyleSheet(
            f"#wm_result_box {{ background: {res_bg}; border: 1px solid {res_brd}; border-radius: 8px; }}"
            f"#wm_result_box QLabel {{ color: {res_txt}; font-size: 9.2pt; font-weight: 500; background: transparent; border: none; }}"
            f"#wm_result_box QPushButton#wm_res_close {{ color: {sec_btn_fg}; border: none; background: transparent; padding: 0; border-radius: 4px; }}"
            f"#wm_result_box QPushButton#wm_res_close:hover {{ background: {sec_btn_hover}; }}"
            f"#wm_result_box QPushButton#wm_res_action {{ color: {sec_btn_fg}; background: {sec_btn_bg}; border: 1px solid {res_brd}; border-radius: 5px; font-size: 9pt; padding: 3px 10px; }}"
            f"#wm_result_box QPushButton#wm_res_action:hover {{ background: {sec_btn_hover}; }}"
            f"#wm_result_box QPushButton#wm_res_save {{ color: #FFFFFF; background: #10B981; border: none; border-radius: 5px; font-size: 9pt; font-weight: 600; padding: 4px 12px; }}"
            f"#wm_result_box QPushButton#wm_res_save:hover {{ background: #059669; }}"
        )
        self._lbl_res_icon.setPixmap(svg_pixmap(SVG_CHECK, color=res_brd, size=16, dpr=dpr))
        self._btn_res_close.setIcon(svg_icon(SVG_CLOSE, color=sec_btn_fg, size=12))

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        self._apply_local_theme(dark)
        self.base_card.update_theme(dark)
        self.wm_card.update_theme(dark)
        self._refresh_open_tabs()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_open_tabs()

    def set_compact_mode(self, active: bool, path: str = "") -> None:
        if active and path:
            self.auto_load(path)
        self._compact_active = active
        self._pipeline_active = False
        self._refresh_open_tabs()

    def _get_open_tab_entries(self) -> list[tuple[str, str, str, str]]:
        """Return [(display_label, current_path, orig_path, password), ...] for all open PDF tabs."""
        win: Any = self.window()
        viewers = getattr(win, "_viewers", None) or []
        entries: list[tuple[str, str, str, str]] = []
        raw_items: list[tuple[str, str, str, str]] = []
        name_counts: dict[str, int] = {}

        for v in viewers:
            curr_p = v.current_path() if hasattr(v, "current_path") else ""
            if not curr_p or not os.path.isfile(curr_p):
                continue
            orig_p = getattr(v, "_original_doc_path", "") or curr_p
            pwd = getattr(v, "_pdf_password", "") or ""
            bname = os.path.basename(orig_p)
            name_counts[bname] = name_counts.get(bname, 0) + 1
            raw_items.append((bname, curr_p, orig_p, pwd))

        for idx, (bname, curr_p, orig_p, pwd) in enumerate(raw_items, start=1):
            label = f"Tab {idx}: {bname}" if len(raw_items) > 1 or name_counts.get(bname, 0) > 1 else bname
            entries.append((label, curr_p, orig_p, pwd))
        return entries

    def _refresh_open_tabs(self) -> None:
        entries = self._get_open_tab_entries()
        self.base_card.populate_tabs(entries)
        self.wm_card.populate_tabs(entries)

    def _on_base_tab_selected(self, data: tuple[str, str, str]) -> None:
        curr_p, orig_p, pwd = data
        if curr_p and os.path.isfile(curr_p):
            self._pdf_password = pwd or ""
            self._base_orig_path = orig_p or curr_p
            self._load_input(curr_p, display_name=os.path.basename(self._base_orig_path))

    def _on_base_file_picked(self, path: str) -> None:
        if path and os.path.isfile(path):
            self._base_orig_path = path
            self._load_input(path)

    def _on_base_cleared(self) -> None:
        self._base_orig_path = ""
        self._pdf_password = ""
        self.drop_in.blockSignals(True)
        self.drop_in.clear()
        self.drop_in.blockSignals(False)
        self.base_card.set_info("")
        self._refresh_open_tabs()

    def _on_wm_tab_selected(self, data: tuple[str, str, str]) -> None:
        curr_p, orig_p, pwd = data
        if curr_p and os.path.isfile(curr_p):
            self._wm_password = pwd or ""
            self._wm_orig_path = orig_p or curr_p
            self.wm_card.set_path(curr_p, display_name=os.path.basename(self._wm_orig_path))
            self.drop_wm.blockSignals(True)
            self.drop_wm.set_path(curr_p, display_name=os.path.basename(self._wm_orig_path))
            self.drop_wm.blockSignals(False)
            self._probe_wm_info(curr_p)
            self._refresh_open_tabs()

    def _on_wm_file_picked(self, path: str) -> None:
        if path and os.path.isfile(path):
            self._wm_orig_path = path
            self._wm_password = ""
            self.wm_card.set_path(path)
            self.drop_wm.blockSignals(True)
            self.drop_wm.set_path(path)
            self.drop_wm.blockSignals(False)
            self._probe_wm_info(path)
            self._refresh_open_tabs()

    def _on_wm_cleared(self) -> None:
        self._wm_orig_path = ""
        self._wm_password = ""
        self.drop_wm.blockSignals(True)
        self.drop_wm.clear()
        self.drop_wm.blockSignals(False)
        self.wm_card.set_info("")
        self._refresh_open_tabs()

    def _on_match_toggled(self, checked: bool):
        self.spin_wm_page.setEnabled(not checked)

    def _probe_wm_info(self, wm_path: str) -> None:
        if not wm_path or not os.path.isfile(wm_path):
            self.wm_card.set_info("")
            return
        try:
            with fitz.open(wm_path) as d:
                if d.needs_pass and self._wm_password:
                    authenticate_fitz(d, self._wm_password)
                if not d.needs_pass:
                    self.spin_wm_page.setMaximum(max(1, d.page_count))
                    self.wm_card.set_info(t("edit.status.pages", n=d.page_count))
                else:
                    self.wm_card.set_info("Protected")
        except Exception as exc:
            _log.debug("Could not probe stamp PDF page count: %s", exc)
            self.wm_card.set_info("")

    def _load_input(self, p: str, display_name: str = ""):
        if not p:
            self._on_base_cleared()
            return
        disp = display_name or os.path.basename(self._base_orig_path or p)
        self.base_card.set_path(p, display_name=disp)
        self.drop_in.blockSignals(True)
        self.drop_in.set_path(p, display_name=disp)
        self.drop_in.blockSignals(False)
        if not self._maybe_prompt_password(p):
            self._on_base_cleared()
            return
        if not self._base_orig_path:
            self._base_orig_path = p
        if not self.drop_out.path():
            base, ext = os.path.splitext(self._base_orig_path)
            self.drop_out.set_path(base + "_watermark" + ext)
        try:
            r = self._open_reader(p)
            self.base_card.set_info(t("edit.status.pages", n=len(r.pages)))
        except Exception as e:
            self.base_card.set_info(t("tool.split.error_info", e=e))
        self._refresh_open_tabs()

    def auto_load(self, path: str):
        self._refresh_open_tabs()
        if not path or not os.path.isfile(path):
            return
        norm_p = os.path.abspath(path)
        if norm_p in self._generated_outputs:
            return
        if self.base_card.path() and os.path.isfile(self.base_card.path()):
            return
        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)
        orig_p = getattr(viewer, "_original_doc_path", "") if viewer else ""
        self._base_orig_path = orig_p or path
        self._load_input(path, display_name=os.path.basename(self._base_orig_path))

    def _prompt_watermark_password(self, wm_path: str) -> str | None:
        try:
            with fitz.open(wm_path) as d:
                if not d.needs_pass:
                    return ""
                if self._wm_password:
                    winner = authenticate_fitz(d, self._wm_password)
                    if winner is not None:
                        self._wm_password = winner
                        return winner
        except Exception:
            pass
        from app.utils import prompt_pdf_password
        ok, pwd = prompt_pdf_password(wm_path, self)
        if not ok:
            return None
        self._wm_password = pwd
        return pwd

    def _show_result_box(self, message: str, can_save: bool = True) -> None:
        self._lbl_res_msg.setText(message)
        self._btn_res_save.setVisible(can_save)
        self._result_box.setVisible(True)

    def _save_last_output_as(self) -> None:
        src_temp = self._last_output_temp
        if not src_temp or not os.path.isfile(src_temp):
            return
        suggested = self._last_suggested_path or os.path.join(DESKTOP, "watermarked.pdf")
        chosen_path, _ = QFileDialog.getSaveFileName(
            self, t("widget.save_as"), suggested, t("file_filter.pdf")
        )
        if not chosen_path:
            return

        win: Any = self.window()
        try:
            if win and hasattr(win, "_atomic_copy_file"):
                win._atomic_copy_file(src_temp, chosen_path)
            else:
                dst_dir = os.path.dirname(chosen_path) or "."
                fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=dst_dir)
                os.close(fd)
                try:
                    shutil.copyfile(src_temp, tmp)
                    os.replace(tmp, chosen_path)
                except Exception:
                    with contextlib.suppress(Exception):
                        os.unlink(tmp)
                    raise
        except Exception as exc:
            show_error(self, exc)
            return

        self._last_saved_path = chosen_path
        target_v = self._last_output_viewer
        if win and target_v is not None and target_v in getattr(win, "_viewers", []):
            with contextlib.suppress(Exception):
                win._cleanup_pipeline(id(target_v))
                target_v.load(chosen_path)
                tab_idx = win._viewers.index(target_v)
                win._tab_bar.setTabText(tab_idx, os.path.basename(chosen_path))
                win._tab_bar.setTabToolTip(tab_idx, chosen_path)
                win._update_tab_bar_visibility()
                win._update_breadcrumb()
        add_recent_file(chosen_path)
        self._show_result_box(f"Saved: {os.path.basename(chosen_path)}", can_save=False)
        self._status(f"Saved: {chosen_path}")

    def _open_last_output_folder(self) -> None:
        target = self._last_saved_path or self._last_output_temp
        if target and os.path.exists(target):
            if self._last_saved_path:
                reveal_file(target)
            else:
                open_folder(os.path.dirname(self._last_suggested_path) if self._last_suggested_path else target)

    def cleanup_pipeline(self) -> None:
        super().cleanup_pipeline()
        for d in self._wm_tmp_dirs:
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        self._wm_tmp_dirs.clear()

    def _run(self):
        pdf_path = self.base_card.path() or self.drop_in.path()
        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)
        if (not pdf_path or not os.path.isfile(pdf_path)) and viewer and viewer.current_path():
            pdf_path = viewer.current_path()
            self._base_orig_path = getattr(viewer, "_original_doc_path", "") or pdf_path

        wm_path = self.wm_card.path() or self.drop_wm.path()
        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.select_source"))
            return
        if not wm_path or not os.path.isfile(wm_path):
            QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.select_wm"))
            return

        wm_pwd = self._prompt_watermark_password(wm_path)
        if wm_pwd is None:
            return

        match_pages = self.chk_match_pages.isChecked()
        include_omitted_wm = self.chk_include_omitted_wm.isChecked()

        try:
            wm_doc = fitz.open(wm_path)
            try:
                if wm_doc.needs_pass:
                    if not wm_pwd or authenticate_fitz(wm_doc, wm_pwd) is None:
                        raise WrongPasswordError(t("tool.err.wrong_password"))
                if wm_doc.page_count <= 0:
                    QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.empty_wm"))
                    return
                wm_total = wm_doc.page_count
            finally:
                wm_doc.close()

            src_probe = self._open_fitz(pdf_path)
            try:
                if src_probe.page_count <= 0:
                    QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.empty_source"))
                    return
                base_total = src_probe.page_count
            finally:
                src_probe.close()

            expanding_base = bool(match_pages and wm_total > base_total)
            range_limit = max(wm_total, base_total) if (expanding_base or include_omitted_wm) else base_total
            txt = self.edit_pages.text().strip()
            targets = sorted(parse_pages(txt, range_limit)) if txt else list(range(range_limit))
            target_set = set(targets)
        except Exception as e:
            _log.debug("Watermark pre-flight failed: %s", traceback.format_exc())
            show_error(self, e)
            return

        # Write to an isolated temp PDF so the selected Base document is never modified
        base_orig = self._base_orig_path or pdf_path
        stem, _ = os.path.splitext(os.path.basename(base_orig))
        wm_filename = f"{stem}_watermarked.pdf"
        run_tmp_dir = tempfile.mkdtemp(prefix="PDF_Editor_wm_")
        self._wm_tmp_dirs.append(run_tmp_dir)
        out_path = os.path.join(run_tmp_dir, wm_filename)
        suggested_save_path = os.path.join(os.path.dirname(base_orig) or DESKTOP, wm_filename)

        over = self.cmb_layer.currentIndex() == 0
        base_opacity_pct = self.spin_base_opacity.value()
        base_opacity = max(0.0, min(1.0, base_opacity_pct / 100.0))
        stamp_opacity_pct = self.spin_opacity.value()
        stamp_opacity = max(0.0, min(1.0, stamp_opacity_pct / 100.0))
        transparent_white = self.chk_transparent_white.isChecked()
        single_wm_idx = min(max(0, self.spin_wm_page.value() - 1), wm_total - 1)
        pwd = self._pdf_password

        def _render_rgba_png(doc_obj: fitz.Document, p_idx: int, opacity_f: float, strip_white: bool) -> tuple[bytes, float, float]:
            pg = doc_obj[p_idx]
            pix = pg.get_pixmap(dpi=200, alpha=True)
            pil_img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
            r_ch, g_ch, b_ch, a_ch = pil_img.split()
            if strip_white:
                min_rgb = ImageChops.darker(ImageChops.darker(r_ch, g_ch), b_ch)
                non_white_mask = min_rgb.point(lambda v: 0 if v >= 242 else 255)
                a_ch = ImageChops.darker(a_ch, non_white_mask)
            if opacity_f < 0.999:
                a_ch = a_ch.point(lambda a: int(round(a * opacity_f)))
            pil_img.putalpha(a_ch)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue(), float(pix.width), float(pix.height)

        def _render_blended_white_png(doc_obj: fitz.Document, p_idx: int, opacity_f: float) -> bytes:
            pg = doc_obj[p_idx]
            pix = pg.get_pixmap(dpi=200, alpha=False)
            rgb_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            white_bg = Image.new("RGB", rgb_img.size, (255, 255, 255))
            blended = Image.blend(white_bg, rgb_img, opacity_f)
            buf = io.BytesIO()
            blended.save(buf, format="PNG")
            return buf.getvalue()

        def _fit_rect(pr: fitz.Rect, ww: float, wh: float) -> fitz.Rect:
            scale = min(pr.width / max(1.0, ww), pr.height / max(1.0, wh))
            fw, fh = ww * scale, wh * scale
            fx = pr.x0 + (pr.width - fw) / 2.0
            fy = pr.y0 + (pr.height - fh) / 2.0
            return fitz.Rect(fx, fy, fx + fw, fy + fh)

        def _compose_page(out_doc: fitz.Document, src_doc: fitz.Document, b_idx: int, wm_doc_obj: fitz.Document, wm_idx: int, wm_cache: dict, base_cache: dict) -> None:
            src_page = src_doc[b_idx]
            pr = src_page.rect
            new_pg = out_doc.new_page(width=pr.width, height=pr.height)

            if wm_idx not in wm_cache:
                wm_cache[wm_idx] = _render_rgba_png(wm_doc_obj, wm_idx, stamp_opacity, transparent_white)
            wm_png, ww, wh = wm_cache[wm_idx]
            wm_rect = _fit_rect(new_pg.rect, ww, wh)

            if base_opacity >= 0.999:
                new_pg.show_pdf_page(new_pg.rect, src_doc, b_idx)
                if stamp_opacity > 0.0:
                    new_pg.insert_image(wm_rect, stream=wm_png, overlay=over)
            else:
                if over:
                    if b_idx not in base_cache:
                        base_cache[b_idx] = _render_blended_white_png(src_doc, b_idx, base_opacity)
                    new_pg.insert_image(new_pg.rect, stream=base_cache[b_idx], overlay=True)
                    if stamp_opacity > 0.0:
                        new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
                else:
                    if b_idx not in base_cache:
                        base_cache[b_idx] = _render_rgba_png(src_doc, b_idx, base_opacity, transparent_white)[0]
                    if stamp_opacity > 0.0:
                        new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
                    if base_opacity > 0.0:
                        new_pg.insert_image(new_pg.rect, stream=base_cache[b_idx], overlay=True)

        def do_work(worker):
            wm_d = fitz.open(wm_path)
            src_d = fitz.open(pdf_path)
            out_d = fitz.open()
            try:
                if wm_d.needs_pass:
                    if not (wm_pwd and authenticate_fitz(wm_d, wm_pwd) is not None):
                        raise WrongPasswordError(t("tool.err.wrong_password"))
                if src_d.needs_pass:
                    if not (pwd and authenticate_fitz(src_d, pwd) is not None):
                        raise WrongPasswordError(t("tool.err.wrong_password"))

                wm_cache: dict[int, tuple[bytes, float, float]] = {}
                base_cache: dict[int, bytes] = {}

                if expanding_base:
                    if include_omitted_wm:
                        total_steps = max(1, wm_total)
                        composed_count = 0
                        for wm_idx in range(wm_total):
                            if worker.is_cancelled():
                                return None
                            if wm_idx in target_set:
                                b_src_idx = composed_count % src_d.page_count
                                composed_count += 1
                                _compose_page(out_d, src_d, b_src_idx, wm_d, wm_idx, wm_cache, base_cache)
                            else:
                                # Copy omitted watermark page without applying the base page
                                out_d.insert_pdf(wm_d, from_page=wm_idx, to_page=wm_idx)
                            worker.progress.emit(
                                int((wm_idx + 1) / total_steps * 100),
                                t("progress.watermark.page", current=wm_idx + 1, total=total_steps),
                            )
                    else:
                        total_steps = max(1, len(targets))
                        for step_idx, wm_idx in enumerate(targets):
                            if worker.is_cancelled():
                                return None
                            b_src_idx = step_idx % src_d.page_count
                            _compose_page(out_d, src_d, b_src_idx, wm_d, wm_idx, wm_cache, base_cache)
                            worker.progress.emit(
                                int((step_idx + 1) / total_steps * 100),
                                t("progress.watermark.page", current=step_idx + 1, total=total_steps),
                            )
                else:
                    n = src_d.page_count
                    total_iter = max(n, wm_total) if include_omitted_wm else n
                    for i in range(total_iter):
                        if worker.is_cancelled():
                            return None
                        if i < n and i in target_set:
                            wm_idx = (i % wm_total) if match_pages else single_wm_idx
                            _compose_page(out_d, src_d, i, wm_d, wm_idx, wm_cache, base_cache)
                        elif include_omitted_wm:
                            # Copy omitted watermark page directly without copying the base page
                            if i < wm_total:
                                out_d.insert_pdf(wm_d, from_page=i, to_page=i)
                        else:
                            if i < n:
                                out_d.insert_pdf(src_d, from_page=i, to_page=i)
                        worker.progress.emit(
                            int((i + 1) / max(1, total_iter) * 100),
                            t("progress.watermark.page", current=i + 1, total=total_iter),
                        )

                if worker.is_cancelled():
                    return None

                if out_d.page_count <= 0:
                    raise ValueError("No output pages were produced with the selected page range.")

                atomic_pdf_write(
                    out_d,
                    out_path,
                    sources=[pdf_path, wm_path],
                    save_opts={"garbage": 4, "deflate": True},
                    close_writer=True,
                )
            finally:
                with contextlib.suppress(Exception):
                    wm_d.close()
                with contextlib.suppress(Exception):
                    src_d.close()
                with contextlib.suppress(Exception):
                    if not getattr(out_d, "is_closed", False):
                        out_d.close()
            return out_path

        def on_done(saved):
            if not saved or not os.path.isfile(saved):
                return
            self._generated_outputs.add(os.path.abspath(saved))
            self._last_output_temp = saved
            self._last_suggested_path = suggested_save_path
            self._last_saved_path = ""
            self._last_output_viewer = None

            if win and hasattr(win, "_add_viewer_tab"):
                new_viewer = win._add_viewer_tab()
                new_tab_idx = win._viewers.index(new_viewer)
                new_viewer.load(saved, _is_history_step=True)
                new_viewer._original_doc_path = suggested_save_path
                win._pipeline_state[id(new_viewer)] = {
                    "original_path": suggested_save_path,
                    "temp_path": saved,
                }
                disp_name = os.path.basename(suggested_save_path)
                win._tab_bar.setTabText(new_tab_idx, disp_name)
                win._tab_bar.setTabToolTip(new_tab_idx, f"{suggested_save_path} (unsaved watermarked PDF)")
                new_viewer._name_lbl.setText(disp_name)
                win._update_tab_bar_visibility()
                win._update_breadcrumb()
                win._update_page_nav()
                self._last_output_viewer = new_viewer
                self._refresh_open_tabs()
                self._show_result_box("Opened watermarked PDF in a new tab — click Save As to keep.", can_save=True)
                self._status(f"Opened {disp_name} in a new tab")
            else:
                self._show_result_box(f"Created: {os.path.basename(saved)}", can_save=True)
                self._status(t("tool.watermark.status.done", name=os.path.basename(saved)))

        self._run_background(
            do_work,
            total=100,
            label=t("progress.watermark.applying"),
            on_done=on_done,
        )
