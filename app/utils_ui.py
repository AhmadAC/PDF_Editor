# app/utils_ui.py
"""PDF_Editor – UI widget factories, palette construction, logging, and error handling."""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import traceback
from typing import Any

from PySide6.QtCore import QLocale, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOption,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    ACCENT,
    BG_BASE,
    BG_CARD,
    BG_INPUT,
    DESKTOP,
    SUCCESS_DARK,
    SUCCESS_LIGHT,
    TEXT_PRI,
    TEXT_SEC,
    _LA,
    _LC,
    _LI,
    _LN,
    _LO,
    _LP,
    _LQ,
)
from app.i18n import t
from app.utils_compress import WrongPasswordError
from app.utils_svg import svg_for_name

_logging_initialised = False


def _log_path() -> str:
    from app.i18n import _CONFIG_PATH
    return os.path.join(os.path.dirname(_CONFIG_PATH), "PDF_Editor.log")


def setup_logging() -> None:
    """Configure a rotating file logger and console logger."""
    global _logging_initialised
    if _logging_initialised:
        return
    _logging_initialised = True
    try:
        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        root = logging.getLogger()
        level = logging.DEBUG if os.environ.get("PDF_Editor_DEBUG") or os.environ.get("DEBUG") else logging.INFO
        root.setLevel(level)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root.addHandler(console_handler)

        log_path = _log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except Exception:
        pass


def show_error(parent: QWidget | None, exc: BaseException) -> None:
    """Show a friendly error dialog and copy details to clipboard."""
    if isinstance(exc, WrongPasswordError):
        logging.warning("Wrong PDF password: %s", exc)
        err_msg = str(exc) or t("tool.err.wrong_password")
        cb = QApplication.clipboard()
        if cb is not None:
            with contextlib.suppress(Exception):
                cb.setText(err_msg)
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("msg.warning"))
        box.setText(err_msg)
        box.exec()
        return

    logging.error("UI error surfaced: %s: %s", type(exc).__name__, exc, exc_info=exc)
    try:
        err_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        err_text = f"{type(exc).__name__}: {exc}"

    cb = QApplication.clipboard()
    if cb is not None:
        with contextlib.suppress(Exception):
            cb.setText(err_text)

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(t("msg.error"))
    box.setText(f"{t('msg.unexpected')}\n\n(Error details have been copied to your clipboard)")
    box.setDetailedText(err_text)
    box.exec()


def is_dark() -> bool:
    try:
        from app.i18n import _CONFIG_PATH
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("dark_mode", True))
    except Exception:
        return True


def error_color() -> str:
    return "#F87171" if is_dark() else "#DC2626"


def success_color(dark: bool | None = None) -> str:
    if dark is None:
        dark = is_dark()
    return SUCCESS_DARK if dark else SUCCESS_LIGHT


def result_label_style(dark: bool | None = None) -> str:
    return f"font-weight:600; font-size:11pt; color:{success_color(dark)}; background:transparent; padding:10px 4px;"


def resource_path(rel: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def reveal_file(path: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif shutil.which("dolphin"):
            subprocess.Popen(["dolphin", "--select", os.path.abspath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(os.path.abspath(path)) or "."])
    except OSError:
        pass


def open_folder(path: str) -> None:
    try:
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif shutil.which("dolphin"):
            subprocess.Popen(["dolphin", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except OSError:
        pass


def format_size_localized(value: float, decimals: int = 1) -> str:
    try:
        return QLocale.system().toString(float(value), 'f', decimals)
    except Exception:
        return f"{value:.{decimals}f}"


def _make_palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        p.setColor(QPalette.ColorRole.Window,          QColor(BG_BASE))
        p.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Base,            QColor(BG_INPUT))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_CARD))
        p.setColor(QPalette.ColorRole.Text,            QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Button,          QColor("#1E2235"))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    else:
        p.setColor(QPalette.ColorRole.Window,          QColor("#CBD5E1"))
        p.setColor(QPalette.ColorRole.WindowText,      QColor(_LP))
        p.setColor(QPalette.ColorRole.Base,            QColor(_LI))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor(_LN))
        p.setColor(QPalette.ColorRole.Text,            QColor(_LP))
        p.setColor(QPalette.ColorRole.Button,          QColor(_LC))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor(_LP))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(_LA))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    return p


def _paint_bg(widget: QWidget) -> None:
    opt = QStyleOption()
    opt.initFrom(widget)
    p = QPainter(widget)
    widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, widget)


def parse_pages(text: str, total: int) -> list[int]:
    _MAX_PAGES = 100_000
    pages: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a_int = int(a.strip()) if a.strip() else 1
                b_int = int(b.strip()) if b.strip() else total
            except ValueError as exc:
                raise ValueError(t("tool.err.bad_page_input", text=part)) from exc
            if b_int - a_int + 1 > _MAX_PAGES:
                raise ValueError(f"Range too large: {a_int}-{b_int}")
            pages.extend(range(a_int - 1, b_int))
        else:
            try:
                pages.append(int(part) - 1)
            except ValueError as exc:
                raise ValueError(t("tool.err.bad_page_input", text=part)) from exc
    invalid = [p for p in pages if p < 0 or p >= total]
    if invalid:
        bad = sorted({(p + 1) if p >= 0 else 0 for p in invalid})
        raise ValueError(f"Pages out of range: {bad}  (valid: 1-{total})")
    return sorted(set(pages))


def check_image_size(path: str) -> tuple[bool, int, int]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
        return (w * h) <= 100_000_000, w, h
    except Exception:
        return True, 0, 0


def pick_pdfs(parent: QWidget) -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, t("btn.select_pdfs"), DESKTOP, t("file_filter.pdf"))
    return paths


def pick_folder(parent: QWidget) -> str:
    return QFileDialog.getExistingDirectory(parent, t("btn.select_folder")) or ""


def wipe_pdf_password(obj: Any) -> None:
    obj._pdf_password = ""
    for name in ("_pwd_map", "_written_pwd"):
        cache = getattr(obj, name, None)
        if isinstance(cache, dict):
            cache.clear()


def prompt_pdf_password(path: str, parent=None) -> tuple[bool, str]:
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return True, ""
    try:
        if not doc.needs_pass:
            return True, ""
        from app.editor.dialogs import _PdfPasswordDialog
        from app.pdf_password import authenticate_fitz
        wrong = False
        while True:
            dlg = _PdfPasswordDialog(os.path.basename(path), wrong=wrong, parent=parent)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False, ""
            winner = authenticate_fitz(doc, dlg.password())
            if winner is not None:
                return True, winner
            wrong = True
    finally:
        doc.close()


def ToolHeader(icon_name: str, title: str, desc: str) -> QWidget:
    w = QWidget()
    w.setObjectName("tool_header")
    h = QHBoxLayout(w)
    h.setContentsMargins(20, 12, 20, 12)
    h.setSpacing(12)
    ico = QPushButton()
    ico.setIcon(svg_for_name(icon_name, color=ACCENT, size=22))
    ico.setIconSize(QSize(22, 22))
    ico.setFixedSize(36, 36)
    ico.setObjectName("th_icon")
    ico.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    col = QVBoxLayout()
    col.setSpacing(3)
    t_lbl = QLabel(title)
    t_lbl.setObjectName("th_title")
    t_lbl.setWordWrap(True)
    d = QLabel(desc)
    d.setObjectName("th_desc")
    d.setWordWrap(True)
    col.addWidget(t_lbl)
    col.addWidget(d)
    h.addWidget(ico, 0)
    h.addLayout(col, 1)
    return w


def _action_progress_stylesheet(dark: bool) -> str:
    return (
        f"QProgressBar {{ background: {BG_INPUT if dark else _LO}; border-radius: 2px; }}"
        f"QProgressBar::chunk {{ background: {ACCENT if dark else _LA}; border-radius: 2px; }}"
    )


class _ActionBarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress: QProgressBar | None = None

    def update_theme(self, dark: bool) -> None:
        if self.progress is not None:
            with contextlib.suppress(RuntimeError):
                self.progress.setStyleSheet(_action_progress_stylesheet(dark))


def ActionBar(btn_text: str, slot) -> tuple[_ActionBarWidget, QPushButton]:
    bar = _ActionBarWidget()
    bar.setObjectName("action_bar")
    v = QVBoxLayout(bar)
    v.setContentsMargins(24, 4, 24, 14)
    v.setSpacing(4)

    progress = QProgressBar()
    progress.setVisible(False)
    progress.setFixedHeight(4)
    progress.setTextVisible(False)
    progress.setObjectName("action_progress")
    progress.setStyleSheet(_action_progress_stylesheet(is_dark()))
    v.addWidget(progress)

    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.addStretch()

    btn = QPushButton(btn_text)
    btn.setObjectName("btn_action_small")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(32)
    btn.setMinimumWidth(80)
    btn.clicked.connect(slot)
    h.addWidget(btn)
    v.addLayout(h)
    bar.progress = progress
    return bar, btn


def section(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("section_lbl")
    return lbl


def info_lbl() -> QLabel:
    lbl = QLabel("")
    lbl.setObjectName("info_lbl")
    return lbl


def primary_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("btn_primary")
    b.setFixedHeight(38)
    return b


def danger_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("btn_danger")
    return b


def scrolled(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(widget)
    return sa