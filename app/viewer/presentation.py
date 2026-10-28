# app/viewer/presentation.py
"""PDF_Editor – Presentation mode: fullscreen single-page viewer with page transitions, auto-advance, and synchronized position/zoom matching normal view."""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, QPoint, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget, QLabel, QApplication
from shiboken6 import isValid

from app.viewer.annotation_layer import AnnotationOverlay, ToolMode
from app.viewer.annotation_hud import AnnotationHUD


_log = logging.getLogger(__name__)

_HUD_AUTO_HIDE_MS = 3000
_HUD_RESTART_DEBOUNCE_MS = 100

_PALETTE_HOTKEYS = {
    Qt.Key.Key_1: "#EF4444",
    Qt.Key.Key_2: "#10B981",
    Qt.Key.Key_3: "#3B82F6",
    Qt.Key.Key_4: "#FBBF24",
    Qt.Key.Key_5: "#111111",
    Qt.Key.Key_6: "#FFFFFF",
}


class PresentationWidget(QWidget):
    """Fullscreen single-page PDF viewer with keyboard navigation, zoom controls,
    smooth page transitions, auto-advance (/Dur), and a PowerPoint/Edge-style annotation HUD.
    Maintains the exact zoom and viewport position from normal view upon launch."""

    _viewer: Any

    def __init__(
        self,
        path: str,
        password: str,
        start_page: int,
        total_pages: int,
        dark_mode: bool = True,
        viewer: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._path = path
        self._password = password
        self._current = start_page
        self._total = total_pages
        self._pixmap: QPixmap | None = None
        self._prev_pixmap: QPixmap | None = None
        self._ready = False
        self._dark_mode = bool(dark_mode)
        self._hud_last_shown_ms = 0.0
        self._viewer: Any = viewer

        # Zoom and Pan state
        self._zoom: float | None = None
        self._base_fit_zoom: float = 1.0
        self._zoom_factor: float = 1.0
        self._pan_x: int = 0
        self._pan_y: int = 0
        self._is_panning = False
        self._pan_start_pos = QPoint()
        self._pan_start_offset = QPoint()

        # Document geometry & synchronized viewport focal point from normal view
        self._page_rotations: dict[int, int] = {}
        self._page_crops: dict[int, tuple[float, float, float, float]] = {}
        self._initial_top_left_pdf_x: float | None = None
        self._initial_top_left_pdf_y: float | None = None

        if self._viewer is not None:
            canvas = getattr(self._viewer, "_canvas", None)
            sb_scroll = getattr(self._viewer, "_canvas_scroll", None)
            if canvas is not None:
                # Use the active page currently visible on screen in normal view
                active_idx = getattr(canvas, "_active_page_idx", None)
                if active_idx is not None and 0 <= active_idx < self._total:
                    self._current = active_idx

                self._page_rotations = dict(getattr(canvas, "_page_rotations", {}))
                self._page_crops = dict(getattr(canvas, "_page_crops", {}))

                # Maintain the exact render zoom from normal view
                norm_zoom = getattr(canvas, "_zoom", None)
                if norm_zoom and norm_zoom > 0:
                    self._zoom = float(norm_zoom)
                    print(f"[Presentation DEBUG] Inherited absolute zoom from normal view: {self._zoom:.4f}")
                norm_zf = getattr(canvas, "_zoom_factor", 1.0)
                if norm_zf and norm_zf > 0:
                    self._zoom_factor = float(norm_zf)
                    print(f"[Presentation DEBUG] Inherited zoom_factor from normal view: {self._zoom_factor:.4f}")

                # Capture top-left of visible normal view relative to this page
                entries = getattr(canvas, "_entries", [])
                if sb_scroll is not None and 0 <= self._current < len(entries):
                    entry = entries[self._current]
                    sb_v = sb_scroll.verticalScrollBar()
                    scroll_y = float(sb_v.value()) if sb_v else 0.0
                    sb_h = sb_scroll.horizontalScrollBar()
                    scroll_x = float(sb_h.value()) if sb_h else 0.0

                    x_off = float(canvas.page_x_offset(entry))
                    y_off = float(entry.y_off)

                    # Calculate top-left visible point on the page in normal view
                    view_left_x = max(0.0, scroll_x - x_off)
                    view_top_y = max(0.0, scroll_y - y_off)

                    c_zoom = float(canvas._zoom or 1.0)
                    self._initial_top_left_pdf_x = view_left_x / c_zoom
                    self._initial_top_left_pdf_y = view_top_y / c_zoom

        # Transitions state
        self._trans_style = None
        self._trans_progress = 1.0
        self._trans_step = 0.05
        self._trans_direction = 0
        self._trans_timer = QTimer(self)
        self._trans_timer.setInterval(25)
        self._trans_timer.timeout.connect(self._on_trans_tick)

        # Auto-advance (/Dur) timer
        self._auto_advance_timer = QTimer(self)
        self._auto_advance_timer.setSingleShot(True)
        self._auto_advance_timer.timeout.connect(self._on_auto_advance)

        import fitz
        self._doc = fitz.open(self._path)
        if self._password:
            self._doc.authenticate(self._password)

        self._counter = QLabel(self)
        self._counter.setStyleSheet(
            "background: rgba(0,0,0,0.6); color: white; "
            "padding: 6px 16px; border-radius: 8px; font-size: 14px;"
        )
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._overlay = AnnotationOverlay(self, self._dark_mode)
        self._overlay.set_current_page(self._current)
        self._overlay.setGeometry(self.rect())

        self._hud = AnnotationHUD(self, self._dark_mode)
        self._hud.tool_selected.connect(self._on_tool_selected)
        self._hud.color_selected.connect(self._on_color_selected)
        self._hud.stroke_toggled.connect(self._on_stroke_toggled)
        self._hud.cloud_toggled.connect(self._on_cloud_toggled)
        self._hud.clear_requested.connect(self._on_clear_requested)
        self._hud.set_active_tool(int(ToolMode.POINTER))
        self._hud.set_active_color(self._overlay.pen_color())
        self._hud.hide()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(
            lambda: self._counter.setVisible(False)
            if isValid(self._counter) else None)

        self._hud_hide_timer = QTimer(self)
        self._hud_hide_timer.setSingleShot(True)
        self._hud_hide_timer.timeout.connect(
            lambda: self._hud.hide() if isValid(self._hud) else None)

        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet("background: #000000;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setWindowState(Qt.WindowState.WindowFullScreen)

        for seq, fn in [
            ("Ctrl++", self._zoom_in),
            ("Ctrl+=", self._zoom_in),
            ("Ctrl+-", self._zoom_out),
            ("Ctrl+_", self._zoom_out),
            ("Ctrl+0", self._zoom_reset),
        ]:
            sc = QShortcut(QKeySequence(seq), self, fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        self._ready = True

        # Render synchronously immediately to eliminate the black screen startup delay
        self._render(transition=False)

    def update_theme(self, dark: bool) -> None:
        self._dark_mode = bool(dark)
        if isValid(self._overlay):
            self._overlay.update_theme(self._dark_mode)
        if isValid(self._hud):
            self._hud.update_theme(self._dark_mode)

    # ── Zoom & Pan Controls ───────────────────────────────────────────────

    def _zoom_in(self, anchor_pos: QPoint | None = None):
        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom
        new_zoom = min(self._base_fit_zoom * 8.0, old_zoom * 1.25)
        self._apply_anchored_zoom(new_zoom, anchor_pos)

    def _zoom_out(self, anchor_pos: QPoint | None = None):
        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom
        new_zoom = max(self._base_fit_zoom * 0.25, old_zoom / 1.25)
        self._apply_anchored_zoom(new_zoom, anchor_pos)

    def _zoom_reset(self):
        self._zoom = self._base_fit_zoom
        self._zoom_factor = 1.0
        self._pan_x = 0
        self._pan_y = 0
        print("[Presentation DEBUG] Resetting zoom to fit screen.")
        self._render(transition=False)

    def _apply_anchored_zoom(self, new_zoom: float, anchor_pos: QPoint | None = None):
        if not self._pixmap:
            self._zoom = new_zoom
            self._render(transition=False)
            return

        sw = float(self.width())
        sh = float(self.height())

        old_zoom = self._zoom if self._zoom and self._zoom > 0 else self._base_fit_zoom
        
        # Enforce boundary clamping securely
        min_z = self._base_fit_zoom * 0.25
        max_z = self._base_fit_zoom * 8.0
        new_zoom = max(min_z, min(max_z, new_zoom))
        
        ratio = new_zoom / old_zoom

        print(f"[Presentation DEBUG] Apply Zoom | old_zoom={old_zoom:.4f}, new_zoom={new_zoom:.4f} | anchor_pos={anchor_pos}")

        # Retrieve the logical page dimensions prior to this zoom operation
        old_pw = getattr(self, "_rendered_pw", sw)
        old_ph = getattr(self, "_rendered_ph", sh)

        # Calculate exact screen coordinates of the old layout
        old_x = (sw - old_pw) / 2.0 if old_pw <= sw else float(self._pan_x)
        old_y = (sh - old_ph) / 2.0 if old_ph <= sh else float(self._pan_y)

        # Determine anchor point on screen
        if anchor_pos is not None:
            vp_x = float(anchor_pos.x())
            vp_y = float(anchor_pos.y())
        else:
            vp_x = sw / 2.0
            vp_y = sh / 2.0

        # Anchor offset relative to the logical page
        page_x = vp_x - old_x
        page_y = vp_y - old_y

        # Scale the offset to the new zoom level
        new_pan_x = vp_x - page_x * ratio
        new_pan_y = vp_y - page_y * ratio

        print(f"[Presentation DEBUG] Apply Zoom | new_pan_x={new_pan_x:.4f} -> {int(round(new_pan_x))}, new_pan_y={new_pan_y:.4f} -> {int(round(new_pan_y))}")

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
        print(f"[Presentation DEBUG] Scroll Page | up={up}, amount={amount:.1f} | pan_y before={self._pan_y}")

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
                        self.update()
                else:
                    self._pan_y = int(round(new_pan_y))
                    self.update()
            else:
                new_pan_y = self._pan_y - amount
                if new_pan_y < min_pan_y:
                    if self._current < self._total - 1:
                        self._go_to_page(self._current + 1, align_bottom=False)
                    else:
                        self._pan_y = int(round(min_pan_y))
                        self.update()
                else:
                    self._pan_y = int(round(new_pan_y))
                    self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = e.position().toPoint()
            print(f"[Presentation DEBUG] Wheel Zoom | delta={e.angleDelta().y()} | anchor_pos={pos}")
            if e.angleDelta().y() > 0:
                self._zoom_in(anchor_pos=pos)
            else:
                self._zoom_out(anchor_pos=pos)
            e.accept()
            return
        else:
            delta = e.angleDelta().y()
            print(f"[Presentation DEBUG] Wheel Scroll | delta={delta}")
            if delta > 0:
                self._scroll_page(up=True, amount=abs(delta) * 0.6)
            elif delta < 0:
                self._scroll_page(up=False, amount=abs(delta) * 0.6)
            e.accept()
            return

    def _go_to_page(self, new_page: int, align_bottom: bool = False):
        if new_page == self._current or not (0 <= new_page < self._total):
            return
        self._prev_pixmap = self._pixmap
        self._current = new_page
        self._pan_x = 0
        self._pan_y = 0
        self._align_bottom_next_render = align_bottom
        self._render(transition=True)

    def _on_auto_advance(self):
        if self._current < self._total - 1:
            self._go_to_page(self._current + 1)

    def _on_trans_tick(self):
        self._trans_progress += self._trans_step
        if self._trans_progress >= 1.0:
            self._trans_progress = 1.0
            self._trans_timer.stop()
            self._prev_pixmap = None
        self.update()

    def _render(self, transition: bool = False):
        import fitz
        self._auto_advance_timer.stop()

        try:
            if self._doc is None:
                self._pixmap = None
                return
            page = self._doc[self._current]

            crop = self._page_crops.get(self._current)
            if crop:
                crop_rect = fitz.Rect(crop) & page.mediabox
                if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
                    page.set_cropbox(crop_rect)

            rot = self._page_rotations.get(self._current, 0) % 360

            screen = self.screen() or QApplication.primaryScreen()
            geom = screen.geometry()
            dpr = screen.devicePixelRatio() or 1.0
            sw = float(self.width()) if self.width() > 100 else float(geom.width())
            sh = float(self.height()) if self.height() > 100 else float(geom.height())

            eff_w = page.rect.height if rot in (90, 270) else page.rect.width
            eff_h = page.rect.width if rot in (90, 270) else page.rect.height
            eff_w = max(1.0, eff_w)
            eff_h = max(1.0, eff_h)

            fit_width_zoom = sw / eff_w
            fit_height_zoom = sh / eff_h
            self._base_fit_zoom = min(fit_width_zoom, fit_height_zoom)

            if self._zoom is None or self._zoom <= 0:
                self._zoom = self._base_fit_zoom * self._zoom_factor
                
            # Enforce 800% max and 25% min relative to presentation base fit zoom
            min_z = self._base_fit_zoom * 0.25
            max_z = self._base_fit_zoom * 8.0

            if self._zoom > max_z:
                print(f"[Presentation DEBUG] Clamping absolute zoom {self._zoom:.4f} down to max {max_z:.4f} (800%)")
                self._zoom = max_z
            elif self._zoom < min_z:
                print(f"[Presentation DEBUG] Clamping absolute zoom {self._zoom:.4f} up to min {min_z:.4f} (25%)")
                self._zoom = min_z

            self._zoom_factor = self._zoom / self._base_fit_zoom
            zoom = self._zoom
            
            print(f"[Presentation DEBUG] Render | current_page={self._current} | sw={sw}, sh={sh} | base_fit={self._base_fit_zoom:.4f} | zoom={zoom:.4f} (factor={self._zoom_factor:.4f}) | pan_x={self._pan_x}, pan_y={self._pan_y}")

            rz = zoom * dpr

            max_d = max(eff_w, eff_h)
            if max_d * rz > 8192:
                rz = 8192.0 / max_d

            mat = fitz.Matrix(rz, rz)
            if rot:
                mat = mat.prerotate(rot)

            pix = page.get_pixmap(matrix=mat, annots=True)
            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            qi = QImage(pix.samples, pix.width, pix.height,
                        pix.stride, QImage.Format.Format_RGB888).copy()
            qp = QPixmap.fromImage(qi)
            qp.setDevicePixelRatio(dpr)
            self._pixmap = qp

            # Accurate logical dimensions decoupled from resolution capping
            pw = eff_w * zoom
            ph = eff_h * zoom
            self._rendered_pw = pw
            self._rendered_ph = ph

            # Maintain position from normal view on initial launch
            if not transition and getattr(self, "_initial_top_left_pdf_x", None) is not None:
                top_left_pdf_x = self._initial_top_left_pdf_x
                top_left_pdf_y = self._initial_top_left_pdf_y

                # Direct top-left alignment matching normal view
                self._pan_x = -int(round(top_left_pdf_x * zoom))
                self._pan_y = -int(round(top_left_pdf_y * zoom))

                if self.isVisible() and self.width() > 300:
                    self._initial_top_left_pdf_x = None
                    self._initial_top_left_pdf_y = None

            # Strictly clamp horizontal and vertical panning to keep view on-screen
            if pw > sw:
                min_pan_x = int(round(sw - pw))
                max_pan_x = 0
                self._pan_x = max(min_pan_x, min(max_pan_x, self._pan_x))
            else:
                self._pan_x = 0

            if ph > sh:
                min_pan_y = int(round(sh - ph))
                max_pan_y = 0
                if getattr(self, "_align_bottom_next_render", False):
                    self._align_bottom_next_render = False
                    self._pan_y = min_pan_y
                else:
                    self._pan_y = max(min_pan_y, min(max_pan_y, self._pan_y))
            else:
                self._pan_y = 0
                self._align_bottom_next_render = False

            # Check /Dur for auto-advance
            val_dur = self._doc.xref_get_key(page.xref, "Dur")
            if val_dur and val_dur[0] != "null":
                try:
                    dur_s = float(val_dur[1].strip())
                    if dur_s > 0:
                        self._auto_advance_timer.start(int(dur_s * 1000))
                except ValueError:
                    pass

            # Check /Trans for transition animation
            if transition and self._prev_pixmap is not None:
                trans_val = self._doc.xref_get_key(page.xref, "Trans")
                trans_style = None
                trans_dur = 0.6
                direction = 0
                if trans_val and trans_val[0] != "null":
                    raw = trans_val[1]
                    m_s = re.search(r"/S\s+/([A-Za-z]+)", raw)
                    if m_s:
                        trans_style = m_s.group(1)
                    m_d = re.search(r"/D\s+([\d\.]+)", raw)
                    if m_d:
                        trans_dur = min(2.5, max(0.1, float(m_d.group(1))))
                    m_di = re.search(r"/Di\s+(\d+)", raw)
                    if m_di:
                        direction = int(m_di.group(1))

                if trans_style and trans_style != "None":
                    self._trans_style = trans_style
                    self._trans_direction = direction
                    self._trans_progress = 0.0
                    frames = max(10, int(trans_dur * 40.0))
                    self._trans_step = 1.0 / frames
                    self._trans_timer.start(25)
                else:
                    self._prev_pixmap = None
                    self._trans_progress = 1.0
            else:
                self._prev_pixmap = None
                self._trans_progress = 1.0
        except Exception:
            _log.exception("presentation _render failed")
            self._pixmap = None

        self._overlay.set_current_page(self._current)
        self._overlay.raise_()
        self._hud.raise_()
        self._update_counter()
        self.update()

    def _update_counter(self):
        disp_pct = round((self._zoom / max(1e-4, self._base_fit_zoom)) * 100) if self._zoom else 100
        zoom_str = f"  ·  {disp_pct}%" if disp_pct != 100 else ""
        self._counter.setText(f"{self._current + 1} / {self._total}{zoom_str}")
        self._counter.adjustSize()
        lw = self._counter.width()
        self._counter.move(self.width() // 2 - lw // 2, self.height() - 50)
        self._counter.setVisible(True)
        self._counter.raise_()
        self._hide_timer.start(3000)

    def _sync_hud_text_options(self):
        if isValid(self._hud) and isValid(self._overlay):
            self._hud.set_stroke_active(self._overlay.is_active_box_stroke())
            self._hud.set_cloud_active(self._overlay.is_active_box_cloud())

    def _show_hud(self):
        if not isValid(self._hud):
            return
        self._sync_hud_text_options()
        if not self._hud.isVisible():
            self._hud.reposition()
            self._hud.show()
            self._hud.raise_()
        now = time.monotonic() * 1000.0
        if now - self._hud_last_shown_ms < _HUD_RESTART_DEBOUNCE_MS:
            return
        self._hud_last_shown_ms = now
        self._hud_hide_timer.start(_HUD_AUTO_HIDE_MS)
        if self._overlay.tool() in (int(ToolMode.POINTER), int(ToolMode.TYPE)):
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_tool_selected(self, mode: int):
        self._overlay.set_tool(int(mode))
        self._hud.set_active_tool(int(mode))
        if int(mode) in (int(ToolMode.POINTER), int(ToolMode.TYPE)):
            self.setCursor(Qt.CursorShape.ArrowCursor
                           if self._hud.isVisible()
                           else Qt.CursorShape.BlankCursor)
        else:
            self.setCursor(Qt.CursorShape.BlankCursor)
        self._show_hud()

    def _on_color_selected(self, color):
        self._overlay.set_pen_color(color)
        self._hud.set_active_color(color)
        self._show_hud()

    def _on_stroke_toggled(self):
        new_state = self._overlay.toggle_text_stroke()
        self._hud.set_stroke_active(new_state)
        self._show_hud()

    def _on_cloud_toggled(self):
        new_state = self._overlay.toggle_text_cloud()
        self._hud.set_cloud_active(new_state)
        self._show_hud()

    def _on_clear_requested(self):
        self._overlay.clear_current_page()
        self._sync_hud_text_options()
        self.update()
        self._show_hud()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))

        pixmap = self._pixmap
        if pixmap is not None and not pixmap.isNull():
            pw = getattr(self, "_rendered_pw", float(self.width()))
            ph = getattr(self, "_rendered_ph", float(self.height()))

            x = float(self._pan_x) if pw > self.width() else (self.width() - pw) / 2.0
            y = float(self._pan_y) if ph > self.height() else (self.height() - ph) / 2.0

            t = self._trans_progress
            prev_pixmap = self._prev_pixmap

            if prev_pixmap is not None and not prev_pixmap.isNull() and t < 1.0 and self._trans_style:
                old_w = float(prev_pixmap.width())
                old_h = float(prev_pixmap.height())
                old_x = (self.width() - old_w) / 2.0
                old_y = (self.height() - old_h) / 2.0

                style = self._trans_style
                if style == "Push":
                    di = self._trans_direction
                    if di == 90:    # bottom to top
                        dy = int(self.height() * t)
                        p.drawPixmap(int(round(old_x)), int(round(old_y - dy)), prev_pixmap)
                        p.drawPixmap(int(round(x)), int(round(y + self.height() - dy)), pixmap)
                    elif di == 180: # right to left
                        dx = int(self.width() * t)
                        p.drawPixmap(int(round(old_x + dx)), int(round(old_y)), prev_pixmap)
                        p.drawPixmap(int(round(x - self.width() + dx)), int(round(y)), pixmap)
                    elif di == 270: # top to bottom
                        dy = int(self.height() * t)
                        p.drawPixmap(int(round(old_x)), int(round(old_y + dy)), prev_pixmap)
                        p.drawPixmap(int(round(x)), int(round(y - self.height() + dy)), pixmap)
                    else:           # left to right (0)
                        dx = int(self.width() * t)
                        p.drawPixmap(int(round(old_x - dx)), int(round(old_y)), prev_pixmap)
                        p.drawPixmap(int(round(x + self.width() - dx)), int(round(y)), pixmap)

                elif style in ("Wipe", "Cover"):
                    p.drawPixmap(int(round(old_x)), int(round(old_y)), prev_pixmap)
                    di = self._trans_direction
                    p.save()
                    if di == 90:
                        split_y = int(self.height() * (1.0 - t))
                        p.setClipRect(QRect(0, split_y, self.width(), self.height() - split_y))
                    elif di == 180:
                        split_x = int(self.width() * (1.0 - t))
                        p.setClipRect(QRect(split_x, 0, self.width() - split_x, self.height()))
                    elif di == 270:
                        split_y = int(self.height() * t)
                        p.setClipRect(QRect(0, 0, self.width(), split_y))
                    else:
                        split_x = int(self.width() * t)
                        p.setClipRect(QRect(0, 0, split_x, self.height()))
                    p.drawPixmap(int(round(x)), int(round(y)), pixmap)
                    p.restore()

                elif style in ("Fade", "Dissolve"):
                    p.setOpacity(max(0.0, 1.0 - t))
                    p.drawPixmap(int(round(old_x)), int(round(old_y)), prev_pixmap)
                    p.setOpacity(min(1.0, t))
                    p.drawPixmap(int(round(x)), int(round(y)), pixmap)
                    p.setOpacity(1.0)

                elif style == "Box":
                    p.drawPixmap(int(round(old_x)), int(round(old_y)), prev_pixmap)
                    bw = int(self.width() * t)
                    bh = int(self.height() * t)
                    bx = (self.width() - bw) // 2
                    by = (self.height() - bh) // 2
                    p.save()
                    p.setClipRect(QRect(bx, by, bw, bh))
                    p.drawPixmap(int(round(x)), int(round(y)), pixmap)
                    p.restore()

                else:
                    p.setOpacity(max(0.0, 1.0 - t))
                    p.drawPixmap(int(round(old_x)), int(round(old_y)), prev_pixmap)
                    p.setOpacity(min(1.0, t))
                    p.drawPixmap(int(round(x)), int(round(y)), pixmap)
                    p.setOpacity(1.0)
            else:
                # Direct 1:1 hardware pixel output without bilinear scaling blur
                p.drawPixmap(int(round(x)), int(round(y)), pixmap)

        p.end()

    def keyPressEvent(self, e):
        key = e.key()
        modifiers = e.modifiers()

        if self._overlay._active_box is not None:
            self._overlay.keyPressEvent(e)
            self._sync_hud_text_options()
            if e.isAccepted():
                return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._zoom_in()
                e.accept()
                return
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self._zoom_out()
                e.accept()
                return
            if key == Qt.Key.Key_0:
                self._zoom_reset()
                e.accept()
                return
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._overlay.redo()
                else:
                    self._overlay.undo()
                self._sync_hud_text_options()
                e.accept()
                return
            if key == Qt.Key.Key_Y:
                self._overlay.redo()
                self._sync_hud_text_options()
                e.accept()
                return

        if modifiers & (Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(e)
            return

        if key == Qt.Key.Key_P:
            self._on_tool_selected(int(ToolMode.PEN))
            return
        if key == Qt.Key.Key_H:
            self._on_tool_selected(int(ToolMode.HIGHLIGHTER))
            return
        if key == Qt.Key.Key_E:
            self._on_tool_selected(int(ToolMode.ERASER))
            return
        if key == Qt.Key.Key_T:
            self._on_tool_selected(int(ToolMode.TYPE))
            return
        if key == Qt.Key.Key_L:
            self._on_tool_selected(int(ToolMode.LASER))
            return
        if key == Qt.Key.Key_C:
            self._on_clear_requested()
            return
        if key in _PALETTE_HOTKEYS:
            current = self._overlay.tool()
            if current in (int(ToolMode.PEN), int(ToolMode.HIGHLIGHTER), int(ToolMode.TYPE)):
                color = QColor(_PALETTE_HOTKEYS[key])
                self._overlay.set_pen_color(color)
                self._hud.set_active_color(color)
                self._show_hud()
                return

        if key == Qt.Key.Key_Up:
            self._scroll_page(up=True)
            e.accept()
            return
        if key == Qt.Key.Key_Down:
            self._scroll_page(up=False)
            e.accept()
            return

        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Right:
            if self._current < self._total - 1:
                self._go_to_page(self._current + 1, align_bottom=False)
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            ph = getattr(self, "_rendered_ph", float(self.height()))
            sh = float(self.height())
            if ph > sh and self._pan_y > (sh - ph) + 10:
                self._scroll_page(up=False, amount=sh * 0.8)
            elif self._current < self._total - 1:
                self._go_to_page(self._current + 1, align_bottom=False)
        elif key == Qt.Key.Key_Left:
            if self._current > 0:
                self._go_to_page(self._current - 1, align_bottom=False)
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_PageUp):
            ph = getattr(self, "_rendered_ph", float(self.height()))
            sh = float(self.height())
            if ph > sh and self._pan_y < -10:
                self._scroll_page(up=True, amount=sh * 0.8)
            elif self._current > 0:
                self._go_to_page(self._current - 1, align_bottom=True)
        elif key == Qt.Key.Key_Home:
            self._go_to_page(0)
        elif key == Qt.Key.Key_End:
            self._go_to_page(self._total - 1)

    def mousePressEvent(self, e):
        pw = getattr(self, "_rendered_pw", float(self.width()))
        ph = getattr(self, "_rendered_ph", float(self.height()))
        can_pan = (pw > float(self.width()) or ph > float(self.height()))

        if (e.button() == Qt.MouseButton.MiddleButton or
                (e.button() == Qt.MouseButton.LeftButton and
                 self._overlay.tool() == int(ToolMode.POINTER) and can_pan)):
            self._is_panning = True
            self._pan_start_pos = e.position().toPoint()
            self._pan_start_offset = QPoint(int(self._pan_x), int(self._pan_y))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._show_hud()
        if self._is_panning:
            delta = e.position().toPoint() - self._pan_start_pos
            new_x = float(self._pan_start_offset.x() + delta.x())
            new_y = float(self._pan_start_offset.y() + delta.y())

            pw = getattr(self, "_rendered_pw", float(self.width()))
            ph = getattr(self, "_rendered_ph", float(self.height()))
            sw = float(self.width())
            sh = float(self.height())

            if pw > sw:
                new_x = max(sw - pw, min(0.0, new_x))
            else:
                new_x = 0.0
            if ph > sh:
                new_y = max(sh - ph, min(0.0, new_y))
            else:
                new_y = 0.0

            print(f"[Presentation DEBUG] Panning | delta=({delta.x()}, {delta.y()}) | new_pan_x={new_x:.1f}, new_pan_y={new_y:.1f}")
            self._pan_x = int(round(new_x))
            self._pan_y = int(round(new_y))

            self.update()
            e.accept()
            return

        if self._overlay.tool() == int(ToolMode.LASER):
            self._overlay.set_laser_pos(e.position().toPoint())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._is_panning:
            self._is_panning = False
            if self._overlay.tool() in (int(ToolMode.POINTER), int(ToolMode.TYPE)):
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                self.setCursor(Qt.CursorShape.BlankCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def resizeEvent(self, _):
        if not self._ready:
            return
        if isValid(self._overlay):
            self._overlay.setGeometry(self.rect())
        if isValid(self._hud):
            self._hud.reposition()
        self._update_counter()
        QTimer.singleShot(0, lambda: self._render(transition=False))

    def _burn_annotations_to_doc(self) -> bool:
        """Burn overlay strokes and text boxes into the PDF document."""
        if not self._doc:
            return False

        any_modified = False
        import fitz

        pages_with_annots = set(self._overlay._strokes.keys()) | set(self._overlay._text_boxes.keys())
        if not pages_with_annots:
            return False

        screen = self.screen() or QApplication.primaryScreen()
        geom = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        sw = float(self.width()) if self.width() > 100 else float(geom.width())
        sh = float(self.height()) if self.height() > 100 else float(geom.height())

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

            eff_w = page.rect.height if rot in (90, 270) else page.rect.width
            eff_h = page.rect.width if rot in (90, 270) else page.rect.height
            eff_w = max(1.0, eff_w)
            eff_h = max(1.0, eff_h)

            fit_width_zoom = sw / eff_w
            fit_height_zoom = sh / eff_h

            base_zoom = min(fit_width_zoom, fit_height_zoom)
            zoom = self._zoom if self._zoom and self._zoom > 0 else (base_zoom * self._zoom_factor)

            pw = eff_w * zoom
            ph = eff_h * zoom

            if pw <= self.width():
                offset_x = (self.width() - pw) / 2.0
            else:
                offset_x = float(self._pan_x)

            if ph <= self.height():
                offset_y = (self.height() - ph) / 2.0
            else:
                offset_y = float(self._pan_y)

            scale_x = page.rect.width / pw
            scale_y = page.rect.height / ph

            def to_pdf_coord(pt) -> tuple[float, float]:
                x_pdf = float((pt.x() - offset_x) * scale_x + page.rect.x0)
                y_pdf = float((pt.y() - offset_y) * scale_y + page.rect.y0)
                return (x_pdf, y_pdf)

            strokes = self._overlay._strokes.get(page_idx, [])
            for stroke in strokes:
                if not stroke.points:
                    continue
                raw_pts = [to_pdf_coord(p) for p in stroke.points]
                clean_pts: list[tuple[float, float]] = []
                for pt in raw_pts:
                    if not clean_pts or abs(pt[0] - clean_pts[-1][0]) > 1e-4 or abs(pt[1] - clean_pts[-1][1]) > 1e-4:
                        clean_pts.append(pt)
                if len(clean_pts) == 1:
                    clean_pts.append((clean_pts[0][0] + 0.1, clean_pts[0][1]))
                if len(clean_pts) >= 2:
                    try:
                        annot = page.add_ink_annot([clean_pts])
                        annot.set_colors(stroke=(
                            float(stroke.color.redF()),
                            float(stroke.color.greenF()),
                            float(stroke.color.blueF()),
                        ))
                        border_w = max(1, int(round(stroke.width * scale_x)))
                        annot.set_border(width=border_w)
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
                    p1_x, p1_y = to_pdf_coord(box.rect.topLeft())
                    p2_x, p2_y = to_pdf_coord(box.rect.bottomRight())
                    pdf_rect = fitz.Rect(p1_x, p1_y, p2_x, p2_y)

                    pdf_font_size = max(4.0, float(box.font_size * scale_y))

                    if box.has_cloud:
                        bg_annot = page.add_rect_annot(pdf_rect)
                        bg_annot.set_colors(fill=(1, 1, 1))
                        bg_annot.set_opacity(102 / 255.0)
                        bg_annot.update()

                    text_color = (
                        float(box.color.redF()),
                        float(box.color.greenF()),
                        float(box.color.blueF()),
                    )
                    inner_rect = pdf_rect + fitz.Rect(7 * scale_x, 5 * scale_y, -7 * scale_x, -5 * scale_y)

                    if box.has_stroke:
                        offsets = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]
                        for dx, dy in offsets:
                            out_rect = inner_rect + fitz.Rect(dx * scale_x, dy * scale_y, dx * scale_x, dy * scale_y)
                            page.insert_textbox(
                                out_rect,
                                box.text,
                                fontsize=pdf_font_size,
                                color=(0, 0, 0),
                                fontname="helv",
                            )

                    rc = page.insert_textbox(
                        inner_rect,
                        box.text,
                        fontsize=pdf_font_size,
                        color=text_color,
                        fontname="helv",
                    )
                    if rc < 0:
                        page.insert_text(
                            fitz.Point(inner_rect.x0, inner_rect.y0 + pdf_font_size),
                            box.text,
                            fontsize=pdf_font_size,
                            color=text_color,
                            fontname="helv",
                        )
                    any_modified = True
                except Exception as e:
                    _log.warning("Failed to burn text box: %s", e)

        return any_modified

    def closeEvent(self, event):
        any_modified = False
        try:
            any_modified = self._burn_annotations_to_doc()
        except Exception as e:
            _log.exception("Error burning presentation annotations: %s", e)

        if isValid(self._overlay):
            self._overlay.clear_all(record_undo=False)
        for tmr in (
            getattr(self, "_hide_timer", None),
            getattr(self, "_hud_hide_timer", None),
            getattr(self, "_auto_advance_timer", None),
            getattr(self, "_trans_timer", None),
        ):
            if tmr is not None and isValid(tmr):
                tmr.stop()

        doc_to_save = None
        if any_modified and self._doc is not None:
            doc_to_save = self._doc

        viewer: Any = getattr(self, "_viewer", None)
        if doc_to_save and viewer is not None:
            try:
                save_and_reload = getattr(viewer, "_save_and_reload", None)
                if callable(save_and_reload):
                    save_and_reload(doc_to_save, target_page=self._current)
            except Exception as e:
                _log.exception("Failed to save presentation annotations to viewer: %s", e)
            self._doc = None
        else:
            if self._doc is not None:
                with contextlib.suppress(Exception):
                    self._doc.close()
                self._doc = None

        super().closeEvent(event)