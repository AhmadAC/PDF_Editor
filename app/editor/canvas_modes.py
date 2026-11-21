# app/editor/canvas_modes.py
"""Edit mode toggles, brush settings, and stamp placement states for PdfEditCanvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.constants import BG_INNER, _LN

try:
    from app.editor.canvas_render1 import _get_icon_cursor
except ImportError:
    from .canvas_render1 import _get_icon_cursor  # type: ignore

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object


class CanvasModesMixin(_Base):
    """Mixin providing tool mode transitions, stamp modes, and cursor states."""

    if TYPE_CHECKING:
        _bg_color: Any
        _select_mode: bool
        _redact_mode: bool
        _note_mode: bool
        _highlight_mode: bool
        _highlight_color: tuple
        _draw_mode: bool
        _draw_color: tuple
        _draw_width: int
        _text_mode: bool
        _text_submode: str
        _selected_overlay_idx: int
        _overlay_mgr: Any
        _current_stroke: Any
        _stroke_page: int
        _placing_signature: bool
        _placing_stamp: bool
        _placing_stamp_type: str
        _stamp_color: tuple
        _stamp_size: float
        _stamp_cursor_pos: Any
        _active_sel_rect: Any
        _active_sel_page: int
        _active_sel_handle: int
        _active_sel_moving: bool

        HANDLE_NONE: int

        def update(self) -> None: ...
        def setCursor(self, cursor: Any) -> None: ...
        def cancel_signature_placement(self) -> None: ...

    def set_dark_mode(self, dark: bool):
        self._bg_color = BG_INNER if dark else _LN
        self.update()

    def set_select_mode(self, active: bool):
        self._select_mode = active
        if active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.clear_active_selection()

    def set_redact_mode(self, active: bool):
        self._redact_mode = active
        if active:
            self._selected_overlay_idx = -1
            self._overlay_mgr.discard_untouched_existing()
            self.update()

    def set_note_mode(self, active: bool):
        self._note_mode = active
        if active:
            self._selected_overlay_idx = -1
            self._overlay_mgr.discard_untouched_existing()
            self.update()

    def set_highlight_mode(
        self,
        active: bool,
        color: tuple = (1.0, 1.0, 0.0),
        cursor_shape: Qt.CursorShape = Qt.CursorShape.CrossCursor,
    ):
        self._highlight_mode = active
        self._highlight_color = color
        if active:
            self._draw_mode = False
            self._text_mode = False
            self._select_mode = False
            self._selected_overlay_idx = -1
            self._overlay_mgr.discard_untouched_existing()
            self.setCursor(cursor_shape)
        elif (
            not self._draw_mode
            and not self._text_mode
            and not self._placing_signature
            and not getattr(self, "_placing_stamp", False)
            and not self._select_mode
        ):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_text_mode(self, active: bool):
        self._text_mode = active
        if active:
            self._draw_mode = False
            self._highlight_mode = False
            self._select_mode = False
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif (
            not self._draw_mode
            and not self._placing_signature
            and not getattr(self, "_placing_stamp", False)
            and not self._highlight_mode
            and not self._select_mode
        ):
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_text_submode(self, mode: str):
        self._text_submode = mode
        if mode == "add":
            self._selected_overlay_idx = -1
            self.clear_active_selection()
            self._overlay_mgr.discard_untouched_existing()
            self.setCursor(Qt.CursorShape.IBeamCursor)
            self.update()

    def is_add_text_mode(self) -> bool:
        return bool(self._text_mode and getattr(self, "_text_submode", "edit") == "add")

    def set_draw_mode(self, active: bool, color=None, width=None):
        self._draw_mode = active
        if color is not None:
            self._draw_color = color
        if width is not None:
            self._draw_width = max(1, int(width))
        if active:
            self._highlight_mode = False
            self._text_mode = False
            self._select_mode = False
            self._selected_overlay_idx = -1
            self._overlay_mgr.discard_untouched_existing()
            self.setCursor(_get_icon_cursor("fa5s.pen", 4, 4, size=28, rotate=90))
        else:
            self._current_stroke = None
            self._stroke_page = -1
            if (
                not self._text_mode
                and not self._placing_signature
                and not getattr(self, "_placing_stamp", False)
                and not self._highlight_mode
                and not self._select_mode
            ):
                self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def begin_stamp_placement(self, stamp_type: str, color=(0.0, 0.0, 0.0), size=20.0):
        self.cancel_signature_placement()
        self.clear_active_selection()
        self._overlay_mgr.discard_untouched_existing()
        self._selected_overlay_idx = -1
        self._placing_stamp = True
        self._placing_stamp_type = stamp_type
        self._stamp_color = color
        self._stamp_size = max(8.0, float(size))
        self._stamp_cursor_pos = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def cancel_stamp_placement(self):
        self._placing_stamp = False
        self._placing_stamp_type = ""
        self._stamp_cursor_pos = None
        if not self._draw_mode and not self._text_mode and not self._highlight_mode:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def clear_active_selection(self):
        self._active_sel_rect = None
        self._active_sel_page = -1
        self._active_sel_handle = self.HANDLE_NONE
        self._active_sel_moving = False
        self.update()