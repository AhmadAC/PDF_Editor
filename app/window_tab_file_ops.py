
# app/window_tab_file_ops.py
"""PDF_Editor – Tab disk file operations: Rename on Disk and Delete from Disk with lock release."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
import traceback
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from app.i18n import t

if TYPE_CHECKING:
    from app.viewer.panel import PdfViewerPanel

_log = logging.getLogger(__name__)

_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_tab_disk_path(win: Any, viewer: PdfViewerPanel) -> str:
    """Resolve the intended on-disk path for a viewer tab (normalized for the current OS)."""
    ps_map = getattr(win, "_pipeline_state", {})
    ps = ps_map.get(id(viewer)) if isinstance(ps_map, dict) else None
    if ps and ps.get("original_path"):
        return os.path.normpath(ps["original_path"])
    orig_path = getattr(viewer, "_original_doc_path", "")
    if orig_path:
        return os.path.normpath(orig_path)
    curr = viewer.current_path() or ""
    return os.path.normpath(curr) if curr else ""


def _update_recent_files_path(old_path: str, new_path: str | None) -> None:
    """Replace or remove old_path in recent_files config."""
    try:
        from app.i18n import _update_config
        old_norm = os.path.normcase(os.path.normpath(os.path.abspath(old_path)))

        def _mutate(cfg: dict) -> None:
            recents = cfg.get("recent_files", [])
            if not isinstance(recents, list):
                return
            updated: list[str] = []
            for r in recents:
                if not isinstance(r, str):
                    continue
                if os.path.normcase(os.path.normpath(os.path.abspath(r))) == old_norm:
                    if new_path:
                        updated.append(os.path.normpath(new_path))
                else:
                    updated.append(os.path.normpath(r))
            cfg["recent_files"] = updated

        _update_config(_mutate)
    except Exception:
        pass


def rename_tab_file_on_disk(win: Any, tab_idx: int, viewer: PdfViewerPanel) -> None:
    """Prompt for a new filename and rename the tab's backing file on disk or rename an unsaved tab's target."""
    disk_path = get_tab_disk_path(win, viewer)
    if not disk_path:
        QMessageBox.warning(win, t("msg.warning"), "No backing file to rename.")
        return

    parent_dir = os.path.dirname(disk_path)
    old_name = os.path.basename(disk_path)
    _, old_ext = os.path.splitext(old_name)

    new_name, ok = QInputDialog.getText(
        win,
        "Rename File on Disk",
        f"Enter new name for '{old_name}':",
        QLineEdit.EchoMode.Normal,
        old_name,
    )
    if not ok:
        return

    new_name = new_name.strip()
    if not new_name:
        QMessageBox.warning(win, t("msg.warning"), "File name cannot be empty.")
        return

    if _INVALID_WIN_CHARS.search(new_name) or os.path.basename(new_name) != new_name:
        QMessageBox.warning(win, t("msg.warning"), "File name contains invalid characters.")
        return

    if old_ext and not os.path.splitext(new_name)[1]:
        new_name += old_ext

    if new_name == old_name:
        return

    new_path = os.path.normpath(os.path.join(parent_dir, new_name))
    is_case_only = os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(disk_path))

    file_exists_on_disk = os.path.isfile(disk_path)

    if file_exists_on_disk and not is_case_only and os.path.exists(new_path):
        QMessageBox.warning(win, t("msg.warning"), f"A file named '{new_name}' already exists in this folder.")
        return

    ps_map = getattr(win, "_pipeline_state", {})
    ps = ps_map.get(id(viewer)) if isinstance(ps_map, dict) else None

    if not file_exists_on_disk:
        if ps is not None:
            ps["original_path"] = new_path
        viewer._original_doc_path = new_path
        viewer._name_lbl.setText(f"* {new_name}")
        win._tab_bar.setTabText(tab_idx, f"* {new_name}")
        win._tab_bar.setTabToolTip(tab_idx, f"{new_path} (unsaved)")
        if hasattr(win, "_refresh_secondary_tab_combo"):
            win._refresh_secondary_tab_combo()
        if hasattr(win, "_update_breadcrumb"):
            win._update_breadcrumb()
        if viewer is getattr(win, "_viewer", None) and hasattr(win, "setWindowTitle"):
            win.setWindowTitle(f"{t('app.name')} - * {new_name}")
        msg = f"Renamed to '{new_name}'"
        if hasattr(win, "_set_status"):
            win._set_status(msg)
        elif hasattr(win, "_sb"):
            win._sb.showMessage(msg)
        return

    curr_viewer_path = viewer.current_path()
    viewer_holds_disk_file = bool(
        curr_viewer_path and os.path.normcase(os.path.normpath(os.path.abspath(curr_viewer_path))) == os.path.normcase(os.path.normpath(os.path.abspath(disk_path)))
    )
    scroll_val = 0
    cur_page = 0
    if viewer_holds_disk_file and hasattr(viewer, "_canvas_scroll") and hasattr(viewer, "_canvas"):
        sb = viewer._canvas_scroll.verticalScrollBar()
        scroll_val = sb.value() if sb else 0
        if viewer._canvas.page_count() > 0:
            cur_page = viewer._canvas.page_at_y(scroll_val)

    if viewer_holds_disk_file:
        viewer._canvas.close_doc()
        doc = getattr(viewer, "_fitz_doc", None)
        if doc is not None:
            with contextlib.suppress(Exception):
                if not getattr(doc, "is_closed", False):
                    doc.close()
            viewer._fitz_doc = None
        viewer._thumbnails._stop_all_workers()

    edit_idx = win._edit_tool_idx() if hasattr(win, "_edit_tool_idx") else -1
    edit_w = win.stack.widget(edit_idx) if (0 <= edit_idx < win.stack.count()) else None
    edit_holds_disk_file = False
    if edit_w is not None and not getattr(edit_w, "_is_lazy_placeholder", False):
        cur_edit_path = getattr(edit_w, "_doc_path", None)
        if cur_edit_path and os.path.normcase(os.path.normpath(os.path.abspath(cur_edit_path))) == os.path.normcase(os.path.normpath(os.path.abspath(disk_path))):
            edit_holds_disk_file = True
            edit_w._canvas.release_doc()

    try:
        if is_case_only:
            fd, tmp_path = tempfile.mkstemp(prefix=".rename_", suffix=old_ext, dir=parent_dir)
            os.close(fd)
            os.unlink(tmp_path)
            os.rename(disk_path, tmp_path)
            os.rename(tmp_path, new_path)
        else:
            os.rename(disk_path, new_path)
    except Exception as exc:
        _log.debug("Rename failed: %s", traceback.format_exc())
        if viewer_holds_disk_file:
            viewer.load(disk_path, target_page=cur_page, target_scroll=scroll_val, _is_history_step=True)
        if edit_holds_disk_file and edit_w is not None:
            edit_w._load_pdf(disk_path)
        QMessageBox.critical(win, t("msg.error"), f"Could not rename file:\n{exc}")
        return

    viewer._original_doc_path = new_path
    old_abs = os.path.normcase(os.path.normpath(os.path.abspath(disk_path)))
    for stack_attr in ("_undo_stack", "_redo_stack"):
        for entry in getattr(viewer, stack_attr, []):
            if isinstance(entry, dict) and entry.get("path") and os.path.normcase(os.path.normpath(os.path.abspath(entry["path"]))) == old_abs:
                entry["path"] = new_path

    is_modified = bool(ps and ps.get("temp_path"))

    if is_modified and ps is not None:
        ps["original_path"] = new_path
        viewer._name_lbl.setText(f"* {new_name}")
        win._tab_bar.setTabText(tab_idx, f"* {new_name}")
        win._tab_bar.setTabToolTip(tab_idx, f"{new_path} (modified)")
    else:
        win._tab_bar.setTabText(tab_idx, new_name)
        win._tab_bar.setTabToolTip(tab_idx, new_path)

    if viewer_holds_disk_file:
        viewer.load(new_path, target_page=cur_page, target_scroll=scroll_val, _is_history_step=True)
        viewer._original_doc_path = new_path

    if edit_w is not None and not getattr(edit_w, "_is_lazy_placeholder", False):
        doc_states = getattr(edit_w, "_doc_states", None)
        if isinstance(doc_states, dict) and old_abs in doc_states:
            doc_states[os.path.normcase(os.path.normpath(os.path.abspath(new_path)))] = doc_states.pop(old_abs)
        if edit_holds_disk_file:
            edit_w._doc_path = new_path
            edit_w._drop_in.blockSignals(True)
            edit_w._drop_in.set_path(new_path)
            edit_w._drop_in.blockSignals(False)
            edit_w._load_pdf(new_path)

    _update_recent_files_path(disk_path, new_path)
    for v in getattr(win, "_viewers", []):
        ref = getattr(v, "_refresh_recents", None)
        if callable(ref):
            with contextlib.suppress(Exception):
                ref()

    if hasattr(win, "_refresh_secondary_tab_combo"):
        win._refresh_secondary_tab_combo()
    if hasattr(win, "_update_breadcrumb"):
        win._update_breadcrumb()
    if viewer is getattr(win, "_viewer", None) and hasattr(win, "setWindowTitle"):
        title_name = f"* {new_name}" if is_modified else new_name
        win.setWindowTitle(f"{t('app.name')} - {title_name}")

    msg = f"Renamed to '{new_name}'"
    if hasattr(win, "_set_status"):
        win._set_status(msg)
    elif hasattr(win, "_sb"):
        win._sb.showMessage(msg)


def delete_tab_file_from_disk(win: Any, tab_idx: int, viewer: PdfViewerPanel) -> None:
    """Confirm and permanently delete the tab's backing file from disk."""
    disk_path = get_tab_disk_path(win, viewer)
    curr_path = viewer.current_path() or ""
    if not disk_path:
        return

    filename = os.path.basename(disk_path)
    reply = QMessageBox.question(
        win,
        "Delete From Disk",
        f"Are you sure you want to permanently delete '{filename}' from disk?\n\n{disk_path}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    if hasattr(win, "_cleanup_pipeline"):
        win._cleanup_pipeline(id(viewer))
    win._close_tab(tab_idx)

    for target in {disk_path, curr_path}:
        if target and os.path.isfile(target):
            try:
                os.remove(target)
            except Exception as exc:
                QMessageBox.critical(win, "Error", f"Could not delete file:\n{exc}")

    _update_recent_files_path(disk_path, None)
    for v in getattr(win, "_viewers", []):
        ref = getattr(v, "_refresh_recents", None)
        if callable(ref):
            with contextlib.suppress(Exception):
                ref()

    msg = f"Deleted '{filename}' from disk"
    if hasattr(win, "_set_status"):
        win._set_status(msg)
    elif hasattr(win, "_sb"):
        win._sb.showMessage(msg)

