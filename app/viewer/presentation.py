# app/viewer/presentation.py
"""PDF_Editor – Presentation mode facade: fullscreen single-page viewer (standalone or split-embedded) with transitions, auto-advance, and annotation sync."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from typing import Any

from PySide6.QtCore import Qt, QTimer, QPoint, Signal
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut, QCursor
from PySide6.QtWidgets import QWidget, QLabel, QApplication, QMenu
from shiboken6 import isValid
import fitz

from app.constants import ACCENT
from app.utils import (
    SVG_CHECK,
    SVG_CHEVRON_LEFT,
    SVG_CHEVRON_RIGHT,
    SVG_CLOSE,
    SVG_DOT_CIRCLE,
    SVG_ERASER,
    SVG_EYE,
    SVG_FONT,
    SVG_HIGHLIGHTER,
    SVG_PEN,
    SVG_POINTER,
    SVG_TRASH,
    svg_icon,
)
from app.viewer.annotation_layer import AnnotationOverlay, ToolMode
from app.viewer.annotation_hud import AnnotationHUD
from app.viewer.presentation_zoom_pan import PresentationZoomPanMixin
from app.viewer.presentation_transitions import PresentationTransitionsMixin
from app.viewer.presentation_burn import PresentationBurnMixin

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


class PresentationWidget(PresentationZoomPanMixin, PresentationTransitionsMixin, PresentationBurnMixin, QWidget):
    """Fullscreen presentation viewer with PowerPoint/Edge-style annotation HUD, page transitions, and right-click toolbar toggle."""

    close_requested = Signal()

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
        super().__init__(kwargs.get("parent"))
        self._embedded = bool(kwargs.get("embedded", False))
        self._cleaned_up = False
        if not self._embedded:
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.setWindowFlags(Qt.WindowType.Window)

        screen = (
            QApplication.screenAt(QCursor.pos())
            or (self.parentWidget().screen() if self.parentWidget() else None)
            or QApplication.primaryScreen()
        )
        if screen:
            self.setGeometry(screen.geometry())

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
        self._edit_tab: Any = kwargs.get("edit_tab")
        self._temp_source_path: str = kwargs.get("temp_source_path", "")
        self._toolbar_enabled: bool = True

        self._zoom: float | None = None
        self._base_fit_zoom: float = 1.0
        self._zoom_factor: float = 1.0
        self._pan_x: int = 0
        self._pan_y: int = 0
        self._is_panning = False
        self._pan_start_pos = QPoint()
        self._pan_start_offset = QPoint()

        self._page_render_info: dict[int, dict[str, float]] = {}
        self._page_rotations: dict[int, int] = {}
        self._page_crops: dict[int, tuple[float, float, float, float]] = {}
        self._initial_top_left_pdf_x: float | None = None
        self._initial_top_left_pdf_y: float | None = None

        win = self._viewer.window() if self._viewer is not None else None
        if not self._embedded and self._edit_tab is None and win is not None and hasattr(win, "_edit_tool_idx") and hasattr(win, "stack"):
            edit_idx = win._edit_tool_idx()
            if getattr(win, "_current_tool", -1) == edit_idx and edit_idx >= 0:
                candidate = win.stack.widget(edit_idx)
                if candidate is not None and getattr(candidate, "_doc_path", None):
                    self._edit_tab = candidate

        if self._edit_tab is not None:
            edit_canvas = getattr(self._edit_tab, "_canvas", None)
            sb_scroll = getattr(self._edit_tab, "_canvas_scroll", None)
            self._path = self._edit_tab._doc_path
            self._password = getattr(self._edit_tab, "_pdf_password", "") or self._password

            if edit_canvas is not None:
                inline_edit = getattr(edit_canvas, "_inline_edit", None)
                if inline_edit is not None and inline_edit.isVisible():
                    commit_fn = getattr(edit_canvas, "_commit_inline", None)
                    if callable(commit_fn):
                        commit_fn()

                cnt = edit_canvas.page_count()
                if cnt > 0:
                    self._total = cnt

                sb_v = sb_scroll.verticalScrollBar() if sb_scroll else None
                sb_h = sb_scroll.horizontalScrollBar() if sb_scroll else None
                if sb_v is not None and self._total > 0:
                    self._current = edit_canvas.page_at_y(sb_v.value())

                norm_zoom = getattr(edit_canvas, "_zoom", None)
                if norm_zoom and norm_zoom > 0 and not self._embedded:
                    self._zoom = float(norm_zoom)
                norm_zf = getattr(edit_canvas, "_zoom_factor", 1.0)
                if norm_zf and norm_zf > 0 and not self._embedded:
                    self._zoom_factor = float(norm_zf)

                offsets = getattr(edit_canvas, "_page_offsets", [])
                if sb_scroll is not None and 0 <= self._current < len(offsets):
                    y_off, _pw, _ph = offsets[self._current]
                    scroll_y = float(sb_v.value()) if sb_v else 0.0
                    scroll_x = float(sb_h.value()) if sb_h else 0.0
                    c_zoom = float(edit_canvas._zoom or 1.0)
                    self._initial_top_left_pdf_x = max(0.0, scroll_x) / c_zoom
                    self._initial_top_left_pdf_y = max(0.0, scroll_y - float(y_off)) / c_zoom

        elif self._viewer is not None:
            canvas = getattr(self._viewer, "_canvas", None)
            sb_scroll = getattr(self._viewer, "_canvas_scroll", None)
            if canvas is not None:
                active_idx = getattr(canvas, "_active_page_idx", None)
                if active_idx is not None and 0 <= active_idx < self._total:
                    self._current = active_idx

                self._page_rotations = dict(getattr(canvas, "_page_rotations", {}))
                self._page_crops = dict(getattr(canvas, "_page_crops", {}))

                norm_zoom = getattr(canvas, "_zoom", None)
                if norm_zoom and norm_zoom > 0 and not self._embedded:
                    self._zoom = float(norm_zoom)
                norm_zf = getattr(canvas, "_zoom_factor", 1.0)
                if norm_zf and norm_zf > 0 and not self._embedded:
                    self._zoom_factor = float(norm_zf)

                entries = getattr(canvas, "_entries", [])
                if sb_scroll is not None and 0 <= self._current < len(entries):
                    entry = entries[self._current]
                    sb_v = sb_scroll.verticalScrollBar()
                    scroll_y = float(sb_v.value()) if sb_v else 0.0
                    sb_h = sb_scroll.horizontalScrollBar()
                    scroll_x = float(sb_h.value()) if sb_h else 0.0

                    x_off = float(canvas.page_x_offset(entry))
                    y_off = float(entry.y_off)

                    view_left_x = max(0.0, scroll_x - x_off)
                    view_top_y = max(0.0, scroll_y - y_off)

                    c_zoom = float(canvas._zoom or 1.0)
                    self._initial_top_left_pdf_x = view_left_x / c_zoom
                    self._initial_top_left_pdf_y = view_top_y / c_zoom

        self._trans_style = None
        self._trans_progress = 1.0
        self._trans_step = 0.05
        self._trans_direction = 0
        self._trans_timer = QTimer(self)
        self._trans_timer.setInterval(25)
        self._trans_timer.timeout.connect(self._on_trans_tick)

        self._auto_advance_timer = QTimer(self)
        self._auto_advance_timer.setSingleShot(True)
        self._auto_advance_timer.timeout.connect(self._on_auto_advance)

        self._doc = fitz.open(self._path)
        if self._password and self._doc.needs_pass:
            self._doc.authenticate(self._password)

        if self._edit_tab is not None:
            user_pending = getattr(self._edit_tab, "_user_pending", [])
            if user_pending:
                try:
                    from app.editor.apply_edits import apply_pending_edits
                    apply_pending_edits(self._doc, [dict(e) for e in user_pending])
                except Exception as exc:
                    _log.warning("Failed to apply pending Edit Mode overlays: %s", exc)

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
            lambda: self._counter.setVisible(False) if isValid(self._counter) else None
        )

        self._hud_hide_timer = QTimer(self)
        self._hud_hide_timer.setSingleShot(True)
        self._hud_hide_timer.timeout.connect(self._on_hud_hide)

        self.setStyleSheet("background: #000000;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)

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
        self._render(transition=False)

    def show(self) -> None:
        if not self._embedded:
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
        else:
            super().show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._embedded:
            self.showFullScreen()
            self.raise_()
            self.activateWindow()

    def update_theme(self, dark: bool) -> None:
        self._dark_mode = bool(dark)
        if isValid(self._overlay):
            self._overlay.update_theme(self._dark_mode)
        if isValid(self._hud):
            self._hud.update_theme(self._dark_mode)

    def toggle_toolbar(self, enabled: bool | None = None) -> None:
        """Toggle the annotation HUD toolbar on or off."""
        if enabled is None:
            self._toolbar_enabled = not getattr(self, "_toolbar_enabled", True)
        else:
            self._toolbar_enabled = bool(enabled)

        if self._toolbar_enabled:
            self._show_hud()
        else:
            self._hud_hide_timer.stop()
            if isValid(self._hud):
                self._hud.hide()
            if isValid(self._overlay) and self._overlay.tool() in (int(ToolMode.POINTER), int(ToolMode.TYPE)):
                self.setCursor(Qt.CursorShape.BlankCursor)
        self.update()

    def _on_hud_hide(self) -> None:
        if isValid(self._hud):
            self._hud.hide()
        if isValid(self._overlay) and self._overlay.tool() in (int(ToolMode.POINTER), int(ToolMode.TYPE)):
            self.setCursor(Qt.CursorShape.BlankCursor)

    def contextMenuEvent(self, e) -> None:
        """Right-click context menu offering instant toolbar toggle, tools, navigation, and exit."""
        menu = QMenu(self)
        dark = self._dark_mode
        bg = "#262626" if dark else "#FFFFFF"
        fg = "#F0F0F0" if dark else "#1A2B28"
        border = "#444444" if dark else "#D1D5DB"
        menu.setStyleSheet(
            f"QMenu {{ background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 6px; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; margin: 2px 4px; background: transparent; }}"
            f"QMenu::item:selected {{ background: {ACCENT}; color: #FFFFFF; }}"
            f"QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}"
        )

        is_toolbar_on = getattr(self, "_toolbar_enabled", True)
        toolbar_text = "Hide Toolbar\tCtrl+T" if is_toolbar_on else "Show Toolbar\tCtrl+T"
        toolbar_icon = svg_icon(SVG_CLOSE if is_toolbar_on else SVG_EYE, color=ACCENT, size=15)
        act_toggle_toolbar = menu.addAction(toolbar_icon, toolbar_text)
        act_toggle_toolbar.triggered.connect(lambda _=False: self.toggle_toolbar())

        menu.addSeparator()

        tools_menu = menu.addMenu(svg_icon(SVG_PEN, color=ACCENT, size=15), "Annotation Tools")
        tools_menu.setStyleSheet(menu.styleSheet())

        current_tool = self._overlay.tool()
        tool_defs = [
            (ToolMode.POINTER, "Pointer / Select", SVG_POINTER),
            (ToolMode.PEN, "Pen", SVG_PEN),
            (ToolMode.HIGHLIGHTER, "Highlighter", SVG_HIGHLIGHTER),
            (ToolMode.TYPE, "Text Box", SVG_FONT),
            (ToolMode.LASER, "Laser Pointer", SVG_DOT_CIRCLE),
            (ToolMode.ERASER, "Eraser", SVG_ERASER),
        ]

        for mode_val, mode_name, mode_icon in tool_defs:
            is_active = (current_tool == int(mode_val))
            icon_col = ACCENT if is_active else ("#9CA3AF" if dark else "#6B7280")
            act_t = tools_menu.addAction(svg_icon(mode_icon, color=icon_col, size=15), mode_name)
            if is_active:
                act_t.setIcon(svg_icon(SVG_CHECK, color=ACCENT, size=15))
            act_t.triggered.connect(lambda _=False, m=int(mode_val): self._on_tool_selected(m))

        act_clear = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Clear Page Annotations (Ctrl+R)")
        act_clear.triggered.connect(lambda _=False: self._on_clear_requested())

        menu.addSeparator()

        act_prev = menu.addAction(svg_icon(SVG_CHEVRON_LEFT, color=fg, size=15), "Previous Page\tLeft / PgUp")
        act_prev.setEnabled(self._current > 0)
        act_prev.triggered.connect(lambda _=False: self._go_to_page(self._current - 1, align_bottom=False))

        act_next = menu.addAction(svg_icon(SVG_CHEVRON_RIGHT, color=fg, size=15), "Next Page\tRight / Space")
        act_next.setEnabled(self._current < self._total - 1)
        act_next.triggered.connect(lambda _=False: self._go_to_page(self._current + 1, align_bottom=False))

        menu.addSeparator()

        act_exit = menu.addAction(svg_icon(SVG_CLOSE, color="#EF4444", size=15), "End Presentation\tEsc")
        act_exit.triggered.connect(lambda _=False: self.close_requested.emit() if self._embedded else self.close())

        if hasattr(e, "globalPos"):
            global_pt = e.globalPos()
        elif hasattr(e, "globalPosition"):
            global_pt = e.globalPosition().toPoint()
        else:
            global_pt = self.mapToGlobal(e.pos())

        menu.exec(global_pt)
        e.accept()

    def _render(self, transition: bool = False):
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

            screen = (
                self.screen()
                or (self.parentWidget().screen() if self.parentWidget() else None)
                or QApplication.primaryScreen()
            )
            geom = screen.geometry()
            dpr = screen.devicePixelRatio() or 1.0
            if self._embedded:
                sw = float(self.width()) if self.width() > 50 else float(geom.width()) / 2.0
                sh = float(self.height()) if self.height() > 50 else float(geom.height())
            else:
                sw = float(self.width()) if self.width() > 100 else float(geom.width())
                sh = float(self.height()) if self.height() > 100 else float(geom.height())

            eff_w = page.rect.height if rot in (90, 270) else page.rect.width
            eff_h = page.rect.width if rot in (90, 270) else page.rect.height
            eff_w = max(1.0, eff_w)
            eff_h = max(1.0, eff_h)

            if self._embedded:
                self._base_fit_zoom = min(sw / eff_w, sh / eff_h)
            elif self._edit_tab is not None and hasattr(self._edit_tab, "_canvas"):
                base_avail = float(getattr(self._edit_tab._canvas, "_base_avail", 0) or 0)
                self._base_fit_zoom = (base_avail / eff_w) if base_avail > 100 else (sw / eff_w)
            elif self._viewer is not None and hasattr(self._viewer, "_canvas"):
                normal_canvas = self._viewer._canvas
                vp_w = normal_canvas.get_viewport_width()
                target_fit_w = max(400, vp_w - 36)
                self._base_fit_zoom = target_fit_w / eff_w
            else:
                self._base_fit_zoom = sw / eff_w

            if self._zoom is None or self._zoom <= 0:
                self._zoom = self._base_fit_zoom * self._zoom_factor

            min_z = self._base_fit_zoom * 0.25
            max_z = self._base_fit_zoom * 8.0
            self._zoom = max(min_z, min(max_z, self._zoom))

            self._zoom_factor = self._zoom / self._base_fit_zoom
            zoom = self._zoom

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

            qi = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            qp = QPixmap.fromImage(qi)
            qp.setDevicePixelRatio(dpr)
            self._pixmap = qp

            pw = eff_w * zoom
            ph = eff_h * zoom
            self._rendered_pw = pw
            self._rendered_ph = ph

            if not transition and getattr(self, "_initial_top_left_pdf_x", None) is not None:
                top_left_pdf_x = self._initial_top_left_pdf_x or 0.0
                top_left_pdf_y = self._initial_top_left_pdf_y or 0.0
                self._pan_x = -int(round(top_left_pdf_x * zoom))
                self._pan_y = -int(round(top_left_pdf_y * zoom))
                min_ready_w = 100 if self._embedded else 300
                if self.isVisible() and self.width() > min_ready_w:
                    self._initial_top_left_pdf_x = None
                    self._initial_top_left_pdf_y = None

            if pw > sw:
                self._pan_x = max(int(round(sw - pw)), min(0, self._pan_x))
            else:
                self._pan_x = 0

            if ph > sh:
                min_pan_y = int(round(sh - ph))
                if getattr(self, "_align_bottom_next_render", False):
                    self._align_bottom_next_render = False
                    self._pan_y = min_pan_y
                else:
                    self._pan_y = max(min_pan_y, min(0, self._pan_y))
            else:
                self._pan_y = 0
                self._align_bottom_next_render = False

            self._record_current_render_info(pw, ph, eff_w, eff_h)

            val_dur = self._doc.xref_get_key(page.xref, "Dur")
            if val_dur and val_dur[0] != "null":
                try:
                    dur_s = float(val_dur[1].strip())
                    if dur_s > 0:
                        self._auto_advance_timer.start(int(dur_s * 1000))
                except ValueError:
                    pass

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
        if not getattr(self, "_toolbar_enabled", True):
            return
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
            is_toolbar_active = (self._hud.isVisible() and getattr(self, "_toolbar_enabled", True))
            self.setCursor(Qt.CursorShape.ArrowCursor if is_toolbar_active else Qt.CursorShape.BlankCursor)
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

    def keyPressEvent(self, e):
        key = e.key()
        modifiers = e.modifiers()

        if self._overlay._active_box is not None:
            self._overlay.keyPressEvent(e)
            self._sync_hud_text_options()
            if e.isAccepted():
                return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_R and not (modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
                self._on_clear_requested()
                e.accept()
                return
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
            if key == Qt.Key.Key_T:
                self.toggle_toolbar()
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

        if key in (Qt.Key.Key_P, Qt.Key.Key_H, Qt.Key.Key_E, Qt.Key.Key_T, Qt.Key.Key_L, Qt.Key.Key_C):
            mapping = {
                Qt.Key.Key_P: ToolMode.PEN,
                Qt.Key.Key_H: ToolMode.HIGHLIGHTER,
                Qt.Key.Key_E: ToolMode.ERASER,
                Qt.Key.Key_T: ToolMode.TYPE,
                Qt.Key.Key_L: ToolMode.LASER,
            }
            if key == Qt.Key.Key_C:
                self._on_clear_requested()
            else:
                self._on_tool_selected(int(mapping[key]))
            return

        if key in _PALETTE_HOTKEYS:
            current = self._overlay.tool()
            if current in (int(ToolMode.PEN), int(ToolMode.HIGHLIGHTER), int(ToolMode.TYPE)):
                from PySide6.QtGui import QColor
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

        if key in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            if self._embedded:
                self.close_requested.emit()
                e.accept()
            else:
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
        self.setFocus()
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

            new_x = max(sw - pw, min(0.0, new_x)) if pw > sw else 0.0
            new_y = max(sh - ph, min(0.0, new_y)) if ph > sh else 0.0

            self._pan_x = int(round(new_x))
            self._pan_y = int(round(new_y))
            self._update_current_render_offsets()
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
        if self._embedded:
            self._zoom = None
        if isValid(self._overlay):
            self._overlay.setGeometry(self.rect())
        if isValid(self._hud):
            self._hud.reposition()
        self._update_counter()
        QTimer.singleShot(0, lambda: self._render(transition=False) if isValid(self) else None)

    def commit_and_cleanup(self) -> None:
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True

        any_modified = False
        try:
            if self._edit_tab is not None:
                any_modified = self._export_annotations_to_edit_tab()
            else:
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

        if self._temp_source_path and os.path.isfile(self._temp_source_path):
            with contextlib.suppress(Exception):
                os.unlink(self._temp_source_path)
            self._temp_source_path = ""

        if self._edit_tab is not None:
            if self._doc is not None:
                with contextlib.suppress(Exception):
                    self._doc.close()
                self._doc = None
            if isValid(self._edit_tab):
                if any_modified and hasattr(self._edit_tab, "_scroll_to"):
                    with contextlib.suppress(Exception):
                        self._edit_tab._scroll_to(self._current)
                edit_canvas = getattr(self._edit_tab, "_canvas", None)
                if edit_canvas is not None and isValid(edit_canvas):
                    edit_canvas.update()
                win = self._edit_tab.window()
                if win and hasattr(win, "_update_undo_redo_buttons"):
                    with contextlib.suppress(Exception):
                        win._update_undo_redo_buttons()
            return

        doc_to_save = self._doc if (any_modified and self._doc is not None) else None
        viewer: Any = getattr(self, "_viewer", None)

        if viewer is not None and not self._embedded:
            canvas = getattr(viewer, "_canvas", None)
            if canvas is not None:
                try:
                    canvas._zoom_factor = self._zoom_factor
                    if hasattr(canvas, "_save_zoom_pref"):
                        canvas._save_zoom_pref()
                except Exception as e:
                    _log.warning("Failed to save zoom pref: %s", e)

        if doc_to_save and viewer is not None:
            try:
                save_and_reload = getattr(viewer, "_save_and_reload", None)
                if callable(save_and_reload):
                    save_and_reload(doc_to_save, target_page=self._current)
            except Exception as e:
                _log.exception("Failed to save presentation annotations: %s", e)
            self._doc = None
        else:
            if self._doc is not None:
                with contextlib.suppress(Exception):
                    self._doc.close()
                self._doc = None

        if viewer is not None and not self._embedded:
            canvas = getattr(viewer, "_canvas", None)
            if canvas is not None:
                try:
                    canvas._zoom_factor = self._zoom_factor
                    if hasattr(canvas, "_invalidate_and_relayout"):
                        try:
                            canvas._invalidate_and_relayout(preserve_pixmaps=True)
                        except TypeError:
                            canvas._invalidate_and_relayout()
                except Exception as e:
                    _log.warning("Failed to layout normal view on exit: %s", e)

    def closeEvent(self, event):
        self.commit_and_cleanup()
        super().closeEvent(event)