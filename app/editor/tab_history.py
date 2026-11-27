# app/editor/tab_history.py
"""PDF_Editor – tab_history: synchronized action-based history, undo/redo (including thumbnail page ops), grouping/ungrouping, reset, and pending edits management."""

import contextlib
import os
import tempfile
import fitz

from app.i18n import t
from app.editor.canvas_overlay1 import restore_overlay_snapshot, snapshot_overlay
from app.editor.tab_constants import _MAX_PENDING, _MAX_REDO

_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


def _clone_overlay(ov: dict) -> dict:
    """Deep-clone an overlay dictionary safely preserving geometry objects."""
    cloned = dict(ov)
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
    if "runs" in cloned and cloned["runs"] is not None:
        cloned["runs"] = [dict(r) for r in cloned["runs"]]
    if "children" in cloned and cloned["children"] is not None:
        cloned["children"] = [_clone_overlay(ch) for ch in cloned["children"]]
    return cloned


class TabHistoryManager:
    """Manages pending visual edits, annotation imports, page-level document history, and action-level undo/redo stacks."""

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
        if win and hasattr(win, "_sync_edit_dirty_indicator"):
            win._sync_edit_dirty_indicator()

    def has_page_ops(self) -> bool:
        return any(act.get("kind") == "page_doc" for act in self._undo_stack)

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
            "group":        lambda e: f"{e.get('_name') or 'Picture Group'}{deleted_tag} " + suffix,
            "draw":         lambda e: t("edit.mode.draw") + deleted_tag + suffix,
            "delete_annot": lambda e: t("edit.label.note_delete") + suffix,
        }
        builder = labels.get(etype, lambda e: f"{etype}{deleted_tag} {suffix}")
        return builder(edit)

    def rebuild_pending_list(self) -> None:
        """Reconstruct the pending edit list widget cleanly from _user_pending and trigger thumbnail sync."""
        self.tab._pending_list.clear()
        for edit in self.tab._user_pending:
            self.tab._pending_list.addItem(self._format_edit_label(edit))
        self.tab._canvas.set_overlays(self.pending)
        if hasattr(self.tab, "_sync_thumbnails"):
            self.tab._sync_thumbnails()
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

    def record_page_doc_change(self, before_state: dict, after_state: dict) -> None:
        """Record a page-level operation (delete/duplicate/insert/reorder/rotate pages) for Ctrl+Z / Ctrl+Y."""
        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "page_doc",
            "before": before_state,
            "after": after_state,
            "edit": None,
        })
        self._notify_toolbar()

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

    def reset_all(self) -> bool:
        """Reset the PDF by removing all text, drawings, and pending edits without a confirmation dialog.
        Pushes state to undo stack so Ctrl+Z can restore everything."""
        if not self.pending and not self._undo_stack:
            self.tab._status("PDF is already in original state")
            return False

        before_pending = [_clone_overlay(e) for e in self.pending]
        before_undo_stack = list(self._undo_stack)
        before_selected_idx = getattr(self.tab._canvas, "_selected_overlay_idx", -1)

        if hasattr(self.tab._canvas, "_cancel_inline"):
            self.tab._canvas._cancel_inline()

        self.pending.clear()
        self.tab._canvas._selected_overlay_idx = -1
        self.tab._canvas.clear_active_selection()
        self.tab._canvas.set_overlays(self.pending)
        self.tab._canvas.refresh_pages()
        self.rebuild_pending_list()

        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "reset_all",
            "before_pending": before_pending,
            "before_undo_stack": before_undo_stack,
            "before_selected_idx": before_selected_idx,
            "edit": None,
        })
        self._notify_toolbar()
        self.tab._status("PDF reset — all text, drawings and edits deleted (Ctrl+Z to undo)")
        return True

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

    def handle_overlay_arranged(self, info: dict) -> None:
        """Called when z-order of an overlay is changed."""
        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "arrange",
            "info": info,
            "edit": info.get("edit"),
        })
        self.rebuild_pending_list()
        self.tab._status("Arranged object order (Ctrl+Z to undo)")

    def handle_overlay_ungrouped(self, info: dict) -> None:
        """Called when a 'group' overlay is split into individual objects."""
        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "ungroup",
            "info": info,
            "edit": info.get("group"),
        })
        n_ch = len(info.get("children", []))
        self.rebuild_pending_list()
        self.tab._status(f"Ungrouped {n_ch} objects (Ctrl+Z to undo)")

    def handle_overlay_grouped(self, info: dict) -> None:
        """Called when 2+ overlays are combined into a 'group' overlay."""
        self.redo_stack.clear()
        self._push_undo_action({
            "kind": "group",
            "info": info,
            "edit": info.get("group"),
        })
        n_ch = len(info.get("children", []))
        self.rebuild_pending_list()
        self.tab._status(f"Grouped {n_ch} objects (Ctrl+Z to undo)")

    def handle_overlay_duplicated(self, new_edit: dict) -> None:
        self.add(new_edit)

    def handle_overlay_deleted(self, idx: int, edit: dict) -> None:
        self.redo_stack.clear()
        if edit.get("_existing"):
            was_dirty = any(act.get("edit") is edit for act in self._undo_stack)
            edit["_deleted"] = True
            edit["_dirty"] = True
            if edit.get("type") == "group":
                for ch in edit.get("children", []):
                    if ch.get("_existing"):
                        ch["_deleted"] = True
                        ch["_dirty"] = True
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
        if getattr(self.tab, "_mode_idx", -1) == 6:  # _MODE_FORMS
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

        if kind == "page_doc":
            self.redo_stack.append(action)
            if len(self.redo_stack) > _MAX_REDO:
                self.redo_stack.pop(0)
            self.tab._apply_editor_doc_state(action["before"])
            self._notify_toolbar()
            self.tab._status("Undid page change")
            return

        if kind == "reset_all":
            self.pending[:] = [_clone_overlay(e) for e in action.get("before_pending", [])]
            self.tab._canvas._selected_overlay_idx = action.get("before_selected_idx", -1)
            self.redo_stack.append(action)
            if len(self.redo_stack) > _MAX_REDO:
                self.redo_stack.pop(0)
            self.rebuild_pending_list()
            self.tab._canvas.set_overlays(self.pending)
            self.tab._canvas.refresh_pages()
            self._notify_toolbar()
            self.tab._status("Restored all text, drawings and edits (Ctrl+Y to redo reset)")
            return

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
            if edit.get("type") == "group":
                for ch in edit.get("children", []):
                    ch["_dirty"] = False
            self.tab._canvas._selected_overlay_idx = -1

        elif kind == "transform":
            restore_overlay_snapshot(edit, action["before"])

        elif kind == "arrange":
            info = action.get("info", {})
            before_order = info.get("before_order", [])
            if before_order:
                self.pending[:] = list(before_order)
            ov = info.get("edit")
            if ov is not None:
                ov["_z_back"] = info.get("before_z_back", False)
                if info.get("was_untouched"):
                    ov["_dirty"] = False
                    if ov.get("type") == "group":
                        for ch in ov.get("children", []):
                            ch["_dirty"] = False

        elif kind == "ungroup":
            info = action.get("info", {})
            for ch, snap in zip(info.get("children", []), info.get("child_before_snaps", [])):
                restore_overlay_snapshot(ch, snap)
            grp = info.get("group")
            if grp is not None and info.get("group_snap") is not None:
                restore_overlay_snapshot(grp, info["group_snap"])
            before_order = info.get("before_order", [])
            if before_order:
                self.pending[:] = list(before_order)
            if grp is not None and grp in self.pending:
                self.tab._canvas._selected_overlay_idx = self.pending.index(grp)

        elif kind == "group":
            info = action.get("info", {})
            before_order = info.get("before_order", [])
            if before_order:
                self.pending[:] = list(before_order)
            self.tab._canvas._selected_overlay_idx = -1

        elif kind == "delete_new":
            idx = max(0, min(int(action.get("index", len(self.pending))), len(self.pending)))
            self.pending.insert(idx, edit)

        elif kind == "delete_existing":
            edit["_deleted"] = False
            if edit.get("type") == "group":
                for ch in edit.get("children", []):
                    ch["_deleted"] = False
                    if not action.get("was_dirty"):
                        ch["_dirty"] = False
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
        if isinstance(edit, dict) and hasattr(self.tab._canvas, "refresh_pages"):
            self.tab._canvas.refresh_pages({edit.get("page", 0)})
        if hasattr(self.tab, "_on_canvas_overlay_changed"):
            self.tab._on_canvas_overlay_changed()
        self.tab._status(t("edit.status.undo", n=len(self.tab._user_pending)))

    def redo(self) -> None:
        if not self.redo_stack:
            return
        entry = self.redo_stack.pop()

        if isinstance(entry, dict) and "kind" not in entry:
            self.add(entry, _from_redo=True)
            return

        kind = entry["kind"]

        if kind == "page_doc":
            self._push_undo_action(entry)
            self.tab._apply_editor_doc_state(entry["after"])
            self._notify_toolbar()
            self.tab._status("Redid page change")
            return

        if kind == "reset_all":
            self._push_undo_action(entry)
            if hasattr(self.tab._canvas, "_cancel_inline"):
                self.tab._canvas._cancel_inline()
            self.pending.clear()
            self.tab._canvas._selected_overlay_idx = -1
            self.tab._canvas.clear_active_selection()
            self.tab._canvas.set_overlays(self.pending)
            self.tab._canvas.refresh_pages()
            self.rebuild_pending_list()
            self._notify_toolbar()
            self.tab._status("PDF reset — all text, drawings and edits deleted (Ctrl+Z to undo)")
            return

        edit = entry["edit"]

        if kind == "add":
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action(entry)

        elif kind == "modify_existing":
            if "after" in entry:
                restore_overlay_snapshot(edit, entry["after"])
            edit["_dirty"] = True
            if edit.get("type") == "group":
                for ch in edit.get("children", []):
                    if ch.get("_existing"):
                        ch["_dirty"] = True
            if edit not in self.pending:
                self.pending.append(edit)
            self._push_undo_action(entry)

        elif kind == "transform":
            restore_overlay_snapshot(edit, entry["after"])
            self._push_undo_action(entry)

        elif kind == "arrange":
            info = entry.get("info", {})
            after_order = info.get("after_order", [])
            if after_order:
                self.pending[:] = list(after_order)
            ov = info.get("edit")
            if ov is not None:
                ov["_z_back"] = info.get("after_z_back", False)
                ov["_dirty"] = True
                if ov.get("type") == "group":
                    for ch in ov.get("children", []):
                        if ch.get("_existing"):
                            ch["_dirty"] = True
            self._push_undo_action(entry)

        elif kind == "ungroup":
            info = entry.get("info", {})
            for ch, snap in zip(info.get("children", []), info.get("child_after_snaps", [])):
                restore_overlay_snapshot(ch, snap)
            after_order = info.get("after_order", [])
            if after_order:
                self.pending[:] = list(after_order)
            children = info.get("children", [])
            if children and children[0] in self.pending:
                self.tab._canvas._selected_overlay_idx = self.pending.index(children[0])
            self._push_undo_action(entry)

        elif kind == "group":
            info = entry.get("info", {})
            after_order = info.get("after_order", [])
            if after_order:
                self.pending[:] = list(after_order)
            grp = info.get("group")
            if grp is not None and grp in self.pending:
                self.tab._canvas._selected_overlay_idx = self.pending.index(grp)
            self._push_undo_action(entry)

        elif kind == "delete_new":
            if edit in self.pending:
                self.pending.remove(edit)
            self.tab._canvas._selected_overlay_idx = -1
            self._push_undo_action(entry)

        elif kind == "delete_existing":
            edit["_deleted"] = True
            edit["_dirty"] = True
            if edit.get("type") == "group":
                for ch in edit.get("children", []):
                    if ch.get("_existing"):
                        ch["_deleted"] = True
                        ch["_dirty"] = True
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
        if isinstance(edit, dict) and hasattr(self.tab._canvas, "refresh_pages"):
            self.tab._canvas.refresh_pages({edit.get("page", 0)})
        if hasattr(self.tab, "_on_canvas_overlay_changed"):
            self.tab._on_canvas_overlay_changed()
        self.tab._status(f"Redo ({len(self.tab._user_pending)} pending)")

    def clear(self) -> None:
        self.pending.clear()
        self._undo_stack.clear()
        self.redo_stack.clear()
        self.tab._pending_list.clear()
        self.tab._canvas._selected_overlay_idx = -1
        self.tab._canvas.set_overlays(self.pending)
        if hasattr(self.tab, "_sync_thumbnails"):
            self.tab._sync_thumbnails()
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

