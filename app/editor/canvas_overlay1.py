# app/editor/canvas_overlay1.py
"""PDF_Editor – CanvasOverlayManager facade: composes overlay actions, detection, and geometry helpers."""

from __future__ import annotations

try:
    from app.editor.overlay_actions import OverlayActionsMixin
    from app.editor.overlay_detect import (
        OverlayDetectorMixin,
        _PDF_ANNOT_LINK,
        _PDF_ANNOT_REDACT,
        _PDF_ANNOT_TEXT,
        _PDF_ANNOT_WIDGET,
    )
    from app.editor.overlay_geometry import (
        _sample_bg_color,
        _serialize_drawing_items,
        _transform_drawing_items,
        _transform_pt,
        get_page_preview_ops,
        overlay_pdf_rect,
        rects_close,
        restore_overlay_snapshot,
        snapshot_overlay,
    )
except ImportError:
    from .overlay_actions import OverlayActionsMixin  # type: ignore
    from .overlay_detect import (  # type: ignore
        OverlayDetectorMixin,
        _PDF_ANNOT_LINK,
        _PDF_ANNOT_REDACT,
        _PDF_ANNOT_TEXT,
        _PDF_ANNOT_WIDGET,
    )
    from .overlay_geometry import (  # type: ignore
        _sample_bg_color,
        _serialize_drawing_items,
        _transform_drawing_items,
        _transform_pt,
        get_page_preview_ops,
        overlay_pdf_rect,
        rects_close,
        restore_overlay_snapshot,
        snapshot_overlay,
    )


class CanvasOverlayManager(OverlayActionsMixin, OverlayDetectorMixin):
    """Facade coordinating object detection, hit-testing, and transformations on the PDF edit canvas."""

    HANDLE_NONE = 0
    HANDLE_TL   = 1
    HANDLE_TR   = 2
    HANDLE_BL   = 3
    HANDLE_BR   = 4

    _RESIZABLE_TYPES = (
        "signature", "image", "text_edit", "text", "redact",
        "check", "cross", "drawing", "highlight", "draw",
    )

    def __init__(self, canvas):
        self.canvas = canvas


__all__ = [
    "CanvasOverlayManager",
    "OverlayActionsMixin",
    "OverlayDetectorMixin",
    "_PDF_ANNOT_LINK",
    "_PDF_ANNOT_REDACT",
    "_PDF_ANNOT_TEXT",
    "_PDF_ANNOT_WIDGET",
    "_sample_bg_color",
    "_serialize_drawing_items",
    "_transform_drawing_items",
    "_transform_pt",
    "get_page_preview_ops",
    "overlay_pdf_rect",
    "rects_close",
    "restore_overlay_snapshot",
    "snapshot_overlay",
]