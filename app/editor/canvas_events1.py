# app/editor/canvas_events1.py
"""PDF_Editor – CanvasEventHandler facade: composes mouse, keyboard, context menu, and event helpers."""

from __future__ import annotations

try:
    from app.editor.canvas_events_helpers import (
        _OpacityAdjustDialog,
        _find_tab_editar,
        _overlay_geometry_changed,
        _resize_overlay_from_rect,
        _translate_overlay,
        smooth_stroke_points,
    )
    from app.editor.canvas_events_menu import CanvasContextMenuMixin
    from app.editor.canvas_events_move_key import CanvasMoveKeyMixin
    from app.editor.canvas_events_press_release import CanvasPressReleaseMixin
except ImportError:
    from .canvas_events_helpers import (  # type: ignore
        _OpacityAdjustDialog,
        _find_tab_editar,
        _overlay_geometry_changed,
        _resize_overlay_from_rect,
        _translate_overlay,
        smooth_stroke_points,
    )
    from .canvas_events_menu import CanvasContextMenuMixin  # type: ignore
    from .canvas_events_move_key import CanvasMoveKeyMixin  # type: ignore
    from .canvas_events_press_release import CanvasPressReleaseMixin  # type: ignore


class CanvasEventHandler(CanvasPressReleaseMixin, CanvasMoveKeyMixin, CanvasContextMenuMixin):
    """Facade coordinating mouse, drag-handle, keyboard, and context-menu interactions for PdfEditCanvas."""

    def __init__(self, canvas):
        self.canvas = canvas
        self._menu_style = None
        self._started_untouched_drag = False


__all__ = [
    "CanvasContextMenuMixin",
    "CanvasEventHandler",
    "CanvasMoveKeyMixin",
    "CanvasPressReleaseMixin",
    "_OpacityAdjustDialog",
    "_find_tab_editar",
    "_overlay_geometry_changed",
    "_resize_overlay_from_rect",
    "_translate_overlay",
    "smooth_stroke_points",
]