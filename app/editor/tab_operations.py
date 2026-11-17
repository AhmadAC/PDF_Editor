# app/editor/tab_operations.py
"""PDF_Editor – tab_operations: form operations and PDF saving logic with read-only Save As fallback for TabEditar."""

import contextlib
import io
import logging
import os
import tempfile
import fitz
from PySide6.QtWidgets import QFileDialog, QMessageBox, QTableWidgetItem

from app.constants import DESKTOP
from app.i18n import t
from app.utils import show_error, WrongPasswordError
from app.editor.apply_edits import apply_pending_edits
from app.pdf_io import atomic_pdf_write, is_path_writable
from app.pdf_password import decrypt_pypdf

_log = logging.getLogger(__name__)

_PDF_ENCRYPT_AES_256: int = getattr(fitz, "PDF_ENCRYPT_AES_256", 5)


def prompt_encryption_choice(parent) -> str | None:
    """Prompt user whether to keep or remove PDF encryption upon saving."""
    box = QMessageBox(parent)
    box.setWindowTitle(t("editor.encrypt.warning_title"))
    box.setText(t("editor.encrypt.warning_text"))
    box.setIcon(QMessageBox.Icon.Warning)
    keep_btn = box.addButton(t("editor.encrypt.save_protected"), QMessageBox.ButtonRole.AcceptRole)
    plain_btn = box.addButton(t("editor.encrypt.save_unprotected"), QMessageBox.ButtonRole.DestructiveRole)
    cancel_btn = box.addButton(t("btn.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep_btn)
    box.exec()
    clicked = box.clickedButton()
    if clicked is keep_btn:
        return "protect"
    if clicked is plain_btn:
        return "plaintext"
    return None


def get_fitz_permissions(doc) -> int:
    """Extract standard PDF permission bitmask flags from PyMuPDF doc."""
    try:
        perms = getattr(doc, "permissions", -1)
        return int(perms) if perms is not None else -1
    except Exception:
        return -1


def cleanup_signature_temp_file(signature_path: str | None) -> None:
    """Safely delete signature temporary image if located in system temp directory."""
    if not signature_path or not os.path.isfile(signature_path):
        return
    try:
        old_dir = os.path.normcase(os.path.dirname(signature_path))
        tmp_dir = os.path.normcase(tempfile.gettempdir())
        if old_dir.startswith(tmp_dir):
            os.unlink(signature_path)
    except OSError:
        pass


def load_form_fields(tab, path: str) -> None:
    """Populate the AcroForm fields table from the opened PDF document."""
    tab._form_table.setRowCount(0)
    tab._form_status.setText("")
    try:
        from pypdf import PdfReader
        tab._form_table.setUpdatesEnabled(False)
        _r = PdfReader(path)
        if _r.is_encrypted and tab._pdf_password:
            if decrypt_pypdf(_r, tab._pdf_password) is None:
                raise WrongPasswordError(t("tool.err.wrong_password"))
        fields = _r.get_fields() or {}
        for name, field in fields.items():
            r = tab._form_table.rowCount()
            tab._form_table.insertRow(r)
            tab._form_table.setItem(r, 0, QTableWidgetItem(name))
            tab._form_table.setItem(r, 1, QTableWidgetItem(str(field.get("/V", "") or "")))
        tab._form_table.setUpdatesEnabled(True)
        if not fields:
            tab._form_status.setText(t("editor.forms.no_fields"))
    except Exception as exc:
        tab._form_table.setUpdatesEnabled(True)
        _log.warning("Failed to load form fields from %s: %s", path, exc)
        tab._form_status.setText(t("editor.forms.load_failed"))


def invalidate_viewer_pipeline(tab, out_path: str) -> None:
    """Close reader handles in main viewer if the target output file is currently opened."""
    win = tab.window()
    viewers = getattr(win, "_viewers", None) or []
    viewer = getattr(win, "_viewer", None)
    if viewer and viewer not in viewers:
        viewers.append(viewer)
    for v in viewers:
        if v and v.current_path() and os.path.abspath(v.current_path()) == os.path.abspath(out_path):
            v._canvas.close_doc()
            doc = getattr(v, "_fitz_doc", None)
            if doc is not None:
                with contextlib.suppress(Exception):
                    if not getattr(doc, "is_closed", False):
                        doc.close()
                v._fitz_doc = None
            v._thumbnails._stop_all_workers()
            if win and hasattr(win, "_cleanup_pipeline"):
                win._cleanup_pipeline(id(v))


def _prompt_editor_save_as(tab, orig_out: str) -> str:
    """Prompt Save As when the editor target path is read-only or locked."""
    win = tab.window()
    if win and hasattr(win, "_prompt_save_as_fallback"):
        return win._prompt_save_as_fallback(orig_out)
    base_name = os.path.basename(orig_out) if orig_out else "document.pdf"
    suggested = os.path.normpath(os.path.join(DESKTOP, base_name))
    tab._status("File is read-only — choose a location to save a copy")
    new_out, _ = QFileDialog.getSaveFileName(
        tab, t("widget.save_as"), suggested, t("file_filter.pdf")
    )
    return os.path.normpath(new_out) if new_out else ""


def apply_form_fields_and_save(tab, out: str) -> None:
    """Apply updated AcroForm field values to the output file using pypdf."""
    try:
        from pypdf import PdfWriter, PdfReader
        with open(tab._doc_path, "rb") as _src:
            doc_bytes = _src.read()
        _r = PdfReader(io.BytesIO(doc_bytes))
        was_encrypted = bool(_r.is_encrypted)
        if was_encrypted and tab._pdf_password:
            if decrypt_pypdf(_r, tab._pdf_password) is None:
                raise WrongPasswordError(t("tool.err.wrong_password"))
        encrypt_choice = "plaintext"
        if was_encrypted and tab._pdf_password:
            encrypt_choice = prompt_encryption_choice(tab)
            if encrypt_choice is None:
                return
        writer = PdfWriter()
        writer.append(_r)
        fields = {
            tab._form_table.item(r, 0).text():
            (tab._form_table.item(r, 1).text() if tab._form_table.item(r, 1) else "")
            for r in range(tab._form_table.rowCount())
        }
        if "/AcroForm" not in writer._root_object:
            tab._status(t("editor.forms.no_fields"))
            tab._form_status.setText(t("editor.forms.no_fields"))
            return
        try:
            _w_fields = _r.get_fields() or {}
        except Exception:
            _w_fields = {}
        if not _w_fields:
            tab._status(t("editor.forms.no_fields"))
            tab._form_status.setText(t("editor.forms.no_fields"))
            return
        for page in writer.pages:
            writer.update_page_form_field_values(page, fields, auto_regenerate=True)
        if encrypt_choice == "protect" and tab._pdf_password:
            writer.encrypt(
                user_password=tab._pdf_password,
                owner_password=tab._pdf_password,
                algorithm="AES-256",
            )
            _log.info("Re-encrypted forms output with user password as owner")

        if not is_path_writable(out):
            new_out = _prompt_editor_save_as(tab, out)
            if not new_out:
                return
            out = new_out

        invalidate_viewer_pipeline(tab, out)
        try:
            atomic_pdf_write(writer, out)
        except (PermissionError, OSError):
            new_out = _prompt_editor_save_as(tab, out)
            if not new_out:
                return
            out = new_out
            invalidate_viewer_pipeline(tab, out)
            atomic_pdf_write(writer, out)

        win = tab.window()
        if win and hasattr(win, "_remember_save_dir"):
            win._remember_save_dir(os.path.dirname(out))

        tab._status(t("edit.status.form_saved", path=out))
        tab._load_pdf(out)
    except Exception as e:
        show_error(tab, e)


def apply_visual_edits_and_save(tab, out: str) -> None:
    """Apply pending visual and text edits to PDF document and write to disk."""
    try:
        if hasattr(tab, "_canvas") and hasattr(tab._canvas, "_overlay_mgr"):
            tab._canvas._overlay_mgr.discard_untouched_existing()

        edits_to_apply = list(tab._user_pending)

        peek = fitz.open(tab._doc_path)
        was_encrypted = bool(peek.needs_pass)
        if was_encrypted and tab._pdf_password:
            peek.authenticate(tab._pdf_password)
        encrypt_choice = "plaintext"
        if was_encrypted and tab._pdf_password:
            encrypt_choice = prompt_encryption_choice(tab)
            if encrypt_choice is None:
                peek.close()
                return
        peek.close()

        if not is_path_writable(out):
            new_out = _prompt_editor_save_as(tab, out)
            if not new_out:
                return
            out = new_out

        def _build_edited_doc():
            d = fitz.open(tab._doc_path)
            if d.needs_pass and tab._pdf_password:
                d.authenticate(tab._pdf_password)
            res = apply_pending_edits(d, edits_to_apply)
            reenc = bool(encrypt_choice == "protect" and tab._pdf_password)
            if reenc:
                perms = get_fitz_permissions(d)
                opts = dict(
                    garbage=4, deflate=True,
                    encryption=_PDF_ENCRYPT_AES_256,
                    user_pw=tab._pdf_password,
                    owner_pw=tab._pdf_password,
                    permissions=perms,
                )
            else:
                opts = dict(garbage=4, deflate=True)
            return d, res.text_fit_warnings, opts, reenc

        _non_latin = any(
            e.get("type") in ("text", "note", "text_edit")
            and any(ord(c) > 0xFF for c in (e.get("text") or e.get("new_text") or ""))
            for e in edits_to_apply
        )
        if _non_latin:
            tab._status(t("tool.warn.font_latin_only"))

        tab._canvas.release_doc()
        doc, text_fit_warnings, save_opts, reencrypt = _build_edited_doc()

        invalidate_viewer_pipeline(tab, out)
        try:
            atomic_pdf_write(doc, out, save_opts=save_opts, close_writer=True)
        except (PermissionError, OSError):
            with contextlib.suppress(Exception):
                if not getattr(doc, "is_closed", False):
                    doc.close()
            new_out = _prompt_editor_save_as(tab, out)
            if not new_out:
                tab._load_pdf(tab._doc_path)
                return
            out = new_out
            doc2, text_fit_warnings, save_opts, reencrypt = _build_edited_doc()
            invalidate_viewer_pipeline(tab, out)
            atomic_pdf_write(doc2, out, save_opts=save_opts, close_writer=True)

        if reencrypt:
            _log.info("Re-encrypted output with user password as owner")

        win = tab.window()
        if win and hasattr(win, "_remember_save_dir"):
            win._remember_save_dir(os.path.dirname(out))

        tab._history.clear()

        if win and hasattr(win, "_viewers"):
            for v in win._viewers:
                if v and (not v.current_path() or os.path.abspath(v.current_path()) == os.path.abspath(out)):
                    v.load(out)

        if text_fit_warnings:
            QMessageBox.warning(tab, t("msg.warning"), t("msg.pdf_saved", path=out))
        else:
            tab._status(f"Saved: {os.path.basename(out)}")
        tab._load_pdf(out)
    except Exception as e:
        show_error(tab, e)