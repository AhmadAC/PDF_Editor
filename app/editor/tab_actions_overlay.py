# app/editor/tab_actions_overlay.py
"""Overlay lifecycle forwarding and history dispatchers for TabEditar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from app.editor.tab_history import TabHistoryManager
    _Base = QWidget
else:
    _Base = object


class TabOverlayActionsMixin(_Base):
    """Mixin routing overlay creation, modification, grouping, and deletion to TabHistoryManager."""

    if TYPE_CHECKING:
        _history: TabHistoryManager
        def _add(self, edit: dict, *, _from_redo: bool = False) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...

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