# app/tools/watermark.py
"""PDF_Editor – TabMarcaDagua: stamp PDF A onto specific/all pages of PDF B with auto-duplication and opacity."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import traceback
from typing import Any

import fitz
from PIL import Image, ImageChops
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSlider,
)

from app.base import BasePage
from app.constants import DESKTOP, TEXT_SEC
from app.i18n import t
from app.pdf_io import atomic_pdf_write
from app.pdf_password import authenticate_fitz
from app.utils import WrongPasswordError, info_lbl, parse_pages, section, show_error
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
        self._pipeline_supported = True
        f = self._form

        sec_src = section(t("tool.watermark.source"))
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

        f.addWidget(section(t("tool.watermark.wm_file")))
        self.drop_wm = DropFileEdit(t("tool.watermark.wm_hint"))
        self.drop_wm._skip_window_track = True  # type: ignore[attr-defined]
        self.drop_wm.path_changed.connect(self._on_wm_picked)
        f.addWidget(self.drop_wm)

        grp = QGroupBox(t("tool.watermark.options"))
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_pages = QLineEdit()
        self.edit_pages.setPlaceholderText(t("tool.watermark.pages_hint"))
        form.addRow(t("tool.watermark.pages_label"), self.edit_pages)

        self.chk_match_pages = QCheckBox("Match & auto-duplicate pages")
        self.chk_match_pages.setChecked(True)
        self.chk_match_pages.setToolTip(
            "Automatically duplicates a 1-page background PDF to match all pages of a multi-page stamp PDF"
        )
        self.chk_match_pages.toggled.connect(self._on_match_toggled)
        form.addRow("", self.chk_match_pages)

        self.spin_wm_page = FocusSpinBox()
        self.spin_wm_page.setRange(1, 9999)
        self.spin_wm_page.setValue(1)
        self.spin_wm_page.setEnabled(False)
        self.spin_wm_page.setToolTip("Single page number from Stamp PDF (PDF A) to repeat across pages")
        form.addRow("Stamp page:", self.spin_wm_page)

        self.cmb_layer = FocusComboBox()
        full = [t("tool.watermark.above"), t("tool.watermark.below")]
        short = [s.split("(")[0].strip() for s in full]
        self.cmb_layer.addItems(short)
        for i, full_text in enumerate(full):
            self.cmb_layer.setItemData(i, full_text, Qt.ItemDataRole.ToolTipRole)
        self.cmb_layer.setMinimumContentsLength(8)
        form.addRow(t("tool.watermark.position_label"), self.cmb_layer)

        op_row = QHBoxLayout()
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.setSpacing(8)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(5, 100)
        self.slider_opacity.setValue(100)
        self.lbl_opacity_val = QLabel("100%")
        self.lbl_opacity_val.setMinimumWidth(40)
        self.slider_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity_val.setText(f"{v}%")
        )
        op_row.addWidget(self.slider_opacity, 1)
        op_row.addWidget(self.lbl_opacity_val)
        form.addRow("Opacity:", op_row)

        self.chk_transparent_white = QCheckBox("Make white background transparent")
        self.chk_transparent_white.setChecked(True)
        self.chk_transparent_white.setToolTip(
            "Removes solid white page background from the stamp PDF so underlying content shows through"
        )
        form.addRow("", self.chk_transparent_white)

        lbl_tip = QLabel("💡 Use 100% opacity to stamp a full worksheet onto a background, or 20–40% for a faded watermark.")
        lbl_tip.setWordWrap(True)
        lbl_tip.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9pt; padding-top: 4px;")
        form.addRow("", lbl_tip)

        f.addWidget(grp)

        sec_out = section(t("tool.watermark.output"))
        f.addWidget(sec_out)
        self.drop_out = DropFileEdit("watermarked.pdf", save=True, default_name="watermarked.pdf")
        f.addWidget(self.drop_out)
        f.addStretch()
        self._compact_hidden = [sec_src, self.drop_in, self.lbl_info]
        sec_out.setVisible(False)
        self.drop_out.setVisible(False)

    def _on_match_toggled(self, checked: bool):
        self.spin_wm_page.setEnabled(not checked)

    def _on_wm_picked(self, wm_path: str):
        print(f"[DEBUG][Watermark] Stamp PDF (PDF A) selected: {wm_path!r}")
        if not wm_path or not os.path.isfile(wm_path):
            return
        try:
            with fitz.open(wm_path) as d:
                if not d.needs_pass:
                    self.spin_wm_page.setMaximum(max(1, d.page_count))
                    print(f"[DEBUG][Watermark] Stamp PDF A has {d.page_count} page(s)")
        except Exception as exc:
            print(f"[DEBUG][Watermark] Could not probe stamp PDF page count: {exc}")

    def _pick_input(self):
        p, _ = QFileDialog.getOpenFileName(self, t("btn.open_pdf"), DESKTOP, t("file_filter.pdf"))
        if p:
            self._load_input(p)

    def _load_input(self, p: str):
        print(f"[DEBUG][Watermark] _load_input(PDF B) called with: {p!r}")
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
            self.drop_out.set_path(base + "_watermark" + ext)
        try:
            r = self._open_reader(p)
            self.lbl_info.setText(t("edit.status.pages", n=len(r.pages)))
        except Exception as e:
            self.lbl_info.setText(t("tool.split.error_info", e=e))

    def auto_load(self, path: str):
        if path and self.drop_in.path() != path:
            self._load_input(path)

    def _prompt_watermark_password(self, wm_path: str) -> str | None:
        from app.utils import prompt_pdf_password
        ok, pwd = prompt_pdf_password(wm_path, self)
        if not ok:
            return None
        return pwd

    def _run(self):
        pdf_path = self.drop_in.path()
        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)
        if (not pdf_path or not os.path.isfile(pdf_path)) and viewer and viewer.current_path():
            pdf_path = viewer.current_path()
            print(f"[DEBUG][Watermark] Fallback source PDF B from active viewer: {pdf_path!r}")

        wm_path = self.drop_wm.path()
        print(
            f"[DEBUG][Watermark] _run invoked | pdf_b={pdf_path!r} | pdf_a(wm)={wm_path!r} "
            f"| pipeline_active={self._pipeline_active}"
        )

        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.select_source"))
            return
        if not wm_path or not os.path.isfile(wm_path):
            QMessageBox.warning(self, t("msg.warning"), t("tool.watermark.select_wm"))
            return

        out_path = self._resolve_output_file(self.drop_out, pdf_path)
        print(f"[DEBUG][Watermark] Resolved output path: {out_path!r}")
        if not out_path:
            return

        wm_pwd = self._prompt_watermark_password(wm_path)
        if wm_pwd is None:
            print("[DEBUG][Watermark] Aborted: user cancelled watermark password prompt")
            return

        match_pages = self.chk_match_pages.isChecked()

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

            # Determine if we are auto-duplicating a smaller background PDF to match a multi-page stamp PDF
            expanding_base = bool(match_pages and wm_total > base_total)
            range_limit = wm_total if expanding_base else base_total
            txt = self.edit_pages.text().strip()
            targets = sorted(parse_pages(txt, range_limit)) if txt else list(range(range_limit))
            target_set = set(targets)
            print(
                f"[DEBUG][Watermark] base_total={base_total}, wm_total={wm_total}, "
                f"expanding_base={expanding_base}, targets={[p + 1 for p in targets]}"
            )
        except Exception as e:
            print(f"[DEBUG][Watermark] Pre-flight failed: {traceback.format_exc()}")
            show_error(self, e)
            return

        over = self.cmb_layer.currentIndex() == 0
        opacity_pct = self.slider_opacity.value()
        opacity_float = max(0.05, min(1.0, opacity_pct / 100.0))
        transparent_white = self.chk_transparent_white.isChecked()
        single_wm_idx = min(max(0, self.spin_wm_page.value() - 1), wm_total - 1)
        pwd = self._pdf_password

        def _render_wm_png(wm_doc_obj: fitz.Document, p_idx: int) -> tuple[bytes, float, float]:
            wm_page = wm_doc_obj[p_idx]
            wm_pix = wm_page.get_pixmap(dpi=200, alpha=True)
            pil_img = Image.frombytes("RGBA", (wm_pix.width, wm_pix.height), wm_pix.samples)
            r_ch, g_ch, b_ch, a_ch = pil_img.split()
            if transparent_white:
                min_rgb = ImageChops.darker(ImageChops.darker(r_ch, g_ch), b_ch)
                non_white_mask = min_rgb.point(lambda v: 0 if v >= 242 else 255)
                a_ch = ImageChops.darker(a_ch, non_white_mask)
            if opacity_float < 0.999:
                a_ch = a_ch.point(lambda a: int(round(a * opacity_float)))
            pil_img.putalpha(a_ch)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue(), float(wm_pix.width), float(wm_pix.height)

        def do_work(worker):
            wm_d = fitz.open(wm_path)
            src_d = fitz.open(pdf_path)
            out_d = None
            try:
                if wm_d.needs_pass:
                    if not (wm_pwd and authenticate_fitz(wm_d, wm_pwd) is not None):
                        raise WrongPasswordError(t("tool.err.wrong_password"))
                if src_d.needs_pass:
                    if not (pwd and authenticate_fitz(src_d, pwd) is not None):
                        raise WrongPasswordError(t("tool.err.wrong_password"))

                rendered_cache: dict[int, tuple[bytes, float, float]] = {}

                def get_wm_cached(idx_to_render: int) -> tuple[bytes, float, float]:
                    if idx_to_render not in rendered_cache:
                        rendered_cache[idx_to_render] = _render_wm_png(wm_d, idx_to_render)
                    return rendered_cache[idx_to_render]

                if expanding_base:
                    # Auto-duplicate base background page(s) to match all targeted pages of PDF A
                    out_d = fitz.open()
                    total_steps = max(1, len(targets))
                    for step_idx, wm_idx in enumerate(targets):
                        if worker.is_cancelled():
                            return None
                        b_src_idx = step_idx % src_d.page_count
                        out_d.insert_pdf(src_d, from_page=b_src_idx, to_page=b_src_idx)
                        page_b = out_d[step_idx]
                        png_bytes, ww, wh = get_wm_cached(wm_idx)
                        pr = page_b.rect
                        scale = min(pr.width / max(1.0, ww), pr.height / max(1.0, wh))
                        fw, fh = ww * scale, wh * scale
                        fx = pr.x0 + (pr.width - fw) / 2.0
                        fy = pr.y0 + (pr.height - fh) / 2.0
                        target_rect = fitz.Rect(fx, fy, fx + fw, fy + fh)
                        page_b.insert_image(target_rect, stream=png_bytes, overlay=over)
                        print(
                            f"[DEBUG][Watermark] Auto-duplicated base page {b_src_idx + 1} -> out page {step_idx + 1} "
                            f"& stamped PDF A page {wm_idx + 1}"
                        )
                        worker.progress.emit(
                            int((step_idx + 1) / total_steps * 100),
                            t("progress.watermark.page", current=step_idx + 1, total=total_steps),
                        )
                    target_save_doc = out_d
                else:
                    n = src_d.page_count
                    for i in range(n):
                        if worker.is_cancelled():
                            return None
                        if i in target_set:
                            wm_idx = (i % wm_total) if match_pages else single_wm_idx
                            png_bytes, ww, wh = get_wm_cached(wm_idx)
                            page_b = src_d[i]
                            pr = page_b.rect
                            scale = min(pr.width / max(1.0, ww), pr.height / max(1.0, wh))
                            fw, fh = ww * scale, wh * scale
                            fx = pr.x0 + (pr.width - fw) / 2.0
                            fy = pr.y0 + (pr.height - fh) / 2.0
                            target_rect = fitz.Rect(fx, fy, fx + fw, fy + fh)
                            page_b.insert_image(target_rect, stream=png_bytes, overlay=over)
                            print(f"[DEBUG][Watermark] Stamped PDF A page {wm_idx + 1} onto PDF B page {i + 1}/{n}")
                        worker.progress.emit(
                            int((i + 1) / max(1, n) * 100),
                            t("progress.watermark.page", current=i + 1, total=n),
                        )
                    target_save_doc = src_d

                if worker.is_cancelled():
                    return None

                if (
                    viewer
                    and viewer.current_path()
                    and os.path.abspath(viewer.current_path()) == os.path.abspath(out_path)
                ):
                    viewer._canvas.close_doc()
                    if viewer._fitz_doc:
                        with contextlib.suppress(Exception):
                            viewer._fitz_doc.close()
                        viewer._fitz_doc = None
                    viewer._thumbnails._stop_all_workers()

                atomic_pdf_write(
                    target_save_doc,
                    out_path,
                    sources=[pdf_path, wm_path],
                    save_opts={"garbage": 4, "deflate": True},
                    close_writer=True,
                )
                print(f"[DEBUG][Watermark] Successfully wrote watermarked PDF to {out_path!r}")
            finally:
                with contextlib.suppress(Exception):
                    wm_d.close()
                with contextlib.suppress(Exception):
                    if not getattr(src_d, "is_closed", False):
                        src_d.close()
                if out_d is not None:
                    with contextlib.suppress(Exception):
                        if not getattr(out_d, "is_closed", False):
                            out_d.close()
            return out_path

        def on_done(saved):
            print(f"[DEBUG][Watermark] on_done: saved={saved!r}, pipeline={self._pipeline_active}")
            self._status(t("tool.watermark.status.done", name=os.path.basename(saved)))
            msg = t("tool.watermark.done", path=saved)
            if self._pipeline_active:
                self._pipeline_success(msg, saved)
            else:
                if viewer:
                    viewer.load(saved)
                QMessageBox.information(self, t("msg.done"), msg)

        self._run_background(
            do_work,
            total=100,
            label=t("progress.watermark.applying"),
            on_done=on_done,
        )