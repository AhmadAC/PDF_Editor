# app/editor/tab_nav.py
"""PDF_Editor – Navigation, thumbnails, TOC, and splitter layout operations for TabEditar."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

if TYPE_CHECKING:
    import fitz
    from app.editor.canvas import PdfEditCanvas
    from app.viewer.thumbnails import ThumbnailPanel
    _Base = QWidget
else:
    _Base = object


class TabNavMixin(_Base):
    """Mixin encapsulating page scrolling, thumbnails, and TOC synchronization."""

    if TYPE_CHECKING:
        _canvas: PdfEditCanvas
        _canvas_scroll: QScrollArea
        _ctrl_scroll: QScrollArea
        _thumbnails: ThumbnailPanel
        _toc_tree: QTreeWidget
        _sidebar_tabs: QTabWidget
        _sidebar_panel: QWidget
        _edit_splitter: QSplitter
        _pages_sidebar_collapsed: bool
        _saved_sidebar_width: int
        _toc_tab_idx: int
        _pages_tab_idx: int
        _page_idx: int
        _btn_prev: Any
        _btn_next: Any
        _lbl_page: Any

        def _update_nav(self) -> None: ...

    def toggle_controls(self) -> bool:
        """Toggle the right-hand controls sidebar within the editor and adapt canvas to full width."""
        is_vis = self._ctrl_scroll.isVisible()
        self._ctrl_scroll.setVisible(not is_vis)
        self._relayout_canvas()
        return not is_vis

    def _relayout_canvas(self) -> None:
        if hasattr(self, "_canvas") and self._canvas._doc:
            QTimer.singleShot(10, self._canvas.relayout_to_viewport)
            QTimer.singleShot(60, self._canvas.relayout_to_viewport)

    def toggle_pages_sidebar(self) -> bool:
        """Toggle the left-hand pages/thumbnail sidebar within the editor."""
        if not hasattr(self, "_sidebar_panel"):
            return False

        if not self._pages_sidebar_collapsed:
            self._saved_sidebar_width = max(70, self._sidebar_panel.width())
            self._pages_sidebar_collapsed = True
            self._sidebar_panel.setVisible(False)
            total = self._edit_splitter.width() or 1020
            self._edit_splitter.setSizes([0, total])
            pref_open = False
        else:
            self._pages_sidebar_collapsed = False
            self._sidebar_panel.setVisible(True)
            self._sidebar_tabs.setVisible(True)
            if hasattr(self, "_thumbnails") and hasattr(self, "_sidebar_tabs"):
                self._sidebar_tabs.setCurrentWidget(self._thumbnails)
            w = min(800, max(70, getattr(self, "_saved_sidebar_width", 220)))
            total = self._edit_splitter.width() or 1020
            self._edit_splitter.setSizes([w, max(300, total - w)])
            pref_open = True

        try:
            from app.viewer.panel import PdfViewerPanel
            PdfViewerPanel._pages_sidebar_visible_pref = pref_open
            PdfViewerPanel._saved_sidebar_width_pref = self._saved_sidebar_width
        except Exception:
            pass

        try:
            from app.i18n import _update_config
            saved_w = self._saved_sidebar_width
            _update_config(lambda cfg: (
                cfg.__setitem__("pages_sidebar_open", pref_open),
                cfg.__setitem__("sidebar_panel_width", saved_w),
            ))
        except Exception:
            pass

        self._relayout_canvas()
        return not self._pages_sidebar_collapsed

    def _set_sidebar_width(self, target_w: int) -> None:
        target_w = max(70, min(600, target_w))
        self._saved_sidebar_width = target_w
        try:
            from app.viewer.panel import PdfViewerPanel
            PdfViewerPanel._saved_sidebar_width_pref = target_w
        except Exception:
            pass
        if not getattr(self, "_pages_sidebar_collapsed", False) and hasattr(self, "_edit_splitter"):
            sizes = self._edit_splitter.sizes()
            if len(sizes) >= 2:
                total = sum(sizes)
                canvas_w = max(200, total - target_w)
                self._edit_splitter.setSizes([target_w, canvas_w])
        try:
            from app.i18n import _update_config
            _update_config(lambda cfg: cfg.__setitem__("sidebar_panel_width", target_w))
        except Exception:
            pass

    def _on_edit_splitter_moved(self, pos: int, index: int) -> None:
        if index == 1 and pos >= 70 and not self._pages_sidebar_collapsed:
            self._saved_sidebar_width = pos
            try:
                from app.viewer.panel import PdfViewerPanel
                PdfViewerPanel._saved_sidebar_width_pref = pos
            except Exception:
                pass
            self._relayout_canvas()

    def _update_nav(self):
        n = self._canvas.page_count()
        self._btn_prev.setEnabled(n > 0 and self._page_idx > 0)
        self._btn_next.setEnabled(n > 0 and self._page_idx < n - 1)
        self._lbl_page.setText(f"{self._page_idx+1} / {n}" if n else "—")

    def _on_canvas_scroll(self, val: int = 0):
        self._canvas.on_scroll()
        self._update_page_nav_and_thumbnails()

    def _update_page_nav_and_thumbnails(self):
        offsets = getattr(self._canvas, "_page_offsets", [])
        if not offsets:
            return
        sb = self._canvas_scroll.verticalScrollBar()
        sb_val = sb.value() if sb else 0
        idx = self._canvas.page_at_y(sb_val)

        h_sb = self._canvas_scroll.horizontalScrollBar()
        v_sb = self._canvas_scroll.verticalScrollBar()
        h_val = h_sb.value() if h_sb else 0
        v_val = v_sb.value() if v_sb else 0
        vp = self._canvas_scroll.viewport()
        vp_w = vp.width() if vp else 0
        vp_h = vp.height() if vp else 0

        best_idx = idx
        max_vis_h = -1
        viewport_rect = None

        if vp_w > 0 and vp_h > 0:
            for p_i, (py0, pw, ph) in enumerate(offsets):
                if py0 + ph < v_val:
                    continue
                if py0 > v_val + vp_h:
                    break
                px0 = max(0, (self._canvas.width() - pw) // 2)
                ix0 = max(px0, h_val)
                iy0 = max(py0, v_val)
                ix1 = min(px0 + pw, h_val + vp_w)
                iy1 = min(py0 + ph, v_val + vp_h)
                if ix1 > ix0 and iy1 > iy0:
                    vis_h = iy1 - iy0
                    if vis_h > max_vis_h:
                        max_vis_h = vis_h
                        best_idx = p_i
                        norm_x = max(0.0, min(1.0, (ix0 - px0) / pw))
                        norm_y = max(0.0, min(1.0, (iy0 - py0) / ph))
                        norm_w = max(0.0, min(1.0 - norm_x, (ix1 - ix0) / pw))
                        norm_h = max(0.0, min(1.0 - norm_y, (iy1 - iy0) / ph))
                        viewport_rect = (norm_x, norm_y, norm_w, norm_h)

        if best_idx != self._page_idx:
            self._page_idx = best_idx
            self._update_nav()
        if hasattr(self, "_thumbnails"):
            self._thumbnails.set_current_page(best_idx, viewport_rect)

    def _scroll_to(self, idx: int):
        self._page_idx = idx
        y = self._canvas.scroll_to_page(idx)
        self._canvas_scroll.verticalScrollBar().setValue(y)
        self._update_nav()
        if hasattr(self, "_thumbnails"):
            self._thumbnails.set_current_page(idx)

    def _prev_page(self):
        if self._page_idx > 0:
            self._scroll_to(self._page_idx - 1)

    def _next_page(self):
        if self._page_idx < self._canvas.page_count() - 1:
            self._scroll_to(self._page_idx + 1)

    def _on_thumbnail_action(self, action: str, pages_arg: object) -> None:
        if action == "fit_sidebar_width":
            if isinstance(pages_arg, (int, float)):
                self._set_sidebar_width(int(pages_arg))
            return
        if action == "prev_page":
            self._prev_page()
            return
        if action == "next_page":
            self._next_page()
            return
        if action == "go_to_page":
            if isinstance(pages_arg, int):
                self._scroll_to(pages_arg)
            return

    def _on_thumbnail_viewport_scroll(self, page_idx: int, norm_x: float, norm_y: float) -> None:
        offsets = getattr(self._canvas, "_page_offsets", [])
        if not offsets or not (0 <= page_idx < len(offsets)):
            return
        py0, pw, ph = offsets[page_idx]
        px0 = max(0, (self._canvas.width() - pw) // 2)

        target_x = px0 + int(round(norm_x * pw))
        target_y = py0 + int(round(norm_y * ph))

        h_sb = self._canvas_scroll.horizontalScrollBar()
        v_sb = self._canvas_scroll.verticalScrollBar()
        if h_sb:
            h_sb.setValue(max(0, min(target_x, h_sb.maximum())))
        if v_sb:
            v_sb.setValue(max(0, min(target_y, v_sb.maximum())))

    def _on_toc_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        page_idx = item.data(0, Qt.ItemDataRole.UserRole)
        if page_idx is not None:
            self._scroll_to(int(page_idx))

    def _populate_toc(self, doc: fitz.Document | None) -> None:
        if not hasattr(self, "_toc_tree"):
            return
        self._toc_tree.clear()
        if not doc:
            if hasattr(self, "_sidebar_tabs") and hasattr(self._sidebar_tabs, "setTabVisible"):
                self._sidebar_tabs.setTabVisible(self._toc_tab_idx, False)
            return
        try:
            toc = doc.get_toc()
        except Exception:
            toc = []
        if not toc:
            if hasattr(self, "_sidebar_tabs") and hasattr(self._sidebar_tabs, "setTabVisible"):
                self._sidebar_tabs.setTabVisible(self._toc_tab_idx, False)
            pages_idx = self._sidebar_tabs.indexOf(self._thumbnails)
            if pages_idx >= 0:
                self._sidebar_tabs.setCurrentIndex(pages_idx)
            return
        try:
            stack = [(0, self._toc_tree.invisibleRootItem())]
            for level, title, page in toc:
                while stack and stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1] if stack else self._toc_tree.invisibleRootItem()
                item = QTreeWidgetItem(parent, [title])
                item.setData(0, Qt.ItemDataRole.UserRole, max(0, page - 1))
                item.setToolTip(0, title)
                stack.append((level, item))
            self._toc_tree.expandToDepth(1)
            if hasattr(self, "_sidebar_tabs"):
                self._sidebar_tabs.setTabVisible(self._toc_tab_idx, True)
            pages_idx = self._sidebar_tabs.indexOf(self._thumbnails)
            if pages_idx >= 0:
                self._sidebar_tabs.setCurrentIndex(pages_idx)
        except Exception:
            self._toc_tree.clear()
            if hasattr(self, "_sidebar_tabs"):
                self._sidebar_tabs.setTabVisible(self._toc_tab_idx, False)

    def eventFilter(self, obj, event):
        if hasattr(self, "_edit_splitter") and obj is self._edit_splitter.handle(1):
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if hasattr(self, "_thumbnails"):
                    self._thumbnails.fit_window_to_thumbnails()
                    return True
        if hasattr(self, "_canvas_scroll") and obj is self._canvas_scroll.viewport() and event.type() == QEvent.Type.Resize:
            if hasattr(self, "_canvas") and self._canvas._doc:
                QTimer.singleShot(0, self._canvas.relayout_to_viewport)
        return super().eventFilter(obj, event)