# app/editor/tab_document.py
"""PDF_Editor – Document loading, caching, password authentication, and asset file dialogs for TabEditar."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget
from shiboken6 import isValid

from app.constants import DESKTOP
from app.editor.dialogs import _SignatureDialog, load_signature_pixmap
from app.editor.tab_operations import cleanup_signature_temp_file, load_form_fields
from app.i18n import t
from app.pdf_password import authenticate_fitz

if TYPE_CHECKING:
    from app.editor.canvas import PdfEditCanvas
    from app.editor.tab_history import TabHistoryManager
    from app.viewer.thumbnails import ThumbnailPanel
    _Base = QWidget
else:
    _Base = object


class TabDocumentMixin(_Base):
    """Mixin managing PDF document states, caching, password prompts, and assets."""

    if TYPE_CHECKING:
        _doc_path: str | None
        _pdf_password: str
        _canvas_scroll: Any
        _doc_states: dict[str, dict]
        _pending: list[dict]
        _redo_stack: list[dict]
        _page_idx: int
        _last_selected_page: int
        _last_selected_rect: Any
        _last_selected_original_text: str
        _signature_path: str | None
        _drop_in: Any
        _drop_out: Any
        _history: TabHistoryManager
        _canvas: PdfEditCanvas
        _lbl_info: Any
        _thumbnails: ThumbnailPanel
        _toc_tree: Any
        _img_drop: Any
        _sig_preview: Any

        def _update_nav(self) -> None: ...
        def _populate_toc(self, doc: fitz.Document | None) -> None: ...
        def _status(self, msg: str) -> None: ...
        def _sync_thumbnails(self, affected_pages: set[int] | list[int] | None = None) -> None: ...

    def _capture_viewer_view(self, path: str) -> dict | None:
        """Capture current page, zoom, scroll anchor, and pixmaps from MainWindow's active viewer if showing `path`."""
        win = self.window() if hasattr(self, "window") else None
        viewer = getattr(win, "_viewer", None) if win else None
        if not viewer or not hasattr(viewer, "current_path"):
            return None
        v_curr = viewer.current_path() or ""
        v_orig = getattr(viewer, "_original_doc_path", "") or v_curr
        norm_target = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        if not any(
            vp_str and os.path.normcase(os.path.normpath(os.path.abspath(vp_str))) == norm_target
            for vp_str in (v_curr, v_orig)
        ):
            return None

        canvas = getattr(viewer, "_canvas", None)
        sa = getattr(viewer, "_canvas_scroll", None)
        if not canvas or not sa or not getattr(canvas, "_entries", None):
            return None

        sb_v = sa.verticalScrollBar()
        sb_h = sa.horizontalScrollBar()
        v_val = sb_v.value() if sb_v else 0
        h_val = sb_h.value() if sb_h else 0
        z = float(getattr(canvas, "_zoom", 1.0) or 1.0)
        zf = float(getattr(canvas, "_zoom_factor", 1.0) or 1.0)
        entries = canvas._entries
        top_page = canvas.page_at_y(v_val)
        anchor = None
        src_page = 0
        if 0 <= top_page < len(entries):
            entry = entries[top_page]
            src_page = int(getattr(entry, "src_page", top_page))
            x_off = canvas.page_x_offset(entry)
            pdf_x = max(0.0, (h_val - x_off) / z)
            pdf_y = (v_val - entry.y_off) / z
            anchor = (src_page, pdf_x, pdf_y, 0.0, 0.0)

        pixmaps = None
        if (
            not getattr(canvas, "_night_mode", False)
            and not getattr(canvas, "_page_rotations", None)
            and not getattr(canvas, "_page_crops", None)
            and getattr(canvas, "_page_order", None) is None
        ):
            pixmaps = [
                (e.pixmap if e.pixmap is not None else e.prev_pixmap)
                for e in entries
            ]

        return {
            "page_idx": src_page,
            "zoom_factor": zf,
            "anchor": anchor,
            "pixmaps": pixmaps,
            "sidebar_collapsed": bool(getattr(viewer, "_pages_sidebar_collapsed", False)),
            "sidebar_width": int(getattr(viewer, "_saved_sidebar_width", 220) or 220),
        }

    def _sync_sidebar_from_viewer_view(self, viewer_view: dict) -> None:
        if "sidebar_collapsed" not in viewer_view:
            return
        sidebar_panel = getattr(self, "_sidebar_panel", None)
        edit_splitter = getattr(self, "_edit_splitter", None)
        if sidebar_panel is None or edit_splitter is None:
            return
        v_col = bool(viewer_view["sidebar_collapsed"])
        v_w = max(70, int(viewer_view.get("sidebar_width", 220) or 220))
        cur_col = bool(getattr(self, "_pages_sidebar_collapsed", False))
        cur_w = int(getattr(self, "_saved_sidebar_width", 220) or 220)
        if cur_col != v_col or (not v_col and abs(cur_w - v_w) > 4):
            self._pages_sidebar_collapsed = v_col
            self._saved_sidebar_width = v_w
            sidebar_panel.setVisible(not v_col)
            total = edit_splitter.width() or 1020
            if v_col:
                edit_splitter.setSizes([0, total])
            else:
                edit_splitter.setSizes([v_w, max(300, total - v_w)])

    def _apply_viewer_view(self, viewer_view: dict) -> None:
        """Apply a captured viewer view (sidebar, page, zoom, scroll anchor) to the active editor canvas."""
        if not viewer_view:
            return
        self._scroll_restore_token = getattr(self, "_scroll_restore_token", 0) + 1
        self._sync_sidebar_from_viewer_view(viewer_view)

        target_page = int(viewer_view.get("page_idx", 0) or 0)
        target_zf = float(viewer_view.get("zoom_factor", 1.0) or 1.0)
        anchor = viewer_view.get("anchor")

        self._page_idx = target_page
        self._canvas.apply_view_state(target_zf, target_page, anchor)
        self._update_nav()
        if hasattr(self, "_thumbnails"):
            self._thumbnails.set_current_page(self._page_idx)

    def _save_current_doc_state(self):
        """Save the current document's pending edits, scroll, zoom, and active page before switching away."""
        if not self._doc_path:
            return
        key = os.path.abspath(self._doc_path)
        sb_v = self._canvas_scroll.verticalScrollBar()
        sb_h = self._canvas_scroll.horizontalScrollBar()
        self._doc_states[key] = {
            "pending": list(self._pending),
            "redo_stack": list(self._redo_stack),
            "page_idx": self._page_idx,
            "zoom_factor": getattr(self._canvas, "_zoom_factor", 1.0),
            "scroll_v": sb_v.value() if sb_v else 0,
            "scroll_h": sb_h.value() if sb_h else 0,
            "mode_idx": getattr(self, "_mode_idx", 1),
            "text_submode": getattr(self, "_text_submode", "edit"),
            "signature_path": self._signature_path,
            "drop_out": self._drop_out.path() if hasattr(self, "_drop_out") else "",
            "last_selected_page": self._last_selected_page,
            "last_selected_rect": self._last_selected_rect,
            "last_selected_original_text": self._last_selected_original_text,
        }
        print(f"[DEBUG] [TabDocument] _save_current_doc_state: saved state for {os.path.basename(self._doc_path)} ({len(self._pending)} pending edits)", flush=True)

    def _restore_scroll(self, val_v: int, val_h: int, token: int | None = None) -> None:
        if token is not None and token != getattr(self, "_scroll_restore_token", 0):
            return
        if hasattr(self, "_canvas_scroll"):
            sb_v = self._canvas_scroll.verticalScrollBar()
            if sb_v:
                sb_v.setValue(min(val_v, sb_v.maximum()))
            sb_h = self._canvas_scroll.horizontalScrollBar()
            if sb_h:
                sb_h.setValue(min(val_h, sb_h.maximum()))

    def _pick_pdf(self):
        p, _ = QFileDialog.getOpenFileName(self, t("btn.open_pdf"), DESKTOP, t("file_filter.pdf"))
        if p:
            self._load_pdf(p)

    def _load_pdf(self, p: str, viewer_view: dict | None = None):
        if not p:
            return
        if not os.path.isfile(p):
            if os.path.isfile(p + ".pdf"):
                p = p + ".pdf"
            else:
                return

        key = os.path.abspath(p)
        saved = self._doc_states.get(key)
        print(f"[DEBUG] [TabDocument] _load_pdf: loading {p} (has_saved_state={bool(saved)})", flush=True)

        try:
            probe = fitz.open(p)
            needs_pass = bool(probe.needs_pass)
            if needs_pass and self._pdf_password:
                winner = authenticate_fitz(probe, self._pdf_password)
                self._pdf_password = winner or ""
            probe.close()
        except Exception:
            needs_pass = False
        if needs_pass and not self._pdf_password:
            from app.utils import prompt_pdf_password
            ok, pwd = prompt_pdf_password(p, self)
            if not ok:
                return
            self._pdf_password = pwd
        elif not needs_pass:
            self._pdf_password = ""

        self._doc_path = p
        self._drop_in.blockSignals(True)
        self._drop_in.set_path(p)
        self._drop_in.blockSignals(False)

        self._scroll_restore_token = getattr(self, "_scroll_restore_token", 0) + 1
        restore_tok = self._scroll_restore_token

        initial_anchor = None
        initial_pixmaps = None

        if saved:
            self._drop_out.set_path(saved.get("drop_out") or p)
            self._pending = list(saved.get("pending", []))
            self._redo_stack = list(saved.get("redo_stack", []))
            self._page_idx = saved.get("page_idx", 0)
            self._last_selected_page = saved.get("last_selected_page", -1)
            self._last_selected_rect = saved.get("last_selected_rect")
            self._last_selected_original_text = saved.get("last_selected_original_text", "")
            self._signature_path = saved.get("signature_path")
            self._history.rebuild_pending_list()
            target_zoom_factor = saved.get("zoom_factor", 1.0)
        else:
            self._drop_out.set_path(p)
            self._pending.clear()
            self._pending_list.clear() if hasattr(self, "_pending_list") else None
            if viewer_view is None:
                viewer_view = self._capture_viewer_view(p)
            if viewer_view is not None:
                self._sync_sidebar_from_viewer_view(viewer_view)
                self._page_idx = int(viewer_view.get("page_idx", 0) or 0)
                target_zoom_factor = float(viewer_view.get("zoom_factor", 1.0) or 1.0)
                initial_anchor = viewer_view.get("anchor")
                initial_pixmaps = viewer_view.get("pixmaps")
            else:
                self._page_idx = 0
                target_zoom_factor = 1.0

        try:
            self._canvas.load(
                p,
                password=self._pdf_password,
                initial_zoom_factor=target_zoom_factor,
                initial_page=self._page_idx,
                initial_anchor=initial_anchor,
                initial_pixmaps=initial_pixmaps,
            )
            self._canvas.set_overlays(self._pending)
        except ModuleNotFoundError as ex:
            QMessageBox.critical(self, t("msg.missing_dep"), t("msg.dep_pymupdf", ex=ex))
            return
        except Exception as ex:
            QMessageBox.critical(self, t("msg.error"), t("msg.pdf_open_error", ex=ex))
            return

        n = self._canvas.page_count()
        self._lbl_info.setText(t("edit.status.pages", n=n))
        self._update_nav()

        if hasattr(self, "_thumbnails"):
            self._thumbnails.set_document(p, n, password=self._pdf_password)
            self._thumbnails.set_current_page(self._page_idx)
            if hasattr(self, "_sync_thumbnails"):
                self._sync_thumbnails()

        if hasattr(self, "_populate_toc"):
            self._populate_toc(self._canvas._doc)

        if saved:
            v_val = saved.get("scroll_v", 0)
            h_val = saved.get("scroll_h", 0)
            QTimer.singleShot(
                60,
                lambda v=v_val, h=h_val, tok=restore_tok: (
                    self._restore_scroll(v, h, token=tok) if isValid(self) else None
                ),
            )
        else:
            QTimer.singleShot(100, lambda: self._load_existing_annotations() if isValid(self) else None)

        QTimer.singleShot(200, lambda: self._load_form_fields(p) if isValid(self) else None)

    def _load_existing_annotations(self):
        self._history.load_existing_annotations()

    def auto_load(self, path: str):
        if not path:
            return
        norm_p = os.path.abspath(path)
        viewer_view = self._capture_viewer_view(path)
        if self._doc_path and os.path.abspath(self._doc_path) == norm_p:
            if viewer_view is not None:
                self._apply_viewer_view(viewer_view)
            return
        self._save_current_doc_state()
        self._load_pdf(path, viewer_view=viewer_view)

    def _close_pdf(self):
        if self._doc_path:
            self._doc_states.pop(os.path.abspath(self._doc_path), None)
        self._doc_path = None
        self._canvas.close_doc()
        self._canvas.set_overlays([])
        if hasattr(self, "_thumbnails"):
            self._thumbnails.clear()
        if hasattr(self, "_toc_tree"):
            self._toc_tree.clear()
        self._pending.clear()
        self._pending_list.clear() if hasattr(self, "_pending_list") else None
        self._lbl_info.setText("")
        self._page_idx = 0
        self._update_nav()
        self._clear_pdf_password()
        print("[DEBUG] [TabDocument] _close_pdf: document closed and thumbnails cleared", flush=True)

    def _clear_pdf_password(self) -> None:
        from app.utils import wipe_pdf_password
        wipe_pdf_password(self)

    def _pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, t("edit.image"), DESKTOP, t("file_filter.images"))
        if p:
            from app.utils import check_image_size
            ok, w, h = check_image_size(p)
            if not ok:
                QMessageBox.warning(
                    self, t("msg.warning"),
                    t("editor.image_too_large", width=w, height=h, megapix=w * h // 1_000_000)
                )
                return
            self._img_drop.blockSignals(True)
            self._img_drop.set_path(p)
            self._img_drop.blockSignals(False)

    def _cleanup_signature_temp(self):
        cleanup_signature_temp_file(self._signature_path)

    def _pick_signature(self):
        dlg = _SignatureDialog(self)
        if dlg.exec() == _SignatureDialog.DialogCode.Accepted:
            path = dlg.selected_signature_path()
            if path and os.path.isfile(path):
                self._cleanup_signature_temp()
                self._signature_path = path
                pix = load_signature_pixmap(path, max_size=(200, 50))
                if not pix.isNull():
                    self._sig_preview.setPixmap(pix)
                self._canvas.begin_signature_placement(path)
                self._status(t("edit.signature.place_hint"))

    def _clear_signature(self):
        from PySide6.QtGui import QPixmap
        self._cleanup_signature_temp()
        self._signature_path = None
        self._sig_preview.setText(t("edit.signature.none"))
        self._sig_preview.setPixmap(QPixmap())
        self._canvas.cancel_signature_placement()
        from app.i18n import clear_saved_signature
        clear_saved_signature()

    def _load_form_fields(self, path: str):
        load_form_fields(self, path)