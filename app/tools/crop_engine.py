# app/tools/crop_engine.py
"""Headless PDF crop math, rotation mapping, and atomic PDF persistence."""

from __future__ import annotations

import contextlib
from typing import Callable

import fitz

from app.pdf_io import atomic_pdf_write
from app.utils import parse_pages


def resolve_target_pages(
    page_count: int,
    mode: int,
    current_page_1based: int,
    custom_text: str,
    subset: int,
) -> list[int]:
    """Resolve 0-based page indices based on mode (all/current/custom) and odd/even subset."""
    if page_count <= 0:
        return []
    if mode == 0:
        candidates = list(range(page_count))
    elif mode == 1:
        pg = current_page_1based - 1
        candidates = [pg] if 0 <= pg < page_count else [0]
    else:
        txt = custom_text.strip()
        if not txt:
            candidates = list(range(page_count))
        else:
            try:
                candidates = parse_pages(txt, page_count)
            except ValueError:
                candidates = []

    if subset == 1:
        return [p for p in candidates if (p + 1) % 2 != 0]
    if subset == 2:
        return [p for p in candidates if (p + 1) % 2 == 0]
    return candidates


def map_margins_for_rotation(
    top_m: float, bot_m: float, left_m: float, right_m: float, total_rot: int
) -> tuple[float, float, float, float]:
    """Map display-space (left, top, right, bottom) margins to unrotated PDF cropbox space."""
    rot = total_rot % 360
    if rot == 90:
        return top_m, right_m, bot_m, left_m
    if rot == 180:
        return right_m, bot_m, left_m, top_m
    if rot == 270:
        return bot_m, left_m, top_m, right_m
    return left_m, top_m, right_m, bot_m


def compute_page_crops(
    doc: fitz.Document,
    targets: list[int],
    margins: tuple[float, float, float, float],
    existing_crops: dict[int, tuple[float, float, float, float]],
    get_user_rot: Callable[[int], int],
    check_min_span: bool = True,
) -> tuple[dict[int, tuple[float, float, float, float]], bool]:
    """Compute updated crop rectangles (x0, y0, x1, y1) for target pages."""
    top_m, bot_m, left_m, right_m = margins
    new_crops = dict(existing_crops)
    any_changed = False

    for idx in targets:
        if idx < 0 or idx >= doc.page_count:
            continue
        page = doc[idx]
        total_rot = (page.rotation + get_user_rot(idx)) % 360
        u_left, u_top, u_right, u_bottom = map_margins_for_rotation(
            top_m, bot_m, left_m, right_m, total_rot
        )

        if idx in new_crops:
            base_x0, base_y0, base_x1, base_y1 = new_crops[idx]
        else:
            cb = page.cropbox
            base_x0, base_y0, base_x1, base_y1 = cb.x0, cb.y0, cb.x1, cb.y1

        new_x0 = base_x0 + u_left
        new_y0 = base_y0 + u_top
        new_x1 = base_x1 - u_right
        new_y1 = base_y1 - u_bottom

        if check_min_span and ((new_x1 - new_x0 < 10) or (new_y1 - new_y0 < 10)):
            continue

        crop_rect = fitz.Rect(new_x0, new_y0, new_x1, new_y1) & page.mediabox
        if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
            new_crops[idx] = (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)
            any_changed = True

    return new_crops, any_changed


def compute_ref_dimensions(
    open_fitz_fn: Callable[[str], fitz.Document],
    pdf_path: str | None,
    page_idx: int,
    applied_crops: dict[int, tuple[float, float, float, float]],
    user_rot: int,
    fallback_w: float,
    fallback_h: float,
) -> tuple[float, float]:
    """Compute display-space (width, height) in points for the reference page."""
    total_rot = 0
    u_w, u_h = fallback_w, fallback_h

    if pdf_path:
        doc = None
        try:
            doc = open_fitz_fn(pdf_path)
            page = doc[page_idx]
            total_rot = (page.rotation + user_rot) % 360
            if page_idx in applied_crops:
                c = applied_crops[page_idx]
                u_w = c[2] - c[0]
                u_h = c[3] - c[1]
            else:
                u_w = page.cropbox.width
                u_h = page.cropbox.height
        except Exception:
            pass
        finally:
            if doc is not None:
                with contextlib.suppress(Exception):
                    doc.close()
    elif page_idx in applied_crops:
        c = applied_crops[page_idx]
        u_w = c[2] - c[0]
        u_h = c[3] - c[1]

    if total_rot in (90, 270):
        return u_h, u_w
    return u_w, u_h


def save_cropped_pdf(
    open_fitz_fn: Callable[[str], fitz.Document],
    pdf_path: str,
    out_path: str,
    crops_to_save: dict[int, tuple[float, float, float, float]],
    before_write: Callable[[], None] | None = None,
) -> None:
    """Apply cropboxes to pdf_path and atomically persist to out_path."""
    doc = open_fitz_fn(pdf_path)
    try:
        for idx, crop_tuple in crops_to_save.items():
            if 0 <= idx < doc.page_count:
                page = doc[idx]
                crop_rect = fitz.Rect(crop_tuple) & page.mediabox
                if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
                    page.set_cropbox(crop_rect)

        if before_write is not None:
            before_write()

        atomic_pdf_write(
            doc,
            out_path,
            sources=[pdf_path],
            save_opts={"garbage": 4, "deflate": True},
            close_writer=True,
        )
    finally:
        with contextlib.suppress(Exception):
            doc.close()