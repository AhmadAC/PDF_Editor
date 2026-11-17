# app/tools/page_numbers.py
"""PDF_Editor – TabPageNumbers: add page numbers to a PDF with font selection, live preview, JSON preference persistence, SVG icons, and undo/redo."""

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Any, cast

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QGroupBox, QFormLayout, QHBoxLayout, QLineEdit, QFileDialog, QMessageBox,
    QPushButton,
)

from app.base import BasePage
from app.pdf_io import atomic_pdf_write
from app.i18n import t
from app.utils import (
    SVG_BOLD,
    SVG_ITALIC,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    section,
    info_lbl,
    parse_pages,
    show_error,
    svg_icon,
    WrongPasswordError,
)
from app.constants import DESKTOP, TEXT_PRI, _LQ
from app.widgets import DropFileEdit, FocusComboBox, FocusSpinBox

_log = logging.getLogger(__name__)

_POSITIONS = [
    ("tool.page_numbers.pos.top_left",      "tl"),
    ("tool.page_numbers.pos.top_center",    "tc"),
    ("tool.page_numbers.pos.top_right",     "tr"),
    ("tool.page_numbers.pos.bottom_left",   "bl"),
    ("tool.page_numbers.pos.bottom_center", "bc"),
    ("tool.page_numbers.pos.bottom_right",  "br"),
]

_FORMATS = [
    ("tool.page_numbers.fmt.simple",     "tool.page_numbers.template.simple"),
    ("tool.page_numbers.fmt.slash",      "tool.page_numbers.template.slash"),
    ("tool.page_numbers.fmt.page",       "tool.page_numbers.template.page"),
    ("tool.page_numbers.fmt.page_of",    "tool.page_numbers.template.page_of"),
]

_STANDARD_FONTS = [
    "Helvetica",
    "Arial",
    "Times New Roman",
    "Courier New",
    "Calibri",
    "Georgia",
    "Segoe UI",
    "Trebuchet MS",
    "Verdana",
    "Comic Sans MS",
]


def _page_numbers_config_file() -> str:
    """Resolve destination path for page_numbers_settings.json."""
    if sys.platform in ("win32", "darwin"):
        return os.path.normpath(
            os.path.join(os.path.expanduser("~"), ".PDF_Editor_page_numbers_settings.json")
        )
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(xdg, "PDF_Editor")
    os.makedirs(d, exist_ok=True)
    return os.path.normpath(os.path.join(d, "page_numbers_settings.json"))


def _find_system_font_file(family: str, bold: bool = False, italic: bool = False) -> str | None:
    """Locate a .ttf or .otf file on disk for the requested font family and style."""
    if not family:
        return None
    fam_lower = family.strip().lower()

    # Standard PDF Base-14 Helvetica uses built-in helv/hebo/heit/hebi
    if fam_lower == "helvetica":
        return None

    if sys.platform == "win32":
        try:
            import winreg
            win_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
            local_fonts_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")

            candidates: list[tuple[int, str]] = []
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            ]
            for hive, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        i = 0
                        while True:
                            try:
                                name, val, _ = winreg.EnumValue(key, i)
                                i += 1
                            except OSError:
                                break
                            if not isinstance(val, str):
                                continue
                            ext = os.path.splitext(val)[1].lower()
                            if ext not in (".ttf", ".otf"):
                                continue
                            name_clean = re.sub(
                                r"\s*\((TrueType|OpenType)\)\s*$", "", name, flags=re.IGNORECASE
                            ).strip()
                            nl = name_clean.lower()
                            if nl != fam_lower and not nl.startswith(fam_lower + " "):
                                continue

                            has_bold = "bold" in nl
                            has_italic = "italic" in nl or "oblique" in nl
                            extra_penalty = 0
                            for mod in ("narrow", "condensed", "black", "light", "semibold", "thin", "medium"):
                                if mod in nl and mod not in fam_lower:
                                    extra_penalty += 20

                            score = 0
                            if nl == fam_lower and not bold and not italic:
                                score += 100
                            score += 30 if has_bold == bold else -30
                            score += 30 if has_italic == italic else -30
                            score -= extra_penalty

                            full_p = val if os.path.isabs(val) else os.path.join(win_fonts_dir, val)
                            if not os.path.isfile(full_p) and local_fonts_dir:
                                alt_p = os.path.join(local_fonts_dir, val)
                                if os.path.isfile(alt_p):
                                    full_p = alt_p
                            if os.path.isfile(full_p):
                                candidates.append((score, full_p))
                except OSError:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]
        except Exception:
            pass
    else:
        fc_bin = shutil.which("fc-match")
        if fc_bin:
            try:
                pattern = family
                if bold:
                    pattern += ":bold"
                if italic:
                    pattern += ":italic"
                res = subprocess.run(
                    [fc_bin, "-f", "%{file}", pattern],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    p = res.stdout.strip()
                    if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in (".ttf", ".otf"):
                        return p
            except Exception:
                pass

    return None


def _get_fitz_font_args(fitz_mod, family: str, bold: bool, italic: bool):
    """Return (fontname, fontfile, fitz.Font) for exact text placement and embedding."""
    ttf_path = _find_system_font_file(family, bold, italic)
    if ttf_path:
        try:
            f_obj = fitz_mod.Font(fontfile=ttf_path)
            if f_obj:
                return "pn_custom_font", ttf_path, f_obj
        except Exception:
            pass

    fn_l = (family or "Helvetica").lower()
    if any(k in fn_l for k in ("courier", "mono", "consol")):
        b14 = {
            (False, False): "cour",
            (True, False): "cobo",
            (False, True): "coit",
            (True, True): "cobi",
        }[(bold, italic)]
    elif any(k in fn_l for k in ("times", "serif", "roman", "georgia", "garamond", "cambria")):
        b14 = {
            (False, False): "tiro",
            (True, False): "tibo",
            (False, True): "tiit",
            (True, True): "tibi",
        }[(bold, italic)]
    else:
        b14 = {
            (False, False): "helv",
            (True, False): "hebo",
            (False, True): "heit",
            (True, True): "hebi",
        }[(bold, italic)]
    return b14, None, fitz_mod.Font(b14)


class TabPageNumbers(BasePage):
    numbers_preview_changed = Signal(object)

    def __init__(self, status_fn):
        super().__init__("fa5s.list-ol", t("tool.page_numbers.name"),
                         t("tool.page_numbers.desc"),
                         t("tool.page_numbers.btn"), status_fn)
        self._pipeline_supported = True
        self._page_count = 0
        self._updating = False
        self._loading_settings = False

        f = self._form

        sec_src = section(t("tool.page_numbers.source"))
        f.addWidget(sec_src)
        self.drop_in = DropFileEdit()
        try:
            self.drop_in.btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.drop_in.btn.clicked.connect(self._pick_input)
        self.drop_in.path_changed.connect(self._load_input)
        self.lbl_info = info_lbl()
        f.addWidget(self.drop_in)
        f.addWidget(self.lbl_info)

        grp = QGroupBox(t("tool.page_numbers.options"))
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cmb_format = FocusComboBox()
        for key, _ in _FORMATS:
            self.cmb_format.addItem(t(key))
        self.cmb_format.currentIndexChanged.connect(self._on_option_changed)
        form.addRow(t("tool.page_numbers.format"), self.cmb_format)

        self.cmb_position = FocusComboBox()
        for key, _ in _POSITIONS:
            self.cmb_position.addItem(t(key))
        self.cmb_position.setCurrentIndex(4)  # bottom_center
        self.cmb_position.currentIndexChanged.connect(self._on_option_changed)
        form.addRow(t("tool.page_numbers.position"), self.cmb_position)

        # Font selector
        self.cmb_font = FocusComboBox()
        self.cmb_font.setMaxVisibleItems(16)
        try:
            sys_families = [
                fam for fam in QFontDatabase.families()
                if not fam.startswith("@") and fam not in _STANDARD_FONTS
            ]
        except Exception:
            sys_families = []
        self.cmb_font.addItems(_STANDARD_FONTS + sys_families)
        self.cmb_font.currentTextChanged.connect(self._on_option_changed)
        form.addRow("Font:", self.cmb_font)

        # Font size + Bold / Italic toggles
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(6)

        self.spin_size = FocusSpinBox()
        self.spin_size.setRange(6, 48)
        self.spin_size.setValue(10)
        self.spin_size.setSuffix(" pt")
        self.spin_size.valueChanged.connect(self._on_option_changed)
        size_row.addWidget(self.spin_size, 1)

        self.btn_bold = QPushButton()
        self.btn_bold.setIcon(svg_icon(SVG_BOLD, color=TEXT_PRI, size=13))
        self.btn_bold.setIconSize(QSize(13, 13))
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(28, 28)
        self.btn_bold.setToolTip("Bold")
        self.btn_bold.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bold.toggled.connect(self._on_option_changed)
        size_row.addWidget(self.btn_bold)

        self.btn_italic = QPushButton()
        self.btn_italic.setIcon(svg_icon(SVG_ITALIC, color=TEXT_PRI, size=13))
        self.btn_italic.setIconSize(QSize(13, 13))
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(28, 28)
        self.btn_italic.setToolTip("Italic")
        self.btn_italic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_italic.toggled.connect(self._on_option_changed)
        size_row.addWidget(self.btn_italic)

        form.addRow(t("tool.page_numbers.font_size"), size_row)

        self.spin_start_page = FocusSpinBox()
        self.spin_start_page.setRange(1, 99999)
        self.spin_start_page.setValue(1)
        self.spin_start_page.valueChanged.connect(self._on_option_changed)
        form.addRow(t("tool.page_numbers.start_page"), self.spin_start_page)

        self.spin_start_number = FocusSpinBox()
        self.spin_start_number.setRange(1, 99999)
        self.spin_start_number.setValue(1)
        self.spin_start_number.valueChanged.connect(self._on_option_changed)
        form.addRow(t("tool.page_numbers.start_number"), self.spin_start_number)

        self.edit_pages = QLineEdit()
        self.edit_pages.setPlaceholderText(t("tool.page_numbers.pages_hint"))
        self.edit_pages.textChanged.connect(self._on_option_changed)
        form.addRow(t("tool.page_numbers.pages_label"), self.edit_pages)

        # Undo / Redo controls in tool panel
        undo_redo_row = QHBoxLayout()
        undo_redo_row.setSpacing(6)

        self.btn_undo = QPushButton(t("btn.undo", default="Undo"))
        self.btn_undo.setIcon(svg_icon(SVG_ROTATE_LEFT, color=TEXT_PRI, size=14))
        self.btn_undo.setIconSize(QSize(14, 14))
        self.btn_undo.setToolTip("Ctrl+Z")
        self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo.clicked.connect(self._undo)
        self.btn_undo.setEnabled(False)

        self.btn_redo = QPushButton(t("btn.redo", default="Redo"))
        self.btn_redo.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=TEXT_PRI, size=14))
        self.btn_redo.setIconSize(QSize(14, 14))
        self.btn_redo.setToolTip("Ctrl+Y")
        self.btn_redo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_redo.clicked.connect(self._redo)
        self.btn_redo.setEnabled(False)

        undo_redo_row.addWidget(self.btn_undo)
        undo_redo_row.addWidget(self.btn_redo)
        form.addRow("", undo_redo_row)

        f.addWidget(grp)

        sec_out = section(t("tool.page_numbers.output"))
        f.addWidget(sec_out)
        self.drop_out = DropFileEdit("numbered.pdf", save=True, default_name="numbered.pdf")
        f.addWidget(self.drop_out)
        f.addStretch()

        self._compact_hidden = [sec_src, self.drop_in, self.lbl_info]
        sec_out.setVisible(False)
        self.drop_out.setVisible(False)

        self._load_settings()

    def _on_option_changed(self, *_args) -> None:
        self._save_settings()
        self._emit_preview()

    def _save_settings(self) -> None:
        """Persist page number preferences to JSON."""
        if getattr(self, "_loading_settings", False) or getattr(self, "_updating", False):
            return
        data = {
            "format_index": self.cmb_format.currentIndex(),
            "position_index": self.cmb_position.currentIndex(),
            "font_family": self.cmb_font.currentText(),
            "font_size": self.spin_size.value(),
            "bold": self.btn_bold.isChecked(),
            "italic": self.btn_italic.isChecked(),
            "start_page": self.spin_start_page.value(),
            "start_number": self.spin_start_number.value(),
            "pages": self.edit_pages.text().strip(),
        }
        cfg_file = _page_numbers_config_file()
        try:
            os.makedirs(os.path.dirname(cfg_file), exist_ok=True)
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as exc:
            _log.warning("Could not save page numbers settings to %s: %s", cfg_file, exc)

        with contextlib.suppress(Exception):
            from app.i18n import _update_config
            _update_config(lambda cfg: cfg.__setitem__("page_numbers_settings", data))

    def _load_settings(self) -> None:
        """Restore saved page number preferences from JSON."""
        data: dict[str, Any] = {}
        cfg_file = _page_numbers_config_file()
        if os.path.isfile(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                _log.warning("Could not parse %s: %s", cfg_file, exc)

        if not data:
            try:
                from app.i18n import _CONFIG_PATH
                if os.path.isfile(_CONFIG_PATH):
                    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        data = cfg.get("page_numbers_settings", {})
            except Exception:
                pass

        if not isinstance(data, dict) or not data:
            return

        self._loading_settings = True
        try:
            if "format_index" in data:
                with contextlib.suppress(Exception):
                    idx = int(data["format_index"])
                    if 0 <= idx < self.cmb_format.count():
                        self.cmb_format.setCurrentIndex(idx)
            if "position_index" in data:
                with contextlib.suppress(Exception):
                    idx = int(data["position_index"])
                    if 0 <= idx < self.cmb_position.count():
                        self.cmb_position.setCurrentIndex(idx)
            if "font_family" in data:
                fam = str(data["font_family"])
                idx = self.cmb_font.findText(fam, Qt.MatchFlag.MatchExactly)
                if idx < 0:
                    idx = self.cmb_font.findText(fam, Qt.MatchFlag.MatchContains)
                if idx >= 0:
                    self.cmb_font.setCurrentIndex(idx)
            if "font_size" in data:
                with contextlib.suppress(Exception):
                    sz = max(6, min(48, int(data["font_size"])))
                    self.spin_size.setValue(sz)
            if "bold" in data:
                self.btn_bold.setChecked(bool(data["bold"]))
            if "italic" in data:
                self.btn_italic.setChecked(bool(data["italic"]))
            if "start_page" in data:
                with contextlib.suppress(Exception):
                    self.spin_start_page.setValue(max(1, int(data["start_page"])))
            if "start_number" in data:
                with contextlib.suppress(Exception):
                    self.spin_start_number.setValue(max(1, int(data["start_number"])))
            if "pages" in data:
                self.edit_pages.setText(str(data["pages"]))
        finally:
            self._loading_settings = False

    def _calc_preview(self) -> dict | None:
        pdf_path = self.drop_in.path()
        win: Any = self.window()
        viewer: Any = getattr(win, "_viewer", None)
        if not pdf_path or not os.path.isfile(pdf_path):
            if viewer and viewer.current_path():
                pdf_path = viewer.current_path()
        if not pdf_path or not os.path.isfile(pdf_path):
            return None

        total = self._page_count
        if total <= 0:
            try:
                with self._open_fitz(pdf_path) as doc:
                    total = doc.page_count
                    self._page_count = total
            except Exception:
                return None

        if total <= 0:
            return None

        fmt_template = t(_FORMATS[self.cmb_format.currentIndex()][1])
        pos_code = _POSITIONS[self.cmb_position.currentIndex()][1]
        font_size = self.spin_size.value()
        font_family = self.cmb_font.currentText()
        is_bold = self.btn_bold.isChecked()
        is_italic = self.btn_italic.isChecked()
        start_page = self.spin_start_page.value() - 1
        start_num = self.spin_start_number.value()
        txt = self.edit_pages.text().strip()

        try:
            targets = set(parse_pages(txt, total)) if txt else set(range(total))
        except ValueError:
            targets = set()

        numbered_total = sum(1 for i in range(total) if i in targets and i >= start_page)
        if numbered_total == 0:
            return None

        page_labels: dict[int, str] = {}
        counter = 0
        for i in range(total):
            if i not in targets or i < start_page:
                continue
            counter += 1
            n_display = start_num + counter - 1
            label = fmt_template.format(n=n_display, total=numbered_total)
            page_labels[i] = label

        return {
            "pos_code": pos_code,
            "font_size": font_size,
            "font_family": font_family,
            "bold": is_bold,
            "italic": is_italic,
            "targets": page_labels,
        }

    def _emit_preview(self):
        if self._updating or self._loading_settings:
            return
        preview = self._calc_preview()
        self.numbers_preview_changed.emit(preview)

    def _undo(self):
        win: Any = self.window()
        viewer: Any = getattr(win, "_viewer", None)
        if viewer and hasattr(viewer, "undo"):
            viewer.undo()
            self._update_undo_redo_state()
            self.numbers_preview_changed.emit(None)
            if hasattr(self, "_status"):
                self._status(t("tool.page_numbers.undone", default="Page numbers undone (Ctrl+Y to redo)"))

    def _redo(self):
        win: Any = self.window()
        viewer: Any = getattr(win, "_viewer", None)
        if viewer and hasattr(viewer, "redo"):
            viewer.redo()
            self._update_undo_redo_state()
            self.numbers_preview_changed.emit(None)
            if hasattr(self, "_status"):
                self._status(t("tool.page_numbers.redone", default="Page numbers redone"))

    def _update_undo_redo_state(self):
        win: Any = self.window()
        viewer: Any = getattr(win, "_viewer", None)
        can_u = viewer.can_undo() if viewer and hasattr(viewer, "can_undo") else False
        can_r = viewer.can_redo() if viewer and hasattr(viewer, "can_redo") else False
        self.btn_undo.setEnabled(can_u)
        self.btn_redo.setEnabled(can_r)
        if hasattr(win, "_undo_top_btn"):
            win._undo_top_btn.setEnabled(can_u)
        if hasattr(win, "_redo_top_btn"):
            win._redo_top_btn.setEnabled(can_r)

    def _pick_input(self):
        p, _ = QFileDialog.getOpenFileName(self, t("btn.open_pdf"), DESKTOP, t("file_filter.pdf"))
        if p:
            self._load_input(p)

    def _load_input(self, p: str):
        self.drop_in.blockSignals(True)
        self.drop_in.set_path(p)
        self.drop_in.blockSignals(False)
        if not self._maybe_prompt_password(p):
            self.drop_in.blockSignals(True)
            self.drop_in.set_path("")
            self.drop_in.blockSignals(False)
            return
        if not self.drop_out.path():
            base, ext = os.path.splitext(p)
            self.drop_out.set_path(base + "_numbered" + ext)
        try:
            r = self._open_reader(p)
            self._page_count = len(r.pages)
            self.lbl_info.setText(t("edit.status.pages", n=self._page_count))
            self._updating = True
            self.spin_start_page.blockSignals(True)
            self.spin_start_page.setMaximum(max(1, self._page_count))
            self.spin_start_page.blockSignals(False)
            self._updating = False
        except Exception as e:
            self.lbl_info.setText(t("tool.split.error_info", e=e))
        self._update_undo_redo_state()
        self._emit_preview()

    def auto_load(self, path: str):
        if path:
            self._load_input(path)

    def set_compact_mode(self, active: bool, path: str = "") -> None:
        super().set_compact_mode(active, path)
        self._update_undo_redo_state()
        self._emit_preview()

    def update_theme(self, dark: bool) -> None:
        super().update_theme(dark)
        pri = TEXT_PRI if dark else _LQ
        self.btn_undo.setStyleSheet(f"color: {pri};")
        self.btn_undo.setIcon(svg_icon(SVG_ROTATE_LEFT, color=pri, size=14))
        self.btn_redo.setStyleSheet(f"color: {pri};")
        self.btn_redo.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=pri, size=14))
        self.btn_bold.setIcon(svg_icon(SVG_BOLD, color=pri, size=13))
        self.btn_italic.setIcon(svg_icon(SVG_ITALIC, color=pri, size=13))

    def closeEvent(self, event) -> None:
        self._save_settings()
        super().closeEvent(event)

    def _run(self):
        self._save_settings()
        pdf_path = self.drop_in.path()
        win: Any = self.window()
        viewer: Any = getattr(win, "_viewer", None)
        if not pdf_path or not os.path.isfile(pdf_path):
            if viewer and viewer.current_path():
                pdf_path = viewer.current_path()
        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, t("msg.warning"), t("tool.page_numbers.select_source"))
            return

        fmt_template = t(_FORMATS[self.cmb_format.currentIndex()][1])
        pos_code = _POSITIONS[self.cmb_position.currentIndex()][1]
        font_size = self.spin_size.value()
        font_family = self.cmb_font.currentText()
        is_bold = self.btn_bold.isChecked()
        is_italic = self.btn_italic.isChecked()
        start_page = self.spin_start_page.value() - 1
        start_num = self.spin_start_number.value()
        margin = max(18, font_size + 8)
        txt = self.edit_pages.text().strip()

        try:
            import fitz
            fontname, fontfile_path, f_obj = _get_fitz_font_args(
                fitz, font_family, is_bold, is_italic
            )
            if not fontfile_path and any(ord(c) > 0xFF for c in fmt_template):
                self._status(t("tool.warn.font_latin_only"))

            with self._open_fitz(pdf_path) as doc:
                total = doc.page_count
                self._page_count = total
                targets = set(parse_pages(txt, total)) if txt else set(range(total))
                band_h = max(50, font_size * 4)
                num_re = re.compile(
                    r"^\s*(?:\d+\s*(?:/\s*\d+)?|"
                    r"(?:page|página|pagina|seite|stránka)\s+\d+(?:\s+(?:of|de|sur|von|di|van)\s+\d+)?)\s*$",
                    re.IGNORECASE,
                )
                existing: list = []
                for i in range(total):
                    if i not in targets or i < start_page:
                        continue
                    page = doc[i]
                    rect = page.rect
                    if pos_code[0] == "t":
                        band = fitz.Rect(0, 0, rect.width, band_h)
                    else:
                        band = fitz.Rect(0, rect.height - band_h, rect.width, rect.height)
                    hits = []
                    text_dict = cast(dict, page.get_text("dict", clip=band))
                    blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []
                    for block in blocks:
                        if not isinstance(block, dict) or block.get("type") != 0:
                            continue
                        for line in block.get("lines", []):
                            if not isinstance(line, dict):
                                continue
                            for span in line.get("spans", []):
                                if not isinstance(span, dict):
                                    continue
                                stxt = span.get("text", "").strip()
                                if stxt and num_re.match(stxt):
                                    hits.append(tuple(span["bbox"]))
                    if hits:
                        existing.append((i, hits))
        except Exception as e:
            show_error(self, e)
            return

        numbered_total = sum(1 for i in range(total)
                             if i in targets and i >= start_page)
        if numbered_total == 0:
            QMessageBox.warning(self, t("msg.warning"),
                                t("tool.page_numbers.no_targets"))
            return

        replace = False
        if existing:
            ans = QMessageBox.question(
                self, t("msg.warning"),
                t("tool.page_numbers.existing_found", n=len(existing)),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            replace = (ans == QMessageBox.StandardButton.Yes)

        if viewer is not None and viewer.current_path():
            try:
                import fitz
                active_path = viewer.current_path() or pdf_path
                doc = fitz.open(active_path)
                if doc.needs_pass and self._pdf_password:
                    doc.authenticate(self._pdf_password)

                if replace:
                    for pg_idx, rects in existing:
                        pg = doc[pg_idx]
                        for bbox in rects:
                            pg.add_redact_annot(fitz.Rect(*bbox), fill=(1, 1, 1))
                        pg.apply_redactions()

                counter = 0
                for i in range(total):
                    if i not in targets or i < start_page:
                        continue
                    counter += 1
                    n_display = start_num + counter - 1
                    label = fmt_template.format(n=n_display, total=numbered_total)

                    page = doc[i]
                    rect = page.rect
                    tw = f_obj.text_length(label, fontsize=font_size)
                    if pos_code[0] == "t":
                        y = margin
                    else:
                        y = rect.height - margin + font_size * 0.3
                    if pos_code[1] == "l":
                        x = margin
                    elif pos_code[1] == "c":
                        x = (rect.width - tw) / 2
                    else:
                        x = rect.width - margin - tw

                    if fontfile_path:
                        page.insert_text(
                            fitz.Point(x, y),
                            label,
                            fontsize=font_size,
                            fontname=fontname,
                            fontfile=fontfile_path,
                            color=(0, 0, 0),
                        )
                    else:
                        page.insert_text(
                            fitz.Point(x, y),
                            label,
                            fontsize=font_size,
                            fontname=fontname,
                            color=(0, 0, 0),
                        )

                if fontfile_path:
                    with contextlib.suppress(Exception):
                        doc.subset_fonts()

                self.numbers_preview_changed.emit(None)

                first_target = min(i for i in range(total) if i in targets and i >= start_page)
                viewer._save_and_reload(doc, target_page=first_target)

                cur_p = viewer.current_path()
                if cur_p:
                    self.drop_in.blockSignals(True)
                    self.drop_in.set_path(cur_p)
                    self.drop_in.blockSignals(False)

                self._status(t("tool.page_numbers.applied", default="Page numbers added (Ctrl+Z to undo, Ctrl+S to save)"))
                self._update_undo_redo_state()
                return
            except Exception as e:
                show_error(self, e)
                return

        default_name = "numbered.pdf"
        if pdf_path:
            base, ext = os.path.splitext(os.path.basename(pdf_path))
            default_name = f"{base}_numbered{ext}"
        start_dir = os.path.dirname(pdf_path) if pdf_path else ""
        out_path = self._prompt_save_as(default_name, start_dir)
        if not out_path:
            return
        self.drop_out.set_path(out_path)

        pwd = self._pdf_password

        def do_work(worker):
            import fitz
            doc = fitz.open(pdf_path)
            if doc.needs_pass:
                if not (pwd and doc.authenticate(pwd)):
                    raise WrongPasswordError(t("tool.err.wrong_password"))
            try:
                if replace:
                    for pg_idx, rects in existing:
                        if worker.is_cancelled():
                            return None
                        pg = doc[pg_idx]
                        for bbox in rects:
                            pg.add_redact_annot(fitz.Rect(*bbox), fill=(1, 1, 1))
                        pg.apply_redactions()

                counter = 0
                for i in range(total):
                    if i not in targets or i < start_page:
                        continue
                    if worker.is_cancelled():
                        return None
                    counter += 1
                    n_display = start_num + counter - 1
                    label = fmt_template.format(
                        n=n_display, total=numbered_total)

                    page = doc[i]
                    rect = page.rect
                    tw = f_obj.text_length(label, fontsize=font_size)
                    if pos_code[0] == "t":
                        y = margin
                    else:
                        y = rect.height - margin + font_size * 0.3
                    if pos_code[1] == "l":
                        x = margin
                    elif pos_code[1] == "c":
                        x = (rect.width - tw) / 2
                    else:
                        x = rect.width - margin - tw

                    if fontfile_path:
                        page.insert_text(
                            fitz.Point(x, y),
                            label,
                            fontsize=font_size,
                            fontname=fontname,
                            fontfile=fontfile_path,
                            color=(0, 0, 0),
                        )
                    else:
                        page.insert_text(
                            fitz.Point(x, y),
                            label,
                            fontsize=font_size,
                            fontname=fontname,
                            color=(0, 0, 0),
                        )
                    worker.progress.emit(counter,
                                         t("progress.page_numbers.page",
                                           current=counter,
                                           total=numbered_total))

                if worker.is_cancelled():
                    return None

                if fontfile_path:
                    with contextlib.suppress(Exception):
                        doc.subset_fonts()

                atomic_pdf_write(
                    doc, out_path,
                    sources=[pdf_path],
                    save_opts={"garbage": 4, "deflate": True},
                    close_writer=True,
                )
            finally:
                with contextlib.suppress(Exception):
                    doc.close()
            return out_path

        def on_done(saved):
            self.numbers_preview_changed.emit(None)
            self._status(t("tool.page_numbers.status.done",
                           name=os.path.basename(saved)))
            msg = t("tool.page_numbers.done", path=saved)
            QMessageBox.information(self, t("msg.done"), msg)

        self._run_background(do_work, total=numbered_total,
                             label=t("progress.page_numbers.applying"),
                             on_done=on_done)