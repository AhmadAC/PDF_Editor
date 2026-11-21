# app/editor/tab_actions_text.py
"""Typography settings, JSON preferences persistence, and text effect handlers for TabEditar."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.constants import ACCENT

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    _Base = QWidget
else:
    _Base = object

_log = logging.getLogger(__name__)


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


class TabTextActionsMixin(_Base):
    """Mixin providing text preferences, formatting, and effect handlers."""

    if TYPE_CHECKING:
        _dark_mode: bool
        _canvas: PdfEditCanvas
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