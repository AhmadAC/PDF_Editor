# app/viewer/thumbnail_panel.py
"""Thumbnail panel facade composing context menu, responsive grid layout, and background render orchestration."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QListView, QVBoxLayout, QWidget

from app.i18n import _CONFIG_PATH
from app.viewer.thumbnail_layout import ThumbnailLayoutMixin
from app.viewer.thumbnail_menu import (
    ThumbnailMenuMixin,
    _ScrollableMenuStyle,
    _SpinEnterFilter,
)
from app.viewer.thumbnail_model import ThumbnailDelegate, ThumbnailModel
from app.viewer.thumbnail_render import ThumbnailRenderMixin
from app.viewer.thumbnail_view import _ThumbnailListView
from app.viewer.thumbnail_worker import (
    DEFAULT_THUMB_HEIGHT,
    DEFAULT_THUMB_WIDTH,
    ThumbnailWorker,
    _install_debug_log,
)


class ThumbnailPanel(ThumbnailMenuMixin, ThumbnailLayoutMixin, ThumbnailRenderMixin, QWidget):
    """Sidebar container hosting the QListView of thumbnails with responsive multi-column grid support."""

    page_requested = Signal(int)
    action_requested = Signal(str, object)  # (action_name, list[int] | dict | tuple)
    viewport_scroll_requested = Signal(int, float, float)  # (page_idx, norm_x, norm_y)

    _thumb_scale_pref: float | None = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        _install_debug_log()
        self.setObjectName("thumbnail_panel")
        self._doc_path = ""
        self._password = ""
        self._rotations: dict[int, int] = {}
        self._crops: dict[int, tuple[float, float, float, float]] = {}
        self._page_order: list[int] | None = None
        self._page_edits: dict[int, list[dict]] = {}
        self._doc_page_count: int = 0
        self._workers: list[ThumbnailWorker] = []
        self._inflight: set[int] = set()
        self._anchor = 0
        self._epoch = 0
        self._menu_style: _ScrollableMenuStyle | None = None

        if ThumbnailPanel._thumb_scale_pref is None:
            try:
                if os.path.isfile(_CONFIG_PATH):
                    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
                        cfg = json.load(_f)
                        val = cfg.get("thumbnail_scale", 1.0)
                        ThumbnailPanel._thumb_scale_pref = max(0.5, min(2.5, float(val)))
                else:
                    ThumbnailPanel._thumb_scale_pref = 1.0
            except Exception:
                ThumbnailPanel._thumb_scale_pref = 1.0

        self._thumb_scale = ThumbnailPanel._thumb_scale_pref or 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._model = ThumbnailModel(self)
        self._delegate = ThumbnailDelegate(self)
        tw = int(DEFAULT_THUMB_WIDTH * self._thumb_scale)
        th = int(DEFAULT_THUMB_HEIGHT * self._thumb_scale)
        self._delegate.set_thumb_size(tw, th)

        self._view = _ThumbnailListView(self)
        self._view.setModel(self._model)
        self._view.setItemDelegate(self._delegate)
        self._view.setViewMode(QListView.ViewMode.IconMode)
        self._view.setResizeMode(QListView.ResizeMode.Adjust)
        self._view.setWrapping(True)
        self._view.setFlow(QListView.Flow.LeftToRight)
        self._view.setMovement(QListView.Movement.Static)
        self._view.setUniformItemSizes(True)
        self._view.setMouseTracking(True)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._view.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._view.setDragEnabled(True)
        self._view.setAcceptDrops(True)
        self._view.viewport().setAcceptDrops(True)
        self._view.setDropIndicatorShown(True)
        self._view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._view.setDefaultDropAction(Qt.DropAction.MoveAction)

        self._view.clicked.connect(self._on_activated)
        self._view.activated.connect(self._on_activated)

        sm = self._view.selectionModel()
        if sm is not None:
            sm.currentChanged.connect(self._on_selection_current_changed)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(120)
        self._scroll_timer.timeout.connect(self._render_visible)
        sb = self._view.verticalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(lambda _=0: self._scroll_timer.start())
        layout.addWidget(self._view)

    def showEvent(self, event):
        super().showEvent(event)
        if self._view is None:
            return
        self._update_grid_layout()
        self._render_visible()
        vp = self._view.viewport()
        if vp is not None:
            self._model.refresh_decorations()
            vp.update()

            def _refresh_again():
                if self._view is None:
                    return
                self._update_grid_layout()
                self._render_visible()
                self._model.refresh_decorations()
                v = self._view.viewport()
                if v is not None:
                    v.update()

            QTimer.singleShot(0, _refresh_again)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._view is None:
            return
        self._update_grid_layout()
        self._render_visible()
        vp = self._view.viewport()
        if vp is not None:
            self._model.refresh_decorations()
            vp.update()

    def closeEvent(self, event):
        self._stop_all_workers()
        super().closeEvent(event)


__all__ = [
    "ThumbnailLayoutMixin",
    "ThumbnailMenuMixin",
    "ThumbnailPanel",
    "ThumbnailRenderMixin",
    "_ScrollableMenuStyle",
    "_SpinEnterFilter",
]