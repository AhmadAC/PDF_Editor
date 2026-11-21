# app/editor/tab_actions_modes.py
"""Editing modes, stamp placement tools, and color shortcut actions for TabEditar."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

from app.constants import ACCENT, TEXT_SEC, _LQ
from app.editor.canvas_overlay1 import snapshot_overlay
from app.editor.tab_constants import (
    _MODE_DRAW,
    _MODE_FORMS,
    _MODE_HIGHLIGHT,
    _MODE_HIGHLIGHT_RECT,
    _MODE_IMAGE,
    _MODE_NOTE,
    _MODE_REDACT,
    _MODE_SELECT,
    _MODE_SIGNATURE,
    _MODE_TEXT,
)
from app.i18n import t
from app.utils import svg_for_name

try:
    from app.editor.canvas_render1 import _get_icon_cursor
except ImportError:
    from app.editor.canvas import _get_icon_cursor  # type: ignore

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    from app.editor.tab_history import TabHistoryManager
    _Base = QWidget
else:
    _Base = object

_COLOR_SHORTCUTS: dict[int, tuple[str, tuple[float, float, float]]] = {
    1: ("Green",  (0.06, 0.72, 0.50)),
    2: ("Red",    (0.94, 0.27, 0.27)),
    3: ("Yellow", (0.95, 0.77, 0.06)),
    4: ("Pink",   (0.95, 0.45, 0.71)),
    5: ("Blue",   (0.15, 0.45, 0.96)),
    6: ("Orange", (0.97, 0.45, 0.09)),
    7: ("Purple", (0.55, 0.36, 0.96)),
    8: ("Cyan",   (0.02, 0.71, 0.83)),
    9: ("Grey",   (0.42, 0.45, 0.50)),
    0: ("Black",  (0.00, 0.00, 0.00)),
}


class TabModesActionsMixin(_Base):
    """Mixin providing mode switching, color shortcut routing, and stamp insertion."""

    if TYPE_CHECKING:
        _mode_idx: int
        _dark_mode: bool
        _mode_btns: list[QPushButton]
        _mode_btn_idx: dict[int, int]
        _opt_stack: Any
        _btn_undo: Any
        _btn_redo: Any
        _canvas: PdfEditCanvas
        _history: TabHistoryManager
        _pending: list[dict]
        _draw_color_cb: Any
        _draw_width_slider: Any
        _hi_color: Any
        _hi_rect_color: Any
        _img_drop: Any
        _stamp_size: Any
        _stamp_color: Any
        _text_color: Any
        _text_submode: str

        @property
        def _MODE_DEFS(self) -> list[tuple[str, str]]: ...
        def _add(self, edit: dict, *, _from_redo: bool = False) -> None: ...
        def _pick_image(self) -> None: ...
        def _status(self, msg: str) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...
        def _save_text_settings(self) -> None: ...
        def _on_overlay_committed(self, edit: dict) -> None: ...
        def _on_overlay_transformed(self, edit: dict, before_snap: dict) -> None: ...

    def _apply_color_shortcut(self, digit: int) -> bool:
        """Apply Ctrl+1..0 color shortcut to selected text inside active inline editor, selected overlay, or all text boxes."""
        entry = _COLOR_SHORTCUTS.get(digit)
        if entry is None:
            return False
        color_name, col = entry
        c = getattr(self, "_canvas", None)
        if c is None or not getattr(c, "_doc", None):
            return False

        # 1. Active inline text editor open -> apply to selected text (or whole box if no selection)
        inline_edit = getattr(c, "_inline_edit", None)
        if inline_edit is not None and inline_edit.isVisible():
            c._inline_mgr.apply_color(col)
            if hasattr(self, "_text_color"):
                self._text_color.blockSignals(True)
                self._text_color.set_color(col)
                self._text_color.blockSignals(False)
                self._save_text_settings()
            self._status(f"Text color: {color_name}")
            return True

        # 2. A canvas overlay is currently selected -> update its color immediately
        sel_idx = getattr(c, "_selected_overlay_idx", -1)
        if 0 <= sel_idx < len(c._overlays):
            ov = c._overlays[sel_idx]
            if not ov.get("_deleted") and ov.get("type") in ("text", "text_edit", "check", "cross", "draw", "highlight"):
                before_snap = snapshot_overlay(ov)
                was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
                ov["color"] = col
                if ov.get("runs"):
                    ov["runs"] = [dict(r, color=col) for r in ov["runs"]]
                if ov.get("type") == "text_edit":
                    ov["_font_changed"] = True
                if ov.get("type") in ("text", "text_edit") and hasattr(self, "_text_color"):
                    self._text_color.blockSignals(True)
                    self._text_color.set_color(col)
                    self._text_color.blockSignals(False)
                    self._save_text_settings()

                if was_untouched:
                    ov["_dirty"] = True
                    self._on_overlay_committed(ov)
                else:
                    self._on_overlay_transformed(ov, before_snap)

                if was_untouched or ov.get("_z_back"):
                    c.refresh_pages({ov.get("page", 0)})
                c.overlay_changed.emit()
                c.update()
                self._status(f"Changed selected object color to {color_name}")
                return True

        # 3. No text box selected -> update ALL user text boxes immediately + active text color
        targets = [
            ov for ov in self._pending
            if not ov.get("_deleted")
            and ov.get("type") in ("text", "text_edit")
            and (not ov.get("_existing") or ov.get("_dirty"))
        ]
        if targets:
            if hasattr(self, "_text_color"):
                self._text_color.blockSignals(True)
                self._text_color.set_color(col)
                self._text_color.blockSignals(False)
                self._save_text_settings()
            affected_pages: set[int] = set()
            refresh_pages: set[int] = set()
            for ov in targets:
                before_snap = snapshot_overlay(ov)
                ov["color"] = col
                if ov.get("runs"):
                    ov["runs"] = [dict(r, color=col) for r in ov["runs"]]
                if ov.get("type") == "text_edit":
                    ov["_font_changed"] = True
                self._history.handle_overlay_transformed(ov, before_snap)
                pg = ov.get("page", 0)
                affected_pages.add(pg)
                if ov.get("_z_back"):
                    refresh_pages.add(pg)
            if refresh_pages:
                c.refresh_pages(refresh_pages)
            c.overlay_changed.emit()
            c.update()
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails(affected_pages)
            self._status(f"Changed all text ({len(targets)}) to {color_name}")
            return True

        if digit == 0 and self._mode_idx != _MODE_TEXT:
            return False

        if hasattr(self, "_text_color"):
            self._text_color.blockSignals(True)
            self._text_color.set_color(col)
            self._text_color.blockSignals(False)
            self._save_text_settings()
        self._status(f"Text color set to {color_name}")
        return True

    def _set_mode_by_idx(self, idx: int):
        if 0 <= idx < len(self._mode_btns):
            self._on_mode_btn(self._mode_btns[idx])

    def _sync_canvas_color(self, color_tuple: tuple[float, float, float]) -> None:
        if hasattr(self, "_draw_color_cb"):
            self._draw_color_cb.set_color(color_tuple)
        if hasattr(self, "_hi_color"):
            self._hi_color.set_color(color_tuple)
        if hasattr(self, "_hi_rect_color"):
            self._hi_rect_color.set_color(color_tuple)

    def _start_stamp_check(self):
        sz = float(self._stamp_size.value()) if hasattr(self, "_stamp_size") else 20.0
        col = self._stamp_color.color_tuple() if hasattr(self, "_stamp_color") else (0.0, 0.0, 0.0)
        self._canvas.begin_stamp_placement("check", color=col, size=sz)
        self._status("Click directly inside any box or cell to place the checkmark (Right-click to cancel).")

    def _start_stamp_cross(self):
        sz = float(self._stamp_size.value()) if hasattr(self, "_stamp_size") else 20.0
        col = self._stamp_color.color_tuple() if hasattr(self, "_stamp_color") else (0.0, 0.0, 0.0)
        self._canvas.begin_stamp_placement("cross", color=col, size=sz)
        self._status("Click directly inside any box or cell to place the crossmark (Right-click to cancel).")

    def _on_stamp_added(self, page_idx: int, rect, stamp_type: str):
        col = self._stamp_color.color_tuple() if hasattr(self, "_stamp_color") else (0.0, 0.0, 0.0)
        size = float(rect.width)
        self._add({
            "type": stamp_type,
            "page": page_idx,
            "rect": rect,
            "color": col,
            "width": max(1.5, size * 0.12),
        })
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_mode_btn(self, btn: QPushButton):
        idx = self._mode_btn_idx.get(id(btn), 0)
        self._mode_idx = idx
        sec = TEXT_SEC if self._dark_mode else _LQ
        for i, b in enumerate(self._mode_btns):
            active = b is btn
            b.setChecked(active)
            b.setIcon(svg_for_name(self._MODE_DEFS[i][1], color=ACCENT if active else sec, size=18))
            if active:
                b.setStyleSheet(
                    f"background:#264F78; border:1px solid {ACCENT}; color:#FFFFFF; border-radius:6px;"
                    if self._dark_mode else
                    f"background:#D6E8FA; border:1px solid #70A7DB; color:{ACCENT}; border-radius:6px;"
                )
            else:
                b.setStyleSheet(
                    "background:#333333; border:1px solid #444444; color:#CCCCCC; border-radius:6px;"
                    if self._dark_mode else
                    "background:#FFFFFF; border:1px solid #D1D5DB; color:#555555; border-radius:6px;"
                )
        self._opt_stack.setCurrentIndex(idx)
        if idx == _MODE_FORMS:
            tip = t("editor.forms.undo_unavailable")
            self._btn_undo.setToolTip(tip)
            self._btn_redo.setToolTip(tip)
        else:
            self._btn_undo.setToolTip(t("edit.undo_tip"))
            self._btn_redo.setToolTip(t("edit.redo_tip"))
        if hasattr(self, "_canvas") and self._canvas._inline_edit.isVisible():
            self._canvas._commit_inline()

        self._canvas.set_select_mode(idx in (_MODE_SIGNATURE, _MODE_SELECT))
        self._canvas.set_redact_mode(idx == _MODE_REDACT)
        self._canvas.set_note_mode(idx == _MODE_NOTE)
        is_draw = (idx == _MODE_DRAW)
        self._canvas.set_draw_mode(
            is_draw,
            color=self._draw_color_cb.color_tuple() if is_draw else None,
            width=self._draw_width_slider.value() if is_draw else None,
        )
        is_text = (idx == _MODE_TEXT)
        self._canvas.set_text_mode(is_text)
        if is_text and hasattr(self._canvas, "set_text_submode"):
            self._canvas.set_text_submode(getattr(self, "_text_submode", "edit"))

        is_hi_text = (idx == _MODE_HIGHLIGHT)
        is_hi_rect = (idx == _MODE_HIGHLIGHT_RECT)
        if is_hi_text:
            hi_col = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)
            self._canvas.set_highlight_mode(True, color=hi_col, cursor_shape=Qt.CursorShape.IBeamCursor)
        elif is_hi_rect:
            hi_col = self._hi_rect_color.color_tuple() if hasattr(self, "_hi_rect_color") else (1.0, 1.0, 0.0)
            self._canvas.set_highlight_mode(True, color=hi_col, cursor_shape=Qt.CursorShape.CrossCursor)
        else:
            self._canvas.set_highlight_mode(False)

        cursors = {
            _MODE_REDACT: _get_icon_cursor("fa5s.eraser", 22, 22),
            _MODE_TEXT: Qt.CursorShape.IBeamCursor,
            _MODE_IMAGE: _get_icon_cursor("fa5s.image", 14, 14),
            _MODE_HIGHLIGHT: Qt.CursorShape.IBeamCursor,
            _MODE_HIGHLIGHT_RECT: Qt.CursorShape.CrossCursor,
            _MODE_NOTE: _get_icon_cursor("fa5s.sticky-note", 4, 4),
            _MODE_FORMS: Qt.CursorShape.ArrowCursor,
            _MODE_SIGNATURE: Qt.CursorShape.ArrowCursor,
            _MODE_SELECT: Qt.CursorShape.ArrowCursor,
        }
        if idx in cursors:
            self._canvas.setCursor(cursors[idx])

        if idx == _MODE_IMAGE:
            cur = self._img_drop.path()
            if not cur or not os.path.isfile(cur):
                self._pick_image()