# app/tools/watermark_page.py
"""TabMarcaDagua page controller coordinating UI, open tabs, and background watermarking."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import traceback
from typing import Any

import fitz
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
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
from app.pdf_password import authenticate_fitz
from app.tools.watermark_engine import (
    EmptyPdfError,
    WatermarkJobConfig,
    copy_file_atomic,
    execute_watermark,
    preflight_watermark,
)
from app.tools.watermark_widgets import (
    _OpacitySpinBox,
    _PdfSourceCard,
    _WatermarkResultBox,
)
from app.utils import (
    SVG_LIGHTBULB,
    SVG_STAMP,
    is_dark,
    open_folder,
    reveal_file,
    show_error,
    svg_icon,
    svg_pixmap,
)
from app.widgets import DropFileEdit, FocusComboBox, FocusSpinBox

_log = logging.getLogger(__name__)


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

        self._header.setFixedHeight(44)
        hdr_lay = self._header.layout()
        if hdr_lay is not None:
            hdr_lay.setContentsMargins(14, 4, 14, 4)

        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical, QScrollBar:horizontal {"
            " width: 0px; height: 0px; max-width: 0px; max-height: 0px;"
            " background: transparent; border: none; }"
        )
        self._action_bar.setVisible(False)

        f = self._form
        f.setContentsMargins(12, 6, 12, 8)
        f.setSpacing(6)

        # ── 1. Base PDF Card ──────────────────────────────────────────────
        self.base_card = _PdfSourceCard(
            "Base PDF (Document)",
            "Select open tab or drop PDF...",
            refresh_fn=self._refresh_open_tabs,
        )
        self.base_card.tab_selected.connect(self._on_base_tab_selected)
        self.base_card.file_picked.connect(self._on_base_file_picked)
        self.base_card.cleared.connect(self._on_base_cleared)
        f.addWidget(self.base_card)

        # ── 2. Stamp PDF Card ─────────────────────────────────────────────
        self.wm_card = _PdfSourceCard(
            "Stamp PDF (Watermark)",
            "Select open tab or drop PDF...",
            refresh_fn=self._refresh_open_tabs,
        )
        self.wm_card.tab_selected.connect(self._on_wm_tab_selected)
        self.wm_card.file_picked.connect(self._on_wm_file_picked)
        self.wm_card.cleared.connect(self._on_wm_cleared)
        f.addWidget(self.wm_card)

        # ── 3. Options Card ───────────────────────────────────────────────
        self._build_options_card(f)

        # ── 4. In-Window Green Result Box ─────────────────────────────────
        self._result_box = _WatermarkResultBox()
        self._lbl_res_icon = self._result_box.lbl_icon
        self._lbl_res_msg = self._result_box.lbl_msg
        self._btn_res_close = self._result_box.btn_close
        self._btn_res_save = self._result_box.btn_save
        self._btn_res_folder = self._result_box.btn_folder
        self._result_box.save_requested.connect(self._save_last_output_as)
        self._result_box.folder_requested.connect(self._open_last_output_folder)
        f.addWidget(self._result_box)

        # ── 5. In-Window Apply Button ─────────────────────────────────────
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

    def _build_options_card(self, parent_layout) -> None:
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
        self.cmb_layer.addItems([s.split("(")[0].strip() for s in full])
        for i, full_text in enumerate(full):
            self.cmb_layer.setItemData(i, full_text, Qt.ItemDataRole.ToolTipRole)
        self.cmb_layer.setMinimumContentsLength(8)
        form.addRow(t("tool.watermark.position_label"), self.cmb_layer)

        self.slider_base_opacity, self.spin_base_opacity = self._make_opacity_row(
            form,
            "Base opacity:",
            self._sync_base_opacity_from_slider,
            self._sync_base_opacity_from_spin,
        )
        self.slider_opacity, self.spin_opacity = self._make_opacity_row(
            form,
            "Stamp opacity:",
            self._sync_stamp_opacity_from_slider,
            self._sync_stamp_opacity_from_spin,
        )

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
        parent_layout.addWidget(self._options_card)

    def _make_opacity_row(self, form: QFormLayout, label: str, on_slider, on_spin):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimumWidth(60)
        slider.setRange(0, 100)
        slider.setValue(100)
        spin = _OpacitySpinBox()
        slider.valueChanged.connect(on_slider)
        spin.valueChanged.connect(on_spin)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        form.addRow(label, row)
        return slider, spin

    @staticmethod
    def _sync_paired(widget, value: int) -> None:
        widget.blockSignals(True)
        widget.setValue(value)
        widget.blockSignals(False)

    def _sync_base_opacity_from_slider(self, value: int) -> None:
        self._sync_paired(self.spin_base_opacity, value)

    def _sync_base_opacity_from_spin(self, value: int) -> None:
        self._sync_paired(self.slider_base_opacity, value)

    def _sync_stamp_opacity_from_slider(self, value: int) -> None:
        self._sync_paired(self.spin_opacity, value)

    def _sync_stamp_opacity_from_spin(self, value: int) -> None:
        self._sync_paired(self.slider_opacity, value)

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
        self._result_box.update_theme(dark, dpr=dpr)

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
            self._sync_wm_selection(curr_p, os.path.basename(self._wm_orig_path))

    def _on_wm_file_picked(self, path: str) -> None:
        if path and os.path.isfile(path):
            self._wm_orig_path = path
            self._wm_password = ""
            self._sync_wm_selection(path, "")

    def _sync_wm_selection(self, path: str, display_name: str) -> None:
        self.wm_card.set_path(path, display_name=display_name)
        self.drop_wm.blockSignals(True)
        self.drop_wm.set_path(path, display_name=display_name)
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

    def _on_match_toggled(self, checked: bool) -> None:
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

    def _load_input(self, p: str, display_name: str = "") -> None:
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

    def auto_load(self, path: str) -> None:
        self._refresh_open_tabs()
        if not path or not os.path.isfile(path):
            return
        if os.path.abspath(path) in self._generated_outputs:
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
        self._result_box.show_message(message, can_save=can_save)

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
                copy_file_atomic(src_temp, chosen_path)
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
                open_folder(
                    os.path.dirname(self._last_suggested_path) if self._last_suggested_path else target
                )

    def cleanup_pipeline(self) -> None:
        super().cleanup_pipeline()
        for d in self._wm_tmp_dirs:
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        self._wm_tmp_dirs.clear()

    def _run(self) -> None:
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
            pf = preflight_watermark(
                pdf_path=pdf_path,
                wm_path=wm_path,
                wm_pwd=wm_pwd,
                pages_text=self.edit_pages.text(),
                match_pages=match_pages,
                include_omitted_wm=include_omitted_wm,
                open_base_fitz=self._open_fitz,
            )
        except EmptyPdfError as e:
            QMessageBox.warning(self, t("msg.warning"), str(e))
            return
        except Exception as e:
            _log.debug("Watermark pre-flight failed: %s", traceback.format_exc())
            show_error(self, e)
            return

        base_orig = self._base_orig_path or pdf_path
        stem, _ = os.path.splitext(os.path.basename(base_orig))
        wm_filename = f"{stem}_watermarked.pdf"
        run_tmp_dir = tempfile.mkdtemp(prefix="PDF_Editor_wm_")
        self._wm_tmp_dirs.append(run_tmp_dir)
        out_path = os.path.join(run_tmp_dir, wm_filename)
        suggested_save_path = os.path.join(os.path.dirname(base_orig) or DESKTOP, wm_filename)

        cfg = WatermarkJobConfig(
            pdf_path=pdf_path,
            wm_path=wm_path,
            out_path=out_path,
            base_password=self._pdf_password,
            wm_password=wm_pwd,
            match_pages=match_pages,
            include_omitted_wm=include_omitted_wm,
            over=self.cmb_layer.currentIndex() == 0,
            base_opacity=max(0.0, min(1.0, self.spin_base_opacity.value() / 100.0)),
            stamp_opacity=max(0.0, min(1.0, self.spin_opacity.value() / 100.0)),
            transparent_white=self.chk_transparent_white.isChecked(),
            single_wm_idx=min(max(0, self.spin_wm_page.value() - 1), pf.wm_total - 1),
        )

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
            lambda worker: execute_watermark(cfg, pf, worker),
            total=100,
            label=t("progress.watermark.applying"),
            on_done=on_done,
        )