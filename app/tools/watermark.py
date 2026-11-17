# app/tools/watermark.py
"""PDF_Editor – TabMarcaDagua: stamp PDF A onto PDF B with independent opacities and auto-page duplication."""

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

        # Base PDF (open document) opacity slider (5% - 100%)
        base_op_row = QHBoxLayout()
        base_op_row.setContentsMargins(0, 0, 0, 0)
        base_op_row.setSpacing(8)
        self.slider_base_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_base_opacity.setRange(5, 100)
        self.slider_base_opacity.setValue(100)
        self.lbl_base_opacity_val = QLabel("100%")
        self.lbl_base_opacity_val.setMinimumWidth(40)
        self.slider_base_opacity.valueChanged.connect(
            lambda v: self.lbl_base_opacity_val.setText(f"{v}%")
        )
        base_op_row.addWidget(self.slider_base_opacity, 1)
        base_op_row.addWidget(self.lbl_base_opacity_val)
        form.addRow("Base opacity:", base_op_row)

        # Stamp PDF (PDF A) opacity slider (5% - 100%)
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
        form.addRow("Stamp opacity:", op_row)

        self.chk_transparent_white = QCheckBox("Make white background transparent")
        self.chk_transparent_white.setChecked(True)
        self.chk_transparent_white.setToolTip(
            "Removes solid white page background from the overlay PDF so underlying content shows through"
        )
        form.addRow("", self.chk_transparent_white)

        lbl_tip = QLabel("💡 Adjust Base opacity to fade the open PDF, or Stamp opacity to fade the overlay PDF.")
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
        base_opacity_pct = self.slider_base_opacity.value()
        base_opacity = max(0.05, min(1.0, base_opacity_pct / 100.0))
        stamp_opacity_pct = self.slider_opacity.value()
        stamp_opacity = max(0.05, min(1.0, stamp_opacity_pct / 100.0))
        transparent_white = self.chk_transparent_white.isChecked()
        single_wm_idx = min(max(0, self.spin_wm_page.value() - 1), wm_total - 1)
        pwd = self._pdf_password

        print(
            f"[DEBUG][Watermark] Config: base_opacity={base_opacity_pct}%, "
            f"stamp_opacity={stamp_opacity_pct}%, over={over}, transparent_white={transparent_white}"
        )

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
                new_pg.insert_image(wm_rect, stream=wm_png, overlay=over)
            else:
                if over:
                    if b_idx not in base_cache:
                        base_cache[b_idx] = _render_blended_white_png(src_doc, b_idx, base_opacity)
                    new_pg.insert_image(new_pg.rect, stream=base_cache[b_idx], overlay=True)
                    new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
                else:
                    if b_idx not in base_cache:
                        base_cache[b_idx] = _render_rgba_png(src_doc, b_idx, base_opacity, transparent_white)[0]
                    new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
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
                    total_steps = max(1, len(targets))
                    for step_idx, wm_idx in enumerate(targets):
                        if worker.is_cancelled():
                            return None
                        b_src_idx = step_idx % src_d.page_count
                        _compose_page(out_d, src_d, b_src_idx, wm_d, wm_idx, wm_cache, base_cache)
                        print(
                            f"[DEBUG][Watermark] Auto-duplicated base page {b_src_idx + 1} -> out page {step_idx + 1} "
                            f"& stamped PDF A page {wm_idx + 1}"
                        )
                        worker.progress.emit(
                            int((step_idx + 1) / total_steps * 100),
                            t("progress.watermark.page", current=step_idx + 1, total=total_steps),
                        )
                else:
                    n = src_d.page_count
                    for i in range(n):
                        if worker.is_cancelled():
                            return None
                        if i in target_set:
                            wm_idx = (i % wm_total) if match_pages else single_wm_idx
                            _compose_page(out_d, src_d, i, wm_d, wm_idx, wm_cache, base_cache)
                            print(f"[DEBUG][Watermark] Stamped PDF A page {wm_idx + 1} onto PDF B page {i + 1}/{n}")
                        else:
                            out_d.insert_pdf(src_d, from_page=i, to_page=i)
                        worker.progress.emit(
                            int((i + 1) / max(1, n) * 100),
                            t("progress.watermark.page", current=i + 1, total=n),
                        )

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
                    out_d,
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
                    src_d.close()
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