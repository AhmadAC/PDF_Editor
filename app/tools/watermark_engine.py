# app/tools/watermark_engine.py
"""Headless PDF/Pillow watermark preflight, page composition, and atomic output helpers."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

import fitz
from PIL import Image, ImageChops

from app.i18n import t
from app.pdf_io import atomic_pdf_write
from app.pdf_password import authenticate_fitz
from app.utils import WrongPasswordError, parse_pages


class EmptyPdfError(ValueError):
    """Raised when either the base PDF or stamp PDF contains zero pages."""

    def __init__(self, msg_key: str):
        super().__init__(t(msg_key))
        self.msg_key = msg_key


@dataclass(frozen=True)
class WatermarkPreflight:
    wm_total: int
    base_total: int
    expanding_base: bool
    targets: list[int]
    target_set: set[int]


@dataclass(frozen=True)
class WatermarkJobConfig:
    pdf_path: str
    wm_path: str
    out_path: str
    base_password: str
    wm_password: str
    match_pages: bool
    include_omitted_wm: bool
    over: bool
    base_opacity: float
    stamp_opacity: float
    transparent_white: bool
    single_wm_idx: int


def preflight_watermark(
    pdf_path: str,
    wm_path: str,
    wm_pwd: str,
    pages_text: str,
    match_pages: bool,
    include_omitted_wm: bool,
    open_base_fitz: Callable[[str], fitz.Document],
) -> WatermarkPreflight:
    """Validate source and stamp PDFs and compute target page indices."""
    wm_doc = fitz.open(wm_path)
    try:
        if wm_doc.needs_pass:
            if not wm_pwd or authenticate_fitz(wm_doc, wm_pwd) is None:
                raise WrongPasswordError(t("tool.err.wrong_password"))
        if wm_doc.page_count <= 0:
            raise EmptyPdfError("tool.watermark.empty_wm")
        wm_total = wm_doc.page_count
    finally:
        wm_doc.close()

    src_probe = open_base_fitz(pdf_path)
    try:
        if src_probe.page_count <= 0:
            raise EmptyPdfError("tool.watermark.empty_source")
        base_total = src_probe.page_count
    finally:
        src_probe.close()

    expanding_base = bool(match_pages and wm_total > base_total)
    range_limit = max(wm_total, base_total) if (expanding_base or include_omitted_wm) else base_total
    txt = pages_text.strip()
    targets = sorted(parse_pages(txt, range_limit)) if txt else list(range(range_limit))
    return WatermarkPreflight(
        wm_total=wm_total,
        base_total=base_total,
        expanding_base=expanding_base,
        targets=targets,
        target_set=set(targets),
    )


def _render_rgba_png(
    doc_obj: fitz.Document, p_idx: int, opacity_f: float, strip_white: bool
) -> tuple[bytes, float, float]:
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


def _compose_page(
    out_doc: fitz.Document,
    src_doc: fitz.Document,
    b_idx: int,
    wm_doc_obj: fitz.Document,
    wm_idx: int,
    cfg: WatermarkJobConfig,
    wm_cache: dict[int, tuple[bytes, float, float]],
    base_cache: dict[int, bytes],
) -> None:
    src_page = src_doc[b_idx]
    pr = src_page.rect
    new_pg = out_doc.new_page(width=pr.width, height=pr.height)

    if wm_idx not in wm_cache:
        wm_cache[wm_idx] = _render_rgba_png(
            wm_doc_obj, wm_idx, cfg.stamp_opacity, cfg.transparent_white
        )
    wm_png, ww, wh = wm_cache[wm_idx]
    wm_rect = _fit_rect(new_pg.rect, ww, wh)

    if cfg.base_opacity >= 0.999:
        new_pg.show_pdf_page(new_pg.rect, src_doc, b_idx)
        if cfg.stamp_opacity > 0.0:
            new_pg.insert_image(wm_rect, stream=wm_png, overlay=cfg.over)
    elif cfg.over:
        if b_idx not in base_cache:
            base_cache[b_idx] = _render_blended_white_png(src_doc, b_idx, cfg.base_opacity)
        new_pg.insert_image(new_pg.rect, stream=base_cache[b_idx], overlay=True)
        if cfg.stamp_opacity > 0.0:
            new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
    else:
        if b_idx not in base_cache:
            base_cache[b_idx] = _render_rgba_png(
                src_doc, b_idx, cfg.base_opacity, cfg.transparent_white
            )[0]
        if cfg.stamp_opacity > 0.0:
            new_pg.insert_image(wm_rect, stream=wm_png, overlay=True)
        if cfg.base_opacity > 0.0:
            new_pg.insert_image(new_pg.rect, stream=base_cache[b_idx], overlay=True)


def execute_watermark(
    cfg: WatermarkJobConfig, pf: WatermarkPreflight, worker: Any
) -> str | None:
    """Compose watermarked pages in the background worker and atomically write out_path."""
    wm_d = fitz.open(cfg.wm_path)
    src_d = fitz.open(cfg.pdf_path)
    out_d = fitz.open()
    try:
        if wm_d.needs_pass:
            if not (cfg.wm_password and authenticate_fitz(wm_d, cfg.wm_password) is not None):
                raise WrongPasswordError(t("tool.err.wrong_password"))
        if src_d.needs_pass:
            if not (cfg.base_password and authenticate_fitz(src_d, cfg.base_password) is not None):
                raise WrongPasswordError(t("tool.err.wrong_password"))

        wm_cache: dict[int, tuple[bytes, float, float]] = {}
        base_cache: dict[int, bytes] = {}

        if pf.expanding_base:
            if cfg.include_omitted_wm:
                total_steps = max(1, pf.wm_total)
                composed_count = 0
                for wm_idx in range(pf.wm_total):
                    if worker.is_cancelled():
                        return None
                    if wm_idx in pf.target_set:
                        b_src_idx = composed_count % src_d.page_count
                        composed_count += 1
                        _compose_page(out_d, src_d, b_src_idx, wm_d, wm_idx, cfg, wm_cache, base_cache)
                    else:
                        out_d.insert_pdf(wm_d, from_page=wm_idx, to_page=wm_idx)
                    worker.progress.emit(
                        int((wm_idx + 1) / total_steps * 100),
                        t("progress.watermark.page", current=wm_idx + 1, total=total_steps),
                    )
            else:
                total_steps = max(1, len(pf.targets))
                for step_idx, wm_idx in enumerate(pf.targets):
                    if worker.is_cancelled():
                        return None
                    b_src_idx = step_idx % src_d.page_count
                    _compose_page(out_d, src_d, b_src_idx, wm_d, wm_idx, cfg, wm_cache, base_cache)
                    worker.progress.emit(
                        int((step_idx + 1) / total_steps * 100),
                        t("progress.watermark.page", current=step_idx + 1, total=total_steps),
                    )
        else:
            n = src_d.page_count
            total_iter = max(n, pf.wm_total) if cfg.include_omitted_wm else n
            for i in range(total_iter):
                if worker.is_cancelled():
                    return None
                if i < n and i in pf.target_set:
                    wm_idx = (i % pf.wm_total) if cfg.match_pages else cfg.single_wm_idx
                    _compose_page(out_d, src_d, i, wm_d, wm_idx, cfg, wm_cache, base_cache)
                elif cfg.include_omitted_wm:
                    if i < pf.wm_total:
                        out_d.insert_pdf(wm_d, from_page=i, to_page=i)
                elif i < n:
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
            cfg.out_path,
            sources=[cfg.pdf_path, cfg.wm_path],
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
    return cfg.out_path


def copy_file_atomic(src_temp: str, chosen_path: str) -> None:
    """Atomically copy a temporary PDF to a user-chosen destination path."""
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