# app/viewer/thumbnail_layout.py
"""Responsive multi-column grid layout, scaling, selection, and theme mixin for ThumbnailPanel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QSize
from PySide6.QtWidgets import QAbstractItemView

from app.i18n import _update_config
from app.viewer.thumbnail_worker import (
    DEFAULT_THUMB_HEIGHT,
    DEFAULT_THUMB_WIDTH,
    PAGE_NUM_HEIGHT,
    THUMB_PADDING,
)


class ThumbnailLayoutMixin:
    """Mixin managing grid geometry, thumbnail scaling/fitting, selection state, and theme."""

    if TYPE_CHECKING:
        _view: Any
        _model: Any
        _delegate: Any
        _thumb_scale: float
        _anchor: int
        _inflight: set[int]
        action_requested: Any

        def width(self) -> int: ...
        def _stop_all_workers(self) -> None: ...
        def _render_visible(self) -> None: ...

    def column_count(self) -> int:
        vp = self._view.viewport() if self._view else None
        if not vp:
            return 1
        vp_w = vp.width()
        if vp_w <= 10:
            return 1
        target_thumb_w = int(DEFAULT_THUMB_WIDTH * self._thumb_scale)
        min_cell_w = target_thumb_w + 16
        return max(1, vp_w // min_cell_w)

    def _update_grid_layout(self) -> None:
        vp = self._view.viewport() if self._view else None
        if not vp:
            return
        vp_w = vp.width()
        if vp_w <= 10:
            return

        target_thumb_w = int(DEFAULT_THUMB_WIDTH * self._thumb_scale)
        target_thumb_h = int(DEFAULT_THUMB_HEIGHT * self._thumb_scale)
        min_cell_w = target_thumb_w + 16

        cols = max(1, vp_w // min_cell_w)
        cell_w = max(min_cell_w, vp_w // cols)
        cell_h = target_thumb_h + 2 * THUMB_PADDING + PAGE_NUM_HEIGHT

        self._delegate.set_thumb_size(target_thumb_w, target_thumb_h)
        self._delegate.set_cell_width(cell_w)
        self._view.setGridSize(QSize(cell_w, cell_h))

    def _save_thumb_scale_pref(self) -> None:
        type(self)._thumb_scale_pref = self._thumb_scale  # type: ignore[attr-defined]
        try:
            scale = self._thumb_scale
            _update_config(lambda cfg: cfg.__setitem__("thumbnail_scale", scale))
        except Exception:
            pass

    def fit_window_to_thumbnails(self) -> None:
        """Resize the parent sidebar panel in the splitter to fit the thumbnails."""
        cols = self.column_count()
        tw = int(DEFAULT_THUMB_WIDTH * self._thumb_scale)
        needed_w = (tw + 2 * THUMB_PADDING) * cols + 24
        self.action_requested.emit("fit_sidebar_width", needed_w)

    def fit_thumbnails_to_window(self) -> None:
        """Scale thumbnail dimensions so they fill the current sidebar viewport width."""
        vp = self._view.viewport() if self._view else None
        vp_w = vp.width() if vp else 0
        if vp_w <= 0:
            vp_w = self.width()
        cols = self.column_count()
        avail_for_col = max(60, vp_w // cols)
        target_thumb_w = max(60, min(360, avail_for_col - 2 * THUMB_PADDING - 4))
        self._thumb_scale = round(target_thumb_w / DEFAULT_THUMB_WIDTH, 2)
        self._update_grid_layout()
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        self._render_visible()
        self._view.viewport().update()
        self._save_thumb_scale_pref()

    def _enlarge_thumbnails(self) -> None:
        self._thumb_scale = min(2.5, round(self._thumb_scale * 1.25, 2))
        self._update_grid_layout()
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        self._render_visible()
        self._view.viewport().update()
        self._save_thumb_scale_pref()

    def _reduce_thumbnails(self) -> None:
        self._thumb_scale = max(0.5, round(self._thumb_scale / 1.25, 2))
        self._update_grid_layout()
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        self._render_visible()
        self._view.viewport().update()
        self._save_thumb_scale_pref()

    def selected_pages(self) -> list[int]:
        """Return the sorted list of 0-based indices of all currently selected pages."""
        if not self._view or not self._model:
            return [self._anchor] if self._anchor >= 0 else [0]
        selected_indexes = self._view.selectedIndexes()
        pages = sorted(
            {
                i.row()
                for i in selected_indexes
                if i.isValid() and 0 <= i.row() < self._model.rowCount()
            }
        )
        return (
            pages
            if pages
            else (
                [self._anchor]
                if 0 <= self._anchor < self._model.rowCount()
                else [0]
            )
        )

    def set_selected_pages(self, pages: list[int]) -> None:
        """Select the specified list of page indices in the thumbnail view and scroll into view."""
        if not self._view or not self._model or self._model.rowCount() <= 0:
            return
        selection = QItemSelection()
        for p in pages:
            if 0 <= p < self._model.rowCount():
                idx = self._model.index(p)
                selection.select(idx, idx)
        sm = self._view.selectionModel()
        if sm is not None:
            sm.select(
                selection, QItemSelectionModel.SelectionFlag.ClearAndSelect
            )
        if pages:
            first_p = min(pages)
            self._delegate.set_current_page(first_p)
            self._anchor = first_p
            idx = self._model.index(first_p)
            self._view.setCurrentIndex(idx)
            self._view.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        vp = self._view.viewport()
        if vp is not None:
            vp.update()

    def set_current_page(
        self,
        page_idx: int,
        viewport_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        if not (0 <= page_idx < self._model.rowCount()):
            return
        self._anchor = page_idx
        old = self._delegate.set_current_page(page_idx, viewport_rect)
        idx = self._model.index(page_idx)
        if len(self._view.selectedIndexes()) <= 1:
            self._view.setCurrentIndex(idx)
        self._view.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        vp = self._view.viewport()
        if vp is not None:
            if old >= 0 and old != page_idx:
                old_rect = self._view.visualRect(self._model.index(old))
                vp.update(old_rect)
            vp.update(self._view.visualRect(idx))
        self._render_visible()

    def update_theme(self, dark: bool) -> None:
        self._delegate.set_dark(dark)
        self._view.viewport().update()