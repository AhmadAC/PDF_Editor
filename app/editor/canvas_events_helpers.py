# app/editor/canvas_events_helpers.py
"""Shared helpers, stroke smoothing, geometry mutation, and live opacity dialog for canvas events."""

from __future__ import annotations

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from app.editor.canvas_overlay1 import (
    restore_overlay_snapshot,
    snapshot_overlay,
)
from app.i18n import t


class _OpacityAdjustDialog(QDialog):
    """Compact live-preview dialog for setting the opacity of the selected object."""

    def __init__(self, canvas, idx: int, initial_pct: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Object Opacity")
        self.setModal(True)
        self.setMinimumWidth(320)
        self._canvas = canvas
        self._idx = idx
        self._ov = canvas._overlays[idx]
        self._before_snap = snapshot_overlay(self._ov)
        self._was_untouched = bool(self._ov.get("_existing") and not self._ov.get("_dirty"))
        self._accepted = False

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(12)

        lbl = QLabel("Adjust Opacity:")
        v.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(5, 100)
        self._slider.setValue(max(5, min(100, int(initial_pct))))

        self._spin = QSpinBox()
        self._spin.setRange(5, 100)
        self._spin.setSuffix("%")
        self._spin.setValue(self._slider.value())
        self._spin.setFixedWidth(72)

        row.addWidget(self._slider, 1)
        row.addWidget(self._spin)
        v.addLayout(row)

        btns = QHBoxLayout()
        btns.addStretch()
        ca = QPushButton(t("btn.cancel"))
        ca.clicked.connect(self.reject)
        ok = QPushButton(t("btn.ok"))
        ok.setObjectName("btn_primary")
        ok.clicked.connect(self.accept)
        btns.addWidget(ca)
        btns.addWidget(ok)
        v.addLayout(btns)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)

    def _apply_live_preview(self, pct: int) -> None:
        self._ov["opacity"] = max(0.05, min(1.0, float(pct) / 100.0))
        if self._ov.get("_existing"):
            self._ov["_dirty"] = True
        if self._ov.get("_existing") or self._ov.get("_z_back"):
            self._canvas.refresh_pages({self._ov.get("page", 0)})
        self._canvas.update()

    def _on_slider(self, val: int) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(val)
        self._spin.blockSignals(False)
        self._apply_live_preview(val)

    def _on_spin(self, val: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(val)
        self._slider.blockSignals(False)
        self._apply_live_preview(val)

    def accept(self) -> None:
        self._accepted = True
        final_op = max(0.05, min(1.0, float(self._slider.value()) / 100.0))
        if self._was_untouched:
            self._ov["_dirty"] = True
            self._ov["opacity"] = final_op
            if hasattr(self._canvas, "overlay_committed"):
                self._canvas.overlay_committed.emit(self._ov)
        else:
            self._ov["opacity"] = final_op
            if hasattr(self._canvas, "overlay_transformed"):
                self._canvas.overlay_transformed.emit(self._ov, self._before_snap)
        self._canvas.overlay_changed.emit()
        self._canvas.refresh_pages({self._ov.get("page", 0)})
        self._canvas.update()
        super().accept()

    def reject(self) -> None:
        if not self._accepted:
            restore_overlay_snapshot(self._ov, self._before_snap)
            self._canvas.refresh_pages({self._ov.get("page", 0)})
            self._canvas.update()
        super().reject()


def _find_tab_editar(canvas):
    p = canvas.parentWidget() if hasattr(canvas, "parentWidget") else None
    while p is not None:
        if hasattr(p, "_set_mode_by_idx") and hasattr(p, "_set_text_submode"):
            return p
        p = p.parentWidget()
    return None


def smooth_stroke_points(pts: list[tuple[float, float]], iterations: int = 2) -> list[tuple[float, float]]:
    """Apply Chaikin's corner-cutting curve algorithm to smooth freehand strokes into natural curves."""
    if len(pts) <= 2:
        return pts
    smoothed = pts
    for _ in range(iterations):
        new_pts = [smoothed[0]]
        for i in range(len(smoothed) - 1):
            p0 = smoothed[i]
            p1 = smoothed[i + 1]
            q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
            r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
            new_pts.append(q)
            new_pts.append(r)
        new_pts.append(smoothed[-1])
        smoothed = new_pts
    return smoothed


def _overlay_geometry_changed(ov: dict, before_snap: dict | None) -> bool:
    if not before_snap:
        return False
    for key in ("rect", "bbox", "rects", "points", "point"):
        if ov.get(key) != before_snap.get(key):
            return True
    return False


def _translate_overlay(ov: dict, before_snap: dict, dx: float, dy: float) -> None:
    if "rect" in before_snap:
        orig_r = fitz.Rect(before_snap["rect"])
        ov["rect"] = fitz.Rect(orig_r.x0 + dx, orig_r.y0 + dy, orig_r.x1 + dx, orig_r.y1 + dy)
    if "bbox" in before_snap:
        orig_b = before_snap["bbox"]
        ov["bbox"] = [orig_b[0] + dx, orig_b[1] + dy, orig_b[2] + dx, orig_b[3] + dy]
    if "origin" in before_snap:
        orig_o = before_snap["origin"]
        ov["origin"] = [orig_o[0] + dx, orig_o[1] + dy]
    if "rects" in before_snap:
        ov["rects"] = [
            fitz.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
            for r in before_snap["rects"]
        ]
    if "points" in before_snap:
        ov["points"] = [
            (float(px) + dx, float(py) + dy)
            for px, py in before_snap["points"]
        ]
    if "point" in before_snap:
        orig_pt = before_snap["point"]
        ov["point"] = fitz.Point(orig_pt.x + dx, orig_pt.y + dy)


def _resize_overlay_from_rect(ov: dict, before_snap: dict, orig_r: fitz.Rect, new_r: fitz.Rect) -> None:
    if "rect" in ov or "rect" in before_snap:
        ov["rect"] = fitz.Rect(new_r)
    if "bbox" in ov or "bbox" in before_snap:
        ov["bbox"] = [new_r.x0, new_r.y0, new_r.x1, new_r.y1]

    orig_h = max(4.0, orig_r.height)
    scale_h = new_r.height / orig_h
    scale_w = new_r.width / max(4.0, orig_r.width)

    if ov.get("type") in ("text", "text_edit"):
        base_sz = float(before_snap.get("font_size", before_snap.get("size", 12.0)))
        new_sz = max(4.0, base_sz * scale_h)
        if "font_size" in ov or "font_size" in before_snap:
            ov["font_size"] = new_sz
        if "size" in ov or "size" in before_snap:
            ov["size"] = new_sz
        if "origin" in before_snap:
            ov["origin"] = [new_r.x0, new_r.y0 + new_sz * 0.85]
        if "point" in before_snap:
            ov["point"] = fitz.Point(new_r.x0, new_r.y0 + new_sz * 0.85)
    elif ov.get("type") == "draw" and "points" in before_snap:
        sx = (new_r.width / orig_r.width) if orig_r.width > 1e-3 else 1.0
        sy = (new_r.height / orig_r.height) if orig_r.height > 1e-3 else 1.0
        ov["points"] = [
            (
                new_r.x0 + (px - orig_r.x0) * sx,
                new_r.y0 + (py - orig_r.y0) * sy,
            )
            for px, py in before_snap["points"]
        ]
    elif ov.get("type") in ("check", "cross"):
        base_w = float(before_snap.get("width", 2.2))
        ov["width"] = max(1.5, base_w * ((scale_w + scale_h) * 0.5))