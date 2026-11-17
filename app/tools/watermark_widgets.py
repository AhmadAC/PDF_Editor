# app/tools/watermark_widgets.py
"""Reusable Qt sub-widgets for TabMarcaDagua."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

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
from app.i18n import t
from app.utils import (
    SVG_CHECK,
    SVG_CLOSE,
    SVG_FILE_PDF,
    SVG_FOLDER,
    SVG_TAB,
    is_dark,
    svg_icon,
    svg_pixmap,
)
from app.widgets import FocusComboBox, FocusSpinBox


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


class _WatermarkResultBox(QFrame):
    """In-window green notification banner with Save As and Open Folder actions."""

    save_requested = Signal()
    folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wm_result_box")
        self.setVisible(False)

        rv = QVBoxLayout(self)
        rv.setContentsMargins(10, 7, 10, 7)
        rv.setSpacing(5)

        r_top = QHBoxLayout()
        r_top.setContentsMargins(0, 0, 0, 0)
        r_top.setSpacing(8)

        self.lbl_icon = QLabel()
        self.lbl_icon.setFixedSize(16, 16)
        r_top.addWidget(self.lbl_icon, 0, Qt.AlignmentFlag.AlignTop)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        r_top.addWidget(self.lbl_msg, 1)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("wm_res_close")
        self.btn_close.setFixedSize(20, 20)
        self.btn_close.setIconSize(QSize(12, 12))
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(lambda: self.setVisible(False))
        r_top.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        rv.addLayout(r_top)

        r_btns = QHBoxLayout()
        r_btns.setContentsMargins(0, 0, 0, 0)
        r_btns.setSpacing(6)
        r_btns.addStretch(1)

        self.btn_save = QPushButton(t("widget.save_as"))
        self.btn_save.setObjectName("wm_res_save")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_requested)
        r_btns.addWidget(self.btn_save)

        self.btn_folder = QPushButton(t("toast.open_folder"))
        self.btn_folder.setObjectName("wm_res_action")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(self.folder_requested)
        r_btns.addWidget(self.btn_folder)
        rv.addLayout(r_btns)

    def show_message(self, message: str, can_save: bool = True) -> None:
        self.lbl_msg.setText(message)
        self.btn_save.setVisible(can_save)
        self.setVisible(True)

    def update_theme(self, dark: bool, dpr: float = 1.0) -> None:
        res_bg = "#132E25" if dark else "#ECFDF5"
        res_brd = "#10B981" if dark else "#059669"
        res_txt = "#E2F8F0" if dark else "#064E3B"
        sec_bg = "rgba(16, 185, 129, 0.14)" if dark else "rgba(5, 150, 105, 0.10)"
        sec_hover = "rgba(16, 185, 129, 0.28)" if dark else "rgba(5, 150, 105, 0.22)"
        sec_fg = "#A7F3D0" if dark else "#065F46"

        self.setStyleSheet(
            f"#wm_result_box {{ background: {res_bg}; border: 1px solid {res_brd}; border-radius: 8px; }}"
            f"#wm_result_box QLabel {{ color: {res_txt}; font-size: 9.2pt; font-weight: 500; background: transparent; border: none; }}"
            f"#wm_result_box QPushButton#wm_res_close {{ color: {sec_fg}; border: none; background: transparent; padding: 0; border-radius: 4px; }}"
            f"#wm_result_box QPushButton#wm_res_close:hover {{ background: {sec_hover}; }}"
            f"#wm_result_box QPushButton#wm_res_action {{ color: {sec_fg}; background: {sec_bg}; border: 1px solid {res_brd}; border-radius: 5px; font-size: 9pt; padding: 3px 10px; }}"
            f"#wm_result_box QPushButton#wm_res_action:hover {{ background: {sec_hover}; }}"
            f"#wm_result_box QPushButton#wm_res_save {{ color: #FFFFFF; background: #10B981; border: none; border-radius: 5px; font-size: 9pt; font-weight: 600; padding: 4px 12px; }}"
            f"#wm_result_box QPushButton#wm_res_save:hover {{ background: #059669; }}"
        )
        self.lbl_icon.setPixmap(svg_pixmap(SVG_CHECK, color=res_brd, size=16, dpr=dpr))
        self.btn_close.setIcon(svg_icon(SVG_CLOSE, color=sec_fg, size=12))