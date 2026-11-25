# PDF_Editor.py
import argparse
import ctypes
import importlib.util
import logging
import os
import re
import sys
from functools import wraps
from pathlib import Path

# Configure environment for native KDE Plasma 6 / Dolphin file picker dialogs on Linux / Fedora Kinoite
# via XDG desktop portal integration before PySide6/Qt initializes.
if sys.platform.startswith("linux"):
    os.environ["QT_USE_PORTAL"] = "1"
    _desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        + ":"
        + os.environ.get("DESKTOP_SESSION", "")
    ).upper()
    if "KDE" in _desktop or "PLASMA" in _desktop or os.environ.get("KDE_FULL_SESSION"):
        if not os.environ.get("QT_QPA_PLATFORMTHEME") or os.environ.get("QT_QPA_PLATFORMTHEME") == "kde":
            os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"

# Ensure the project directory is always the first lookup path in sys.path,
# preventing ModuleNotFoundError when Windows launches the script from another folder.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication, QMessageBox


def _clean_action_text(text: str) -> str:
    """Strips mnemonic '&' prefixes while preserving escaped '&&' and standalone ' & ' as '&'."""
    return re.sub(r'&&|&(?=\S)', lambda m: '&' if m.group(0) == '&&' else '', text)


_AMPERSAND_FIX_INSTALLED = False


def install_global_ampersand_fix() -> None:
    """Globally strips mnemonic '&' prefixes across PySide6 actions, menus, buttons, tabs, and group boxes."""
    global _AMPERSAND_FIX_INSTALLED
    if _AMPERSAND_FIX_INSTALLED:
        return

    from PySide6 import QtGui, QtWidgets

    def _clean_kwargs(kwargs: dict) -> dict:
        for kw in ("text", "title", "label"):
            if kw in kwargs and isinstance(kwargs[kw], str):
                kwargs[kw] = _clean_action_text(kwargs[kw])
        return kwargs

    def _clean_text_or_icon_text(args: tuple, kwargs: dict):
        if args:
            if isinstance(args[0], str):
                args = (_clean_action_text(args[0]),) + args[1:]
            elif len(args) > 1 and isinstance(args[0], QIcon) and isinstance(args[1], str):
                args = (args[0], _clean_action_text(args[1])) + args[2:]
        return args, _clean_kwargs(kwargs)

    def _clean_after_one_arg(args: tuple, kwargs: dict):
        if len(args) > 1:
            if isinstance(args[1], str):
                args = (args[0], _clean_action_text(args[1])) + args[2:]
            elif len(args) > 2 and isinstance(args[1], QIcon) and isinstance(args[2], str):
                args = (args[0], args[1], _clean_action_text(args[2])) + args[3:]
        return args, _clean_kwargs(kwargs)

    def _clean_after_two_args(args: tuple, kwargs: dict):
        if len(args) > 2:
            if isinstance(args[2], str):
                args = (args[0], args[1], _clean_action_text(args[2])) + args[3:]
            elif len(args) > 3 and isinstance(args[2], QIcon) and isinstance(args[3], str):
                args = (args[0], args[1], args[2], _clean_action_text(args[3])) + args[4:]
        return args, _clean_kwargs(kwargs)

    def _wrap_method(cls, method_name: str, cleaner) -> None:
        if cls is None or method_name not in cls.__dict__:
            return
        orig = cls.__dict__[method_name]
        if getattr(orig, "_ampersand_fixed", False):
            return

        @wraps(orig)
        def wrapper(self, *args, **kwargs):
            args, kwargs = cleaner(args, kwargs)
            return orig(self, *args, **kwargs)

        wrapper._ampersand_fixed = True
        setattr(cls, method_name, wrapper)

    # Actions
    QAction = getattr(QtGui, "QAction", None)
    for m in ("__init__", "setText", "setIconText"):
        _wrap_method(QAction, m, _clean_text_or_icon_text)

    # Buttons
    for cls_name in (
        "QAbstractButton",
        "QPushButton",
        "QCheckBox",
        "QRadioButton",
        "QToolButton",
        "QCommandLinkButton",
    ):
        cls = getattr(QtWidgets, cls_name, None)
        for m in ("__init__", "setText"):
            _wrap_method(cls, m, _clean_text_or_icon_text)

    # Menus, menu bars, toolbars, and widget actions
    QMenu = getattr(QtWidgets, "QMenu", None)
    for m in ("__init__", "setTitle", "addAction", "addMenu"):
        _wrap_method(QMenu, m, _clean_text_or_icon_text)

    QMenuBar = getattr(QtWidgets, "QMenuBar", None)
    for m in ("addAction", "addMenu"):
        _wrap_method(QMenuBar, m, _clean_text_or_icon_text)

    _wrap_method(getattr(QtWidgets, "QToolBar", None), "addAction", _clean_text_or_icon_text)
    _wrap_method(getattr(QtWidgets, "QWidget", None), "addAction", _clean_text_or_icon_text)

    # Tabs
    QTabBar = getattr(QtWidgets, "QTabBar", None)
    _wrap_method(QTabBar, "addTab", _clean_text_or_icon_text)
    _wrap_method(QTabBar, "insertTab", _clean_after_one_arg)
    _wrap_method(QTabBar, "setTabText", _clean_after_one_arg)

    QTabWidget = getattr(QtWidgets, "QTabWidget", None)
    _wrap_method(QTabWidget, "addTab", _clean_after_one_arg)
    _wrap_method(QTabWidget, "insertTab", _clean_after_two_args)
    _wrap_method(QTabWidget, "setTabText", _clean_after_one_arg)

    # Group boxes & dialog buttons
    QGroupBox = getattr(QtWidgets, "QGroupBox", None)
    for m in ("__init__", "setTitle"):
        _wrap_method(QGroupBox, m, _clean_text_or_icon_text)

    _wrap_method(QMessageBox, "addButton", _clean_text_or_icon_text)
    _wrap_method(getattr(QtWidgets, "QDialogButtonBox", None), "addButton", _clean_text_or_icon_text)

    _AMPERSAND_FIX_INSTALLED = True


install_global_ampersand_fix()

# Fast non-importing dependency availability checks
if importlib.util.find_spec("pypdf") is None:
    _app = QApplication(sys.argv)
    QMessageBox.critical(None, "Missing dependency",
                         "Install the pypdf library:\n\npip install pypdf")
    sys.exit(1)

if importlib.util.find_spec("pymupdf") is None and importlib.util.find_spec("fitz") is None:
    _app = QApplication(sys.argv)
    QMessageBox.critical(None, "Missing dependency",
                         "Install PyMuPDF:\n\npip install pymupdf")
    sys.exit(1)

from app.constants import APP_VERSION
from app.window import MainWindow
from app.styles import STYLE, STYLE_LIGHT
from app.utils import _make_palette, setup_logging


def _ensure_icons():
    """Ensure icon_512.png and icon.ico are generated and in sync with PDF_Editor.svg."""
    ico_path = PROJECT_ROOT / "icon.ico"
    png_path = PROJECT_ROOT / "icon_512.png"
    svg_path = PROJECT_ROOT / "PDF_Editor.svg"

    if not svg_path.is_file():
        return

    needs_generation = False
    if not ico_path.is_file() or not png_path.is_file():
        needs_generation = True
    else:
        try:
            svg_mtime = svg_path.stat().st_mtime
            if svg_mtime > ico_path.stat().st_mtime or svg_mtime > png_path.stat().st_mtime:
                needs_generation = True
        except Exception:
            pass

    if needs_generation:
        try:
            from generate_icons import generate_all_icons
            generate_all_icons(PROJECT_ROOT)
        except Exception as exc:
            logging.getLogger(__name__).warning("Icon auto-generation skipped: %s", exc)


def _excepthook(exc_type, exc_value, exc_traceback):
    """Route uncaught exceptions through the logging framework so
    PyInstaller --windowed builds still surface crashes in the log
    file instead of silently swallowing them on closed stderr.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger(__name__).critical(
        "Uncaught exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "Unexpected error",
                f"{exc_type.__name__}: {exc_value}\n\nDetails logged.",
            )
    except Exception:
        pass


def _load_dark_pref() -> bool:
    try:
        import json
        from app.i18n import _CONFIG_PATH
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("dark_mode", True)
    except Exception:
        return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the PDF_Editor command-line."""
    parser = argparse.ArgumentParser(
        prog="PDF_Editor",
        description="PDF_Editor — fast desktop PDF editor.",
        add_help=True,
    )
    parser.add_argument(
        "files", nargs="*", metavar="PDF",
        help=("One or more PDF files to open. Each file opens in its "
              "own tab. Defaults to the welcome screen."),
    )
    parser.add_argument(
        "-v", "--version", action="version",
        version=f"PDF_Editor {APP_VERSION}",
    )
    return parser.parse_args(argv)


def main():
    if sys.platform == "win32":
        try:
            myappid = "PDF_Editor.desktop.editor.release"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    setup_logging()
    sys.excepthook = _excepthook
    args = _parse_args(sys.argv[1:])

    pdf_files = [
        os.path.abspath(f) for f in (args.files or [])
        if os.path.isfile(f) and f.lower().endswith(".pdf")
    ]

    QImageReader.setAllocationLimit(0)
    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)

    if pdf_files:
        from app.single_instance import send_to_existing
        if send_to_existing(pdf_files):
            sys.exit(0)

    _ensure_icons()

    ico_path = PROJECT_ROOT / "icon.ico"
    png_path = PROJECT_ROOT / "icon_512.png"
    svg_path = PROJECT_ROOT / "PDF_Editor.svg"
    icon_file = ico_path if ico_path.is_file() else (png_path if png_path.is_file() else (svg_path if svg_path.is_file() else None))
    if icon_file:
        app_icon = QIcon(str(icon_file))
        app.setWindowIcon(app_icon)

    app.setApplicationName(" ")
    app.setApplicationDisplayName(" ")
    app.setStyle("Fusion")
    dark = _load_dark_pref()
    app.setPalette(_make_palette(dark))
    app.setStyleSheet(STYLE if dark else STYLE_LIGHT)

    window = MainWindow()
    if icon_file:
        window.setWindowIcon(QIcon(str(icon_file)))
    window.showMaximized()

    for pdf_arg in args.files:
        abs_arg = os.path.abspath(pdf_arg)
        if os.path.isfile(abs_arg):
            window._load_and_track(abs_arg)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()