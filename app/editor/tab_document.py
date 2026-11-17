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

    def _restore_scroll(self, val_v: int, val_h: int) -> None:
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

    def _load_pdf(self, p: str):
        if not p:
            return
        if not os.path.isfile(p):
            if os.path.isfile(p + ".pdf"):
                p = p + ".pdf"
            else:
                return

        key = os.path.abspath(p)
        saved = self._doc_states.get(key)

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

        if saved:
            self._drop_out.set_path(saved.get("drop_out") or (os.path.splitext(p)[0] + "_edited.pdf"))
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
            if not self._drop_out.path():
                self._drop_out.set_path(os.path.splitext(p)[0] + "_edited.pdf")
            self._pending.clear()
            self._pending_list.clear() if hasattr(self, "_pending_list") else None
            self._page_idx = 0
            target_zoom_factor = 1.0

        try:
            self._canvas.load(p, password=self._pdf_password, initial_zoom_factor=target_zoom_factor, initial_page=self._page_idx)
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
        if hasattr(self, "_populate_toc"):
            self._populate_toc(self._canvas._doc)

        if saved:
            v_val = saved.get("scroll_v", 0)
            h_val = saved.get("scroll_h", 0)
            QTimer.singleShot(60, lambda v=v_val, h=h_val: self._restore_scroll(v, h))
        else:
            QTimer.singleShot(100, lambda: self._load_existing_annotations() if isValid(self) else None)

        QTimer.singleShot(200, lambda: self._load_form_fields(p) if isValid(self) else None)

    def _load_existing_annotations(self):
        self._history.load_existing_annotations()

    def auto_load(self, path: str):
        if not path:
            return
        norm_p = os.path.abspath(path)
        if self._doc_path and os.path.abspath(self._doc_path) == norm_p:
            return
        self._save_current_doc_state()
        self._load_pdf(path)

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