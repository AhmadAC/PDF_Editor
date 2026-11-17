# app/tools/crop_widgets.py
"""Qt UI sub-widgets and group boxes for TabCortar."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.constants import ACCENT, ACCENT_H, ACCENT_P, TEXT_PRI, TEXT_SEC
from app.i18n import t
from app.utils import (
    SVG_CHECK,
    SVG_HISTORY,
    SVG_LIGHTBULB,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    svg_icon,
    svg_pixmap,
)


class FocusSpinBox(QSpinBox):
    """QSpinBox that ignores mouse wheel events unless it explicitly has keyboard focus."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if self.lineEdit():
            self.lineEdit().setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        has_focus = self.hasFocus() or (self.lineEdit() and self.lineEdit().hasFocus())
        if has_focus:
            super().wheelEvent(event)
        else:
            event.ignore()


class FocusComboBox(QComboBox):
    """QComboBox that ignores mouse wheel events unless it explicitly has keyboard focus."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class CropOptionsGroup(QGroupBox):
    """Page range and odd/even subset controls for TabCortar."""

    def __init__(self, on_mode_changed, on_controls_changed, parent=None):
        super().__init__(t("tool.crop.options"), parent)
        form_opts = QFormLayout(self)
        form_opts.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cmb_page_mode = FocusComboBox()
        self.cmb_page_mode.addItems([
            t("tool.crop.pages_all"),
            t("tool.crop.pages_current"),
            t("tool.crop.pages_custom"),
        ])
        self.cmb_page_mode.currentIndexChanged.connect(on_mode_changed)
        form_opts.addRow(t("tool.crop.pages_label"), self.cmb_page_mode)

        self.spin_current_page = FocusSpinBox()
        self.spin_current_page.setRange(1, 99999)
        self.spin_current_page.setValue(1)
        self.spin_current_page.valueChanged.connect(on_controls_changed)
        self.spin_current_page.setVisible(False)
        form_opts.addRow("", self.spin_current_page)

        self.edit_custom_pages = QLineEdit()
        self.edit_custom_pages.setPlaceholderText("e.g.: 1,3,5-8")
        self.edit_custom_pages.textChanged.connect(on_controls_changed)
        self.edit_custom_pages.setVisible(False)
        form_opts.addRow("", self.edit_custom_pages)

        self.cmb_subset = FocusComboBox()
        self.cmb_subset.addItems([
            t("tool.crop.subset_all"),
            t("tool.crop.subset_odd"),
            t("tool.crop.subset_even"),
        ])
        self.cmb_subset.currentIndexChanged.connect(on_controls_changed)
        form_opts.addRow(t("tool.crop.subset_label"), self.cmb_subset)


class CropMarginsGroup(QGroupBox):
    """Margins grid, dimension readout, preview/undo/redo, and quick trim presets."""

    def __init__(
        self,
        on_controls_changed,
        on_apply_preview,
        on_undo,
        on_redo,
        on_reset,
        on_trim5,
        on_trim10,
        on_trim_half_inch,
        dpr: float = 1.0,
        parent=None,
    ):
        super().__init__(t("tool.crop.margins"), parent)
        v_margins = QVBoxLayout(self)
        v_margins.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)

        def _make_spin():
            sb = FocusSpinBox()
            sb.setRange(0, 5000)
            sb.setValue(0)
            sb.setSuffix(" pt")
            sb.valueChanged.connect(on_controls_changed)
            return sb

        self.spin_top = _make_spin()
        self.spin_bottom = _make_spin()
        self.spin_left = _make_spin()
        self.spin_right = _make_spin()

        grid.addWidget(QLabel(t("tool.crop.top")), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.spin_top, 0, 1)
        grid.addWidget(QLabel(t("tool.crop.bottom")), 0, 2, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.spin_bottom, 0, 3)

        grid.addWidget(QLabel(t("tool.crop.left")), 1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.spin_left, 1, 1)
        grid.addWidget(QLabel(t("tool.crop.right")), 1, 2, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.spin_right, 1, 3)
        v_margins.addLayout(grid)

        self.lbl_dimensions = QLabel("")
        self.lbl_dimensions.setStyleSheet(f"font-weight: 600; color: {ACCENT}; padding: 2px;")
        v_margins.addWidget(self.lbl_dimensions)

        self.btn_apply_crop = QPushButton(t("tool.crop.apply_preview"))
        self.btn_apply_crop.setIcon(svg_icon(SVG_CHECK, color="#FFFFFF", size=15))
        self.btn_apply_crop.setIconSize(QSize(15, 15))
        self.btn_apply_crop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_crop.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: white; font-weight: bold; padding: 8px 14px; border-radius: 6px; border: none; font-size: 11pt; }}"
            f"QPushButton:hover {{ background: {ACCENT_H}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_P}; }}"
        )
        self.btn_apply_crop.clicked.connect(on_apply_preview)
        v_margins.addWidget(self.btn_apply_crop)

        undo_redo_row = QHBoxLayout()
        undo_redo_row.setSpacing(6)

        self.btn_undo = QPushButton(t("btn.undo"))
        self.btn_undo.setIcon(svg_icon(SVG_ROTATE_LEFT, color=TEXT_PRI, size=14))
        self.btn_undo.setIconSize(QSize(14, 14))
        self.btn_undo.setToolTip("Ctrl+Z")
        self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo.clicked.connect(on_undo)
        self.btn_undo.setEnabled(False)

        self.btn_redo = QPushButton(t("btn.redo"))
        self.btn_redo.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=TEXT_PRI, size=14))
        self.btn_redo.setIconSize(QSize(14, 14))
        self.btn_redo.setToolTip("Ctrl+Y")
        self.btn_redo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_redo.clicked.connect(on_redo)
        self.btn_redo.setEnabled(False)

        undo_redo_row.addWidget(self.btn_undo)
        undo_redo_row.addWidget(self.btn_redo)
        v_margins.addLayout(undo_redo_row)

        btn_presets = QHBoxLayout()
        btn_presets.setSpacing(4)

        self.btn_reset = QPushButton(t("tool.crop.reset"))
        self.btn_reset.setIcon(svg_icon(SVG_HISTORY, color=TEXT_PRI, size=14))
        self.btn_reset.setIconSize(QSize(14, 14))
        self.btn_reset.clicked.connect(on_reset)
        btn_presets.addWidget(self.btn_reset)

        self.btn_trim5 = QPushButton(t("tool.crop.trim_5"))
        self.btn_trim5.clicked.connect(on_trim5)
        btn_presets.addWidget(self.btn_trim5)

        self.btn_trim10 = QPushButton(t("tool.crop.trim_10"))
        self.btn_trim10.clicked.connect(on_trim10)
        btn_presets.addWidget(self.btn_trim10)

        self.btn_trim_in = QPushButton(t("tool.crop.trim_half_in"))
        self.btn_trim_in.clicked.connect(on_trim_half_inch)
        btn_presets.addWidget(self.btn_trim_in)
        v_margins.addLayout(btn_presets)

        tip_row = QHBoxLayout()
        tip_row.setContentsMargins(0, 2, 0, 0)
        tip_row.setSpacing(6)
        self.lbl_tip_icon = QLabel()
        self.lbl_tip_icon.setPixmap(svg_pixmap(SVG_LIGHTBULB, color=ACCENT, size=15, dpr=dpr))
        self.lbl_tip_icon.setFixedSize(15, 15)
        self.lbl_hint = QLabel(t("tool.crop.drag_hint"))
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(f"color: {TEXT_SEC}; font-size: 10pt;")
        tip_row.addWidget(self.lbl_tip_icon, 0, Qt.AlignmentFlag.AlignTop)
        tip_row.addWidget(self.lbl_hint, 1)
        v_margins.addLayout(tip_row)