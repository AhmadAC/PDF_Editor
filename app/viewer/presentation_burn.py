# app/viewer/presentation_burn.py
"""PDF_Editor – Presentation annotation export and document burning mixin."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import QWidget

from app.utils import find_system_font_file
from app.viewer.annotation_layer import ToolMode

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object

_log = logging.getLogger(__name__)


def _box_font_metrics(overlay: Any, font_size: float) -> QFontMetricsF:
    """Measure QFont('Segoe UI') with the exact same QPaintDevice metrics used by AnnotationPainter."""
    qfont = QFont("Segoe UI", int(font_size))
    qfont.setPointSizeF(font_size)
    if overlay is not None:
        return QFontMetricsF(qfont, overlay)
    return QFontMetricsF(qfont)


class PresentationBurnMixin(_Base):
    """Mixin for synchronizing annotation strokes/boxes back to TabEditar or burning into PyMuPDF documents."""

    if TYPE_CHECKING:
        _edit_tab: Any
        _overlay: Any
        _doc: fitz.Document | None
        _page_crops: dict[int, tuple[float, float, float, float]]
        _page_rotations: dict[int, int]

        def _get_page_geometry(self, page_idx: int) -> tuple[float, float, float, float, float, float]: ...
        def logicalDpiY(self) -> int: ...

    def _export_annotations_to_edit_tab(self) -> bool:
        edit_tab = self._edit_tab
        if edit_tab is None:
            return False

        pages_with_annots = sorted(
            set(self._overlay._strokes.keys()) | set(self._overlay._text_boxes.keys())
        )
        if not pages_with_annots:
            return False

        dpi_source = self._overlay if self._overlay is not None else self
        dpi_y = float(dpi_source.logicalDpiY()) if hasattr(dpi_source, "logicalDpiY") and dpi_source.logicalDpiY() > 0 else 96.0
        dpi_ratio = dpi_y / 72.0
        added_any = False

        for page_idx in pages_with_annots:
            pw, ph, offset_x, offset_y, eff_w, eff_h = self._get_page_geometry(page_idx)
            scale_x = eff_w / max(1.0, pw)
            scale_y = eff_h / max(1.0, ph)

            for stroke in self._overlay._strokes.get(page_idx, []):
                if not stroke.points:
                    continue
                clean_pts: list[tuple[float, float]] = []
                for pt in stroke.points:
                    px = max(0.0, min(eff_w, (pt.x() - offset_x) * scale_x))
                    py = max(0.0, min(eff_h, (pt.y() - offset_y) * scale_y))
                    if (
                        not clean_pts
                        or abs(px - clean_pts[-1][0]) > 0.05
                        or abs(py - clean_pts[-1][1]) > 0.05
                    ):
                        clean_pts.append((round(px, 2), round(py, 2)))
                if len(clean_pts) == 1:
                    clean_pts.append((round(clean_pts[0][0] + 0.5, 2), clean_pts[0][1]))
                if len(clean_pts) >= 2:
                    col = (
                        float(stroke.color.redF()),
                        float(stroke.color.greenF()),
                        float(stroke.color.blueF()),
                    )
                    w_pdf = max(1, int(round(stroke.width * scale_x)))
                    if stroke.kind == ToolMode.HIGHLIGHTER:
                        xs = [p[0] for p in clean_pts]
                        ys = [p[1] for p in clean_pts]
                        half_w = max(4.0, (stroke.width * scale_x) / 2.0)
                        if (max(ys) - min(ys)) <= half_w * 3.0:
                            r_hi = fitz.Rect(
                                max(0.0, min(xs) - half_w * 0.5),
                                max(0.0, min(ys) - half_w),
                                min(eff_w, max(xs) + half_w * 0.5),
                                min(eff_h, max(ys) + half_w),
                            )
                            edit_tab._add({
                                "type": "highlight",
                                "page": page_idx,
                                "rect": r_hi,
                                "color": col,
                            })
                            added_any = True
                            continue
                    edit_tab._add({
                        "type": "draw",
                        "page": page_idx,
                        "points": clean_pts,
                        "color": col,
                        "width": w_pdf,
                    })
                    added_any = True

            for box in self._overlay._text_boxes.get(page_idx, []):
                if not box.text.strip():
                    continue
                fm = _box_font_metrics(self._overlay, box.font_size)

                # Outer cloud card rect matching presentation box.rect geometry
                cloud_x0 = (box.rect.left() - offset_x) * scale_x
                cloud_y0 = (box.rect.top() - offset_y) * scale_y
                cloud_w = max(24.0, box.rect.width() * scale_x)
                cloud_h = max(16.0, box.rect.height() * scale_y)
                full_rect = fitz.Rect(cloud_x0, cloud_y0, cloud_x0 + cloud_w, cloud_y0 + cloud_h)

                # Inner text baseline matching presentation inner_rect offset (7px left, 5px top)
                inner_x0 = cloud_x0 + (7.0 * scale_x)
                inner_y0 = cloud_y0 + (5.0 * scale_y)
                first_baseline_y = inner_y0 + (fm.ascent() * scale_y)
                pdf_font_size = max(4.0, float(box.font_size * dpi_ratio * scale_y))

                col = (
                    float(box.color.redF()),
                    float(box.color.greenF()),
                    float(box.color.blueF()),
                )
                edit_tab._add({
                    "type": "text",
                    "page": page_idx,
                    "point": fitz.Point(inner_x0, first_baseline_y),
                    "rect": full_rect,
                    "cloud_rect": full_rect,
                    "text": box.text,
                    "size": pdf_font_size,
                    "color": col,
                    "font": "Segoe UI",
                    "flags": 0,
                    "has_cloud": bool(box.has_cloud),
                    "cloud_enabled": bool(box.has_cloud),
                    "has_stroke": bool(box.has_stroke),
                    "outline_enabled": bool(box.has_stroke),
                    "outline_thickness": 1.0,
                    "outline_color": (0.0, 0.0, 0.0),
                })
                added_any = True

        return added_any

    def _burn_annotations_to_doc(self) -> bool:
        if not self._doc:
            return False

        pages_with_annots = sorted(
            set(self._overlay._strokes.keys()) | set(self._overlay._text_boxes.keys())
        )
        if not pages_with_annots:
            return False

        any_modified = False
        embedded_font = False
        segoe_ttf = find_system_font_file("Segoe UI")
        has_segoe = bool(segoe_ttf and os.path.isfile(segoe_ttf))

        dpi_source = self._overlay if self._overlay is not None else self
        dpi_y = float(dpi_source.logicalDpiY()) if hasattr(dpi_source, "logicalDpiY") and dpi_source.logicalDpiY() > 0 else 96.0
        dpi_ratio = dpi_y / 72.0

        for page_idx in pages_with_annots:
            if page_idx < 0 or page_idx >= self._doc.page_count:
                continue

            page = self._doc[page_idx]

            crop = self._page_crops.get(page_idx)
            if crop:
                crop_rect = fitz.Rect(crop) & page.mediabox
                if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
                    page.set_cropbox(crop_rect)

            rot = self._page_rotations.get(page_idx, 0) % 360
            pw, ph, offset_x, offset_y, eff_w, eff_h = self._get_page_geometry(page_idx)

            scale_x = eff_w / max(1.0, pw)
            scale_y = eff_h / max(1.0, ph)

            derot = page.derotation_matrix
            page_rot = page.rotation

            def to_visual_pdf(pt) -> fitz.Point:
                vx = (pt.x() - offset_x) * scale_x
                vy = (pt.y() - offset_y) * scale_y
                if rot == 90:
                    ux, uy = vy, eff_w - vx
                elif rot == 180:
                    ux, uy = eff_w - vx, eff_h - vy
                elif rot == 270:
                    ux, uy = eff_h - vy, vx
                else:
                    ux, uy = vx, vy
                return fitz.Point(ux + page.rect.x0, uy + page.rect.y0)

            def to_unrotated_pdf(pt) -> fitz.Point:
                return to_visual_pdf(pt) * derot

            strokes = self._overlay._strokes.get(page_idx, [])
            for stroke in strokes:
                if not stroke.points:
                    continue
                raw_pts = [to_unrotated_pdf(p) for p in stroke.points]
                clean_pts: list[fitz.Point] = []
                for pt in raw_pts:
                    if not clean_pts or abs(pt.x - clean_pts[-1].x) > 1e-3 or abs(pt.y - clean_pts[-1].y) > 1e-3:
                        clean_pts.append(pt)
                if len(clean_pts) == 1:
                    clean_pts.append(fitz.Point(clean_pts[0].x + 0.5, clean_pts[0].y))
                if len(clean_pts) >= 2:
                    col = (
                        float(stroke.color.redF()),
                        float(stroke.color.greenF()),
                        float(stroke.color.blueF()),
                    )
                    stroke_w = max(0.75, float(stroke.width) * scale_x)
                    try:
                        shape = page.new_shape()
                        shape.draw_polyline(clean_pts)
                        if stroke.kind == ToolMode.HIGHLIGHTER:
                            shape.finish(
                                color=col,
                                width=stroke_w,
                                stroke_opacity=0.35,
                                lineCap=1,
                                lineJoin=1,
                            )
                        else:
                            shape.finish(
                                color=col,
                                width=stroke_w,
                                lineCap=1,
                                lineJoin=1,
                            )
                        shape.commit()
                        any_modified = True
                    except Exception:
                        try:
                            vis_pts = [(p.x, p.y) for p in clean_pts]
                            annot = page.add_ink_annot([vis_pts])
                            annot.set_colors(stroke=col)
                            annot.set_border(width=max(1, int(round(stroke_w))))
                            if stroke.kind == ToolMode.HIGHLIGHTER:
                                annot.set_opacity(0.4)
                                with contextlib.suppress(Exception):
                                    annot.set_blendmode(fitz.PDF_BM_Multiply)
                            annot.update()
                            any_modified = True
                        except Exception as e:
                            _log.warning("Failed to burn stroke: %s", e)

            text_boxes = self._overlay._text_boxes.get(page_idx, [])
            for box in text_boxes:
                if not box.text.strip():
                    continue
                try:
                    tl_unrot = to_unrotated_pdf(box.rect.topLeft())
                    br_unrot = to_unrotated_pdf(box.rect.bottomRight())
                    unrot_rect = fitz.Rect(tl_unrot, br_unrot).normalize()
                    pdf_font_size = max(4.0, float(box.font_size * dpi_ratio * scale_y))

                    # Pure 40% white cloud card with no harsh border, matching presentation mode
                    if box.has_cloud:
                        try:
                            shape = page.new_shape()
                            shape.draw_rect(unrot_rect)
                            shape.finish(
                                color=None,
                                fill=(1.0, 1.0, 1.0),
                                fill_opacity=0.4,
                                stroke_opacity=0.0,
                            )
                            shape.commit(overlay=True)
                        except Exception as e_cloud:
                            _log.debug("Shape cloud failed, using annot fallback: %s", e_cloud)
                            bg_annot = page.add_rect_annot(unrot_rect)
                            bg_annot.set_colors(stroke=None, fill=(1, 1, 1))
                            bg_annot.set_opacity(0.4)
                            bg_annot.update()

                    text_color = (
                        float(box.color.redF()),
                        float(box.color.greenF()),
                        float(box.color.blueF()),
                    )

                    fm = _box_font_metrics(self._overlay, box.font_size)
                    ascent_pdf = fm.ascent() * scale_y
                    line_step_pdf = fm.lineSpacing() * scale_y

                    pad_x = 7.0 * scale_x
                    pad_y = 5.0 * scale_y
                    inner_x0 = to_visual_pdf(box.rect.topLeft()).x + pad_x
                    inner_y0 = to_visual_pdf(box.rect.topLeft()).y + pad_y

                    lines = box.text.split("\n")
                    text_kw: dict[str, Any] = {
                        "fontsize": pdf_font_size,
                        "rotate": (page_rot + rot) % 360,
                    }
                    if has_segoe:
                        text_kw["fontname"] = "pres_segoe"
                        text_kw["fontfile"] = segoe_ttf
                        embedded_font = True
                    else:
                        text_kw["fontname"] = "helv"

                    # 1px-equivalent clean outline offsets avoiding bulky bubble distortion
                    stroke_r = max(0.65, min(1.2, pdf_font_size * 0.035))
                    stroke_offsets = [
                        (-stroke_r, 0.0), (stroke_r, 0.0), (0.0, -stroke_r), (0.0, stroke_r),
                        (-stroke_r * 0.707, -stroke_r * 0.707), (stroke_r * 0.707, stroke_r * 0.707),
                        (-stroke_r * 0.707, stroke_r * 0.707), (stroke_r * 0.707, -stroke_r * 0.707),
                    ]

                    for li, line_txt in enumerate(lines):
                        if not line_txt:
                            continue
                        baseline_vis = fitz.Point(
                            inner_x0,
                            inner_y0 + ascent_pdf + li * line_step_pdf,
                        )
                        if box.has_stroke:
                            for dx, dy in stroke_offsets:
                                out_vis = fitz.Point(
                                    baseline_vis.x + dx,
                                    baseline_vis.y + dy,
                                )
                                page.insert_text(
                                    out_vis * derot,
                                    line_txt,
                                    color=(0.0, 0.0, 0.0),
                                    **text_kw,
                                )
                        page.insert_text(
                            baseline_vis * derot,
                            line_txt,
                            color=text_color,
                            **text_kw,
                        )
                    any_modified = True
                except Exception as e:
                    _log.warning("Failed to burn text box: %s", e)

        if embedded_font:
            with contextlib.suppress(Exception):
                self._doc.subset_fonts()

        return any_modified