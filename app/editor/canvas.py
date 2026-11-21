# app/editor/canvas.py
"""PDF_Editor – PdfEditCanvas facade: visual PDF edit canvas with live background refresh, grouping/ungrouping, z-order, rotate/flip, opacity, lock, and object manipulation."""

from __future__ import annotations

import fitz
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.constants import BG_INNER

try:
    from app.editor.canvas_events1 import CanvasEventHandler
    from app.editor.canvas_modes import CanvasModesMixin
    from app.editor.canvas_overlay1 import (
        CanvasOverlayManager,
        get_page_preview_ops,
        overlay_pdf_rect,
        snapshot_overlay,
    )
    from app.editor.canvas_overlay_ops import CanvasOverlayOpsMixin
    from app.editor.canvas_painter1 import CanvasPainter
    from app.editor.canvas_render1 import (
        _BUFFER_PGS,
        _MAX_THREADS,
        _PAGE_GAP,
        _EditPageJob,
        _EditRenderSignals,
        _get_icon_cursor,
        clear_overlay_pixmap_cache,
        render_single_page_pixmap,
    )
    from app.editor.canvas_text1 import CanvasInlineTextManager
    from app.editor.canvas_view import CanvasViewMixin
except ImportError:
    from .canvas_events1 import CanvasEventHandler  # type: ignore
    from .canvas_modes import CanvasModesMixin  # type: ignore
    from .canvas_overlay1 import (  # type: ignore
        CanvasOverlayManager,
        get_page_preview_ops,
        overlay_pdf_rect,
        snapshot_overlay,
    )
    from .canvas_overlay_ops import CanvasOverlayOpsMixin  # type: ignore
    from .canvas_painter1 import CanvasPainter  # type: ignore
    from .canvas_render1 import (  # type: ignore
        _BUFFER_PGS,
        _MAX_THREADS,
        _PAGE_GAP,
        _EditPageJob,
        _EditRenderSignals,
        _get_icon_cursor,
        clear_overlay_pixmap_cache,
        render_single_page_pixmap,
    )
    from .canvas_text1 import CanvasInlineTextManager  # type: ignore
    from .canvas_view import CanvasViewMixin  # type: ignore

__all__ = [
    "PdfEditCanvas",
    "_PAGE_GAP",
    "_BUFFER_PGS",
    "_MAX_THREADS",
    "_EditRenderSignals",
    "_EditPageJob",
    "_get_icon_cursor",
    "clear_overlay_pixmap_cache",
    "render_single_page_pixmap",
    "CanvasOverlayManager",
    "get_page_preview_ops",
    "overlay_pdf_rect",
    "snapshot_overlay",
    "CanvasInlineTextManager",
    "CanvasPainter",
    "CanvasEventHandler",
]


class PdfEditCanvas(QWidget, CanvasModesMixin, CanvasOverlayOpsMixin, CanvasViewMixin):
    rect_selected                 = Signal(int, object)        # (page_idx, fitz.Rect)
    point_clicked                 = Signal(int, object)        # (page_idx, fitz.Point)
    stroke_finished               = Signal(int, object)        # (page_idx, list[fitz.Point])
    note_deleted                  = Signal(dict)
    zoom_changed                  = Signal(int)
    text_edit_committed           = Signal(int, dict)          # (page_idx, edit_dict)
    text_inserted                 = Signal(int, dict)          # (page_idx, edit_dict)
    text_edit_started             = Signal(dict)               # (format_dict)
    signature_added               = Signal(int, object, str)   # (page_idx, fitz.Rect, sig_path)
    stamp_added                   = Signal(int, object, str)   # (page_idx, fitz.Rect, stamp_type)
    overlay_changed               = Signal()
    overlay_committed             = Signal(dict)               # (overlay_dict)
    overlay_transformed           = Signal(dict, dict)         # (overlay_dict, before_snapshot)
    overlay_arranged              = Signal(dict)               # (arrange_info_dict)
    overlay_ungrouped             = Signal(dict)               # (ungroup_info_dict)
    overlay_grouped               = Signal(dict)               # (group_info_dict)
    overlay_duplicated            = Signal(dict)               # (new_overlay_dict)
    overlay_deleted               = Signal(int, dict)          # (index, overlay_dict)
    delete_selection_requested    = Signal()
    edit_selection_requested      = Signal()
    highlight_selection_requested = Signal()
    redact_selection_requested    = Signal()
    mode_change_requested         = Signal(int)                # (mode_idx)

    HANDLE_NONE = 0
    HANDLE_TL   = 1
    HANDLE_TR   = 2
    HANDLE_BL   = 3
    HANDLE_BR   = 4

    def __init__(self):
        super().__init__()
        self._doc         = None
        self._path        = ""
        self._password    = ""
        self._page_idx    = 0
        self._zoom        = 1.0
        self._zoom_factor = 1.0
        self._base_avail  = 300
        self._page_pixmaps: list = []
        self._page_prev_pixmaps: list = []
        self._page_offsets = []
        self._gen         = 0
        self._pending: set[int] = set()
        self._pending_anchor: tuple[int, float, float, float, float] | None = None
        self._pending_pixmaps: list | None = None
        self._render_signals = _EditRenderSignals()
        self._render_signals.page_ready.connect(self._on_page_ready)
        self._drag_start  = None
        self._drag_rect   = None
        self._overlays    = []
        self._select_mode = False
        self._draw_mode   = False
        self._text_mode   = False
        self._text_submode = "edit"
        self._redact_mode = False
        self._note_mode   = False
        self._highlight_mode = False
        self._highlight_color = (1.0, 1.0, 0.0)
        self._draw_color  = (1.0, 0.0, 0.0)
        self._draw_width  = 2
        self._current_stroke = None
        self._stroke_page = -1
        self._open_note   = None
        self._screen_signal_window = None

        self._active_sel_rect: fitz.Rect | None = None
        self._active_sel_page: int = -1
        self._active_sel_handle: int = self.HANDLE_NONE
        self._active_sel_moving: bool = False
        self._active_sel_drag_start: QPoint = QPoint()
        self._active_sel_orig_rect: fitz.Rect | None = None

        self._placing_signature: bool = False
        self._placing_sig_path: str = ""
        self._placing_sig_pixmap = None
        self._sig_cursor_pos: QPoint | None = None
        self._selected_overlay_idx: int = -1
        self._drag_handle: int = self.HANDLE_NONE
        self._drag_start_pos: QPoint = QPoint()
        self._drag_start_rect: fitz.Rect | None = None
        self._drag_before_snapshot: dict | None = None
        self._moving_overlay: bool = False

        self._placing_stamp: bool = False
        self._placing_stamp_type: str = ""
        self._stamp_cursor_pos: QPoint | None = None
        self._stamp_color: tuple = (0.0, 0.0, 0.0)
        self._stamp_size: float = 20.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMinimumSize(300, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._bg_color = BG_INNER

        self._overlay_mgr = CanvasOverlayManager(self)
        self._inline_mgr = CanvasInlineTextManager(self)
        self._painter = CanvasPainter()
        self._event_handler = CanvasEventHandler(self)

    @property
    def _inline_edit(self):
        return self._inline_mgr.edit

    def begin_inline_text_edit(self, span: dict, page_idx: int, target_overlay: dict | None = None):
        self._inline_mgr.begin_edit(span, page_idx, target_overlay=target_overlay)

    def begin_inline_text_insert(
        self,
        page_idx: int,
        pdf_point,
        size: float,
        color: tuple,
        font: str = "",
        bold: bool = False,
        italic: bool = False,
        effects: dict | None = None,
    ):
        self._inline_mgr.begin_insert(page_idx, pdf_point, size, color, font, bold, italic, effects=effects)

    def update_active_text_format(
        self,
        font: str,
        size: float,
        color: tuple,
        bold: bool,
        italic: bool,
        effects: dict | None = None,
    ):
        self._inline_mgr.update_format(font, size, color, bold, italic, effects=effects)

    def _commit_inline(self):
        self._inline_mgr.commit()

    def _cancel_inline(self):
        self._inline_mgr.cancel()

    def _reposition_inline(self):
        self._inline_mgr.reposition()

    def eventFilter(self, obj, event):
        if self._inline_mgr.handle_event_filter(obj, event):
            return True
        return super().eventFilter(obj, event)

    def paintEvent(self, _):
        p = QPainter(self)
        self._painter.paint(self, p)
        p.end()

    def mousePressEvent(self, e):
        self._event_handler.handle_mouse_press(e)

    def mouseMoveEvent(self, e):
        self._event_handler.handle_mouse_move(e)

    def mouseDoubleClickEvent(self, e):
        self._event_handler.handle_mouse_double_click(e)

    def mouseReleaseEvent(self, e):
        self._event_handler.handle_mouse_release(e)

    def keyPressEvent(self, e):
        if (
            e.key() == Qt.Key.Key_G
            and (e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and (e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and self._selected_overlay_idx >= 0
        ):
            if self.ungroup_selected_overlay():
                e.accept()
                return
        self._event_handler.handle_key_press(e)
        if not e.isAccepted():
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        self._event_handler.handle_context_menu(e)