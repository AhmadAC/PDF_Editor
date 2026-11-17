# app/tools/merge.py
"""PDF_Editor – TabJuntar: merge PDFs with page count listing, nested page-level insertion points, and live merge preview."""

import contextlib
import os
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from pypdf import PdfReader, PdfWriter

from app.base import BasePage
from app.constants import ACCENT, TEXT_PRI, TEXT_SEC, _LQ
from app.i18n import t
from app.pdf_io import atomic_pdf_write
from app.pdf_password import decrypt_pypdf
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CLOSE,
    SVG_MINUS,
    WrongPasswordError,
    danger_btn,
    pick_pdfs,
    section,
    show_error,
    svg_icon,
)
from app.widgets import DropFileEdit, FocusComboBox, FocusSpinBox, MultiDropWidget


_POS_MODES = [
    ("after_page", "After page N"),
    ("before_page", "Before page N"),
    ("before_from_end", "Before Nth-to-last page (e.g. 2 = 2nd last)"),
    ("after_from_end", "After Nth-to-last page (e.g. 2 = after 2nd last)"),
    ("start", "At beginning (before page 1)"),
    ("end", "At end (after last page)"),
]


class _MergeListWidget(QListWidget):
    """List widget emitting order_changed on internal drag-and-drop or Delete key."""

    order_changed = Signal()
    delete_requested = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _compute_slot(mode: str, val: int, target_pages: int) -> int:
    """Return 0-based insertion slot (0 .. target_pages) inside a target PDF."""
    n = max(0, int(target_pages))
    v = max(1, int(val))
    if mode == "start":
        return 0
    if mode == "end":
        return n
    if mode == "after_page":
        return max(0, min(n, v))
    if mode == "before_page":
        return max(0, min(n, v - 1))
    if mode == "before_from_end":
        return max(0, min(n, n - v))
    if mode == "after_from_end":
        return max(0, min(n, n - v + 1))
    return n


def _format_rule_short(entry: dict, target_entry: dict | None) -> str:
    if target_entry is None:
        return ""
    tname = os.path.basename(target_entry["path"])
    tpages = max(1, int(target_entry.get("pages", 1)))
    mode = entry.get("pos_mode", "end")
    val = max(1, int(entry.get("pos_val", 1)))
    slot = _compute_slot(mode, val, tpages)
    if mode == "start":
        return f"-> start of {tname}"
    if mode == "end":
        return f"-> end of {tname}"
    if mode == "after_page":
        return f"-> after p.{min(val, tpages)} of {tname}"
    if mode == "before_page":
        return f"-> before p.{min(val, tpages)} of {tname}"
    if mode == "before_from_end":
        eff_p = min(tpages, slot + 1)
        return f"-> before p.{eff_p} ({val} from end) of {tname}"
    if mode == "after_from_end":
        eff_p = max(1, min(tpages, slot))
        return f"-> after p.{eff_p} ({val} from end) of {tname}"
    return f"-> {tname}"


class TabJuntar(BasePage):
    def __init__(self, status_fn):
        super().__init__(
            "fa5s.object-group",
            t("tool.merge.name"),
            t("tool.merge.desc"),
            t("tool.merge.btn"),
            status_fn,
        )
        self._pwd_map: dict[str, str] = {}
        self._entries: dict[int, dict] = {}
        self._next_entry_id: int = 1
        self._updating_controls: bool = False

        f = self._form

        grp = QGroupBox(t("tool.merge.list"))
        vl = QVBoxLayout(grp)
        vl.setSpacing(8)
        self.drop_multi = MultiDropWidget(self._on_drop)
        self.drop_multi.btn.clicked.connect(self._add_files)
        vl.addWidget(self.drop_multi)

        self.lst = _MergeListWidget()
        self.lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lst.setAlternatingRowColors(True)
        self.lst.setMinimumHeight(150)
        self.lst.setMinimumWidth(0)
        self.lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.lst.currentRowChanged.connect(self._on_selection_changed)
        self.lst.order_changed.connect(self._on_list_reordered)
        self.lst.delete_requested.connect(self._remove)
        vl.addWidget(self.lst)

        hb = QHBoxLayout()
        self._btn_up = QPushButton()
        self._btn_up.setIcon(svg_icon(SVG_ARROW_UP, color=TEXT_PRI, size=14))
        self._btn_up.setIconSize(QSize(14, 14))
        self._btn_up.setToolTip(t("btn.up"))
        self._btn_up.setFixedWidth(40)
        self._btn_up.clicked.connect(self._up)

        self._btn_dn = QPushButton()
        self._btn_dn.setIcon(svg_icon(SVG_ARROW_DOWN, color=TEXT_PRI, size=14))
        self._btn_dn.setIconSize(QSize(14, 14))
        self._btn_dn.setToolTip(t("btn.down"))
        self._btn_dn.setFixedWidth(40)
        self._btn_dn.clicked.connect(self._dn)

        self._btn_rem = danger_btn("")
        self._btn_rem.setIcon(svg_icon(SVG_MINUS, color="#FCA5A5", size=14))
        self._btn_rem.setIconSize(QSize(14, 14))
        self._btn_rem.setToolTip(t("btn.remove"))
        self._btn_rem.setFixedWidth(40)
        self._btn_rem.clicked.connect(self._remove)

        self._btn_clr = QPushButton()
        self._btn_clr.setIcon(svg_icon(SVG_CLOSE, color=TEXT_PRI, size=14))
        self._btn_clr.setIconSize(QSize(14, 14))
        self._btn_clr.setToolTip(t("btn.clear"))
        self._btn_clr.setFixedWidth(40)
        self._btn_clr.clicked.connect(self._clear_all)

        for btn in (self._btn_up, self._btn_dn, self._btn_rem, self._btn_clr):
            hb.addWidget(btn)
        hb.addStretch()
        vl.addLayout(hb)
        f.addWidget(grp)

        # Placement / Insertion Rules Card for the selected PDF
        self._grp_place = QGroupBox("Insertion Position (Selected PDF)")
        pl_form = QFormLayout(self._grp_place)
        pl_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        pl_form.setSpacing(6)

        self._lbl_selected_pdf = QLabel("No PDF selected")
        self._lbl_selected_pdf.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
        self._lbl_selected_pdf.setWordWrap(True)
        pl_form.addRow("Selected:", self._lbl_selected_pdf)

        self._cmb_target = FocusComboBox()
        self._cmb_target.currentIndexChanged.connect(self._on_placement_changed)
        pl_form.addRow("Merge into:", self._cmb_target)

        self._cmb_pos = FocusComboBox()
        for code, label in _POS_MODES:
            self._cmb_pos.addItem(label, code)
        self._cmb_pos.currentIndexChanged.connect(self._on_placement_changed)
        pl_form.addRow("Position:", self._cmb_pos)

        val_row = QHBoxLayout()
        val_row.setContentsMargins(0, 0, 0, 0)
        val_row.setSpacing(6)
        self._spin_pos = FocusSpinBox()
        self._spin_pos.setRange(1, 99999)
        self._spin_pos.setValue(1)
        self._spin_pos.setFixedWidth(78)
        self._spin_pos.valueChanged.connect(self._on_placement_changed)
        val_row.addWidget(self._spin_pos)

        self._lbl_pos_explain = QLabel("")
        self._lbl_pos_explain.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9.5pt;")
        self._lbl_pos_explain.setWordWrap(True)
        val_row.addWidget(self._lbl_pos_explain, 1)
        pl_form.addRow("Page / N:", val_row)

        f.addWidget(self._grp_place)

        # Live Merge Order Preview Card
        self._grp_preview = QGroupBox("Merge Plan Preview")
        pv_lay = QVBoxLayout(self._grp_preview)
        pv_lay.setContentsMargins(10, 8, 10, 8)
        self._lbl_plan_preview = QLabel("Add 2 or more PDFs to see the exact merged page sequence.")
        self._lbl_plan_preview.setWordWrap(True)
        self._lbl_plan_preview.setStyleSheet(f"color: {TEXT_SEC}; font-size: 9.5pt;")
        pv_lay.addWidget(self._lbl_plan_preview)
        f.addWidget(self._grp_preview)

        f.addWidget(section(t("tool.merge.output")))
        self.drop_out = DropFileEdit(save=True, default_name="merged.pdf")
        f.addWidget(self.drop_out)

        btn_merge_row = QHBoxLayout()
        btn_merge_row.setContentsMargins(0, 10, 0, 0)
        btn_merge_row.addStretch()
        self.btn_merge = QPushButton(t("tool.merge.btn"))
        self.btn_merge.setObjectName("btn_action_small")
        self.btn_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_merge.setFixedHeight(32)
        self.btn_merge.setMinimumWidth(110)
        self.btn_merge.clicked.connect(self._run)
        btn_merge_row.addWidget(self.btn_merge)
        f.addLayout(btn_merge_row)
        self.action_btn = self.btn_merge

        f.addStretch()
        self._action_bar.setVisible(False)
        self._refresh_placement_controls()

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        pri = TEXT_PRI if dark else _LQ
        sec = TEXT_SEC if dark else _LQ
        self._btn_up.setIcon(svg_icon(SVG_ARROW_UP, color=pri, size=14))
        self._btn_dn.setIcon(svg_icon(SVG_ARROW_DOWN, color=pri, size=14))
        self._btn_rem.setIcon(svg_icon(SVG_MINUS, color="#FCA5A5" if dark else "#DC2626", size=14))
        self._btn_clr.setIcon(svg_icon(SVG_CLOSE, color=pri, size=14))
        self._lbl_pos_explain.setStyleSheet(f"color: {sec}; font-size: 9.5pt;")
        self._update_plan_preview()

    def _ordered_entry_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            if item is not None:
                eid = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(eid, int) and eid in self._entries:
                    ids.append(eid)
        return ids

    def _current_entry(self) -> dict | None:
        item = self.lst.currentItem()
        if item is None:
            return None
        eid = item.data(Qt.ItemDataRole.UserRole)
        return self._entries.get(eid) if isinstance(eid, int) else None

    def _read_page_count(self, path: str) -> int:
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                pwd = self._pwd_map.get(path, "")
                if pwd:
                    decrypt_pypdf(reader, pwd)
            return max(0, len(reader.pages))
        except Exception:
            return 0

    def _refresh_list_labels(self) -> None:
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            if item is None:
                continue
            eid = item.data(Qt.ItemDataRole.UserRole)
            entry = self._entries.get(eid)
            if not entry:
                continue
            fname = os.path.basename(entry["path"])
            pages = entry.get("pages", 0)
            pg_lbl = f"{pages} page" if pages == 1 else f"{pages} pages"
            target_id = entry.get("target_id")
            target_entry = self._entries.get(target_id) if target_id is not None else None
            rule_str = _format_rule_short(entry, target_entry)
            if rule_str:
                item.setText(f"{fname}  ({pg_lbl})  {rule_str}")
                item.setToolTip(f"{entry['path']}\n{pg_lbl}\n{rule_str}")
            else:
                item.setText(f"{fname}  ({pg_lbl})")
                item.setToolTip(f"{entry['path']}\n{pg_lbl}")

    def _refresh_placement_controls(self) -> None:
        self._updating_controls = True
        try:
            entry = self._current_entry()
            if entry is None:
                self._lbl_selected_pdf.setText("Select a PDF in the list above")
                self._cmb_target.clear()
                self._cmb_target.setEnabled(False)
                self._cmb_pos.setEnabled(False)
                self._spin_pos.setEnabled(False)
                self._lbl_pos_explain.setText("")
                return

            pages = entry.get("pages", 0)
            pg_lbl = f"{pages} page" if pages == 1 else f"{pages} pages"
            self._lbl_selected_pdf.setText(f"{os.path.basename(entry['path'])} ({pg_lbl})")
            self._cmb_target.setEnabled(True)

            self._cmb_target.clear()
            self._cmb_target.addItem("Append sequentially (Top-level)", None)
            ordered_ids = self._ordered_entry_ids()
            selected_idx = 0
            cur_target = entry.get("target_id")
            for eid in ordered_ids:
                if eid == entry["id"]:
                    continue
                other = self._entries[eid]
                o_pages = other.get("pages", 0)
                o_lbl = f"{o_pages}p"
                self._cmb_target.addItem(f"Insert into: {os.path.basename(other['path'])} ({o_lbl})", eid)
                if eid == cur_target:
                    selected_idx = self._cmb_target.count() - 1
            self._cmb_target.setCurrentIndex(selected_idx)

            target_id = self._cmb_target.currentData()
            target_entry = self._entries.get(target_id) if target_id is not None else None
            has_target = target_entry is not None
            self._cmb_pos.setEnabled(has_target)

            mode = entry.get("pos_mode", "after_page")
            mode_idx = 0
            for idx, (code, _) in enumerate(_POS_MODES):
                if code == mode:
                    mode_idx = idx
                    break
            self._cmb_pos.setCurrentIndex(mode_idx)

            if has_target and target_entry is not None:
                t_pages = max(1, int(target_entry.get("pages", 1)))
                self._spin_pos.setRange(1, t_pages)
                val = max(1, min(t_pages, int(entry.get("pos_val", 1))))
                self._spin_pos.setValue(val)
                uses_num = mode in ("after_page", "before_page", "before_from_end", "after_from_end")
                self._spin_pos.setEnabled(uses_num)
                self._lbl_pos_explain.setText(self._build_explain_text(entry, target_entry))
            else:
                self._spin_pos.setRange(1, 99999)
                self._spin_pos.setEnabled(False)
                self._lbl_pos_explain.setText("Included at top level in list order")
        finally:
            self._updating_controls = False

    @staticmethod
    def _build_explain_text(entry: dict, target_entry: dict) -> str:
        src_name = os.path.basename(entry["path"])
        t_name = os.path.basename(target_entry["path"])
        t_pages = max(1, int(target_entry.get("pages", 1)))
        mode = entry.get("pos_mode", "after_page")
        val = max(1, min(t_pages, int(entry.get("pos_val", 1))))
        slot = _compute_slot(mode, val, t_pages)
        if mode == "start":
            return f"Before page 1 of {t_name}"
        if mode == "end":
            return f"After page {t_pages} of {t_name}"
        if slot == 0:
            return f"Before page 1 of {t_name}"
        if slot >= t_pages:
            return f"After page {t_pages} of {t_name}"
        return f"Between page {slot} and {slot + 1} of {t_name} ({src_name})"

    def _on_selection_changed(self, _row: int) -> None:
        self._refresh_placement_controls()

    def _on_list_reordered(self) -> None:
        self._refresh_list_labels()
        self._refresh_placement_controls()
        self._update_plan_preview()

    def _on_placement_changed(self, *_args) -> None:
        if self._updating_controls:
            return
        entry = self._current_entry()
        if entry is None:
            return

        target_id = self._cmb_target.currentData()
        if target_id == entry["id"]:
            target_id = None

        entry["target_id"] = target_id
        mode = self._cmb_pos.currentData() or "after_page"
        entry["pos_mode"] = mode

        if target_id is not None and target_id in self._entries:
            t_pages = max(1, int(self._entries[target_id].get("pages", 1)))
            entry["pos_val"] = max(1, min(t_pages, self._spin_pos.value()))
        else:
            entry["pos_val"] = self._spin_pos.value()

        self._refresh_list_labels()
        self._refresh_placement_controls()
        self._update_plan_preview()

    def _build_merge_plan(self) -> tuple[list[tuple[int, int]], str]:
        """Resolve all nested/sequential insertions into a flat list of (entry_id, 0-based page_idx).
        Returns (plan, error_message)."""
        ordered_ids = self._ordered_entry_ids()
        if not ordered_ids:
            return [], ""

        # Detect circular target references
        for eid in ordered_ids:
            seen: set[int] = set()
            cur: int | None = eid
            while cur is not None:
                if cur in seen:
                    names = " <-> ".join(
                        os.path.basename(self._entries[x]["path"]) for x in seen if x in self._entries
                    )
                    return [], f"Circular insertion detected ({names}). Set one PDF to 'Append sequentially'."
                seen.add(cur)
                nxt = self._entries[cur].get("target_id") if cur in self._entries else None
                cur = nxt if nxt in self._entries else None

        children_map: dict[int, list[int]] = {eid: [] for eid in ordered_ids}
        roots: list[int] = []
        for eid in ordered_ids:
            tid = self._entries[eid].get("target_id")
            if tid is not None and tid in self._entries and tid != eid:
                children_map[tid].append(eid)
            else:
                roots.append(eid)

        def _expand(eid: int) -> list[tuple[int, int]]:
            ent = self._entries[eid]
            n_pages = max(0, int(ent.get("pages", 0)))
            slots: dict[int, list[int]] = {}
            for child_id in children_map.get(eid, []):
                c_ent = self._entries[child_id]
                slot = _compute_slot(
                    c_ent.get("pos_mode", "end"),
                    c_ent.get("pos_val", 1),
                    n_pages,
                )
                slots.setdefault(slot, []).append(child_id)

            out: list[tuple[int, int]] = []
            for child_id in slots.get(0, []):
                out.extend(_expand(child_id))
            for p_idx in range(n_pages):
                out.append((eid, p_idx))
                for child_id in slots.get(p_idx + 1, []):
                    out.extend(_expand(child_id))
            return out

        plan: list[tuple[int, int]] = []
        for r_id in roots:
            plan.extend(_expand(r_id))
        return plan, ""

    def _update_plan_preview(self) -> None:
        ordered_ids = self._ordered_entry_ids()
        if not ordered_ids:
            self._lbl_plan_preview.setText("Add 2 or more PDFs to see the exact merged page sequence.")
            return

        plan, err = self._build_merge_plan()
        if err:
            self._lbl_plan_preview.setText(err)
            self._lbl_plan_preview.setStyleSheet("color: #EF4444; font-size: 9.5pt; font-weight: 600;")
            return

        if not plan:
            self._lbl_plan_preview.setText("0 pages in merge plan.")
            return

        # Compress consecutive runs from the same entry into readable ranges
        runs: list[tuple[int, int, int]] = []
        for eid, p_idx in plan:
            if runs and runs[-1][0] == eid and runs[-1][2] + 1 == p_idx:
                runs[-1] = (eid, runs[-1][1], p_idx)
            else:
                runs.append((eid, p_idx, p_idx))

        parts: list[str] = []
        for eid, p_start, p_end in runs:
            name = os.path.basename(self._entries[eid]["path"])
            if p_start == p_end:
                parts.append(f"{name} (p.{p_start + 1})")
            else:
                parts.append(f"{name} (p.{p_start + 1}-{p_end + 1})")

        summary = "  ->  ".join(parts)
        sec = TEXT_SEC if getattr(self.window(), "_dark_mode", True) else _LQ
        self._lbl_plan_preview.setStyleSheet(f"color: {sec}; font-size: 9.5pt;")
        self._lbl_plan_preview.setText(f"Total: {len(plan)} pages\n{summary}")

    def _add_path(self, p: str) -> bool:
        """Prompt for password if encrypted, read page count, store entry, append to list."""
        if not self._maybe_prompt_password(p):
            return False
        self._pwd_map[p] = self._pdf_password
        pages = self._read_page_count(p)

        eid = self._next_entry_id
        self._next_entry_id += 1
        self._entries[eid] = {
            "id": eid,
            "path": p,
            "pages": pages,
            "target_id": None,
            "pos_mode": "after_page",
            "pos_val": 1,
        }

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, eid)
        self.lst.addItem(item)
        self._refresh_list_labels()
        self.lst.setCurrentItem(item)
        self._refresh_placement_controls()
        self._update_plan_preview()

        if not self.drop_out.path():
            base, ext = os.path.splitext(p)
            self.drop_out.set_path(os.path.normpath(base + "_merged" + ext))
        return True

    def _on_drop(self, paths: list):
        for p in paths:
            self._add_path(p)

    def _add_files(self):
        for p in pick_pdfs(self):
            self._add_path(p)

    def _up(self):
        r = self.lst.currentRow()
        if r > 0:
            item = self.lst.takeItem(r)
            self.lst.insertItem(r - 1, item)
            self.lst.setCurrentRow(r - 1)
            self._on_list_reordered()

    def _dn(self):
        r = self.lst.currentRow()
        if 0 <= r < self.lst.count() - 1:
            item = self.lst.takeItem(r)
            self.lst.insertItem(r + 1, item)
            self.lst.setCurrentRow(r + 1)
            self._on_list_reordered()

    def _remove(self):
        r = self.lst.currentRow()
        if r >= 0:
            item = self.lst.takeItem(r)
            eid = item.data(Qt.ItemDataRole.UserRole)
            removed = self._entries.pop(eid, None) if isinstance(eid, int) else None
            if removed:
                rem_path = removed["path"]
                if not any(e["path"] == rem_path for e in self._entries.values()):
                    self._pwd_map.pop(rem_path, None)
                for other in self._entries.values():
                    if other.get("target_id") == eid:
                        other["target_id"] = None
            if self.lst.count() > 0:
                self.lst.setCurrentRow(min(r, self.lst.count() - 1))
            self._refresh_list_labels()
            self._refresh_placement_controls()
            self._update_plan_preview()

    def _clear_all(self):
        self.lst.clear()
        self._entries.clear()
        self._pwd_map.clear()
        self._refresh_placement_controls()
        self._update_plan_preview()

    def auto_load(self, path: str):
        if not path:
            return
        existing = [e["path"] for e in self._entries.values()]
        if path not in existing:
            self._add_path(path)

    def _run(self):
        ordered_ids = self._ordered_entry_ids()
        if len(ordered_ids) < 2:
            QMessageBox.warning(self, t("msg.warning"), t("tool.merge.min2"))
            return

        paths = [self._entries[eid]["path"] for eid in ordered_ids]
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            QMessageBox.critical(
                self,
                t("msg.error"),
                t("tool.merge.missing_files", files="\n".join(missing)),
            )
            return

        plan, err = self._build_merge_plan()
        if err:
            QMessageBox.warning(self, t("msg.warning"), err)
            return
        if not plan:
            QMessageBox.warning(self, t("msg.warning"), t("tool.merge.min2"))
            return

        out_path = self.drop_out.path()
        if not out_path:
            default_name = "merged.pdf"
            if paths:
                base, ext = os.path.splitext(os.path.basename(paths[0]))
                default_name = f"{base}_merged{ext}"
            start_dir = os.path.dirname(paths[0]) if paths else ""
            out_path = self._prompt_save_as(default_name, start_dir)
            if not out_path:
                return
            self.drop_out.set_path(out_path)
        out_path = os.path.normpath(out_path)

        win: Any = self.window()
        viewer = getattr(win, "_viewer", None)

        try:
            readers: dict[int, PdfReader] = {}
            for eid in ordered_ids:
                p = self._entries[eid]["path"]
                reader = PdfReader(p)
                if reader.is_encrypted:
                    pwd = self._pwd_map.get(p, "")
                    if pwd and decrypt_pypdf(reader, pwd) is None:
                        raise WrongPasswordError(t("tool.err.wrong_password"))
                readers[eid] = reader

            w = PdfWriter()
            for eid, p_idx in plan:
                reader = readers[eid]
                if 0 <= p_idx < len(reader.pages):
                    w.add_page(reader.pages[p_idx])

            if viewer and viewer.current_path() and os.path.abspath(viewer.current_path()) == os.path.abspath(out_path):
                viewer._canvas.close_doc()
                doc = getattr(viewer, "_fitz_doc", None)
                if doc is not None:
                    with contextlib.suppress(Exception):
                        if not getattr(doc, "is_closed", False):
                            doc.close()
                    viewer._fitz_doc = None
                viewer._thumbnails._stop_all_workers()

            atomic_pdf_write(w, out_path, sources=paths)

            self._status(t("tool.merge.status.done", name=os.path.basename(out_path)))
            msg = t("tool.merge.done", path=out_path)

            cleanup_fn = getattr(win, "_cleanup_pipeline", None)
            if callable(cleanup_fn) and viewer:
                cleanup_fn(id(viewer))

            if viewer:
                viewer.load(out_path)

            QMessageBox.information(self, t("msg.done"), msg)
        except Exception as e:
            show_error(self, e)