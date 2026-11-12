"""PDF_Editor – Interaction facade for _SelectCanvas (Mouse, Keyboard, Context Menus, Signatures, Shortcuts)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint

from app.viewer.canvas_action_menus import CanvasActionMenuBuilder, _ScrollableMenuStyle
from app.viewer.canvas_gesture_router import CanvasGestureRouter
from app.viewer.canvas_keystroke_exec import CanvasKeystrokeHandler
from app.viewer.canvas_selection_engine import CanvasSelectionEngine, is_cjk

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas

# Re-export helper functions/classes to preserve external compatibility
__all__ = ["CanvasInteractionHandler", "is_cjk", "_ScrollableMenuStyle"]


class CanvasInteractionHandler:
    """Facade orchestrating text selection, gestures, keystrokes, and context actions."""

    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas
        self.selection_engine = CanvasSelectionEngine(canvas)
        self.menu_builder = CanvasActionMenuBuilder(canvas)
        self.gesture_router = CanvasGestureRouter(canvas, self.selection_engine, self.menu_builder)
        self.keystroke_handler = CanvasKeystrokeHandler(canvas)

    @property
    def _is_dragging_text(self) -> bool:
        return self.selection_engine.is_dragging_text

    @_is_dragging_text.setter
    def _is_dragging_text(self, value: bool) -> None:
        self.selection_engine.is_dragging_text = value

    # Selection Facade Methods
    def clear_selection(self) -> None:
        self.selection_engine.clear_selection()

    def ensure_entry_lines(self, entry: Any) -> list[dict]:
        return self.selection_engine.ensure_entry_lines(entry)

    def select_all(self) -> None:
        self.selection_engine.select_all()

    def compute_selection(self) -> None:
        self.selection_engine.compute_selection()

    def _find_char_at(self, lines: list[dict], px: float, py: float) -> tuple[int, int]:
        return self.selection_engine.find_char_at(lines, px, py)

    def _find_char_in_line(self, chars: list[tuple[float, float, float, float, str]], px: float) -> int:
        return self.selection_engine.find_char_in_line(chars, px)

    def _select_char_range(
        self,
        p1_page: int, l1: int, c1: int,
        p2_page: int, l2: int, c2: int,
    ) -> None:
        self.selection_engine.select_char_range(p1_page, l1, c1, p2_page, l2, c2)

    # Note / Signature Inspection Facade Methods
    def note_icon_at(self, pos: QPoint) -> tuple[int, int] | None:
        return self.menu_builder.note_icon_at(pos)

    def get_sig_handle_at(self, pos: QPoint) -> int:
        return self.gesture_router.get_sig_handle_at(pos)

    def is_pos_inside_active_sig(self, pos: QPoint) -> bool:
        return self.gesture_router.is_pos_inside_active_sig(pos)

    # Event Handlers Facade Methods
    def mouse_press(self, e) -> None:
        self.gesture_router.mouse_press(e)

    def mouse_move(self, e) -> None:
        self.gesture_router.mouse_move(e)

    def mouse_double_click(self, e) -> None:
        self.gesture_router.mouse_double_click(e)

    def mouse_release(self, e) -> None:
        self.gesture_router.mouse_release(e)

    def key_press(self, e) -> bool:
        return self.keystroke_handler.handle_key_press(
            e,
            select_all_fn=self.selection_engine.select_all,
            clear_sel_fn=self.selection_engine.clear_selection,
        )

    def context_menu(self, e) -> None:
        self.menu_builder.show_context_menu(
            e,
            select_all_fn=self.selection_engine.select_all,
            is_sig_inside_fn=self.gesture_router.is_pos_inside_active_sig,
        )