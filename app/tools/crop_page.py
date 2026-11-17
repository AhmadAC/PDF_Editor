# app/tools/crop_page.py
"""TabCortar page controller: interactive crop preview, undo/redo history, and save flow."""

from __future__ import annotations

import contextlib
import os
from typing import Any, Sequence

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton

from app.base import BasePage
from app.constants import ACCENT, DESKTOP, TEXT_PRI, TEXT_SEC, _LQ
from app.i18n import t
from app.tools import crop_i18n as _crop_i18n  # noqa: F401
from app.tools.crop_engine import (
    compute_page_crops,
    compute_ref_dimensions,
    resolve_target_pages,
    save_cropped_pdf,
)
from app.tools.crop_widgets import CropMarginsGroup, CropOptionsGroup
from app.utils import (
    SVG_CROP,
    SVG_HISTORY,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    info_lbl,
    section,
    show_error,
    svg_icon,
)
from app.widgets import DropFileEdit


class TabCortar(BasePage):
    """Crop PDF pages with Foxit-style margin controls, range selection, live preview, SVG icons, and undo/redo."""

    crop_changed = Signal(object)
    crop_mode_toggled = Signal(bool)
    crops_changed = Signal(object)

    def __init__(self, status_fn):
        super().__init__(
            "fa5s.crop-alt",
            t("tool.crop.name"),
            t("tool.crop.desc"),
            t("tool.crop.btn"),
            status_fn,
        )
        self._pipeline_supported = True
        self._page_count = 0
        self._ref_width = 595.0
        self._ref_height = 842.0
        self._updating = False

        self._applied_crops: dict[int, tuple[float, float, float, float]] = {}
        self._undo_stack: list[dict[int, tuple[float, float, float, float]]] = []
        self._redo_stack: list[dict[int, tuple[float, float, float, float]]] = []

        f = self._form

        sec_src = section(t("tool.crop.source"))
        f.addWidget(sec_src)
        self.drop_in = DropFileEdit()
        try:
            self.drop_in.btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.drop_in.btn.clicked.connect(self._pick_input)
        self.drop_in.path_changed.connect(self._load_input)
        self.lbl_info = info_lbl()
        f.addWidget(self.drop_in)
        f.addWidget(self.lbl_info)

        self.btn_draw_crop = QPushButton(t("tool.crop.draw_btn"))
        self.btn_draw_crop.setIcon(svg_icon(SVG_CROP, color=TEXT_PRI, size=15))
        self.btn_draw_crop.setIconSize(QSize(15, 15))
        self.btn_draw_crop.setCheckable(True)
        self.btn_draw_crop.setChecked(True)
        self.btn_draw_crop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_draw_crop.setStyleSheet(
            f"QPushButton:checked {{ background: #264F78; border: 1.5px solid {ACCENT}; color: #FFFFFF; font-weight: bold; padding: 8px; border-radius: 6px; }}"
            f"QPushButton {{ background: #3C3F41; border: 1px solid #555555; color: {TEXT_PRI}; padding: 8px; font-weight: 600; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: #4F5254; border-color: {ACCENT}; }}"
        )
        self.btn_draw_crop.clicked.connect(self._on_draw_btn_clicked)
        f.addWidget(self.btn_draw_crop)

        grp_opts = CropOptionsGroup(self._on_mode_changed, self._on_controls_changed)
        self.cmb_page_mode = grp_opts.cmb_page_mode
        self.spin_current_page = grp_opts.spin_current_page
        self.edit_custom_pages = grp_opts.edit_custom_pages
        self.cmb_subset = grp_opts.cmb_subset
        f.addWidget(grp_opts)

        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        grp_margins = CropMarginsGroup(
            on_controls_changed=self._on_controls_changed,
            on_apply_preview=self.apply_crop_preview,
            on_undo=self._undo,
            on_redo=self._redo,
            on_reset=self._preset_reset,
            on_trim5=lambda: self._preset_percent(0.05),
            on_trim10=lambda: self._preset_percent(0.10),
            on_trim_half_inch=self._preset_half_inch,
            dpr=dpr,
        )
        self.spin_top = grp_margins.spin_top
        self.spin_bottom = grp_margins.spin_bottom
        self.spin_left = grp_margins.spin_left
        self.spin_right = grp_margins.spin_right
        self.lbl_dimensions = grp_margins.lbl_dimensions
        self.btn_apply_crop = grp_margins.btn_apply_crop
        self.btn_undo = grp_margins.btn_undo
        self.btn_redo = grp_margins.btn_redo
        self.btn_reset = grp_margins.btn_reset
        self.btn_trim5 = grp_margins.btn_trim5
        self.btn_trim10 = grp_margins.btn_trim10
        self.btn_trim_in = grp_margins.btn_trim_in
        self._lbl_tip_icon = grp_margins.lbl_tip_icon
        self._lbl_hint = grp_margins.lbl_hint
        f.addWidget(grp_margins)

        btn_save_row = QHBoxLayout()
        btn_save_row.setContentsMargins(0, 10, 0, 0)
        btn_save_row.addStretch()
        self.btn_save = QPushButton(t("tool.crop.btn"))
        self.btn_save.setObjectName("btn_action_small")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setFixedHeight(32)
        self.btn_save.setMinimumWidth(80)
        self.btn_save.clicked.connect(self._run)
        btn_save_row.addWidget(self.btn_save)
        f.addLayout(btn_save_row)
        self.action_btn = self.btn_save

        sec_out = section(t("tool.crop.output"))
        f.addWidget(sec_out)
        self.drop_out = DropFileEdit("cropped.pdf", save=True, default_name="cropped.pdf")
        f.addWidget(self.drop_out)
        f.addStretch()

        self._compact_hidden = [sec_src, self.drop_in, self.lbl_info]
        sec_out.setVisible(False)
        self.drop_out.setVisible(False)
        self._action_bar.setVisible(False)

        sc_enter1 = QShortcut(QKeySequence(Qt.Key.Key_Return), self, self.apply_crop_preview)
        sc_enter2 = QShortcut(QKeySequence(Qt.Key.Key_Enter), self, self.apply_crop_preview)
        for sc in (sc_enter1, sc_enter2):
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _resolve_active_pdf_and_viewer(self) -> tuple[str, Any]:
        pdf_path = self.drop_in.path()
        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)
        if (not pdf_path or not os.path.isfile(pdf_path)) and viewer and viewer.current_path():
            pdf_path = viewer.current_path()
        return pdf_path, viewer

    @staticmethod
    def _viewer_user_rot(viewer: Any, page_idx: int) -> int:
        if viewer and hasattr(viewer, "_canvas"):
            return viewer._canvas._page_rotations.get(page_idx, 0)
        return 0

    def _set_margin_spins(self, top: int, bottom: int, left: int, right: int) -> None:
        self._updating = True
        self.spin_top.setValue(top)
        self.spin_bottom.setValue(bottom)
        self.spin_left.setValue(left)
        self.spin_right.setValue(right)
        self._updating = False

    def _on_draw_btn_clicked(self):
        self.crop_mode_toggled.emit(self.btn_draw_crop.isChecked())

    def _on_mode_changed(self, idx: int):
        self.spin_current_page.setVisible(idx == 1)
        self.edit_custom_pages.setVisible(idx == 2)
        self._on_controls_changed()

    def _preset_reset(self):
        if self._applied_crops:
            self._undo_stack.append(dict(self._applied_crops))
            self._redo_stack.clear()
            self._applied_crops = {}
            self._on_crop_history_changed()
            self._status(t("tool.crop.reset"))
            return
        self._set_margin_spins(0, 0, 0, 0)
        self._on_controls_changed()

    def _preset_percent(self, pct: float):
        h_trim = int(round(self._ref_height * pct))
        w_trim = int(round(self._ref_width * pct))
        self._set_margin_spins(h_trim, h_trim, w_trim, w_trim)
        self._on_controls_changed()

    def _preset_half_inch(self):
        self._set_margin_spins(36, 36, 36, 36)
        self._on_controls_changed()

    def _get_target_pages(self) -> list[int]:
        return resolve_target_pages(
            page_count=self._page_count,
            mode=self.cmb_page_mode.currentIndex(),
            current_page_1based=self.spin_current_page.value(),
            custom_text=self.edit_custom_pages.text(),
            subset=self.cmb_subset.currentIndex(),
        )

    def _update_dim_label(self):
        w = max(0.0, self._ref_width - self.spin_left.value() - self.spin_right.value())
        h = max(0.0, self._ref_height - self.spin_top.value() - self.spin_bottom.value())
        w_mm = w * 25.4 / 72.0
        h_mm = h * 25.4 / 72.0
        self.lbl_dimensions.setText(f"Size: {w:.0f} x {h:.0f} pt  ({w_mm:.0f} x {h_mm:.0f} mm)")

    def _on_controls_changed(self, *_args: Any):
        if self._updating:
            return
        self._update_dim_label()
        self._emit_preview()

    def _emit_preview(self):
        margins = (
            self.spin_top.value(),
            self.spin_bottom.value(),
            self.spin_left.value(),
            self.spin_right.value(),
        )
        self.crop_changed.emit({
            "margins": margins,
            "targets": set(self._get_target_pages()),
            "ref_page": self.spin_current_page.value() - 1,
        })

    def apply_crop_preview(self):
        """Commit the current margin controls into the applied in-memory crop state."""
        pdf_path, viewer = self._resolve_active_pdf_and_viewer()
        if not pdf_path or not os.path.isfile(pdf_path):
            return
        targets = self._get_target_pages()
        if not targets:
            return
        margins = (
            self.spin_top.value(),
            self.spin_bottom.value(),
            self.spin_left.value(),
            self.spin_right.value(),
        )
        if margins == (0, 0, 0, 0):
            return

        doc = None
        try:
            doc = self._open_fitz(pdf_path)
            new_crops, any_changed = compute_page_crops(
                doc,
                targets,
                margins,
                self._applied_crops,
                lambda idx: self._viewer_user_rot(viewer, idx),
                check_min_span=True,
            )
            if not any_changed:
                QMessageBox.warning(self, t("msg.warning"), t("tool.crop.invalid_margins"))
                return
            self._undo_stack.append(dict(self._applied_crops))
            self._redo_stack.clear()
            self._applied_crops = new_crops
            self._on_crop_history_changed()
            self._status(t("tool.crop.status.preview_applied"))
        except Exception as exc:
            show_error(self, exc)
        finally:
            if doc is not None:
                with contextlib.suppress(Exception):
                    doc.close()

    def _undo(self):
        if not self._undo_stack:
            self._status(t("edit.undo_tip") + ": " + t("recent.empty"))
            return
        self._redo_stack.append(dict(self._applied_crops))
        self._applied_crops = self._undo_stack.pop()
        self._on_crop_history_changed()
        self._status(t("tool.crop.status.undone"))

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(dict(self._applied_crops))
        self._applied_crops = self._redo_stack.pop()
        self._on_crop_history_changed()
        self._status(t("tool.crop.status.redone"))

    def _on_crop_history_changed(self):
        pdf_path, viewer = self._resolve_active_pdf_and_viewer()
        ref_idx = max(0, min(self.spin_current_page.value() - 1, self._page_count - 1))
        self._ref_width, self._ref_height = compute_ref_dimensions(
            open_fitz_fn=self._open_fitz,
            pdf_path=pdf_path if (pdf_path and os.path.isfile(pdf_path)) else None,
            page_idx=ref_idx,
            applied_crops=self._applied_crops,
            user_rot=self._viewer_user_rot(viewer, ref_idx),
            fallback_w=self._ref_width,
            fallback_h=self._ref_height,
        )
        self._set_margin_spins(0, 0, 0, 0)
        self._update_dim_label()
        self.crop_changed.emit(None)
        self.crops_changed.emit(self._applied_crops)
        self.btn_undo.setEnabled(len(self._undo_stack) > 0)
        self.btn_redo.setEnabled(len(self._redo_stack) > 0)

    def on_canvas_crop_selected(self, page_idx: int, rect: Sequence[float]):
        """Called when the user drags a rubber-band rectangle on the viewer canvas."""
        if len(rect) >= 6:
            p_x0, p_y0, p_x1, p_y1 = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            self._ref_width, self._ref_height = float(rect[4]), float(rect[5])
        elif len(rect) >= 4:
            p_x0, p_y0, p_x1, p_y1 = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            doc_path, viewer = self._resolve_active_pdf_and_viewer()
            self._ref_width, self._ref_height = compute_ref_dimensions(
                open_fitz_fn=self._open_fitz,
                pdf_path=doc_path if (doc_path and os.path.isfile(doc_path)) else None,
                page_idx=page_idx,
                applied_crops=self._applied_crops,
                user_rot=self._viewer_user_rot(viewer, page_idx),
                fallback_w=self._ref_width,
                fallback_h=self._ref_height,
            )
        else:
            return

        left = max(0.0, p_x0)
        top = max(0.0, p_y0)
        right = max(0.0, self._ref_width - p_x1)
        bottom = max(0.0, self._ref_height - p_y1)

        self._updating = True
        self.spin_current_page.setValue(page_idx + 1)
        self.spin_left.setValue(int(round(left)))
        self.spin_top.setValue(int(round(top)))
        self.spin_right.setValue(int(round(right)))
        self.spin_bottom.setValue(int(round(bottom)))
        self._updating = False

        self._update_dim_label()
        self._emit_preview()

    def _pick_input(self):
        p, _ = QFileDialog.getOpenFileName(self, t("btn.open_pdf"), DESKTOP, t("file_filter.pdf"))
        if p:
            self._load_input(p)

    def _load_input(self, p: str):
        self.drop_in.blockSignals(True)
        self.drop_in.set_path(p)
        self.drop_in.blockSignals(False)
        if not self._maybe_prompt_password(p):
            self.drop_in.blockSignals(True)
            self.drop_in.set_path("")
            self.drop_in.blockSignals(False)
            return
        if not self.drop_out.path():
            base, ext = os.path.splitext(p)
            self.drop_out.set_path(base + "_cropped" + ext)

        self._applied_crops = {}
        self._undo_stack = []
        self._redo_stack = []
        self.btn_undo.setEnabled(False)
        self.btn_redo.setEnabled(False)

        try:
            doc = self._open_fitz(p)
            self._page_count = doc.page_count
            if self._page_count > 0:
                self.spin_current_page.setMaximum(self._page_count)
            doc.close()
            _, viewer = self._resolve_active_pdf_and_viewer()
            if self._page_count > 0:
                self._ref_width, self._ref_height = compute_ref_dimensions(
                    open_fitz_fn=self._open_fitz,
                    pdf_path=p,
                    page_idx=0,
                    applied_crops={},
                    user_rot=self._viewer_user_rot(viewer, 0),
                    fallback_w=self._ref_width,
                    fallback_h=self._ref_height,
                )
        except Exception as e:
            self.lbl_info.setText(t("tool.split.error_info", e=e))
            return

        self._set_margin_spins(0, 0, 0, 0)
        self.lbl_info.setText(t("edit.status.pages", n=self._page_count))
        self._update_dim_label()
        self._emit_preview()
        self.crops_changed.emit(self._applied_crops)

    def auto_load(self, path: str):
        if path:
            self._load_input(path)

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        pri = TEXT_PRI if dark else _LQ
        sec = TEXT_SEC if dark else _LQ
        self.btn_reset.setStyleSheet(f"color: {pri};")
        self.btn_reset.setIcon(svg_icon(SVG_HISTORY, color=pri, size=14))
        self.btn_draw_crop.setIcon(svg_icon(SVG_CROP, color=pri, size=15))
        if hasattr(self, "btn_undo"):
            self.btn_undo.setStyleSheet(f"color: {pri};")
            self.btn_undo.setIcon(svg_icon(SVG_ROTATE_LEFT, color=pri, size=14))
        if hasattr(self, "btn_redo"):
            self.btn_redo.setStyleSheet(f"color: {pri};")
            self.btn_redo.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=pri, size=14))
        if hasattr(self, "_lbl_hint"):
            self._lbl_hint.setStyleSheet(f"color: {sec}; font-size: 10pt;")

    def _run(self):
        pdf_path, viewer = self._resolve_active_pdf_and_viewer()
        win: Any = self.window()
        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, t("msg.warning"), t("msg.select_valid_pdf"))
            return

        margins = (
            self.spin_top.value(),
            self.spin_bottom.value(),
            self.spin_left.value(),
            self.spin_right.value(),
        )
        crops_to_save = dict(self._applied_crops)

        if any(m > 0 for m in margins):
            targets = self._get_target_pages()
            doc_probe = None
            try:
                doc_probe = self._open_fitz(pdf_path)
                crops_to_save, _ = compute_page_crops(
                    doc_probe,
                    targets,
                    margins,
                    crops_to_save,
                    lambda idx: self._viewer_user_rot(viewer, idx),
                    check_min_span=False,
                )
            finally:
                if doc_probe is not None:
                    doc_probe.close()

        if not crops_to_save:
            QMessageBox.warning(self, t("msg.warning"), t("tool.crop.no_pages"))
            return

        default_name = "cropped.pdf"
        if pdf_path:
            base, ext = os.path.splitext(os.path.basename(pdf_path))
            default_name = f"{base}_cropped{ext}"
        out_path = self._prompt_save_as(default_name, os.path.dirname(pdf_path) if pdf_path else "")
        if not out_path:
            return
        self.drop_out.set_path(out_path)

        def _release_open_viewer() -> None:
            if viewer and viewer.current_path() and os.path.abspath(viewer.current_path()) == os.path.abspath(out_path):
                viewer._canvas.close_doc()
                if viewer._fitz_doc:
                    with contextlib.suppress(Exception):
                        viewer._fitz_doc.close()
                    viewer._fitz_doc = None
                viewer._thumbnails._stop_all_workers()

        try:
            save_cropped_pdf(
                open_fitz_fn=self._open_fitz,
                pdf_path=pdf_path,
                out_path=out_path,
                crops_to_save=crops_to_save,
                before_write=_release_open_viewer,
            )
            self._status(t("tool.crop.status.done", name=os.path.basename(out_path)))
            cleanup_fn = getattr(win, "_cleanup_pipeline", None)
            if callable(cleanup_fn) and viewer:
                cleanup_fn(id(viewer))
            if viewer:
                viewer.load(out_path)
            QMessageBox.information(self, t("msg.done"), t("tool.crop.done", path=out_path))
        except Exception as e:
            show_error(self, e)