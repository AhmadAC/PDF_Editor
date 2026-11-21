
# app/editor/tab_actions.py
"""PDF_Editor – Editing modes, typography formatters, JSON preference persistence, text effects, color shortcuts, draw tools, stamps, and element selections for TabEditar."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

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

_log = logging.getLogger(__name__)

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


def _editor_text_config_file() -> str:
    """Resolve destination path for editor_text_settings.json."""
    if sys.platform in ("win32", "darwin"):
        return os.path.normpath(
            os.path.join(os.path.expanduser("~"), ".PDF_Editor_editor_text_settings.json")
        )
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(xdg, "PDF_Editor")
    os.makedirs(d, exist_ok=True)
    return os.path.normpath(os.path.join(d, "editor_text_settings.json"))


class TabActionsMixin(_Base):
    """Mixin containing mode handlers, typography events, JSON settings persistence, color shortcuts, stamps, and selection manipulations."""

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
        _draw_width_lbl: Any
        _hi_color: Any
        _hi_rect_color: Any
        _img_drop: Any
        _signature_path: str | None
        _red_color: Any
        _text_submode: str
        _text_font: Any
        _text_size: Any
        _text_color: Any
        _btn_bold: Any
        _btn_italic: Any
        _btn_text_add: Any
        _btn_text_edit: Any
        _text_hint: Any
        _chk_shadow: Any
        _shadow_color: Any
        _shadow_opts_w: Any
        _shadow_angle: Any
        _shadow_opacity: Any
        _shadow_distance: Any
        _shadow_thickness: Any
        _chk_outline: Any
        _outline_opts_w: Any
        _outline_thickness: Any
        _outline_color: Any
        _stamp_size: Any
        _stamp_color: Any
        _last_selected_page: int
        _last_selected_rect: Any
        _last_selected_original_text: str
        _sel_result: Any
        _page_idx: int

        @property
        def _MODE_DEFS(self) -> list[tuple[str, str]]: ...
        def _add(self, edit: dict, *, _from_redo: bool = False) -> None: ...
        def _pick_image(self) -> None: ...
        def _pick_signature(self) -> None: ...
        def _update_nav(self) -> None: ...
        def _status(self, msg: str) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...

    def _current_text_effects(self) -> dict[str, Any]:
        return {
            "shadow_enabled": bool(getattr(self, "_chk_shadow", None) and self._chk_shadow.isChecked()),
            "shadow_angle": int(self._shadow_angle.value()) if hasattr(self, "_shadow_angle") else 45,
            "shadow_opacity": max(0.05, min(1.0, (self._shadow_opacity.value() if hasattr(self, "_shadow_opacity") else 50) / 100.0)),
            "shadow_distance": float(self._shadow_distance.value()) if hasattr(self, "_shadow_distance") else 3.0,
            "shadow_thickness": float(self._shadow_thickness.value()) if hasattr(self, "_shadow_thickness") else 0.0,
            "shadow_color": tuple(self._shadow_color.color_tuple()) if hasattr(self, "_shadow_color") else (0.0, 0.0, 0.0),
            "outline_enabled": bool(getattr(self, "_chk_outline", None) and self._chk_outline.isChecked()),
            "outline_thickness": float(self._outline_thickness.value()) if hasattr(self, "_outline_thickness") else 1.0,
            "outline_color": tuple(self._outline_color.color_tuple()) if hasattr(self, "_outline_color") else (0.0, 0.0, 0.0),
        }

    def _save_text_settings(self) -> None:
        if getattr(self, "_loading_text_settings", False) or not hasattr(self, "_text_size"):
            return
        data = {
            "font": self._text_font.currentText(),
            "size": int(self._text_size.value()),
            "bold": bool(self._btn_bold.isChecked()),
            "italic": bool(self._btn_italic.isChecked()),
            "color": [float(x) for x in self._text_color.color_tuple()],
            "shadow_enabled": bool(self._chk_shadow.isChecked()),
            "shadow_angle": int(self._shadow_angle.value()),
            "shadow_opacity": int(self._shadow_opacity.value()),
            "shadow_distance": int(self._shadow_distance.value()),
            "shadow_thickness": int(self._shadow_thickness.value()),
            "shadow_color": [float(x) for x in self._shadow_color.color_tuple()],
            "outline_enabled": bool(self._chk_outline.isChecked()),
            "outline_thickness": int(self._outline_thickness.value()),
            "outline_color": [float(x) for x in self._outline_color.color_tuple()],
        }
        cfg_file = _editor_text_config_file()
        try:
            os.makedirs(os.path.dirname(cfg_file), exist_ok=True)
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as exc:
            _log.warning("Could not save editor text settings to %s: %s", cfg_file, exc)

        with contextlib.suppress(Exception):
            from app.i18n import _update_config
            _update_config(lambda cfg: cfg.__setitem__("editor_text_settings", data))

    def _load_text_settings(self) -> None:
        if not hasattr(self, "_text_size"):
            return
        data: dict[str, Any] = {}
        cfg_file = _editor_text_config_file()
        if os.path.isfile(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                _log.warning("Could not parse %s: %s", cfg_file, exc)

        if not data:
            try:
                from app.i18n import _CONFIG_PATH
                if os.path.isfile(_CONFIG_PATH):
                    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        data = cfg.get("editor_text_settings", {})
            except Exception:
                pass

        if not isinstance(data, dict) or not data:
            return

        widgets = (
            self._text_font,
            self._text_size,
            self._btn_bold,
            self._btn_italic,
            self._text_color,
            self._chk_shadow,
            self._shadow_angle,
            self._shadow_opacity,
            self._shadow_distance,
            self._shadow_thickness,
            self._shadow_color,
            self._chk_outline,
            self._outline_thickness,
            self._outline_color,
        )
        self._loading_text_settings = True
        for w in widgets:
            w.blockSignals(True)
        try:
            if "font" in data:
                fam = str(data["font"])
                idx = self._text_font.findText(fam, Qt.MatchFlag.MatchExactly)
                if idx < 0:
                    idx = self._text_font.findText(fam, Qt.MatchFlag.MatchContains)
                if idx >= 0:
                    self._text_font.setCurrentIndex(idx)
            if "size" in data:
                with contextlib.suppress(Exception):
                    self._text_size.setValue(max(4, min(200, int(data["size"]))))
            if "bold" in data:
                self._btn_bold.setChecked(bool(data["bold"]))
            if "italic" in data:
                self._btn_italic.setChecked(bool(data["italic"]))
            if isinstance(data.get("color"), (list, tuple)) and len(data["color"]) >= 3:
                self._text_color.set_color(tuple(float(x) for x in data["color"][:3]))

            sh_en = bool(data.get("shadow_enabled", False))
            self._chk_shadow.setChecked(sh_en)
            self._shadow_opts_w.setVisible(sh_en)
            self._shadow_color.setVisible(sh_en)
            if "shadow_angle" in data:
                with contextlib.suppress(Exception):
                    self._shadow_angle.setValue(max(0, min(360, int(data["shadow_angle"]))))
            if "shadow_opacity" in data:
                with contextlib.suppress(Exception):
                    self._shadow_opacity.setValue(max(5, min(100, int(data["shadow_opacity"]))))
            if "shadow_distance" in data:
                with contextlib.suppress(Exception):
                    self._shadow_distance.setValue(max(0, min(40, int(data["shadow_distance"]))))
            if "shadow_thickness" in data:
                with contextlib.suppress(Exception):
                    self._shadow_thickness.setValue(max(0, min(15, int(data["shadow_thickness"]))))
            if isinstance(data.get("shadow_color"), (list, tuple)) and len(data["shadow_color"]) >= 3:
                self._shadow_color.set_color(tuple(float(x) for x in data["shadow_color"][:3]))

            out_en = bool(data.get("outline_enabled", False))
            self._chk_outline.setChecked(out_en)
            self._outline_opts_w.setVisible(out_en)
            if "outline_thickness" in data:
                with contextlib.suppress(Exception):
                    self._outline_thickness.setValue(max(1, min(15, int(data["outline_thickness"]))))
            if isinstance(data.get("outline_color"), (list, tuple)) and len(data["outline_color"]) >= 3:
                self._outline_color.set_color(tuple(float(x) for x in data["outline_color"][:3]))
        finally:
            for w in widgets:
                w.blockSignals(False)
            self._loading_text_settings = False

    def _on_shadow_toggled(self, checked: bool) -> None:
        if hasattr(self, "_shadow_opts_w"):
            self._shadow_opts_w.setVisible(checked)
        if hasattr(self, "_shadow_color"):
            self._shadow_color.setVisible(checked)
        self._on_text_format_changed()

    def _on_outline_toggled(self, checked: bool) -> None:
        if hasattr(self, "_outline_opts_w"):
            self._outline_opts_w.setVisible(checked)
        self._on_text_format_changed()

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

    def _set_text_submode(self, mode: str):
        is_add = (mode == "add")
        if hasattr(self, "_btn_text_add"):
            self._btn_text_add.setChecked(is_add)
        if hasattr(self, "_btn_text_edit"):
            self._btn_text_edit.setChecked(not is_add)
        self._text_submode = mode
        if is_add:
            self._load_text_settings()
        if hasattr(self, "_canvas") and hasattr(self._canvas, "set_text_submode"):
            self._canvas.set_text_submode(mode)
        self._update_text_submode_styles()
        if hasattr(self, "_text_hint"):
            if is_add:
                self._text_hint.setText("Click anywhere on the page to insert new text exactly where you click.")
            else:
                self._text_hint.setText("Click or drag across any text on the page to edit it.")

    def _update_text_submode_styles(self):
        if not hasattr(self, "_btn_text_add") or not hasattr(self, "_btn_text_edit"):
            return
        is_add = getattr(self, "_text_submode", "edit") == "add"
        active_style = (
            f"background:#264F78; border:1px solid {ACCENT}; color:#FFFFFF; border-radius:4px; font-weight:600; padding:3px 8px;"
            if self._dark_mode else
            f"background:#D6E8FA; border:1px solid #70A7DB; color:{ACCENT}; border-radius:4px; font-weight:600; padding:3px 8px;"
        )
        inactive_style = (
            "background:#333333; border:1px solid #444444; color:#CCCCCC; border-radius:4px; padding:3px 8px;"
            if self._dark_mode else
            "background:#FFFFFF; border:1px solid #D1D5DB; color:#555555; border-radius:4px; padding:3px 8px;"
        )
        self._btn_text_add.setStyleSheet(active_style if is_add else inactive_style)
        self._btn_text_edit.setStyleSheet(inactive_style if is_add else active_style)

    def _on_text_format_changed(self, *_args):
        if getattr(self, "_loading_text_settings", False):
            return
        self._save_text_settings()
        font = self._text_font.currentText()
        size = float(self._text_size.value())
        color = self._text_color.color_tuple()
        bold = self._btn_bold.isChecked()
        italic = self._btn_italic.isChecked()
        effects = self._current_text_effects()
        self._canvas.update_active_text_format(font, size, color, bold, italic, effects=effects)
        if hasattr(self, "_sync_thumbnails") and self._canvas._selected_overlay_idx >= 0:
            ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
            self._sync_thumbnails({ov.get("page", 0)})

    def _on_text_edit_started(self, fmt: dict):
        widgets = (
            self._text_font,
            self._text_size,
            self._text_color,
            self._btn_bold,
            self._btn_italic,
            self._chk_shadow,
            self._shadow_angle,
            self._shadow_opacity,
            self._shadow_distance,
            self._shadow_thickness,
            self._shadow_color,
            self._chk_outline,
            self._outline_thickness,
            self._outline_color,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            font_name = str(fmt.get("font", "Helvetica") or "Helvetica")
            clean_name = font_name.split("+")[-1].strip()

            idx = self._text_font.findText(clean_name, Qt.MatchFlag.MatchExactly)
            if idx < 0:
                idx = self._text_font.findText(clean_name, Qt.MatchFlag.MatchContains)
            if idx < 0 and clean_name != font_name:
                idx = self._text_font.findText(font_name, Qt.MatchFlag.MatchContains)

            if idx >= 0:
                self._text_font.setCurrentIndex(idx)
            else:
                self._text_font.addItem(clean_name)
                self._text_font.setCurrentIndex(self._text_font.count() - 1)

            self._text_size.setValue(max(4, int(round(fmt.get("size", 12)))))
            col = fmt.get("color", (0, 0, 0))
            self._text_color.set_color(col)
            self._btn_bold.setChecked(fmt.get("bold", False))
            self._btn_italic.setChecked(fmt.get("italic", False))

            sh_en = bool(fmt.get("shadow_enabled", False))
            self._chk_shadow.setChecked(sh_en)
            self._shadow_opts_w.setVisible(sh_en)
            self._shadow_color.setVisible(sh_en)
            if "shadow_angle" in fmt:
                self._shadow_angle.setValue(int(round(float(fmt["shadow_angle"]))))
            if "shadow_opacity" in fmt:
                self._shadow_opacity.setValue(max(5, min(100, int(round(float(fmt["shadow_opacity"]) * 100)))))
            if "shadow_distance" in fmt:
                self._shadow_distance.setValue(int(round(float(fmt["shadow_distance"]))))
            if "shadow_thickness" in fmt:
                self._shadow_thickness.setValue(int(round(float(fmt["shadow_thickness"]))))
            if fmt.get("shadow_color"):
                self._shadow_color.set_color(tuple(float(x) for x in fmt["shadow_color"][:3]))

            out_en = bool(fmt.get("outline_enabled", False))
            self._chk_outline.setChecked(out_en)
            self._outline_opts_w.setVisible(out_en)
            if "outline_thickness" in fmt:
                self._outline_thickness.setValue(max(1, int(round(float(fmt["outline_thickness"])))))
            if fmt.get("outline_color"):
                self._outline_color.set_color(tuple(float(x) for x in fmt["outline_color"][:3]))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _delete_active_text(self):
        if self._canvas._inline_edit.isVisible():
            self._canvas._inline_edit.setText("")
            self._canvas._commit_inline()
        elif self._canvas._selected_overlay_idx >= 0:
            self._canvas.delete_selected_overlay()

    def _on_draw_color_changed(self, _color_tuple):
        self._canvas.set_draw_mode(
            self._mode_idx == _MODE_DRAW,
            color=self._draw_color_cb.color_tuple(),
            width=self._draw_width_slider.value(),
        )

    def _on_draw_width_changed(self, v: int):
        self._draw_width_lbl.setText(str(v))
        self._canvas.set_draw_mode(
            self._mode_idx == _MODE_DRAW,
            color=self._draw_color_cb.color_tuple(),
            width=v,
        )

    def _on_stroke(self, page_idx: int, pdf_points):
        self._page_idx = page_idx
        self._update_nav()
        col = getattr(self._canvas, "_draw_color", None)
        if col is None:
            col = self._draw_color_cb.color_tuple()
        self._add({
            "type": "draw",
            "page": page_idx,
            "points": pdf_points,
            "color": col,
            "width": self._draw_width_slider.value(),
        })
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _delete_selection(self):
        if self._canvas._selected_overlay_idx >= 0:
            self._canvas.delete_selected_overlay()
            return
        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        old_txt = self._sel_result.toPlainText().strip() if hasattr(self, "_sel_result") else ""
        edit = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
            "old_text": old_txt,
            "new_text": "",
            "size": max(8.0, rect.height * 0.8),
            "font_size": max(8.0, rect.height * 0.8),
            "color": (0, 0, 0),
            "font": "Helvetica",
            "flags": 0,
            "origin": [rect.x0, rect.y1],
            "_existing": True,
            "_dirty": True,
            "_orig_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        }
        self._add(edit)
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        if hasattr(self, "_sel_result"):
            self._sel_result.setPlainText("")
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Selected element deleted (Ctrl+Z to undo)")

    def _edit_selection(self):
        if hasattr(self, "_canvas") and self._canvas._selected_overlay_idx >= 0:
            ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                self._canvas._event_handler._begin_inline_edit_for_overlay(ov)
                return

        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        center = fitz.Point((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)
        hit = self._canvas.get_span_at(page_idx, center, max_dist=12.0)
        if hit:
            self._canvas.begin_inline_text_edit(hit, page_idx)
        else:
            txt = self._sel_result.toPlainText() if hasattr(self, "_sel_result") else ""
            span = {
                "text": txt,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "size": max(8.0, rect.height * 0.8),
                "font": "Helvetica",
                "color": 0,
                "flags": 0,
                "origin": [rect.x0, rect.y1],
            }
            self._canvas.begin_inline_text_edit(span, page_idx)

    def _apply_text_edit_from_selection(self):
        new_txt = self._sel_result.toPlainText() if hasattr(self, "_sel_result") else ""
        if hasattr(self, "_canvas") and self._canvas._selected_overlay_idx >= 0:
            ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
            if ov.get("type") in ("text", "text_edit"):
                before_snap = snapshot_overlay(ov)
                was_untouched = bool(ov.get("_existing") and not ov.get("_dirty"))
                ov["new_text"] = new_txt
                ov["text"] = new_txt
                ov.pop("runs", None)
                if was_untouched:
                    ov["_dirty"] = True
                    self._on_overlay_committed(ov)
                else:
                    self._on_overlay_transformed(ov, before_snap)
                self._canvas.overlay_changed.emit()
                self._canvas.update()
                if hasattr(self, "_sync_thumbnails"):
                    self._sync_thumbnails({ov.get("page", 0)})
                self._status("Updated text applied (Ctrl+Z to undo)")
                return

        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        old_txt = getattr(self, "_last_selected_original_text", "")
        edit = {
            "type": "text_edit",
            "page": page_idx,
            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
            "old_text": old_txt,
            "new_text": new_txt,
            "size": max(8.0, rect.height * 0.8),
            "font_size": max(8.0, rect.height * 0.8),
            "color": (0, 0, 0),
            "font": "Helvetica",
            "flags": 0,
            "origin": [rect.x0, rect.y1],
            "_existing": True,
            "_dirty": True,
            "_orig_rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        }
        self._add(edit)
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Edited text applied (Ctrl+Z to undo)")

    def _redact_selection(self):
        page_idx = getattr(self, "_last_selected_page", -1)
        rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or rect is None:
            return
        self._add({
            "type": "redact",
            "page": page_idx,
            "rect": rect,
            "fill": (0, 0, 0),
        })
        if hasattr(self._canvas, "_active_sel_rect"):
            self._canvas._active_sel_rect = None
        self._last_selected_rect = None
        if hasattr(self, "_sel_result"):
            self._sel_result.setPlainText("")
        self._canvas.update()
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Selection redacted (Ctrl+Z to undo)")

    def _highlight_selection(self):
        page_idx = getattr(self, "_last_selected_page", -1)
        pdf_rect = getattr(self, "_last_selected_rect", None)
        if page_idx < 0 or pdf_rect is None:
            return
        doc = self._canvas._doc
        if not doc or not (0 <= page_idx < doc.page_count):
            return
        page = doc[page_idx]
        words = page.get_text("words", clip=pdf_rect)
        line_rects = []
        if words:
            lines_dict = {}
            for w in words:
                key = (w[5], w[6])
                lines_dict.setdefault(key, []).append(w)
            for line_words in lines_dict.values():
                lx0 = min(w[0] for w in line_words)
                ly0 = min(w[1] for w in line_words)
                lx1 = max(w[2] for w in line_words)
                ly1 = max(w[3] for w in line_words)
                line_rects.append(fitz.Rect(lx0, ly0, lx1, ly1))
        hi_color = getattr(self._canvas, "_highlight_color", None)
        if hi_color is None:
            hi_color = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)
        self._add({
            "type": "highlight",
            "page": page_idx,
            "rect": pdf_rect,
            "rects": line_rects if line_rects else None,
            "color": hi_color
        })
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
        self._status("Highlight added to selection")

    def _on_rect(self, page_idx: int, pdf_rect):
        self._page_idx = page_idx
        self._update_nav()
        mode = self._mode_idx

        if getattr(self._canvas, "_highlight_mode", False) or mode in (_MODE_HIGHLIGHT, _MODE_HIGHLIGHT_RECT):
            hi_color = getattr(self._canvas, "_highlight_color", None)
            if hi_color is None:
                hi_color = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)

            if mode == _MODE_HIGHLIGHT:
                doc = self._canvas._doc
                line_rects = []
                if doc and 0 <= page_idx < doc.page_count:
                    page = doc[page_idx]
                    words = page.get_text("words", clip=pdf_rect)
                    if words:
                        lines_dict = {}
                        for w in words:
                            key = (w[5], w[6])
                            lines_dict.setdefault(key, []).append(w)
                        for line_words in lines_dict.values():
                            lx0 = min(w[0] for w in line_words)
                            ly0 = min(w[1] for w in line_words)
                            lx1 = max(w[2] for w in line_words)
                            ly1 = max(w[3] for w in line_words)
                            line_rects.append(fitz.Rect(lx0, ly0, lx1, ly1))
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": pdf_rect,
                    "rects": line_rects if line_rects else None,
                    "color": hi_color
                })
            else:
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": pdf_rect,
                    "color": hi_color
                })
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({page_idx})
            self._status("Highlight added (Ctrl+Z to undo)")
            return

        if mode == _MODE_SELECT:
            doc = self._canvas._doc
            if not doc:
                return
            raw_text = doc[page_idx].get_text("text", clip=pdf_rect)
            text = raw_text.strip() if isinstance(raw_text, str) else str(raw_text).strip()
            self._sel_result.setPlainText(text)
            self._last_selected_page = page_idx
            self._last_selected_rect = pdf_rect
            self._last_selected_original_text = text
            if hasattr(self._canvas, "_active_sel_rect"):
                self._canvas._active_sel_rect = fitz.Rect(pdf_rect)
                self._canvas._active_sel_page = page_idx
                self._canvas.update()
            if text:
                clipboard = QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                self._status(t("edit.status.copied_clipboard", n=len(text)))
            else:
                self._status("Element selected (Press Del to delete or click Edit)")
            return

        if mode == _MODE_TEXT:
            submode = getattr(self, "_text_submode", "edit")
            if submode == "edit":
                doc = self._canvas._doc
                if not doc or not (0 <= page_idx < doc.page_count):
                    return
                page = doc[page_idx]
                matched_spans = []
                text_dict = page.get_text("dict", clip=pdf_rect)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if not span.get("text", "").strip():
                                continue
                            s_bbox = fitz.Rect(span["bbox"])
                            inter = s_bbox & pdf_rect
                            if not inter.is_empty and inter.width > 1 and inter.height > 1:
                                matched_spans.append(span)

                if not matched_spans:
                    self._status("No text found in selected area to edit.")
                    return

                if len(matched_spans) == 1:
                    target_span = dict(matched_spans[0])
                else:
                    first = matched_spans[0]
                    union_rect = fitz.Rect(first["bbox"])
                    for sp in matched_spans[1:]:
                        union_rect |= fitz.Rect(sp["bbox"])
                    combined_text = " ".join(
                        sp.get("text", "").strip() for sp in matched_spans if sp.get("text", "").strip()
                    )
                    target_span = dict(first)
                    target_span["bbox"] = [union_rect.x0, union_rect.y0, union_rect.x1, union_rect.y1]
                    target_span["text"] = combined_text

                self._canvas.begin_inline_text_edit(target_span, page_idx)
                return

            insert_pt = fitz.Point(pdf_rect.x0, pdf_rect.y0)
            size = float(self._text_size.value())
            color = self._text_color.color_tuple()
            font = self._text_font.currentText()
            bold = self._btn_bold.isChecked()
            italic = self._btn_italic.isChecked()
            effects = self._current_text_effects()
            self._canvas.begin_inline_text_insert(page_idx, insert_pt, size, color, font, bold, italic, effects=effects)
            self._canvas.update_active_text_format(font, size, color, bold, italic, effects=effects)
            return

        if mode == _MODE_NOTE:
            center = fitz.Point((pdf_rect.x0 + pdf_rect.x1) / 2, (pdf_rect.y0 + pdf_rect.y1) / 2)
            self._on_point(page_idx, center)
            return
        if mode == _MODE_REDACT:
            self._add({"type": "redact", "page": self._page_idx, "rect": pdf_rect, "fill": self._red_color.color_tuple()})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_IMAGE:
            img = self._img_drop.path()
            if not img or not os.path.isfile(img):
                self._pick_image()
                img = self._img_drop.path()
                if not img or not os.path.isfile(img):
                    return
            self._add({"type": "image", "page": self._page_idx, "rect": pdf_rect, "path": img})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_SIGNATURE:
            sig = self._signature_path
            if not sig or not os.path.isfile(sig):
                self._pick_signature()
                sig = self._signature_path
                if not sig or not os.path.isfile(sig):
                    return
            self._add({"type": "signature", "page": self._page_idx, "rect": pdf_rect, "path": sig})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})

    def _on_point(self, page_idx: int, pdf_pt):
        self._page_idx = page_idx
        self._update_nav()
        doc = self._canvas._doc
        if doc:
            page = doc[page_idx]
            for annot in page.annots() or []:
                annot_type = getattr(annot, "type", (None, None))
                if (isinstance(annot_type, (tuple, list)) and len(annot_type) >= 2 and (annot_type[0] == 0 or annot_type[1] == "Text")) or annot_type == 0:
                    ar = annot.rect
                    expanded = fitz.Rect(ar.x0 - 10, ar.y0 - 10, ar.x1 + 10, ar.y1 + 10)
                    if expanded.contains(fitz.Point(pdf_pt.x, pdf_pt.y)):
                        info = getattr(annot, "info", None) or {}
                        txt = info.get("content", "") if isinstance(info, dict) else ""
                        if txt:
                            from PySide6.QtWidgets import QMessageBox
                            QMessageBox.information(self, t("edit.note_popup"), txt)
                            return
        mode = self._mode_idx
        if mode == _MODE_SELECT:
            if 0 <= self._canvas._selected_overlay_idx < len(self._canvas._overlays):
                ov = self._canvas._overlays[self._canvas._selected_overlay_idx]
                txt = (ov.get("new_text") or ov.get("text") or "").strip()
                bb = ov.get("bbox")
                rect = fitz.Rect(bb) if bb else ov.get("rect")
                self._sel_result.setPlainText(txt)
                self._last_selected_page = page_idx
                self._last_selected_rect = rect
                self._last_selected_original_text = txt
                if txt:
                    clipboard = QApplication.clipboard()
                    if clipboard:
                        clipboard.setText(txt)
                self._status(f"Selected: {txt[:30]}" if txt else "Element selected (Drag to move or Del to delete)")
                return

            hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=8.0)
            if hit:
                bb = fitz.Rect(hit["bbox"])
                txt = hit.get("text", "").strip()
                self._sel_result.setPlainText(txt)
                self._last_selected_page = page_idx
                self._last_selected_rect = bb
                self._last_selected_original_text = txt
                if hasattr(self._canvas, "_active_sel_rect"):
                    self._canvas._active_sel_rect = bb
                    self._canvas._active_sel_page = page_idx
                    self._canvas.update()
                if txt:
                    clipboard = QApplication.clipboard()
                    if clipboard:
                        clipboard.setText(txt)
                self._status(f"Selected: {txt[:30]}")
            else:
                if hasattr(self._canvas, "_active_sel_rect"):
                    self._canvas._active_sel_rect = None
                    self._canvas.update()
                self._last_selected_rect = None
        elif mode == _MODE_TEXT:
            submode = getattr(self, "_text_submode", "edit")
            if submode != "add":
                hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=6.0)
                if hit:
                    self._canvas.begin_inline_text_edit(hit, page_idx)
                    return
                self._status("Click or drag across existing text to edit it.")
                return

            size = float(self._text_size.value())
            color = self._text_color.color_tuple()
            font = self._text_font.currentText()
            bold = self._btn_bold.isChecked()
            italic = self._btn_italic.isChecked()
            effects = self._current_text_effects()
            self._canvas.begin_inline_text_insert(page_idx, pdf_pt, size, color, font, bold, italic, effects=effects)
            self._canvas.update_active_text_format(font, size, color, bold, italic, effects=effects)
        elif mode == _MODE_NOTE:
            from app.editor.dialogs import _NoteDialog
            dlg = _NoteDialog(self)
            if dlg.exec() != _NoteDialog.DialogCode.Accepted:
                return
            txt = dlg.edit.toPlainText().strip()
            if not txt:
                return
            self._add({"type": "note", "page": self._page_idx, "point": pdf_pt, "text": txt})
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails({self._page_idx})
        elif mode == _MODE_HIGHLIGHT:
            hit = self._canvas.get_span_at(page_idx, pdf_pt, max_dist=8.0)
            if hit:
                bb = fitz.Rect(hit["bbox"])
                hi_col = getattr(self._canvas, "_highlight_color", None)
                if hi_col is None:
                    hi_col = self._hi_color.color_tuple() if hasattr(self, "_hi_color") else (1.0, 1.0, 0.0)
                self._add({
                    "type": "highlight",
                    "page": page_idx,
                    "rect": bb,
                    "color": hi_col
                })
                if hasattr(self, "_sync_thumbnails"):
                    self._sync_thumbnails({page_idx})
                self._status("Word highlighted")

    def _on_text_edit_committed(self, page_idx: int, edit: dict):
        self._add(edit)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_signature_added(self, page_idx: int, rect, path: str):
        self._add({"type": "signature", "page": page_idx, "rect": rect, "path": path})
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_deleted(self, idx: int, edit: dict):
        page_idx = edit.get("page", 0)
        self._history.handle_overlay_deleted(idx, edit)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_committed(self, edit: dict):
        page_idx = edit.get("page", 0)
        self._history.handle_overlay_committed(edit)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_transformed(self, edit: dict, before_snap: dict):
        page_idx = edit.get("page", 0)
        self._history.handle_overlay_transformed(edit, before_snap)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_arranged(self, info: dict):
        page_idx = info.get("page", 0)
        self._history.handle_overlay_arranged(info)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_ungrouped(self, info: dict):
        page_idx = info.get("page", 0)
        self._history.handle_overlay_ungrouped(info)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_grouped(self, info: dict):
        page_idx = info.get("page", 0)
        self._history.handle_overlay_grouped(info)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_overlay_duplicated(self, new_edit: dict):
        page_idx = new_edit.get("page", 0)
        self._history.handle_overlay_duplicated(new_edit)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})

    def _on_note_deleted(self, overlay: dict):
        page_idx = overlay.get("page", 0)
        self._history.handle_note_deleted(overlay)
        if hasattr(self, "_sync_thumbnails"):
            self._sync_thumbnails({page_idx})
