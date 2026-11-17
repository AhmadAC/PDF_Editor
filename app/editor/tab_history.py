# app/editor/tab_history.py
"""PDF_Editor – tab_history: synchronized action-based history, undo/redo, and pending edits management."""

import contextlib
import os
import tempfile
import fitz

from app.i18n import t
from app.editor.canvas_overlay1 import restore_overlay_snapshot, snapshot_overlay
from app.editor.tab_constants import _MAX_PENDING, _MAX_REDO, _MODE_FORMS

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


class TabHistoryManager:
    """Manages pending visual edits, annotation imports, and action-level undo/redo stacks."""

    def __init__(self, tab):
        self.tab = tab
        self._undo_stack: list[dict] = []

    @property
    def pending(self) -> list:
        return self.tab._pending

    @pending.setter
    def pending(self, val: list) -> None:
        self.tab._pending = val

    @property
    def redo_stack(self) -> list:
        return self.tab._redo_stack

    @redo_stack.setter
    def redo_stack(self, val: list) -> None:
        self.tab._redo_stack = val

    def _notify_toolbar(self) -> None:
        win = self.tab.window() if hasattr(self.tab, "window") else None
        if win and hasattr(win, "_update_undo_redo_buttons"):
            win._update_undo_redo_buttons()

    @staticmethod
    def _format_edit_label(edit: dict) -> str:
        suffix = t("edit.label.page_suffix", n=edit.get("page", 0) + 1)
        deleted_tag = " (Deleted)" if edit.get("_deleted") else ""
        etype = edit.get("type", "")

        labels = {
            "redact":       lambda e: t("edit.label.redact") + suffix,
            "text":         lambda e: t("edit.label.text", txt=(e.get("text") or "")[:18]) + deleted_tag + suffix,
            "image":        lambda e: (f"Image{deleted_tag} " + suffix) if e.get("_existing") else (t("edit.label.image", name=os.path.basename(e.get("path", ""))) + deleted_tag + suffix),
            "highlight":    lambda e: t("edit.label.highlight") + deleted_tag + suffix,
            "note":         lambda e: t("edit.label.note") + deleted_tag + suffix,
            "text_edit":    lambda e: (
                (f"Text (Deleted) {suffix}") if (e.get("_deleted") or not e.get("new_text"))
                else (t("edit.label.edit", old=(e.get("old_text") or "")[:12], new=(e.get("new_text") or "")[:12]) + suffix)
            ),
            "signature":    lambda e: t("edit.mode.signature") + deleted_tag + suffix,
            "check":        lambda e: "Checkmark" + deleted_tag + suffix,
            "cross":        lambda e: "Crossmark" + deleted_tag + suffix,
            "drawing":      lambda e: f"Shape{deleted_tag} " + suffix,
            "draw":         lambda e: t("edit.mode.draw") + deleted_tag + suffix,
            "delete_annot": lambda e: t("edit.label.note_delete") + suffix,
        }
        builder = labels.get(etype, lambda e: f"{etype}{deleted_tag} {suffix}")
        return builder(edit)

    def rebuild_pending_list(self) -> None:
        """Reconstruct the pending edit list widget cleanly from _user_pending."""
        self.tab._pending_list.clear()
        for edit in self.tab._user_pending:
            self.tab._pending_list.addItem(self._format_edit_label(edit))
        self.tab._canvas.set_overlays(self.pending)
        self._notify_toolbar()

    def _trim_overflow(self) -> None:
        if len(self.pending) > _MAX_PENDING:
            to_drop = self.pending[:-_MAX_PENDING]
            self.pending[:] = self.pending[-_MAX_PENDING:]
            tmp_root = os.path.normcase(tempfile.gettempdir())
            for old in to_drop:
                with contextlib.suppress(Exception):
                    p = old.get("path")
                    if p and os.path.isfile(p) and os.path.normcase(p).startswith(tmp_root):
                        os.unlink(p)

    def _push_undo_action(self, action: dict) -> None:
        self._undo_stack.append(action)
        if len(self._undo_stack) > _MAX_REDO:
            self._undo_stack.pop(0)

    def add(self, edit: dict, *, _from_redo: bool = False) -> None:
        if not _from_redo:
            self.redo_stack.clear()
        if edit not in self.pending:
            self.pending.append(edit)
        self._trim_overflow()
        self._push_undo_action({"kind": "add", "edit": edit})
        lbl = self._format_edit_label(edit)
        self.rebuild_pending_list()
        self.tab._status(t("edit.status.added", label=lbl, count=len(self.tab._user_pending)))

    def handle_overlay_committed(self, edit: dict) -> None:
        """Called when an untouched _existing object is moved, resized, or edited for the first time."""
        self.redo_stack.clear()
        if edit not in self.pending:
            self.pending.append(edit)
        self._push_undo_action({
            "kind": "modify_existing",
            "edit": edit,
            "after": snapshot_overlay(edit),
        })
        lbl = self._format_edit_label(edit)
        self.rebuild_pending_list()
        self.tab._status(t("edit.status.added", label=lbl, count=len(self.tab._user_pending)))

    def handle_overlay_transformed(self, edit: dict, before_snap: dict) -> None:
        """Called when an existing/new overlay is moved, resized, or edited again."""
        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "transform",
            "edit": edit,
            "before": before_snap,
            "after": snapshot_overlay(edit),
        })
        self.rebuild_pending_list()
        self.tab._status("Updated object (Ctrl+Z to undo)")

    def handle_overlay_duplicated(self, new_edit: dict) -> None:
        self.add(new_edit)

    def handle_overlay_deleted(self, idx: int, edit: dict) -> None:
        self.redo_stack.clear()
        if edit.get("_existing"):
            was_dirty = any(act.get("edit") is edit for act in self._undo_stack)
            edit["_deleted"] = True
            edit["_dirty"] = True
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action({
                "kind": "delete_existing",
                "edit": edit,
                "was_dirty": was_dirty,
            })
        else:
            if edit in self.pending:
                self.pending.remove(edit)
            self._push_undo_action({
                "kind": "delete_new",
                "edit": edit,
                "index": idx,
            })
        self.rebuild_pending_list()
        self.tab._status("Object deleted (Ctrl+Z to undo)")

    def handle_note_deleted(self, overlay: dict) -> None:
        text = overlay.get("text", "").strip()
        page = overlay.get("page")
        self.redo_stack.clear()
        for i, p in enumerate(self.pending):
            if p.get("type") == "note" and p.get("text", "").strip() == text and p.get("page") == page:
                removed = self.pending.pop(i)
                if removed.get("_existing"):
                    edit = {
                        "type": "delete_annot",
                        "page": removed.get("page"),
                        "annot_type": removed.get("_annot_type"),
                        "bbox": removed.get("_annot_bbox"),
                        "_existing": True,
                        "_original_note": removed,
                    }
                    self.pending.append(edit)
                    self._push_undo_action({
                        "kind": "delete_note",
                        "edit": edit,
                        "removed_note": removed,
                        "removed_idx": i,
                    })
                else:
                    self._push_undo_action({
                        "kind": "delete_new",
                        "edit": removed,
                        "index": i,
                    })
                self.rebuild_pending_list()
                return
        if overlay.get("_existing"):
            edit = {
                "type": "delete_annot",
                "page": page,
                "annot_type": overlay.get("_annot_type"),
                "bbox": overlay.get("_annot_bbox"),
                "_existing": True,
            }
            self.pending.append(edit)
            self._push_undo_action({
                "kind": "delete_note",
                "edit": edit,
                "removed_note": None,
                "removed_idx": -1,
            })
            self.rebuild_pending_list()

    def undo(self) -> None:
        if getattr(self.tab, "_mode_idx", -1) == _MODE_FORMS:
            self.tab._status(t("editor.forms.undo_unavailable"))
            return

        if not self._undo_stack:
            user_items = self.tab._user_pending
            if not user_items:
                return
            edit = user_items[-1]
            if edit in self.pending:
                self.pending.remove(edit)
            if edit.get("_deleted"):
                edit["_deleted"] = False
            self.redo_stack.append({"kind": "add", "edit": edit})
            if len(self.redo_stack) > _MAX_REDO:
                self.redo_stack.pop(0)
            self.tab._canvas._selected_overlay_idx = -1
            self.rebuild_pending_list()
            self.tab._status(t("edit.status.undo", n=len(self.tab._user_pending)))
            return

        action = self._undo_stack.pop()
        kind = action["kind"]
        edit = action["edit"]

        if kind == "add":
            if edit in self.pending:
                self.pending.remove(edit)
            if self.tab._canvas._selected_overlay_idx >= len(self.pending):
                self.tab._canvas._selected_overlay_idx = -1

        elif kind == "modify_existing":
            if edit in self.pending:
                self.pending.remove(edit)
            edit["_dirty"] = False
            self.tab._canvas._selected_overlay_idx = -1

        elif kind == "transform":
            restore_overlay_snapshot(edit, action["before"])

        elif kind == "delete_new":
            idx = max(0, min(int(action.get("index", len(self.pending))), len(self.pending)))
            self.pending.insert(idx, edit)

        elif kind == "delete_existing":
            edit["_deleted"] = False
            if not action.get("was_dirty"):
                if edit in self.pending:
                    self.pending.remove(edit)
                edit["_dirty"] = False
                self.tab._canvas._selected_overlay_idx = -1

        elif kind == "delete_note":
            if edit in self.pending:
                self.pending.remove(edit)
            orig_note = action.get("removed_note")
            if isinstance(orig_note, dict):
                idx = max(0, min(int(action.get("removed_idx", len(self.pending))), len(self.pending)))
                self.pending.insert(idx, orig_note)

        self.redo_stack.append(action)
        if len(self.redo_stack) > _MAX_REDO:
            self.redo_stack.pop(0)

        self.rebuild_pending_list()
        self.tab._status(t("edit.status.undo", n=len(self.tab._user_pending)))

    def redo(self) -> None:
        if not self.redo_stack:
            return
        entry = self.redo_stack.pop()

        if isinstance(entry, dict) and "kind" not in entry:
            self.add(entry, _from_redo=True)
            return

        kind = entry["kind"]
        edit = entry["edit"]

        if kind == "add":
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action(entry)

        elif kind == "modify_existing":
            if "after" in entry:
                restore_overlay_snapshot(edit, entry["after"])
            edit["_dirty"] = True
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action(entry)

        elif kind == "transform":
            restore_overlay_snapshot(edit, entry["after"])
            self._push_undo_action(entry)

        elif kind == "delete_new":
            if edit in self.pending:
                self.pending.remove(edit)
            self.tab._canvas._selected_overlay_idx = -1
            self._push_undo_action(entry)

        elif kind == "delete_existing":
            edit["_deleted"] = True
            edit["_dirty"] = True
            if edit not in self.pending:
                self.pending.append(edit)
            self.tab._canvas._selected_overlay_idx = -1
            self._push_undo_action(entry)

        elif kind == "delete_note":
            orig_note = entry.get("removed_note")
            if isinstance(orig_note, dict) and orig_note in self.pending:
                self.pending.remove(orig_note)
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action(entry)

        self.rebuild_pending_list()
        self.tab._status(f"Redo ({len(self.tab._user_pending)} pending)")

    def clear(self) -> None:
        self.pending.clear()
        self._undo_stack.clear()
        self.redo_stack.clear()
        self.tab._pending_list.clear()
        self.tab._canvas._selected_overlay_idx = -1
        self.tab._canvas.set_overlays(self.pending)
        self._notify_toolbar()

    def load_existing_annotations(self) -> None:
        try:
            doc = self.tab._canvas._doc
            if not doc:
                self.tab._status(t("edit.status.no_doc"))
                return
            count = 0
            total_annots = 0
            for page_idx in range(doc.page_count):
                page = doc[page_idx]
                for annot in page.annots() or []:
                    total_annots += 1
                    if annot.type[0] == _PDF_ANNOT_TEXT:
                        r = annot.rect
                        txt = annot.info.get("content", "")
                        if txt:
                            self.pending.append({
                                "type": "note",
                                "page": page_idx,
                                "point": fitz.Point(r.x0, r.y0 + r.height),
                                "text": txt,
                                "_existing": True,
                                "_existing_annot": annot.xref,
                                "_annot_type": annot.type[0],
                                "_annot_bbox": [r.x0, r.y0, r.x1, r.y1],
                                "_orig_rect": [r.x0, r.y0, r.x1, r.y1],
                                "_dirty": False,
                            })
                            count += 1
            self.tab._status(t("edit.status.note_loaded", count=count, total=total_annots))
            self.rebuild_pending_list()
        except Exception as ex:
            self.tab._status(t("edit.status.annot_error", ex=ex))