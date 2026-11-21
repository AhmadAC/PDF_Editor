# app/editor/tab_actions.py
"""PDF_Editor – Facade unifying typography formatters, modes, stamps, selections, and overlay actions for TabEditar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from app.editor.tab_actions_modes import _COLOR_SHORTCUTS, TabModesActionsMixin
from app.editor.tab_actions_overlay import TabOverlayActionsMixin
from app.editor.tab_actions_selection import TabSelectionActionsMixin
from app.editor.tab_actions_text import _editor_text_config_file, TabTextActionsMixin

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object

__all__ = [
    "_COLOR_SHORTCUTS",
    "_editor_text_config_file",
    "TabActionsMixin",
    "TabTextActionsMixin",
    "TabModesActionsMixin",
    "TabSelectionActionsMixin",
    "TabOverlayActionsMixin",
]


class TabActionsMixin(
    TabTextActionsMixin,
    TabModesActionsMixin,
    TabSelectionActionsMixin,
    TabOverlayActionsMixin,
    _Base,
):
    """Facade mixin aggregating editing modes, typography settings, selection tools, and overlay dispatchers."""