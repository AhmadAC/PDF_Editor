

# app/selection_pane.py
"""PDF_Editor – Selection Pane: inspector window for object layer order, opacity, locking, visibility, and renaming."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import ACCENT, ACCENT_H, BG_CARD, BG_INPUT, BORDER, TEXT_PRI, TEXT_SEC, _LP, _LQ
from app.editor.canvas_overlay1 import snapshot_overlay
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CHECK,
    SVG_CLONE,
    SVG_CLOSE,
    SVG_COPY,
    SVG_EDIT,
    SVG_ERASER,
    SVG_EYE,
    SVG_FILE_PDF,
    SVG_FONT,
    SVG_HIGHLIGHTER,
    SVG_IMAGE,
    SVG_LOCK,
    SVG_PEN,
    SVG_REFRESH,
    SVG_SIGNATURE,
    SVG_STICKY_NOTE,
    SVG_SWAP,
    SVG_SYNC,
    SVG_TRASH,
    SVG_VECTOR_SQUARE,
    is_dark,
    svg_icon,
)

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    from app.editor.tab import TabEditar


_ICON_BY_TYPE = {
    "text": SVG_FONT,
    "text_edit": SVG_FONT,
    "image": SVG_IMAGE,
    "signature": SVG_SIGNATURE,
    "drawing": SVG_VECTOR_SQUARE,
    "check": SVG_CHECK,
    "cross": SVG_CLOSE,
    "highlight": SVG_HIGHLIGHTER,
    "redact": SVG_ERASER,
    "note": SVG_STICKY_NOTE,
    "draw": SVG_PEN,
}


class _SelectionRowWidget(QWidget):
    """Custom row widget inside Selection Pane with layer badge, icon, rename line edit, opacity spinbox, lock, and eye toggle."""

    visibility_toggled = Signal(bool)
    lock_toggled = Signal(bool)
    opacity_changed = Signal(float)
    name_changed = Signal(str)

    def __init__(
        self,
        layer_num: int,
        overlay: dict[str, Any],
        dark: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.overlay = overlay
        self._dark = dark

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        fg = TEXT_PRI if dark else "#1A2B28"
        sec = TEXT_SEC if dark else "#6B7280"
        brd = BORDER if dark else "#D1D5DB"
        inp_bg = BG_INPUT if dark else "#FFFFFF"

        # 1. Layer order number badge (#1, #2, etc.)
        self.lbl_layer = QLabel(f"#{layer_num}")
        self.lbl_layer.setFixedWidth(28)
        self.lbl_layer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_layer.setStyleSheet(
            f"background: rgba(0, 120, 212, 0.18); color: {ACCENT}; font-size: 8.5pt; "
            f"font-weight: 700; border-radius: 4px; padding: 2px 0;"
        )
        layout.addWidget(self.lbl_layer)

        # 2. Type icon
        etype = overlay.get("type", "drawing")
        svg_src = _ICON_BY_TYPE.get(etype, SVG_FILE_PDF)
        ico_lbl = QLabel()
        ico_lbl.setPixmap(svg_icon(svg_src, color=ACCENT if not overlay.get("_deleted") else "#9CA3AF", size=15).pixmap(15, 15))
        ico_lbl.setFixedSize(16, 16)
        layout.addWidget(ico_lbl)

        # 3. Editable Name Label
        self.name_edit = QLineEdit(self._default_friendly_name(overlay))
        self.name_edit.setReadOnly(True)
        self.name_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.name_edit.setStyleSheet("border: none; background: transparent; color: " + fg + "; font-size: 9.5pt;")
        self.name_edit.editingFinished.connect(self._on_name_editing_finished)
        layout.addWidget(self.name_edit, 1)

        # 4. Opacity SpinBox
        op_val = int(round(float(overlay.get("opacity", 1.0)) * 100))
        self.spin_opacity = QSpinBox()
        self.spin_opacity.setRange(0, 100)
        self.spin_opacity.setSuffix("%")
        self.spin_opacity.setValue(op_val)
        self.spin_opacity.setFixedWidth(56)
        self.spin_opacity.setFixedHeight(22)
        self.spin_opacity.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin_opacity.setStyleSheet(
            f"QSpinBox {{ background: {inp_bg}; color: {fg}; border: 1px solid {brd}; "
            f"border-radius: 4px; font-size: 8.5pt; padding: 0 2px; }}"
            f"QSpinBox:focus {{ border-color: {ACCENT}; }}"
        )
        self.spin_opacity.valueChanged.connect(lambda v: self.opacity_changed.emit(v / 100.0))
        layout.addWidget(self.spin_opacity)

        # 5. Lock button
        is_locked = bool(overlay.get("_locked", False))
        self.btn_lock = QPushButton()
        self.btn_lock.setFixedSize(22, 22)
        self.btn_lock.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_lock.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_lock_icon(is_locked)
        self.btn_lock.clicked.connect(self._on_lock_clicked)
        layout.addWidget(self.btn_lock)

        # 6. Visibility button
        is_vis = bool(overlay.get("_visible", True))
        self.btn_vis = QPushButton()
        self.btn_vis.setFixedSize(22, 22)
        self.btn_vis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_vis.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_vis_icon(is_vis)
        self.btn_vis.clicked.connect(self._on_vis_clicked)
        layout.addWidget(self.btn_vis)

    @staticmethod
    def _default_friendly_name(ov: dict) -> str:
        if ov.get("_name"):
            return str(ov["_name"])
        etype = ov.get("type", "object")
        if etype == "text":
            txt = (ov.get("text") or "").strip()
            return f"Text: '{txt[:16]}...'" if len(txt) > 16 else (f"Text: '{txt}'" if txt else "Text Box")
        if etype == "text_edit":
            txt = (ov.get("new_text") or ov.get("old_text") or "").strip()
            return f"Text Edit: '{txt[:16]}...'" if len(txt) > 16 else (f"Text Edit: '{txt}'" if txt else "Text Edit")
        if etype == "image":
            path = ov.get("path", "")
            base = os.path.basename(path) if path else "Image"
            return f"Image: {base}" if ov.get("_existing") else f"Image: {base}"
        if etype == "signature":
            return "Digital Signature"
        if etype == "drawing":
            items = ov.get("items", [])
            return f"Vector Shape ({len(items)} paths)" if items else "Vector Shape"
        if etype == "check":
            return "Checkmark Stamp"
        if etype == "cross":
            return "Crossmark Stamp"
        if etype == "highlight":
            return "Highlight Rect"
        if etype == "redact":
            return "Redaction Box"
        if etype == "note":
            txt = (ov.get("text") or "").strip()
            return f"Note: '{txt[:14]}...'" if len(txt) > 14 else (f"Note: '{txt}'" if txt else "Sticky Note")
        if etype == "draw":
            pts = ov.get("points", [])
            return f"Pen Stroke ({len(pts)} pts)"
        return etype.title()

    def _update_lock_icon(self, locked: bool) -> None:
        col = "#F59E0B" if locked else (TEXT_SEC if self._dark else _LQ)
        self.btn_lock.setIcon(svg_icon(SVG_LOCK, color=col, size=13))
        self.btn_lock.setToolTip("Locked (Cannot move/delete)" if locked else "Unlocked")
        self.btn_lock.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")

    def _update_vis_icon(self, visible: bool) -> None:
        col = ACCENT if visible else ("#EF4444" if self._dark else "#DC2626")
        self.btn_vis.setIcon(svg_icon(SVG_EYE if visible else SVG_CLOSE, color=col, size=14))
        self.btn_vis.setToolTip("Visible" if visible else "Hidden")
        self.btn_vis.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")

    def _on_lock_clicked(self) -> None:
        new_val = not bool(self.overlay.get("_locked", False))
        self.overlay["_locked"] = new_val
        self._update_lock_icon(new_val)
        self.lock_toggled.emit(new_val)

    def _on_vis_clicked(self) -> None:
        new_val = not bool(self.overlay.get("_visible", True))
        self.overlay["_visible"] = new_val
        self._update_vis_icon(new_val)
        self.visibility_toggled.emit(new_val)

    def start_editing(self) -> None:
        self.name_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.name_edit.setReadOnly(False)
        self.name_edit.selectAll()
        self.name_edit.setFocus()
        self.name_edit.setStyleSheet(
            f"border: 1px solid {ACCENT}; background: {'#262626' if self._dark else '#FFFFFF'}; "
            f"color: {'#FFFFFF' if self._dark else '#000000'}; border-radius: 3px;"
        )

    def _on_name_editing_finished(self) -> None:
        self.name_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.name_edit.setReadOnly(True)
        fg = TEXT_PRI if self._dark else "#1A2B28"
        self.name_edit.setStyleSheet("border: none; background: transparent; color: " + fg + "; font-size: 9.5pt;")
        new_name = self.name_edit.text().strip()
        if new_name:
            self.overlay["_name"] = new_name
            self.name_changed.emit(new_name)

    def mousePressEvent(self, event: QMouseEvent):
        event.ignore()


class SelectionPane(QDialog):
    """Floating inspector window for PDF objects on the active page.
    Allows viewing all objects, reordering z-layers, adjusting opacity, locking, hiding, and renaming.
    """

    def __init__(self, tab_editar: TabEditar, parent=None):
        super().__init__(parent or tab_editar)
        self.tab = tab_editar
        self.canvas: PdfEditCanvas = tab_editar._canvas

        self.setWindowTitle("Selection Pane — Objects & Layers")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(360, 500)
        self.resize(380, 560)

        self._dark = is_dark()
        self._page_idx = getattr(self.canvas, "_page_idx", 0)
        self._is_syncing_selection = False

        self._build_ui()
        self._apply_theme()
        self._connect_signals()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Top Bar: Page selector & counter ─────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_pg = QLabel("Page:")
        lbl_pg.setStyleSheet(f"font-weight: 600; color: {TEXT_PRI if self._dark else _LP};")
        top_bar.addWidget(lbl_pg)

        self.spin_page = QSpinBox()
        total_p = self.canvas.page_count() or 1
        self.spin_page.setRange(1, max(1, total_p))
        self.spin_page.setValue(self._page_idx + 1)
        self.spin_page.setFixedWidth(64)
        self.spin_page.valueChanged.connect(self._on_page_changed)
        top_bar.addWidget(self.spin_page)

        self.lbl_count = QLabel("0 objects")
        self.lbl_count.setStyleSheet(f"color: {TEXT_SEC if self._dark else _LQ}; font-size: 9pt;")
        top_bar.addWidget(self.lbl_count)
        top_bar.addStretch(1)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(svg_icon(SVG_REFRESH, color=ACCENT, size=14))
        self.btn_refresh.setFixedSize(26, 26)
        self.btn_refresh.setToolTip("Refresh objects on page")
        self.btn_refresh.clicked.connect(self.refresh)
        top_bar.addWidget(self.btn_refresh)
        root.addLayout(top_bar)

        # ── Main Objects List Widget ─────────────────────────────────────
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemSelectionChanged.connect(self._on_list_selection_changed)
        root.addWidget(self.list_widget, 1)

        # ── Action Buttons: Arrange & Delete ─────────────────────────────
        act_row1 = QHBoxLayout()
        act_row1.setSpacing(4)

        self.btn_front = QPushButton("Bring to Front")
        self.btn_front.setIcon(svg_icon(SVG_ARROW_UP, color=ACCENT, size=14))
        self.btn_front.clicked.connect(lambda: self._arrange_selected("front"))
        act_row1.addWidget(self.btn_front)

        self.btn_fwd = QPushButton("Forward")
        self.btn_fwd.setIcon(svg_icon(SVG_ARROW_UP, color=TEXT_SEC, size=13))
        self.btn_fwd.clicked.connect(lambda: self._arrange_selected("forward"))
        act_row1.addWidget(self.btn_fwd)

        self.btn_bwd = QPushButton("Backward")
        self.btn_bwd.setIcon(svg_icon(SVG_ARROW_DOWN, color=TEXT_SEC, size=13))
        self.btn_bwd.clicked.connect(lambda: self._arrange_selected("backward"))
        act_row1.addWidget(self.btn_bwd)

        self.btn_back = QPushButton("Send to Back")
        self.btn_back.setIcon(svg_icon(SVG_ARROW_DOWN, color=ACCENT, size=14))
        self.btn_back.clicked.connect(lambda: self._arrange_selected("back"))
        act_row1.addWidget(self.btn_back)
        root.addLayout(act_row1)

        act_row2 = QHBoxLayout()
        act_row2.setSpacing(6)

        self.btn_rename = QPushButton("Rename")
        self.btn_rename.setIcon(svg_icon(SVG_EDIT, color=ACCENT, size=14))
        self.btn_rename.clicked.connect(self._rename_selected)
        act_row2.addWidget(self.btn_rename)

        self.btn_delete = QPushButton("Delete Object")
        self.btn_delete.setIcon(svg_icon(SVG_TRASH, color="#EF4444", size=14))
        self.btn_delete.clicked.connect(self._delete_selected)
        act_row2.addWidget(self.btn_delete)
        root.addLayout(act_row2)

    def _apply_theme(self):
        dark = self._dark
        bg_card = BG_CARD if dark else "#FFFFFF"
        bg_inp = BG_INPUT if dark else "#F8FAFC"
        brd = BORDER if dark else "#D1D5DB"
        fg = TEXT_PRI if dark else "#1A2B28"

        self.setStyleSheet(
            f"QDialog {{ background-color: {bg_card}; color: {fg}; }}"
            f"QListWidget {{ background-color: {bg_inp}; border: 1px solid {brd}; "
            f"border-radius: 6px; outline: none; padding: 2px; }}"
            f"QListWidget::item {{ border-radius: 5px; margin: 1px 2px; }}"
            f"QListWidget::item:selected {{ background-color: {'#264F78' if dark else '#D6E8FA'}; border: 1px solid {ACCENT}; }}"
            f"QListWidget::item:hover:!selected {{ background-color: {'#333333' if dark else '#F1F5F9'}; }}"
            f"QPushButton {{ background-color: {'#3C3F41' if dark else '#FFFFFF'}; border: 1px solid {brd}; "
            f"border-radius: 5px; padding: 4px 8px; font-size: 9pt; color: {fg}; }}"
            f"QPushButton:hover {{ background-color: {'#4F5254' if dark else '#F1F5F9'}; border-color: {ACCENT}; }}"
        )

    def _connect_signals(self):
        self.canvas.overlay_changed.connect(self.refresh)
        self.canvas.overlay_committed.connect(lambda _: self.refresh())
        self.canvas.overlay_deleted.connect(lambda _i, _o: self.refresh())
        if hasattr(self.canvas, "zoom_changed"):
            self.canvas.zoom_changed.connect(self._sync_page_from_canvas)

    def _sync_page_from_canvas(self):
        cur_p = getattr(self.canvas, "_page_idx", 0)
        if cur_p != self._page_idx:
            self._page_idx = cur_p
            self.spin_page.blockSignals(True)
            self.spin_page.setValue(cur_p + 1)
            self.spin_page.blockSignals(False)
            self.refresh()

    def _on_page_changed(self, page_1based: int):
        self._page_idx = max(0, page_1based - 1)
        self.canvas.set_page(self._page_idx)
        if hasattr(self.tab, "_scroll_to"):
            self.tab._scroll_to(self._page_idx)
        self.refresh()

    def refresh(self):
        """Rebuild the object list for the current page in visual z-order (top-to-bottom)."""
        if self._is_syncing_selection:
            return

        self._sync_page_from_canvas()

        total_p = self.canvas.page_count() or 1
        self.spin_page.setMaximum(max(1, total_p))

        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        overlays = getattr(self.canvas, "_overlays", [])
        page_items: list[tuple[int, dict[str, Any]]] = [
            (idx, ov)
            for idx, ov in enumerate(overlays)
            if ov.get("page", 0) == self._page_idx and not ov.get("_deleted")
        ]

        self.lbl_count.setText(f"{len(page_items)} object{'s' if len(page_items) != 1 else ''}")

        selected_row = -1
        canvas_sel_idx = getattr(self.canvas, "_selected_overlay_idx", -1)

        # Show in front-to-back order (highest index = top-most layer)
        for visual_order, (global_idx, ov) in enumerate(reversed(page_items), 1):
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(0, 36))
            item.setData(Qt.ItemDataRole.UserRole, global_idx)

            row_widget = _SelectionRowWidget(
                layer_num=visual_order,
                overlay=ov,
                dark=self._dark,
            )
            row_widget.opacity_changed.connect(lambda val, g_idx=global_idx: self._set_object_opacity(g_idx, val))
            row_widget.visibility_toggled.connect(lambda vis, g_idx=global_idx: self._toggle_object_visibility(g_idx, vis))
            row_widget.lock_toggled.connect(lambda lck, g_idx=global_idx: self._toggle_object_lock(g_idx, lck))
            row_widget.name_changed.connect(lambda name, g_idx=global_idx: self._rename_object(g_idx, name))

            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row_widget)

            if global_idx == canvas_sel_idx:
                selected_row = self.list_widget.count() - 1

        if selected_row >= 0:
            self.list_widget.setCurrentRow(selected_row)

        self.list_widget.blockSignals(False)

    def _set_object_opacity(self, global_idx: int, opacity: float):
        self.canvas.set_selected_overlay_opacity(opacity, idx=global_idx)

    def _toggle_object_visibility(self, global_idx: int, visible: bool):
        overlays = getattr(self.canvas, "_overlays", [])
        if 0 <= global_idx < len(overlays):
            ov = overlays[global_idx]
            ov["_visible"] = visible
            if ov.get("_existing"):
                ov["_dirty"] = True
            self.canvas.overlay_changed.emit()
            if hasattr(self.canvas, "refresh_pages"):
                self.canvas.refresh_pages({ov.get("page", 0)})
            self.canvas.update()

    def _toggle_object_lock(self, global_idx: int, locked: bool):
        overlays = getattr(self.canvas, "_overlays", [])
        if 0 <= global_idx < len(overlays):
            ov = overlays[global_idx]
            ov["_locked"] = locked
            self.canvas.update()

    def _rename_object(self, global_idx: int, new_name: str):
        overlays = getattr(self.canvas, "_overlays", [])
        if 0 <= global_idx < len(overlays):
            ov = overlays[global_idx]
            ov["_name"] = new_name
            self.canvas.overlay_changed.emit()

    def _on_list_selection_changed(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        global_idx = item.data(Qt.ItemDataRole.UserRole)
        if global_idx is not None and 0 <= global_idx < len(self.canvas._overlays):
            self._is_syncing_selection = True
            self.canvas._selected_overlay_idx = global_idx
            self.canvas.update()
            self._is_syncing_selection = False

    def _arrange_selected(self, mode: str):
        item = self.list_widget.currentItem()
        if not item:
            return
        global_idx = item.data(Qt.ItemDataRole.UserRole)
        if global_idx is not None:
            self.canvas.arrange_selected_overlay(mode, idx=global_idx)
            self.refresh()

    def _rename_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, _SelectionRowWidget):
            widget.start_editing()

    def _delete_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        global_idx = item.data(Qt.ItemDataRole.UserRole)
        if global_idx is not None and 0 <= global_idx < len(self.canvas._overlays):
            ov = self.canvas._overlays[global_idx]
            if ov.get("_locked"):
                QMessageBox.warning(self, "Object Locked", "This object is locked. Unlock it before deleting.")
                return
            self.canvas._selected_overlay_idx = global_idx
            self.canvas.delete_selected_overlay()
            self.refresh()

    def _show_context_menu(self, pos: QPoint):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        self.list_widget.setCurrentItem(item)
        global_idx = item.data(Qt.ItemDataRole.UserRole)
        if global_idx is None or not (0 <= global_idx < len(self.canvas._overlays)):
            return

        ov = self.canvas._overlays[global_idx]
        menu = QMenu(self)

        act_rename = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=14), "Rename")
        act_rename.triggered.connect(self._rename_selected)
        menu.addSeparator()

        act_front = menu.addAction(svg_icon(SVG_ARROW_UP, color=ACCENT, size=14), "Bring to Front")
        act_front.triggered.connect(lambda: self._arrange_selected("front"))
        act_fwd = menu.addAction(svg_icon(SVG_ARROW_UP, color=TEXT_SEC, size=14), "Bring Forward")
        act_fwd.triggered.connect(lambda: self._arrange_selected("forward"))
        act_bwd = menu.addAction(svg_icon(SVG_ARROW_DOWN, color=TEXT_SEC, size=14), "Send Backward")
        act_bwd.triggered.connect(lambda: self._arrange_selected("backward"))
        act_back = menu.addAction(svg_icon(SVG_ARROW_DOWN, color=ACCENT, size=14), "Send to Back")
        act_back.triggered.connect(lambda: self._arrange_selected("back"))
        menu.addSeparator()

        is_locked = bool(ov.get("_locked", False))
        act_lock = menu.addAction(svg_icon(SVG_LOCK, color="#F59E0B" if is_locked else TEXT_SEC, size=14), "Unlock" if is_locked else "Lock")
        act_lock.triggered.connect(lambda: self._toggle_object_lock(global_idx, not is_locked))

        is_vis = bool(ov.get("_visible", True))
        act_vis = menu.addAction(svg_icon(SVG_EYE if is_vis else SVG_CLOSE, color=ACCENT if is_vis else "#EF4444", size=14), "Hide" if is_vis else "Show")
        act_vis.triggered.connect(lambda: self._toggle_object_visibility(global_idx, not is_vis))
        menu.addSeparator()

        act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=14), "Delete")
        act_del.triggered.connect(self._delete_selected)

        menu.exec(self.list_widget.mapToGlobal(pos))
