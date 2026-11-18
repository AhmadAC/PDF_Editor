

# app/editor/tab_nav.py
"""PDF_Editor – Navigation, thumbnails, TOC, splitter layout, and thumbnail page operations for TabEditar."""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from app.constants import DESKTOP
from app.i18n import t
from app.pdf_io import atomic_pdf_write
from app.utils import show_error

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    from app.editor.tab_history import TabHistoryManager
    from app.viewer.thumbnails import ThumbnailPanel
    _Base = QWidget
else:
    _Base = object


def _clone_overlay_for_page(ov: dict, target_page: int) -> dict:
    """Deep-clone an overlay dict safely (handling fitz.Rect / fitz.Point) targeting `target_page`."""
    cloned = dict(ov)
    cloned["page"] = target_page
    if "rect" in cloned and cloned["rect"] is not None:
        cloned["rect"] = fitz.Rect(cloned["rect"])
    if "bbox" in cloned and cloned["bbox"] is not None:
        cloned["bbox"] = list(cloned["bbox"])
    if "origin" in cloned and cloned["origin"] is not None:
        cloned["origin"] = list(cloned["origin"])
    if "_orig_rect" in cloned and cloned["_orig_rect"] is not None:
        cloned["_orig_rect"] = list(cloned["_orig_rect"])
    if "_orig_origin" in cloned and cloned["_orig_origin"] is not None:
        cloned["_orig_origin"] = list(cloned["_orig_origin"])
    if "_items_rect" in cloned and cloned["_items_rect"] is not None:
        cloned["_items_rect"] = list(cloned["_items_rect"])
    if "_annot_bbox" in cloned and cloned["_annot_bbox"] is not None:
        cloned["_annot_bbox"] = list(cloned["_annot_bbox"])
    if "rects" in cloned and cloned["rects"] is not None:
        cloned["rects"] = [fitz.Rect(r) for r in cloned["rects"]]
    if "points" in cloned and cloned["points"] is not None:
        cloned["points"] = [(float(px), float(py)) for px, py in cloned["points"]]
    if "point" in cloned and cloned["point"] is not None:
        cloned["point"] = fitz.Point(cloned["point"])
    if "items" in cloned and cloned["items"] is not None:
        cloned["items"] = list(cloned["items"])
    if "children" in cloned and cloned["children"] is not None:
        cloned["children"] = [_clone_overlay_for_page(ch, target_page) for ch in cloned["children"]]
    return cloned


class TabNavMixin(_Base):
    """Mixin encapsulating page scrolling, thumbnails, TOC synchronization, and page operations."""

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
        _doc_path: str | None
        _pdf_password: str
        _pending: list[dict]
        _drop_out: Any
        _drop_in: Any
        _lbl_info: Any
        _history: TabHistoryManager
        _btn_prev: Any
        _btn_next: Any
        _lbl_page: Any

        def _update_nav(self) -> None: ...
        def _undo(self) -> None: ...
        def _redo(self) -> None: ...
        def _status(self, msg: str) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...
        def _populate_toc(self, doc: fitz.Document | None) -> None: ...

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

    # ── Thumbnail Page Operations inside Edit Mode ────────────────────────

    def _commit_editor_temp_pdf(
        self,
        new_doc: fitz.Document,
        new_pending: list[dict],
        target_page: int,
        selected_pages: list[int],
    ) -> None:
        """Persist a page-restructured PDF to a temp file, keep _drop_out pointing to the real target, and sync viewer."""
        win: Any = self.window()
        viewer = getattr(win, "_viewer", None) if win else None
        orig_out = (self._drop_out.path() if hasattr(self, "_drop_out") else "") or self._doc_path or ""
        if viewer is not None:
            ps_map = getattr(win, "_pipeline_state", {})
            ps = ps_map.get(id(viewer)) if isinstance(ps_map, dict) else None
            if ps and ps.get("original_path"):
                orig_out = ps["original_path"]
            elif getattr(viewer, "_original_doc_path", ""):
                orig_out = viewer._original_doc_path

        fd, temp_path = tempfile.mkstemp(prefix="PDF_Editor_editpages_", suffix=".pdf")
        os.close(fd)
        temp_path = os.path.normpath(temp_path)

        cur_zf = float(getattr(self._canvas, "_zoom_factor", 1.0) or 1.0)
        cur_scroll_v = self._canvas_scroll.verticalScrollBar().value()
        cur_scroll_h = self._canvas_scroll.horizontalScrollBar().value()

        if viewer is not None and hasattr(viewer, "_history_temp_files"):
            viewer._history_temp_files.add(temp_path)

        atomic_pdf_write(
            new_doc,
            temp_path,
            save_opts={"garbage": 4, "deflate": True},
            close_writer=True,
        )

        self._doc_path = temp_path
        self._pending[:] = new_pending
        self._page_idx = max(0, target_page)

        if hasattr(self, "_drop_in"):
            self._drop_in.blockSignals(True)
            self._drop_in.set_path(temp_path, display_name=os.path.basename(orig_out))
            self._drop_in.blockSignals(False)
        if hasattr(self, "_drop_out") and orig_out:
            self._drop_out.set_path(orig_out)

        target_anchor = (self._page_idx, 0.0, 0.0, 0.0, 0.0)
        self._canvas.load(
            temp_path,
            password=self._pdf_password,
            initial_zoom_factor=cur_zf,
            initial_page=self._page_idx,
            initial_anchor=target_anchor,
        )
        self._canvas.set_overlays(self._pending)

        n = self._canvas.page_count()
        if hasattr(self, "_lbl_info"):
            self._lbl_info.setText(t("edit.status.pages", n=n))
        self._update_nav()

        if hasattr(self, "_thumbnails"):
            self._thumbnails.blockSignals(True)
            self._thumbnails.set_document(temp_path, n, password=self._pdf_password)
            if selected_pages:
                self._thumbnails.set_selected_pages(selected_pages)
            else:
                self._thumbnails.set_current_page(self._page_idx)
            self._thumbnails.blockSignals(False)

        self._populate_toc(self._canvas._doc)
        self._history.rebuild_pending_list()

        # Sync the underlying Viewer tab & pipeline dirty state so Save / exiting Edit Mode stays consistent
        if win and viewer is not None:
            ps_map = getattr(win, "_pipeline_state", None)
            if isinstance(ps_map, dict):
                ps_map[id(viewer)] = {
                    "original_path": orig_out,
                    "temp_path": temp_path,
                }
            viewer.load(
                temp_path,
                target_page=self._page_idx,
                target_scroll=-1,
                selected_pages=selected_pages,
                _is_history_step=True,
            )
            viewer._original_doc_path = orig_out
            if hasattr(win, "_sync_edit_dirty_indicator"):
                win._sync_edit_dirty_indicator()
            if hasattr(win, "_update_page_nav"):
                win._update_page_nav()

    def _edit_duplicate_pages(self, pages: list[int], count: int = 1) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        count = max(1, int(count))
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)

            new_doc = fitz.open()
            pages_set = set(pages)
            new_selected: list[int] = []
            new_pending: list[dict] = []

            by_page: dict[int, list[dict]] = {}
            for ov in self._pending:
                if not ov.get("_deleted"):
                    by_page.setdefault(int(ov.get("page", 0)), []).append(ov)

            for p_idx in range(doc.page_count):
                dest_idx = new_doc.page_count
                new_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)
                for ov in by_page.get(p_idx, []):
                    new_pending.append(_clone_overlay_for_page(ov, dest_idx))
                if p_idx in pages_set:
                    for _ in range(count):
                        dup_idx = new_doc.page_count
                        new_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)
                        new_selected.append(dup_idx)
                        for ov in by_page.get(p_idx, []):
                            cloned = _clone_overlay_for_page(ov, dup_idx)
                            if cloned.get("_existing") and not cloned.get("_dirty"):
                                continue
                            new_pending.append(cloned)
            doc.close()

            target_pg = new_selected[0] if new_selected else 0
            self._commit_editor_temp_pdf(new_doc, new_pending, target_pg, new_selected)
            self._status(f"Duplicated {len(pages_set)} page(s) × {count} (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_delete_pages(self, pages: list[int]) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            valid = sorted({p for p in pages if 0 <= p < doc.page_count})
            if not valid:
                doc.close()
                return
            if doc.page_count <= len(valid):
                ans = QMessageBox.question(
                    self, t("msg.confirm"),
                    "Deleting all pages will reset the document to a single blank page. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    doc.close()
                    return
                r0 = doc[0].rect if doc.page_count > 0 else fitz.Rect(0, 0, 595, 842)
                new_doc = fitz.open()
                new_doc.new_page(width=r0.width, height=r0.height)
                doc.close()
                self._commit_editor_temp_pdf(new_doc, [], 0, [0])
                self._status(f"Deleted {len(valid)} page(s) (Ctrl+S to save)")
                return

            kept = [i for i in range(doc.page_count) if i not in set(valid)]
            old_to_new = {old_i: new_i for new_i, old_i in enumerate(kept)}
            new_doc = fitz.open()
            for old_i in kept:
                new_doc.insert_pdf(doc, from_page=old_i, to_page=old_i)
            doc.close()

            new_pending = [
                _clone_overlay_for_page(ov, old_to_new[ov.get("page", 0)])
                for ov in self._pending
                if not ov.get("_deleted") and ov.get("page", 0) in old_to_new
            ]
            target = min(valid[0], new_doc.page_count - 1)
            self._commit_editor_temp_pdf(new_doc, new_pending, target, [target])
            self._status(f"Deleted {len(valid)} page(s) (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_insert_blank_page(self, page_idx: int, count: int = 1) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        count = max(1, int(count))
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            ref_rect = doc[page_idx].rect if 0 <= page_idx < doc.page_count else fitz.Rect(0, 0, 595, 842)
            insert_at = min(doc.page_count, max(0, page_idx + 1))
            new_selected: list[int] = []
            for offset in range(count):
                doc.new_page(pno=insert_at + offset, width=ref_rect.width, height=ref_rect.height)
                new_selected.append(insert_at + offset)
            new_pending = []
            for ov in self._pending:
                if ov.get("_deleted"):
                    continue
                pg = int(ov.get("page", 0))
                new_pg = pg + count if pg >= insert_at else pg
                new_pending.append(_clone_overlay_for_page(ov, new_pg))
            self._commit_editor_temp_pdf(doc, new_pending, insert_at, new_selected)
            self._status(f"Inserted {count} blank page(s) (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_insert_pages_from_file(self, page_idx: int, count: int = 1) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        count = max(1, int(count))
        p, _ = QFileDialog.getOpenFileName(self, "Insert Pages from PDF", DESKTOP, t("file_filter.pdf"))
        if not p or not os.path.isfile(p):
            return
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            src = fitz.open(p)
            if src.needs_pass:
                from app.utils import prompt_pdf_password
                ok, src_pwd = prompt_pdf_password(p, self)
                if not ok:
                    src.close()
                    doc.close()
                    return
                src.authenticate(src_pwd)
            src_count = src.page_count
            if src_count <= 0:
                src.close()
                doc.close()
                return
            insert_at = min(doc.page_count, max(0, page_idx + 1))
            for i in range(count):
                doc.insert_pdf(src, start_at=insert_at + i * src_count)
            src.close()
            total_inserted = count * src_count
            new_selected = list(range(insert_at, insert_at + total_inserted))
            new_pending = []
            for ov in self._pending:
                if ov.get("_deleted"):
                    continue
                pg = int(ov.get("page", 0))
                new_pg = pg + total_inserted if pg >= insert_at else pg
                new_pending.append(_clone_overlay_for_page(ov, new_pg))
            self._commit_editor_temp_pdf(doc, new_pending, insert_at, new_selected)
            self._status(f"Inserted {total_inserted} page(s) from {os.path.basename(p)} (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_reorder_pages(self, pages: list[int], target_index: int) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        total = self._canvas.page_count()
        if total <= 1 or not pages:
            return
        pages = [p for p in pages if 0 <= p < total]
        if not pages or len(pages) == total:
            return
        order = [i for i in range(total) if i not in pages]
        dest_pos = sum(1 for i in order if i < target_index)
        dest_pos = max(0, min(dest_pos, len(order)))
        for i, p in enumerate(pages):
            order.insert(dest_pos + i, p)
        if order == list(range(total)):
            return
        self._edit_apply_page_order(order, list(range(dest_pos, dest_pos + len(pages))), dest_pos, f"Moved {len(pages)} page(s)")

    def _edit_apply_page_order(self, order: list[int], new_selected: list[int], target_pg: int, status_msg: str) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            new_doc = fitz.open()
            old_to_new: dict[int, int] = {}
            for new_i, old_i in enumerate(order):
                if 0 <= old_i < doc.page_count:
                    new_doc.insert_pdf(doc, from_page=old_i, to_page=old_i)
                    old_to_new[old_i] = new_i
            doc.close()
            new_pending = [
                _clone_overlay_for_page(ov, old_to_new[ov.get("page", 0)])
                for ov in self._pending
                if not ov.get("_deleted") and ov.get("page", 0) in old_to_new
            ]
            self._commit_editor_temp_pdf(new_doc, new_pending, target_pg, new_selected)
            self._status(status_msg + " (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_rotate_pages(self, pages: list[int], delta: int) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            for p_idx in pages:
                if 0 <= p_idx < doc.page_count:
                    pg = doc[p_idx]
                    pg.set_rotation((pg.rotation + delta) % 360)
            new_pending = [_clone_overlay_for_page(ov, ov.get("page", 0)) for ov in self._pending if not ov.get("_deleted")]
            target_pg = min(pages) if pages else 0
            self._commit_editor_temp_pdf(doc, new_pending, target_pg, pages)
            self._status(f"Rotated {len(pages)} page(s) (Ctrl+S to save)")
        except Exception as exc:
            show_error(self, exc)

    def _edit_reverse_pages(self, pages: list[int]) -> None:
        total = self._canvas.page_count()
        if total <= 1:
            return
        order = list(range(total))
        if len(pages) > 1:
            for orig_idx, rev_idx in zip(pages, reversed(pages)):
                order[orig_idx] = rev_idx
            sel = pages
        else:
            order = list(reversed(order))
            sel = [0]
        self._edit_apply_page_order(order, sel, sel[0] if sel else 0, "Reversed pages")

    def _edit_swap_page_dialog(self, page_idx: int) -> None:
        total = max(1, self._canvas.page_count())
        target, ok = QInputDialog.getInt(
            self, "Swap Pages",
            f"Swap page {page_idx + 1} with page (1-{total}):",
            min(total, max(1, page_idx + 2)), 1, total, 1,
        )
        if not ok or target == page_idx + 1:
            return
        t_idx = target - 1
        order = list(range(total))
        order[page_idx], order[t_idx] = order[t_idx], order[page_idx]
        self._edit_apply_page_order(order, [t_idx], t_idx, f"Swapped page {page_idx + 1} and {target}")

    def _edit_move_page_dialog(self, pages: list[int]) -> None:
        total = max(1, self._canvas.page_count())
        page_str = ", ".join(str(p + 1) for p in pages)
        dest, ok = QInputDialog.getInt(
            self, "Move Pages",
            f"Move page(s) {page_str} to position (1-{total}):",
            min(total, max(1, max(pages) + 2)), 1, total, 1,
        )
        if not ok:
            return
        dest_idx = dest - 1
        order = [i for i in range(total) if i not in pages]
        dest_clamped = max(0, min(dest_idx, len(order)))
        target_index = order[dest_clamped] if dest_clamped < len(order) else total
        self._edit_reorder_pages(pages, target_index)

    def _edit_extract_pages(self, pages: list[int]) -> None:
        if not self._doc_path or not os.path.isfile(self._doc_path):
            return
        orig_name = (self._drop_out.path() if hasattr(self, "_drop_out") else "") or self._doc_path
        base, ext = os.path.splitext(os.path.basename(orig_name))
        save_dir = os.path.dirname(orig_name) or DESKTOP
        default_name = f"{base}_page_{pages[0] + 1}{ext or '.pdf'}" if len(pages) == 1 else f"{base}_extracted_pages{ext or '.pdf'}"
        out_path, _ = QFileDialog.getSaveFileName(self, t("tool.extract.name"), os.path.join(save_dir, default_name), t("file_filter.pdf"))
        if not out_path:
            return
        try:
            doc = fitz.open(self._doc_path)
            if self._pdf_password and doc.needs_pass:
                doc.authenticate(self._pdf_password)
            new_doc = fitz.open()
            for p_idx in pages:
                if 0 <= p_idx < doc.page_count:
                    new_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)
            doc.close()
            atomic_pdf_write(new_doc, out_path, close_writer=True)
            QMessageBox.information(self, t("msg.done"), f"{len(pages)} page(s) extracted to:\n{out_path}")
        except Exception as exc:
            show_error(self, exc)

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
        if action == "undo":
            self._undo()
            return
        if action == "redo":
            self._redo()
            return
        if action == "reorder":
            if isinstance(pages_arg, (tuple, list)) and len(pages_arg) == 2:
                pages_to_move, target_index = pages_arg
                if isinstance(pages_to_move, (list, tuple, set)):
                    self._edit_reorder_pages(sorted(list(pages_to_move)), int(target_index))
            return

        count = 1
        if isinstance(pages_arg, dict):
            count = max(1, int(pages_arg.get("count", 1)))
            raw_pages = pages_arg.get("pages", [0])
            pages = [raw_pages] if isinstance(raw_pages, int) else sorted(list(raw_pages)) if isinstance(raw_pages, (list, tuple, set)) else [0]
        elif isinstance(pages_arg, int):
            pages = [pages_arg]
        elif isinstance(pages_arg, (list, tuple, set)):
            pages = sorted(list(pages_arg))
        else:
            pages = [0]
        if not pages:
            pages = [0]

        if action == "duplicate":
            self._edit_duplicate_pages(pages, count=count)
        elif action == "delete":
            self._edit_delete_pages(pages)
        elif action == "insert_blank":
            self._edit_insert_blank_page(pages[-1], count=count)
        elif action == "insert_file":
            self._edit_insert_pages_from_file(pages[-1], count=count)
        elif action in ("rotate_right", "rotate_left", "rotate_180"):
            delta = 90 if action == "rotate_right" else (270 if action == "rotate_left" else 180)
            self._edit_rotate_pages(pages, delta)
        elif action == "reverse":
            self._edit_reverse_pages(pages)
        elif action == "swap":
            self._edit_swap_page_dialog(pages[0])
        elif action == "move":
            self._edit_move_page_dialog(pages)
        elif action == "extract":
            self._edit_extract_pages(pages)
        elif action == "print":
            if self._doc_path and os.path.isfile(self._doc_path):
                from app.viewer.print_dialog import _PdfPrintDialog
                dlg = _PdfPrintDialog(doc_path=self._doc_path, password=self._pdf_password, initial_pages=pages, parent=self)
                dlg.exec()
        elif action == "properties":
            win: Any = self.window()
            if win and hasattr(win, "_open_tool_by_name"):
                win._open_tool_by_name(t("nav.info"))
        elif action == "crop":
            win = self.window()
            if win and hasattr(win, "_open_tool_by_name"):
                win._open_tool_by_name(t("nav.crop"))
        elif action == "page_numbers":
            win = self.window()
            if win and hasattr(win, "_open_tool_by_name"):
                win._open_tool_by_name(t("nav.page_numbers"))
        elif action == "split":
            win = self.window()
            if win and hasattr(win, "_open_tool_by_name"):
                win._open_tool_by_name(t("nav.split"))
        elif action in ("cut", "copy_pages", "paste_pages", "replace", "resize", "copy", "paste"):
            win = self.window()
            viewer = getattr(win, "_viewer", None) if win else None
            if action == "copy" and self._canvas._doc:
                texts = []
                for p_idx in pages:
                    if 0 <= p_idx < self._canvas._doc.page_count:
                        txt = str(self._canvas._doc[p_idx].get_text("text")).strip()
                        if txt:
                            texts.append(txt)
                if texts:
                    cb = QApplication.clipboard()
                    if cb is not None:
                        cb.setText("\n\n--- Page Break ---\n\n".join(texts))
                    self._status(f"Copied text from {len(texts)} page(s)")
            elif viewer is not None and hasattr(viewer, "_on_thumbnail_action"):
                viewer._on_thumbnail_action(action, pages)
                if action in ("cut", "paste_pages", "replace", "resize", "paste") and viewer.current_path():
                    new_p = viewer.current_path()
                    if new_p and os.path.isfile(new_p):
                        doc = fitz.open(new_p)
                        self._commit_editor_temp_pdf(doc, list(self._pending), min(pages[0], doc.page_count - 1), pages)

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
            if hasattr(self, "_sidebar_tabs") and hasattr(self, "_sidebar_tabs") and hasattr(self._sidebar_tabs, "setTabVisible"):
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

