# app/viewer/thumbnail_render.py
"""Document synchronization, selective edit invalidation, visible range calculation, and worker orchestration for ThumbnailPanel."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QModelIndex, QObject, Qt, Slot
from PySide6.QtGui import QImage, QPixmap

from app.viewer.thumbnail_worker import (
    DEFAULT_THUMB_HEIGHT,
    DEFAULT_THUMB_WIDTH,
    HIDDEN_WINDOW,
    ThumbnailWorker,
    VISIBLE_BUFFER,
)

_log = logging.getLogger(__name__)


class ThumbnailRenderMixin:
    """Mixin managing document state updates, selective page-edit rendering, and ThumbnailWorker lifecycle."""

    if TYPE_CHECKING:
        _doc_path: str
        _password: str
        _rotations: dict[int, int]
        _crops: dict[int, tuple[float, float, float, float]]
        _page_order: list[int] | None
        _page_edits: dict[int, list[dict]]
        _doc_page_count: int
        _workers: list[ThumbnailWorker]
        _inflight: set[int]
        _anchor: int
        _epoch: int
        _thumb_scale: float
        _model: Any
        _delegate: Any
        _view: Any
        page_requested: Any

        def column_count(self) -> int: ...
        def _update_grid_layout(self) -> None: ...
        def devicePixelRatioF(self) -> float: ...
        def sender(self) -> QObject | None: ...

    def set_document(
        self, doc_path: str, page_count: int, password: str = ""
    ) -> None:
        self._doc_path = doc_path
        self._password = password or ""
        self._rotations = {}
        self._crops = {}
        self._page_order = None
        self._page_edits = {}
        self._doc_page_count = page_count
        self._stop_all_workers()
        self._epoch += 1
        self._inflight.clear()
        self._anchor = 0
        self._delegate.set_total_pages(page_count)
        self._model.set_document(doc_path, page_count)
        self._delegate.set_current_page(-1, None)
        self._update_grid_layout()
        if page_count > 0 and doc_path:
            self._render_visible()

    def set_page_edits(
        self,
        edits_by_page: dict[int, list[dict]],
        affected_pages: set[int] | None = None,
    ) -> None:
        """Selectively invalidate and re-render thumbnails for pages with live pending edits."""
        new_edits = {
            int(k): [dict(e) for e in v]
            for k, v in edits_by_page.items()
            if v
        }
        changed_src = (
            set(int(p) for p in affected_pages)
            if affected_pages is not None
            else (set(self._page_edits.keys()) | set(new_edits.keys()))
        )
        self._page_edits = new_edits
        if not changed_src or self._model.rowCount() <= 0:
            return

        if self._page_order is not None:
            changed_rows = [
                pos for pos, src in enumerate(self._page_order) if src in changed_src
            ]
        else:
            changed_rows = [
                p for p in changed_src if 0 <= p < self._model.rowCount()
            ]

        if not changed_rows:
            return

        self._stop_all_workers()
        self._epoch += 1
        self._inflight.clear()
        for row in changed_rows:
            self._model._cache.pop(row, None)
            idx = self._model.index(row)
            self._model.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
        self._render_visible()
        vp = self._view.viewport()
        if vp is not None:
            vp.update()

    def set_page_rotations(self, rotations: dict[int, int]) -> None:
        self._rotations = {int(k): int(v) % 360 for k, v in rotations.items()}
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        self._update_grid_layout()
        self._render_visible()

    def set_page_crops(
        self, crops: dict[int, tuple[float, float, float, float]]
    ) -> None:
        self._crops = {
            int(k): (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
            for k, v in crops.items()
        }
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        self._update_grid_layout()
        self._render_visible()

    def set_page_order(self, order: list[int] | None) -> None:
        self._page_order = [int(x) for x in order] if order is not None else None
        self._model.clear_cache()
        self._stop_all_workers()
        self._inflight.clear()
        count = len(self._page_order) if self._page_order is not None else getattr(self, "_doc_page_count", 0)
        self._delegate.set_total_pages(count)
        self._model.set_page_order(self._page_order, count)
        self._update_grid_layout()
        self._render_visible()

    def clear(self) -> None:
        self._doc_path = ""
        self._password = ""
        self._rotations = {}
        self._crops = {}
        self._page_order = None
        self._page_edits = {}
        self._doc_page_count = 0
        self._stop_all_workers()
        self._epoch += 1
        self._inflight.clear()
        self._anchor = 0
        self._model.clear()
        self._delegate.set_total_pages(0)
        self._delegate.set_current_page(-1, None)

    def _on_activated(self, index: QModelIndex) -> None:
        if index.isValid():
            self._anchor = index.row()
            self._delegate.set_current_page(index.row(), self._delegate._viewport_rect)
            self.page_requested.emit(index.row())
            vp = self._view.viewport()
            if vp is not None:
                vp.update()

    def _on_selection_current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if current.isValid():
            row = current.row()
            self._anchor = row
            self._delegate.set_current_page(row, self._delegate._viewport_rect)
            self.page_requested.emit(row)
            vp = self._view.viewport()
            if vp is not None:
                vp.update()

    def _row_height(self) -> int:
        gs = self._view.gridSize()
        if gs.isValid() and gs.height() > 0:
            return gs.height()
        return (
            self._delegate.sizeHint(None, self._model.index(0)).height() or 1
        )

    def _visible_range(self) -> list[int]:
        total = self._model.rowCount()
        if total <= 0:
            return []
        row_h = self._row_height()
        vp = self._view.viewport()
        vp_h = vp.height() if vp is not None else 0
        cols = self.column_count()

        if vp_h <= 0 or not self._view.isVisible():
            first_page = max(0, self._anchor - VISIBLE_BUFFER * cols)
            last_page = min(total - 1, self._anchor + HIDDEN_WINDOW * cols)
            return list(range(first_page, last_page + 1))

        sb = self._view.verticalScrollBar()
        top = sb.value() if sb is not None else 0

        first_row = max(0, top // row_h - VISIBLE_BUFFER)
        last_row = (top + vp_h) // row_h + VISIBLE_BUFFER

        first_page = max(0, first_row * cols)
        last_page = min(total - 1, (last_row + 1) * cols - 1)
        return list(range(first_page, last_page + 1))

    def _render_visible(self) -> None:
        if not self._doc_path or self._model.rowCount() <= 0:
            return
        self._workers = [w for w in self._workers if w.isRunning()]
        want = self._visible_range()
        missing = [
            p
            for p in want
            if p not in self._inflight and not self._model.has_pixmap(p)
        ]
        if not missing:
            return
        self._inflight.update(missing)
        self._start_worker(missing)

    def _start_worker(self, page_indices: list[int]) -> None:
        try:
            dpr = (
                self._view.devicePixelRatioF()
                or self.devicePixelRatioF()
                or 1.0
            )
        except Exception:
            dpr = 1.0
        tw = int(DEFAULT_THUMB_WIDTH * self._thumb_scale)
        th = int(DEFAULT_THUMB_HEIGHT * self._thumb_scale)
        worker = ThumbnailWorker(
            self._doc_path,
            page_indices,
            self._password,
            self._epoch,
            dpr,
            rotations=self._rotations,
            crops=self._crops,
            page_order=self._page_order,
            page_edits=self._page_edits,
            thumb_w=tw,
            thumb_h=th,
            parent=self,  # type: ignore[arg-type]
        )
        worker.thumbnail_ready.connect(
            self._on_image_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.render_failed.connect(
            self._on_render_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._workers.append(worker)
        worker.start()

    @Slot(int, QImage, int)
    def _on_image_ready(self, page_idx: int, img: QImage, epoch: int) -> None:
        if epoch != self._epoch:
            return
        self._inflight.discard(page_idx)
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(img.devicePixelRatio())
        self._model.cache_pixmap(page_idx, pix)

    @Slot(int, str)
    def _on_render_failed(self, epoch: int, reason: str) -> None:
        worker = self.sender()
        if isinstance(worker, ThumbnailWorker):
            self._release_inflight_for(worker)
        if epoch != self._epoch:
            return
        _log.warning(
            "Thumbnail rendering failed for %r: %s",
            self._doc_path,
            reason,
        )

    @Slot()
    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, ThumbnailWorker):
            return
        self._release_inflight_for(worker)
        with contextlib.suppress(ValueError):
            self._workers.remove(worker)
        worker.deleteLater()

    def _release_inflight_for(self, worker: ThumbnailWorker) -> None:
        if worker._epoch != self._epoch:
            return
        for page in worker._pages:
            if not self._model.has_pixmap(page):
                self._inflight.discard(page)

    def _stop_all_workers(self) -> None:
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
            with contextlib.suppress(RuntimeError, TypeError):
                worker.thumbnail_ready.disconnect(self._on_image_ready)
            with contextlib.suppress(RuntimeError, TypeError):
                worker.render_failed.disconnect(self._on_render_failed)
            worker.wait(2000)