
# app/selection_pane.py
"""PDF_Editor – Selection Pane: inspector window for object layer order, grouping/ungrouping, opacity, locking, visibility, and renaming."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import ACCENT, BG_CARD, BG_INPUT, BORDER, TEXT_PRI, TEXT_SEC, _LP, _LQ
from app.editor.canvas_overlay1 import overlay_pdf_rect
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CLOSE,
    SVG_EDIT,
    SVG_ERASER,
    SVG_EYE,
    SVG_FILE_PDF,
    SVG_FONT,
    SVG_GROUP,
    SVG_HIGHLIGHTER,
    SVG_IMAGE,
    SVG_LOCK,
    SVG_PEN,
    SVG_REFRESH,
    SVG_SIGNATURE,
    SVG_STICKY_NOTE,
    SVG_TRASH,
    SVG_VECTOR_SQUARE,
    SVG_CHECK,
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
    "group": SVG_GROUP,
    "check": SVG_CHECK,
    "cross": SVG_CLOSE,
    "highlight": SVG_HIGHLIGHTER,
    "redact": SVG_ERASER,
    "note": SVG_STICKY_NOTE,
    "draw": SVG_PEN,
}


class _SelectionRowWidget(QWidget):
    """Custom row widget inside Selection Pane with layer badge, icon, label/name, opacity spinbox, lock, and eye toggle."""

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
        layout.setContentsMargins(6, 4, 8, 4)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        fg = TEXT_PRI if dark else "#1A2B28"
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
        ico_lbl.setPixmap(
            svg_icon(svg_src, color=ACCENT if not overlay.get("_deleted") else "#9CA3AF", size=16).pixmap(16, 16)
        )
        ico_lbl.setFixedSize(18, 18)
        ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(ico_lbl)

        # 3. Clean Name Display Label (primary view)
        disp_name = self._default_friendly_name(overlay)
        self.name_lbl = QLabel(disp_name)
        if etype == "group":
            n_ch = len([ch for ch in overlay.get("children", []) if not ch.get("_deleted")])
            self.name_lbl.setToolTip(f"{disp_name}\nGrouped ({n_ch} objects) — click Ungroup or Ctrl+Shift+G to edit individual pieces")
        else:
            self.name_lbl.setToolTip(disp_name)
        self.name_lbl.setStyleSheet(f"color: {fg}; font-size: 9.5pt; font-weight: 500; background: transparent;")
        layout.addWidget(self.name_lbl, 1)

        # Inline LineEdit (shown only while actively renaming)
        self.name_edit = QLineEdit(disp_name)
        self.name_edit.setVisible(False)
        self.name_edit.editingFinished.connect(self._on_name_editing_finished)
        layout.addWidget(self.name_edit, 1)

        # 4. Opacity SpinBox
        op_val = int(round(float(overlay.get("opacity", 1.0)) * 100))
        self.spin_opacity = QSpinBox()
        self.spin_opacity.setRange(0, 100)
        self.spin_opacity.setSuffix("%")
        self.spin_opacity.setValue(op_val)
        self.spin_opacity.setFixedWidth(54)
        self.spin_opacity.setFixedHeight(22)
        self.spin_opacity.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin_opacity.setStyleSheet(
            f"QSpinBox {{ background: {inp_bg}; color: {fg}; border: 1px solid {brd}; "
            f"border-radius: 4px; font-size: 8.5pt; padding: 0 1px; min-height: 20px; max-height: 22px; }}"
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
        r = overlay_pdf_rect(ov)
        rw = int(round(r.width)) if r else 0
        rh = int(round(r.height)) if r else 0

        if etype == "group":
            children = [ch for ch in ov.get("children", []) if not ch.get("_deleted")]
            return f"Picture Group ({len(children)} objects, {rw}×{rh})" if rw and rh else f"Picture Group ({len(children)} objects)"
        if etype in ("text", "text_edit"):
            txt = (ov.get("new_text") or ov.get("text") or ov.get("old_text") or "").strip().replace("\n", " ")
            if txt:
                return f"Text: \"{txt[:22]}...\"" if len(txt) > 22 else f"Text: \"{txt}\""
            return f"Text Block ({rw}×{rh})" if rw and rh else "Text Block"
        if etype == "image":
            path = ov.get("path", "")
            base = os.path.basename(path) if path else ""
            if not ov.get("_existing") and base and not base.startswith("PDF_Editor_"):
                return f"Image: {base}"
            if rw and rh:
                return f"Image ({rw}×{rh})"
            return "Image"
        if etype == "signature":
            return f"Signature ({rw}×{rh})" if rw and rh else "Digital Signature"
        if etype == "drawing":
            items = ov.get("items", [])
            return f"Vector Shape ({rw}×{rh})" if rw and rh else f"Shape ({len(items)} paths)"
        if etype == "check":
            return "Checkmark Stamp"
        if etype == "cross":
            return "Crossmark Stamp"
        if etype == "highlight":
            return f"Highlight ({rw}×{rh})" if rw and rh else "Highlight"
        if etype == "redact":
            return f"Redaction Box ({rw}×{rh})" if rw and rh else "Redaction Box"
        if etype == "note":
            txt = (ov.get("text") or "").strip().replace("\n", " ")
            return f"Note: \"{txt[:18]}...\"" if len(txt) > 18 else (f"Note: \"{txt}\"" if txt else "Sticky Note")
        if etype == "draw":
            pts = ov.get("points", [])
            return f"Pen Drawing ({len(pts)} pts)"
        return etype.title()

    def _update_lock_icon(self, locked: bool) -> None:
        col = "#F59E0B" if locked else (TEXT_SEC if self._dark else _LQ)
        self.btn_lock.setIcon(svg_icon(SVG_LOCK, color=col, size=14))
        self.btn_lock.setToolTip("Locked (Cannot move/delete)" if locked else "Unlocked")
        self.btn_lock.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")

    def _update_vis_icon(self, visible: bool) -> None:
        col = ACCENT if visible else ("#EF4444" if self._dark else "#DC2626")
        self.btn_vis.setIcon(svg_icon(SVG_EYE if visible else SVG_CLOSE, color=col, size=15))
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
        self.name_lbl.hide()
        self.name_edit.setText(self.name_lbl.text())
        self.name_edit.show()
        self.name_edit.selectAll()
        self.name_edit.setFocus()
        self.name_edit.setStyleSheet(
            f"border: 1px solid {ACCENT}; background: {'#262626' if self._dark else '#FFFFFF'}; "
            f"color: {'#FFFFFF' if self._dark else '#000000'}; border-radius: 3px; font-size: 9.5pt; padding: 1px 4px;"
        )

    def _on_name_editing_finished(self) -> None:
        new_name = self.name_edit.text().strip()
        if new_name:
            self.overlay["_name"] = new_name
            self.name_lbl.setText(new_name)
            self.name_lbl.setToolTip(new_name)
            self.name_changed.emit(new_name)
        self.name_edit.hide()
        self.name_lbl.show()

    def mousePressEvent(self, event: QMouseEvent):
        event.ignore()


class SelectionPane(QDialog):
    """Floating inspector window for PDF objects on the active page.
    Automatically clusters related graphic objects into picture groups by default,
    and supports ungrouping/regrouping, layer reordering, opacity adjustment, locking, hiding, and renaming.
    """

    def __init__(self, tab_editar: TabEditar, parent=None):
        super().__init__(parent or tab_editar)
        self.tab = tab_editar
        self.canvas: PdfEditCanvas = tab_editar._canvas

        self.setWindowTitle("Selection Pane — Objects & Layers")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(440, 560)
        self.resize(470, 620)

        self._dark = is_dark()
        self._page_idx = getattr(self.canvas, "_page_idx", 0)
        self._is_syncing_selection = False

        self.canvas._selection_pane_active = True

        self._build_ui()
        self._apply_theme()
        self._connect_signals()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Top Bar: Page selector & counter ─────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_pg = QLabel("Page:")
        lbl_pg.setStyleSheet(f"font-weight: 600; color: {TEXT_PRI if self._dark else _LP}; font-size: 10pt;")
        top_bar.addWidget(lbl_pg)

        self.spin_page = QSpinBox()
        total_p = self.canvas.page_count() or 1
        self.spin_page.setRange(1, max(1, total_p))
        self.spin_page.setValue(self._page_idx + 1)
        self.spin_page.setFixedWidth(64)
        self.spin_page.valueChanged.connect(self._on_page_changed)
        top_bar.addWidget(self.spin_page)

        self.lbl_count = QLabel("0 objects")
        self.lbl_count.setStyleSheet(f"color: {TEXT_SEC if self._dark else _LQ}; font-size: 9.5pt;")
        top_bar.addWidget(self.lbl_count)
        top_bar.addStretch(1)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(svg_icon(SVG_REFRESH, color=ACCENT, size=15))
        self.btn_refresh.setFixedSize(28, 28)
        self.btn_refresh.setToolTip("Rescan and refresh objects on current page")
        self.btn_refresh.clicked.connect(self.refresh)
        top_bar.addWidget(self.btn_refresh)
        root.addLayout(top_bar)

        # ── Main Objects List Widget (supports Ctrl/Shift multi-select for Grouping) ──
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemSelectionChanged.connect(self._on_list_selection_changed)
        root.addWidget(self.list_widget, 1)

        # ── Action Buttons: Arrange, Group/Ungroup, Rename & Delete ──────
        act_row1 = QHBoxLayout()
        act_row1.setSpacing(6)

        self.btn_front = QPushButton("To Front")
        self.btn_front.setIcon(svg_icon(SVG_ARROW_UP, color=ACCENT, size=14))
        self.btn_front.setToolTip("Bring to Front")
        self.btn_front.clicked.connect(lambda: self._arrange_selected("front"))
        act_row1.addWidget(self.btn_front)

        self.btn_fwd = QPushButton("Forward")
        self.btn_fwd.setIcon(svg_icon(SVG_ARROW_UP, color=TEXT_SEC, size=13))
        self.btn_fwd.setToolTip("Bring Forward")
        self.btn_fwd.clicked.connect(lambda: self._arrange_selected("forward"))
        act_row1.addWidget(self.btn_fwd)

        self.btn_bwd = QPushButton("Backward")
        self.btn_bwd.setIcon(svg_icon(SVG_ARROW_DOWN, color=TEXT_SEC, size=13))
        self.btn_bwd.setToolTip("Send Backward")
        self.btn_bwd.clicked.connect(lambda: self._arrange_selected("backward"))
        act_row1.addWidget(self.btn_bwd)

        self.btn_back = QPushButton("To Back")
        self.btn_back.setIcon(svg_icon(SVG_ARROW_DOWN, color=ACCENT, size=14))
        self.btn_back.setToolTip("Send to Back")
        self.btn_back.clicked.connect(lambda: self._arrange_selected("back"))
        act_row1.addWidget(self.btn_back)
        root.addLayout(act_row1)

        act_row_grp = QHBoxLayout()
        act_row_grp.setSpacing(8)

        self.btn_group = QPushButton("Group (Ctrl+G)")
        self.btn_group.setIcon(svg_icon(SVG_GROUP, color=ACCENT, size=14))
        self.btn_group.setToolTip("Group 2 or more selected objects (Ctrl+Click or Shift+Click to select multiple)")
        self.btn_group.clicked.connect(self._group_selected)
        act_row_grp.addWidget(self.btn_group)

        self.btn_ungroup = QPushButton("Ungroup (Ctrl+Shift+G)")
        self.btn_ungroup.setIcon(svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=14))
        self.btn_ungroup.setToolTip("Ungroup selected picture group into individual objects")
        self.btn_ungroup.clicked.connect(self._ungroup_selected)
        act_row_grp.addWidget(self.btn_ungroup)
        root.addLayout(act_row_grp)

        act_row2 = QHBoxLayout()
        act_row2.setSpacing(8)

        self.btn_rename = QPushButton("Rename")
        self.btn_rename.setIcon(svg_icon(SVG_EDIT, color=ACCENT, size=14))
        self.btn_rename.clicked.connect(self._rename_selected)
        act_row2.addWidget(self.btn_rename)

        self.btn_delete = QPushButton("Delete Object")
        self.btn_delete.setIcon(svg_icon(SVG_TRASH, color="#EF4444", size=14))
        self.btn_delete.clicked.connect(self._delete_selected)
        act_row2.addWidget(self.btn_delete)
        root.addLayout(act_row2)

        sc_grp = QShortcut(QKeySequence("Ctrl+G"), self, self._group_selected)
        sc_grp.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_ungrp = QShortcut(QKeySequence("Ctrl+Shift+G"), self, self._ungroup_selected)
        sc_ungrp.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _apply_theme(self):
        dark = self._dark
        bg_card = BG_CARD if dark else "#FFFFFF"
        bg_inp = BG_INPUT if dark else "#F8FAFC"
        brd = BORDER if dark else "#D1D5DB"
        fg = TEXT_PRI if dark else "#1A2B28"
        dis_fg = "#6B7280" if dark else "#9CA3AF"

        self.setStyleSheet(
            f"QDialog {{ background-color: {bg_card}; color: {fg}; }}"
            f"QListWidget {{ background-color: {bg_inp}; border: 1px solid {brd}; "
            f"border-radius: 6px; outline: none; padding: 2px; }}"
            f"QListWidget::item {{ padding: 0px; margin: 1px 2px; border-radius: 5px; min-height: 40px; }}"
            f"QListWidget::item:selected {{ background-color: {'#264F78' if dark else '#D6E8FA'}; border: 1px solid {ACCENT}; }}"
            f"QListWidget::item:hover:!selected {{ background-color: {'#333333' if dark else '#F1F5F9'}; }}"
            f"QPushButton {{ background-color: {'#3C3F41' if dark else '#FFFFFF'}; border: 1px solid {brd}; "
            f"border-radius: 5px; padding: 5px 10px; font-size: 9.5pt; color: {fg}; }}"
            f"QPushButton:hover:enabled {{ background-color: {'#4F5254' if dark else '#F1F5F9'}; border-color: {ACCENT}; }}"
            f"QPushButton:disabled {{ color: {dis_fg}; background-color: {'#2A2A2A' if dark else '#F1F5F9'}; }}"
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

    def _update_action_buttons(self):
        selected_indices = self._selected_global_indices()
        n_sel = len(selected_indices)
        overlays = getattr(self.canvas, "_overlays", [])

        single_ov = (
            overlays[selected_indices[0]]
            if n_sel == 1 and 0 <= selected_indices[0] < len(overlays)
            else None
        )
        can_group = sum(
            1 for idx in selected_indices
            if 0 <= idx < len(overlays)
            and overlays[idx].get("type") != "note"
            and not overlays[idx].get("_locked")
        ) >= 2
        can_ungroup = any(
            0 <= idx < len(overlays)
            and overlays[idx].get("type") == "group"
            and not overlays[idx].get("_locked")
            for idx in selected_indices
        )
        can_arrange = (n_sel == 1 and single_ov is not None and not single_ov.get("_locked"))
        can_rename = (n_sel == 1 and single_ov is not None)
        can_delete = any(
            0 <= idx < len(overlays) and not overlays[idx].get("_locked")
            for idx in selected_indices
        )

        self.btn_group.setEnabled(can_group)
        self.btn_ungroup.setEnabled(can_ungroup)
        self.btn_front.setEnabled(can_arrange)
        self.btn_fwd.setEnabled(can_arrange)
        self.btn_bwd.setEnabled(can_arrange)
        self.btn_back.setEnabled(can_arrange)
        self.btn_rename.setEnabled(can_rename)
        self.btn_delete.setEnabled(can_delete)

    def _selected_global_indices(self) -> list[int]:
        indices: list[int] = []
        for item in self.list_widget.selectedItems():
            g_idx = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(g_idx, int):
                indices.append(g_idx)
        return indices

    def refresh(self):
        """Scan all objects on the current page and rebuild the object list in visual z-order."""
        if self._is_syncing_selection:
            return

        self._sync_page_from_canvas()

        total_p = self.canvas.page_count() or 1
        self.spin_page.setMaximum(max(1, total_p))

        if hasattr(self.canvas, "_overlay_mgr") and hasattr(self.canvas._overlay_mgr, "scan_page_objects"):
            self.canvas._overlay_mgr.scan_page_objects(self._page_idx)

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
            item.setSizeHint(QSize(0, 42))
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
        self._update_action_buttons()

    def _set_object_opacity(self, global_idx: int, opacity: float):
        self.canvas.set_selected_overlay_opacity(opacity, idx=global_idx)

    def _toggle_object_visibility(self, global_idx: int, visible: bool):
        overlays = getattr(self.canvas, "_overlays", [])
        if 0 <= global_idx < len(overlays):
            ov = overlays[global_idx]
            ov["_visible"] = visible
            if ov.get("_existing"):
                ov["_dirty"] = True
                if ov.get("type") == "group":
                    for ch in ov.get("children", []):
                        if ch.get("_existing"):
                            ch["_dirty"] = True
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
            self._update_action_buttons()

    def _rename_object(self, global_idx: int, new_name: str):
        overlays = getattr(self.canvas, "_overlays", [])
        if 0 <= global_idx < len(overlays):
            ov = overlays[global_idx]
            ov["_name"] = new_name
            self.canvas.overlay_changed.emit()

    def _on_list_selection_changed(self):
        indices = self._selected_global_indices()
        if len(indices) == 1:
            global_idx = indices[0]
            if 0 <= global_idx < len(self.canvas._overlays):
                self._is_syncing_selection = True
                self.canvas._selected_overlay_idx = global_idx
                self.canvas.update()
                self._is_syncing_selection = False
        elif len(indices) == 0:
            self._is_syncing_selection = True
            self.canvas._selected_overlay_idx = -1
            self.canvas.update()
            self._is_syncing_selection = False
        self._update_action_buttons()

    def _arrange_selected(self, mode: str):
        indices = self._selected_global_indices()
        if len(indices) != 1:
            return
        self.canvas.arrange_selected_overlay(mode, idx=indices[0])
        self.refresh()

    def _group_selected(self):
        indices = self._selected_global_indices()
        if len(indices) < 2:
            return
        grp = self.canvas.group_overlays(indices)
        if grp is not None:
            self.refresh()

    def _ungroup_selected(self):
        indices = self._selected_global_indices()
        overlays = getattr(self.canvas, "_overlays", [])
        group_indices = [
            idx for idx in sorted(indices, reverse=True)
            if 0 <= idx < len(overlays)
            and overlays[idx].get("type") == "group"
            and not overlays[idx].get("_locked")
        ]
        if not group_indices:
            return
        for idx in group_indices:
            self.canvas.ungroup_selected_overlay(idx=idx)
        self.refresh()

    def _rename_selected(self):
        selected = self.list_widget.selectedItems()
        if len(selected) != 1:
            return
        widget = self.list_widget.itemWidget(selected[0])
        if isinstance(widget, _SelectionRowWidget):
            widget.start_editing()

    def _delete_selected(self):
        indices = self._selected_global_indices()
        if not indices:
            return
        overlays = getattr(self.canvas, "_overlays", [])
        if any(0 <= i < len(overlays) and overlays[i].get("_locked") for i in indices):
            QMessageBox.warning(self, "Object Locked", "One or more selected objects are locked. Unlock them before deleting.")
            return
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self.canvas._overlays):
                self.canvas._selected_overlay_idx = idx
                self.canvas.delete_selected_overlay()
        self.refresh()

    def _show_context_menu(self, pos: QPoint):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            self.list_widget.setCurrentItem(item)
        indices = self._selected_global_indices()
        overlays = getattr(self.canvas, "_overlays", [])
        if not indices:
            return

        menu = QMenu(self)

        if len(indices) >= 2:
            can_grp = sum(
                1 for idx in indices
                if 0 <= idx < len(overlays)
                and overlays[idx].get("type") != "note"
                and not overlays[idx].get("_locked")
            ) >= 2
            if can_grp:
                act_grp = menu.addAction(svg_icon(SVG_GROUP, color=ACCENT, size=14), f"Group {len(indices)} Objects (Ctrl+G)")
                act_grp.triggered.connect(self._group_selected)
            if any(0 <= idx < len(overlays) and overlays[idx].get("type") == "group" for idx in indices):
                act_ungrp = menu.addAction(svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=14), "Ungroup Selected (Ctrl+Shift+G)")
                act_ungrp.triggered.connect(self._ungroup_selected)
            menu.addSeparator()
            act_del_m = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=14), f"Delete {len(indices)} Objects")
            act_del_m.triggered.connect(self._delete_selected)
            menu.exec(self.list_widget.mapToGlobal(pos))
            return

        global_idx = indices[0]
        if not (0 <= global_idx < len(overlays)):
            return

        ov = overlays[global_idx]

        if ov.get("type") == "group" and not ov.get("_locked"):
            act_ungrp = menu.addAction(svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=14), "Ungroup Objects (Ctrl+Shift+G)")
            act_ungrp.triggered.connect(self._ungroup_selected)
            menu.addSeparator()

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
        act_lock = menu.addAction(
            svg_icon(SVG_LOCK, color="#F59E0B" if is_locked else TEXT_SEC, size=14),
            "Unlock" if is_locked else "Lock",
        )
        act_lock.triggered.connect(lambda: self._toggle_object_lock(global_idx, not is_locked))

        is_vis = bool(ov.get("_visible", True))
        act_vis = menu.addAction(
            svg_icon(SVG_EYE if is_vis else SVG_CLOSE, color=ACCENT if is_vis else "#EF4444", size=14),
            "Hide" if is_vis else "Show",
        )
        act_vis.triggered.connect(lambda: self._toggle_object_visibility(global_idx, not is_vis))
        menu.addSeparator()

        act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=14), "Delete")
        act_del.triggered.connect(self._delete_selected)

        menu.exec(self.list_widget.mapToGlobal(pos))

    def closeEvent(self, event):
        if hasattr(self.canvas, "_selection_pane_active"):
            self.canvas._selection_pane_active = False
        super().closeEvent(event)
