# app/editor/canvas_view.py
"""Document lifecycle, multi-threaded render scheduling, zoom, and coordinate mapping for PdfEditCanvas."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

import fitz
from PySide6.QtCore import QPoint, QPointF, QThreadPool, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget

try:
    from app.editor.canvas_overlay1 import get_page_preview_ops
    from app.editor.canvas_render1 import (
        _BUFFER_PGS,
        _MAX_THREADS,
        _PAGE_GAP,
        _EditPageJob,
        clear_overlay_pixmap_cache,
        render_single_page_pixmap,
    )
except ImportError:
    from .canvas_overlay1 import get_page_preview_ops  # type: ignore
    from .canvas_render1 import (  # type: ignore
        _BUFFER_PGS,
        _MAX_THREADS,
        _PAGE_GAP,
        _EditPageJob,
        clear_overlay_pixmap_cache,
        render_single_page_pixmap,
    )

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object


class CanvasViewMixin(_Base):
    """Mixin providing viewport layout, page rendering scheduling, zoom logic, and coordinates."""

    if TYPE_CHECKING:
        _doc: fitz.Document | None
        _path: str
        _password: str
        _page_idx: int
        _zoom: float
        _zoom_factor: float
        _base_avail: int
        _page_pixmaps: list
        _page_prev_pixmaps: list
        _page_offsets: list
        _gen: int
        _pending: set[int]
        _pending_anchor: tuple[int, float, float, float, float] | None
        _pending_pixmaps: list | None
        _render_signals: Any
        _overlays: list
        _open_note: Any
        _screen_signal_window: Any
        _selected_overlay_idx: int
        _drag_before_snapshot: dict | None
        _moving_overlay: bool
        _overlay_mgr: Any
        _inline_mgr: Any

        zoom_changed: Any

        def update(self) -> None: ...
        def isVisible(self) -> bool: ...
        def width(self) -> int: ...
        def height(self) -> int: ...
        def window(self) -> Any: ...
        def parent(self) -> Any: ...
        def devicePixelRatioF(self) -> float: ...
        def setFixedSize(self, w: int, h: int) -> None: ...
        def setMinimumSize(self, w: int, h: int) -> None: ...
        def setMaximumSize(self, w: int, h: int) -> None: ...
        def cancel_signature_placement(self) -> None: ...
        def cancel_stamp_placement(self) -> None: ...
        def clear_active_selection(self) -> None: ...
        def scale_selected_overlay(self, factor: float) -> bool: ...
        def _cancel_inline(self) -> None: ...

    def refresh_pages(self, page_indices: set[int] | list[int] | None = None) -> None:
        """Synchronously re-render the specified pages' background pixmaps with current removals and _z_back layers."""
        if not self._doc or not self._page_pixmaps:
            return
        if page_indices is None:
            targets = list(range(len(self._page_pixmaps)))
        else:
            targets = sorted({int(p) for p in page_indices if 0 <= int(p) < len(self._page_pixmaps)})
        if not targets:
            return
        dpr = self.devicePixelRatioF() or 1.0
        for idx in targets:
            ops = get_page_preview_ops(self._overlays, idx)
            qp = render_single_page_pixmap(self._doc, idx, self._zoom, dpr, page_ops=ops)
            if qp is not None:
                self._page_pixmaps[idx] = qp
                if idx < len(self._page_prev_pixmaps):
                    self._page_prev_pixmaps[idx] = None
                self._pending.discard(idx)
        self.update()

    def _get_scroll_area(self):
        from PySide6.QtWidgets import QScrollArea as _SA
        vp = self.parent()
        sa = vp.parent() if vp else None
        return sa if isinstance(sa, _SA) else None

    def load(
        self,
        path: str,
        password: str = "",
        initial_zoom_factor: float | None = None,
        initial_page: int = 0,
        initial_anchor: tuple[int, float, float, float, float] | None = None,
        initial_pixmaps: list | None = None,
    ):
        if self._doc:
            self._doc.close()
        self._doc = None
        doc = fitz.open(path)
        if doc.needs_pass and password:
            doc.authenticate(password)
        self._doc = doc
        self._path = path
        self._password = password
        self._page_idx = max(0, initial_page)
        self._zoom_factor = initial_zoom_factor if initial_zoom_factor is not None else 1.0
        self._gen += 1
        self._pending.clear()
        self._page_offsets.clear()
        self._page_pixmaps.clear()
        self._page_prev_pixmaps.clear()
        self._pending_anchor = initial_anchor
        self._pending_pixmaps = list(initial_pixmaps) if initial_pixmaps is not None else None
        self.cancel_signature_placement()
        self.cancel_stamp_placement()
        self.clear_active_selection()
        self._selected_overlay_idx = -1
        self._drag_before_snapshot = None
        QTimer.singleShot(0, self._on_initial_layout)

    def _on_initial_layout(self):
        if not self._doc:
            return
        if not self._page_offsets or self._pending_anchor is not None or self._pending_pixmaps is not None:
            self._layout_and_schedule()

    def apply_view_state(
        self,
        zoom_factor: float,
        page_idx: int,
        anchor: tuple[int, float, float, float, float] | None,
    ) -> None:
        """Synchronize zoom, active page, and scroll position without reloading the document."""
        if not self._doc or self._doc.page_count <= 0:
            return
        target_p = max(0, min(self._doc.page_count - 1, int(page_idx)))
        self._page_idx = target_p
        target_zf = float(zoom_factor) if zoom_factor and zoom_factor > 0 else 1.0
        zoom_changed = abs(self._zoom_factor - target_zf) > 1e-4
        self._zoom_factor = target_zf
        self._pending_anchor = anchor

        if not self._page_offsets:
            return

        if zoom_changed:
            self._invalidate_and_relayout(anchor=anchor)
            return

        if anchor is None:
            return

        sa = self._get_scroll_area()
        if not sa:
            return
        t_page, pdf_x, pdf_y, vp_x, vp_y = anchor
        if 0 <= t_page < len(self._page_offsets):
            yo, _pw, _ph = self._page_offsets[t_page]
            tx = int(round(pdf_x * self._zoom - vp_x))
            ty = int(round(yo + pdf_y * self._zoom - vp_y))
            sb_h = sa.horizontalScrollBar()
            sb_v = sa.verticalScrollBar()
            if sb_h:
                sb_h.setValue(max(0, min(tx, sb_h.maximum())))
            if sb_v:
                sb_v.setValue(max(0, min(ty, sb_v.maximum())))
            vp = sa.viewport()
            if self.isVisible() and vp and vp.width() > 200 and vp.height() > 150:
                self._pending_anchor = None

    def zoom_in(self, anchor_pos: QPoint | None = None):
        self._pending_anchor = None
        self._zoom_factor = min(8.0, round(self._zoom_factor * 1.25, 4))
        self._invalidate_and_relayout(anchor_pos=anchor_pos)

    def zoom_out(self, anchor_pos: QPoint | None = None):
        self._pending_anchor = None
        self._zoom_factor = max(0.2, round(self._zoom_factor / 1.25, 4))
        self._invalidate_and_relayout(anchor_pos=anchor_pos)

    def zoom_reset(self):
        self._pending_anchor = None
        self._zoom_factor = 1.0
        self._invalidate_and_relayout()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pos = e.position().toPoint()
            if self._selected_overlay_idx >= 0:
                if self._overlay_mgr.is_pos_inside_overlay(self._selected_overlay_idx, pos) or self._moving_overlay:
                    if e.angleDelta().y() > 0:
                        self.scale_selected_overlay(1.08)
                    else:
                        self.scale_selected_overlay(0.92)
                    e.accept()
                    return

            if e.angleDelta().y() > 0:
                self.zoom_in(anchor_pos=pos)
            else:
                self.zoom_out(anchor_pos=pos)
            e.accept()
        else:
            super().wheelEvent(e)

    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 0

    def set_page(self, idx: int):
        if self._doc and 0 <= idx < self._doc.page_count:
            self._page_idx = idx

    def scroll_to_page(self, idx: int) -> int:
        if 0 <= idx < len(self._page_offsets):
            return self._page_offsets[idx][0]
        return 0

    def page_at_y(self, y: int) -> int:
        for i, (yo, w, h) in enumerate(self._page_offsets):
            if y < yo + h + _PAGE_GAP:
                return i
        return max(0, len(self._page_offsets) - 1)

    def get_span_at(self, page_idx: int, pdf_pt: fitz.Point, max_dist: float = 8.0):
        if not self._doc or not (0 <= page_idx < self._doc.page_count):
            return None
        page = self._doc[page_idx]
        click = fitz.Point(pdf_pt.x, pdf_pt.y)
        found, best_dist = None, float(max_dist)
        text_dict = cast(dict, page.get_text("dict"))
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if not span.get("text", "").strip():
                        continue
                    bbox = fitz.Rect(span["bbox"])
                    if bbox.contains(click) or (bbox + fitz.Rect(-3, -3, 3, 3)).contains(click):
                        return span
                    cx = max(bbox.x0, min(click.x, bbox.x1))
                    cy = max(bbox.y0, min(click.y, bbox.y1))
                    dist = ((click.x - cx)**2 + (click.y - cy)**2) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        found = span
        return found

    def release_doc(self):
        if self._doc:
            self._doc.close()
            self._doc = None

    def close_doc(self):
        self._cancel_inline()
        self.cancel_signature_placement()
        self.cancel_stamp_placement()
        self.clear_active_selection()
        self._selected_overlay_idx = -1
        self._drag_before_snapshot = None
        self._gen += 1
        self._pending.clear()
        self._pending_anchor = None
        self._pending_pixmaps = None
        self.release_doc()
        self._page_pixmaps.clear()
        self._page_prev_pixmaps.clear()
        self._page_offsets.clear()
        self._overlays = []
        self._open_note = None
        clear_overlay_pixmap_cache()
        self.setMinimumSize(300, 400)
        self.setMaximumSize(16777215, 16777215)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        win = self.window().windowHandle() if self.window() else None
        prev_win = self._screen_signal_window
        if win is prev_win:
            return
        if prev_win is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                prev_win.screenChanged.disconnect(self._on_screen_changed)
        if win is not None:
            win.screenChanged.connect(self._on_screen_changed)
        self._screen_signal_window = win

    def _on_screen_changed(self, _screen):
        self._gen += 1
        self._pending.clear()
        for i, p in enumerate(self._page_pixmaps):
            if p is not None:
                if i < len(self._page_prev_pixmaps):
                    self._page_prev_pixmaps[i] = p
                self._page_pixmaps[i] = None
        self._schedule_visible()
        self.update()

    def on_scroll(self):
        self._schedule_visible()

    def _calc_anchor(self, anchor_pos: QPoint | None = None) -> tuple[int, float, float, float, float] | None:
        sa = self._get_scroll_area()
        if not sa or not self._page_offsets or self.height() <= 0:
            return None

        vp = sa.viewport()
        vp_w = float(vp.width())
        vp_h = float(vp.height())
        if vp_w <= 0 or vp_h <= 0:
            return None

        cursor_global = QCursor.pos()
        cursor_vp = vp.mapFromGlobal(cursor_global)

        if 0 <= cursor_vp.x() <= vp_w and 0 <= cursor_vp.y() <= vp_h:
            vp_x = float(cursor_vp.x())
            vp_y = float(cursor_vp.y())
        elif isinstance(anchor_pos, (QPoint, QPointF)) and 0 <= anchor_pos.x() <= vp_w and 0 <= anchor_pos.y() <= vp_h:
            vp_x = float(anchor_pos.x())
            vp_y = float(anchor_pos.y())
        elif isinstance(anchor_pos, (QPoint, QPointF)):
            vp_x = float(anchor_pos.x() - sa.horizontalScrollBar().value())
            vp_y = float(anchor_pos.y() - sa.verticalScrollBar().value())
            vp_x = max(0.0, min(vp_w, vp_x))
            vp_y = max(0.0, min(vp_h, vp_y))
        else:
            vp_x = vp_w / 2.0
            vp_y = vp_h / 2.0

        canvas_x = sa.horizontalScrollBar().value() + vp_x
        canvas_y = sa.verticalScrollBar().value() + vp_y

        target_page = 0
        for i, (yo, w, h) in enumerate(self._page_offsets):
            if canvas_y < yo + h + _PAGE_GAP:
                target_page = i
                break
        else:
            target_page = max(0, len(self._page_offsets) - 1)

        yo, pw, ph = self._page_offsets[target_page]
        current_zoom = self._zoom if self._zoom > 0 else 1.0
        pdf_x = canvas_x / current_zoom
        pdf_y = (canvas_y - yo) / current_zoom

        return (target_page, pdf_x, pdf_y, vp_x, vp_y)

    def relayout_to_viewport(self):
        if not self._doc:
            return
        sa = self._get_scroll_area()
        if sa and sa.viewport() and sa.viewport().width() > 100:
            avail = sa.viewport().width() - 8
        else:
            avail = self.width() - 8
        avail = max(avail, 300)
        if self._page_offsets and abs(avail - self._base_avail) <= 2 and self._pending_anchor is None:
            return
        anchor = self._pending_anchor if self._pending_anchor is not None else self._calc_anchor()
        old_pixmaps = [
            p if p is not None else prev
            for p, prev in zip(
                self._page_pixmaps,
                getattr(self, "_page_prev_pixmaps", []),
            )
        ] or None
        self._layout_and_schedule(old_pixmaps=old_pixmaps, anchor=anchor)

    def _invalidate_and_relayout(
        self,
        anchor_pos: QPoint | None = None,
        anchor: tuple[int, float, float, float, float] | None = None,
    ):
        self._gen += 1
        self._pending.clear()
        if anchor is None:
            anchor = self._pending_anchor if self._pending_anchor is not None else self._calc_anchor(anchor_pos)
        old_pixmaps = [
            p if p is not None else prev
            for p, prev in zip(
                self._page_pixmaps,
                getattr(self, "_page_prev_pixmaps", []),
            )
        ]
        self._layout_and_schedule(
            old_pixmaps=old_pixmaps,
            anchor=anchor,
        )

    def _layout_and_schedule(
        self,
        old_pixmaps: list | None = None,
        anchor: tuple[int, float, float, float, float] | None = None,
    ):
        if not self._doc or self._doc.page_count <= 0:
            return

        if old_pixmaps is None and self._pending_pixmaps is not None:
            old_pixmaps = self._pending_pixmaps
            self._pending_pixmaps = None
        if old_pixmaps is None and (self._page_pixmaps or self._page_prev_pixmaps):
            old_pixmaps = [
                p if p is not None else prev
                for p, prev in zip(self._page_pixmaps, self._page_prev_pixmaps)
            ]
        if anchor is None and self._pending_anchor is not None:
            anchor = self._pending_anchor

        sa = self._get_scroll_area()
        if sa and sa.viewport() and sa.viewport().width() > 100:
            avail = sa.viewport().width() - 8
        else:
            avail = self.width() - 8
        self._base_avail = max(avail, 300)

        ref_w = max(1.0, self._doc[0].rect.width)
        self._zoom = (self._base_avail / ref_w) * self._zoom_factor

        self._page_pixmaps.clear()
        self._page_prev_pixmaps = []
        self._page_offsets.clear()
        y_off = 0
        max_w = 0
        for i in range(self._doc.page_count):
            r = self._doc[i].rect
            pw = round(r.width * self._zoom)
            ph = round(r.height * self._zoom)
            self._page_pixmaps.append(None)
            prev_p = old_pixmaps[i] if (old_pixmaps and i < len(old_pixmaps)) else None
            self._page_prev_pixmaps.append(prev_p)
            self._page_offsets.append((y_off, pw, ph))
            max_w = max(max_w, pw)
            y_off += ph + _PAGE_GAP

        total_h = y_off - _PAGE_GAP if y_off > 0 else 400
        new_w = max(max_w, 300)
        new_h = max(total_h, 400)
        self.setFixedSize(new_w, new_h)

        if sa:
            vp = sa.viewport()
            max_scroll_x = max(0, new_w - vp.width())
            max_scroll_y = max(0, new_h - vp.height())
            sb_h = sa.horizontalScrollBar()
            sb_v = sa.verticalScrollBar()
            if sb_h:
                sb_h.setRange(0, max_scroll_x)
            if sb_v:
                sb_v.setRange(0, max_scroll_y)

            if anchor is not None:
                target_page, pdf_x, pdf_y, vp_x, vp_y = anchor
                if 0 <= target_page < len(self._page_offsets):
                    yo, pw, ph = self._page_offsets[target_page]
                    new_canvas_x = pdf_x * self._zoom
                    new_canvas_y = yo + pdf_y * self._zoom

                    target_scroll_x = int(round(new_canvas_x - vp_x))
                    target_scroll_y = int(round(new_canvas_y - vp_y))

                    if sb_h:
                        sb_h.setValue(max(0, min(target_scroll_x, max_scroll_x)))
                    if sb_v:
                        sb_v.setValue(max(0, min(target_scroll_y, max_scroll_y)))
                    self._page_idx = target_page
                    if (
                        self.isVisible()
                        and vp.width() > 200
                        and vp.height() > 150
                        and anchor == self._pending_anchor
                    ):
                        self._pending_anchor = None

        self.zoom_changed.emit(round(self._zoom_factor * 100))
        self.update()
        self._schedule_visible()
        if self._inline_mgr.edit.isVisible():
            self._inline_mgr.reposition()

    def _visible_range(self) -> tuple[int, int]:
        from PySide6.QtWidgets import QScrollArea as _SA
        vp = self.parent()
        sa = vp.parent() if vp else None
        n = len(self._page_offsets)
        if not isinstance(sa, _SA) or not n:
            return (0, min(n - 1, _BUFFER_PGS * 2))
        y0 = sa.verticalScrollBar().value()
        y1 = y0 + sa.viewport().height()
        first = last = 0
        found = False
        for i, (yo, pw, ph) in enumerate(self._page_offsets):
            if not found and yo + ph >= y0:
                first = i
                found = True
            if yo <= y1:
                last = i
        return (max(0, first - _BUFFER_PGS), min(n - 1, last + _BUFFER_PGS))

    def _schedule_visible(self):
        if not self._page_offsets or not self._path:
            return
        first, last = self._visible_range()
        dpr = self.devicePixelRatioF() or 1.0
        gen = self._gen
        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(_MAX_THREADS)
        for i in range(first, last + 1):
            if self._page_pixmaps[i] is None and i not in self._pending:
                self._pending.add(i)
                page_ops = get_page_preview_ops(self._overlays, i)
                pool.start(
                    _EditPageJob(
                        self._path,
                        i,
                        self._zoom,
                        dpr,
                        gen,
                        self._render_signals,
                        password=getattr(self, "_password", ""),
                        is_valid=lambda g=gen: g == self._gen,
                        page_ops=page_ops,
                    )
                )

    def _on_page_ready(self, gen: int, idx: int, pixmap):
        if gen != self._gen:
            return
        self._pending.discard(idx)
        if 0 <= idx < len(self._page_pixmaps):
            self._page_pixmaps[idx] = pixmap
            if idx < len(self._page_prev_pixmaps):
                self._page_prev_pixmaps[idx] = None
            self.update()

    def _page_and_local(self, sx, sy):
        for i, (yo, w, h) in enumerate(self._page_offsets):
            if sy < yo + h + _PAGE_GAP // 2 or i == len(self._page_offsets) - 1:
                return i, sx, sy - yo
        return 0, sx, sy

    def _to_pdf(self, page_idx, sx, sy):
        return fitz.Point(sx / self._zoom, sy / self._zoom)

    def _rect_to_pdf(self, page_idx, local_rect):
        z = self._zoom
        r = fitz.Rect(
            local_rect.left() / z,
            local_rect.top() / z,
            local_rect.right() / z,
            local_rect.bottom() / z,
        )
        if self._doc and 0 <= page_idx < self._doc.page_count:
            page_rect = self._doc[page_idx].rect
            r = r & page_rect
            if r.is_empty or r.width < 1 or r.height < 1:
                return None
        return r