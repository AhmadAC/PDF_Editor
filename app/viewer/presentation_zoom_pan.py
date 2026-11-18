# app/viewer/presentation_zoom_pan.py
"""PDF_Editor – Presentation mode zoom calculations, mouse panning, and coordinate mapping mixin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

if TYPE_CHECKING:
    import fitz
    _Base = QWidget
else:
    _Base = object


class PresentationZoomPanMixin(_Base):
    """Mixin handling smooth zoom scaling, panning offsets, and view coordinate conversions."""

    if TYPE_CHECKING:
        _current: int
        _total: int
        _zoom: float | None
        _base_fit_zoom: float
        _zoom_factor: float
        _pan_x: int
        _pan_y: int
        _is_panning: bool
        _pan_start_pos: QPoint
        _pan_start_offset: QPoint
        _pixmap: QPixmap | None
        _doc: fitz.Document | None
        _page_render_info: dict[int, dict[str, float]]
        _page_rotations: dict[int, int]
        _overlay: Any

        def _render(self, transition: bool = False) -> None: ...
        def _go_to_page(self, new_page: int, align_bottom: bool = False) -> None: ...

    def _record_current_render_info(self, pw: float, ph: float, eff_w: float, eff_h: float) -> None:
        offset_x = float(self._pan_x) if pw > self.width() else (self.width() - pw) / 2.0
        offset_y = float(self._pan_y) if ph > self.height() else (self.height() - ph) / 2.0
        self._page_render_info[self._current] = {
            "pw": float(pw),
            "ph": float(ph),
            "offset_x": float(offset_x),
            "offset_y": float(offset_y),
            "eff_w": float(eff_w),
            "eff_h": float(eff_h),
        }

    def _update_current_render_offsets(self) -> None:
        info = self._page_render_info.get(self._current)
        if not info:
            return
        pw = info["pw"]
        ph = info["ph"]
        info["offset_x"] = float(self._pan_x) if pw > self.width() else (self.width() - pw) / 2.0
        info["offset_y"] = float(self._pan_y) if ph > self.height() else (self.height() - ph) / 2.0
        
        # Keep annotation overlay geometry and transformation explicitly locked to the rendered page surface
        if hasattr(self, "_overlay") and self._overlay is not None:
            self._overlay.setGeometry(self.rect())

    def _zoom_in(self, anchor_pos: QPoint | None = None):
        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom
        new_zoom = min(self._base_fit_zoom * 8.0, old_zoom * 1.25)
        self._apply_anchored_zoom(new_zoom, anchor_pos)

    def _zoom_out(self, anchor_pos: QPoint | None = None):
        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom
        new_zoom = max(self._base_fit_zoom * 0.25, old_zoom / 1.25)
        self._apply_anchored_zoom(new_zoom, anchor_pos)

    def _zoom_reset(self):
        if not self._pixmap:
            return
        sw = float(self.width())
        sh = float(self.height())
        eff_w = getattr(self, "_rendered_pw", sw) / (self._zoom or 1.0)
        eff_h = getattr(self, "_rendered_ph", sh) / (self._zoom or 1.0)
        fit_screen_zoom = min(sw / max(1.0, eff_w), sh / max(1.0, eff_h))

        self._zoom = fit_screen_zoom
        self._pan_x = 0
        self._pan_y = 0
        self._render(transition=False)

    def _apply_anchored_zoom(self, new_zoom: float, anchor_pos: QPoint | None = None):
        if not self._pixmap:
            self._zoom = new_zoom
            self._render(transition=False)
            return

        sw = float(self.width())
        sh = float(self.height())
        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom

        min_z = self._base_fit_zoom * 0.25
        max_z = self._base_fit_zoom * 8.0
        new_zoom = max(min_z, min(max_z, new_zoom))

        ratio = new_zoom / old_zoom

        old_pw = getattr(self, "_rendered_pw", sw)
        old_ph = getattr(self, "_rendered_ph", sh)

        old_x = (sw - old_pw) / 2.0 if old_pw <= sw else float(self._pan_x)
        old_y = (sh - old_ph) / 2.0 if old_ph <= sh else float(self._pan_y)

        if anchor_pos is not None:
            vp_x = float(anchor_pos.x())
            vp_y = float(anchor_pos.y())
        else:
            vp_x = sw / 2.0
            vp_y = sh / 2.0

        page_x = vp_x - old_x
        page_y = vp_y - old_y

        new_pan_x = vp_x - page_x * ratio
        new_pan_y = vp_y - page_y * ratio

        self._zoom = new_zoom
        self._zoom_factor = new_zoom / max(1e-4, self._base_fit_zoom)
        self._pan_x = int(round(new_pan_x))
        self._pan_y = int(round(new_pan_y))
        self._render(transition=False)

    def _scroll_page(self, up: bool, amount: float = 80.0):
        if not self._pixmap or not hasattr(self, "_rendered_ph"):
            return
        ph = getattr(self, "_rendered_ph", float(self.height()))
        sh = float(self.height())

        if ph <= sh:
            if up:
                self._go_to_page(self._current - 1, align_bottom=True)
            else:
                self._go_to_page(self._current + 1, align_bottom=False)
        else:
            min_pan_y = sh - ph
            max_pan_y = 0.0
            if up:
                new_pan_y = self._pan_y + amount
                if new_pan_y > max_pan_y:
                    if self._current > 0:
                        self._go_to_page(self._current - 1, align_bottom=True)
                    else:
                        self._pan_y = int(round(max_pan_y))
                        self._update_current_render_offsets()
                        self.update()
                else:
                    self._pan_y = int(round(new_pan_y))
                    self._update_current_render_offsets()
                    self.update()
            else:
                new_pan_y = self._pan_y - amount
                if new_pan_y < min_pan_y:
                    if self._current < self._total - 1:
                        self._go_to_page(self._current + 1, align_bottom=False)
                    else:
                        self._pan_y = int(round(min_pan_y))
                        self._update_current_render_offsets()
                        self.update()
                else:
                    self._pan_y = int(round(new_pan_y))
                    self._update_current_render_offsets()
                    self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = e.position().toPoint()
            if e.angleDelta().y() > 0:
                self._zoom_in(anchor_pos=pos)
            else:
                self._zoom_out(anchor_pos=pos)
            e.accept()
        else:
            delta = e.angleDelta().y()
            if delta > 0:
                self._scroll_page(up=True, amount=abs(delta) * 0.6)
            elif delta < 0:
                self._scroll_page(up=False, amount=abs(delta) * 0.6)
            e.accept()

    def _get_page_geometry(self, page_idx: int) -> tuple[float, float, float, float, float, float]:
        info = self._page_render_info.get(page_idx)
        if info is not None:
            return (
                info["pw"],
                info["ph"],
                info["offset_x"],
                info["offset_y"],
                info["eff_w"],
                info["eff_h"],
            )

        screen = self.screen() or QApplication.primaryScreen()
        geom = screen.geometry()
        sw = float(self.width()) if self.width() > 100 else float(geom.width())
        sh = float(self.height()) if self.height() > 100 else float(geom.height())

        if self._doc is not None and 0 <= page_idx < self._doc.page_count:
            page = self._doc[page_idx]
            rot = self._page_rotations.get(page_idx, 0) % 360
            eff_w = page.rect.height if rot in (90, 270) else page.rect.width
            eff_h = page.rect.width if rot in (90, 270) else page.rect.height
        else:
            eff_w = 595.0
            eff_h = 842.0
        eff_w = max(1.0, eff_w)
        eff_h = max(1.0, eff_h)

        base_zoom = self._base_fit_zoom if self._base_fit_zoom > 0 else min(sw / eff_w, sh / eff_h)
        zoom = self._zoom if self._zoom and self._zoom > 0 else (base_zoom * self._zoom_factor)
        pw = eff_w * zoom
        ph = eff_h * zoom
        offset_x = float(self._pan_x) if pw > self.width() else (self.width() - pw) / 2.0
        offset_y = float(self._pan_y) if ph > self.height() else (self.height() - ph) / 2.0
        return pw, ph, offset_x, offset_y, eff_w, eff_h