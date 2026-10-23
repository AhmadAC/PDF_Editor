# app/viewer/print_utils.py
"""PDF_Editor – Utility functions and widgets for the print dialog."""
import os
import sys
from PySide6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _get_os_badge() -> str:
    if _is_linux():
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=", 1)[1].strip().strip('"')
                        if line.startswith("NAME="):
                            return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        return "Linux CUPS"
    elif sys.platform == "win32":
        return "Windows Spooler"
    elif sys.platform == "darwin":
        return "macOS"
    return sys.platform


def _find_printer_accounting_script() -> str | None:
    base_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ComputerScripts"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ComputerScripts"),
        os.path.join(os.getcwd(), "ComputerScripts"),
        os.path.join(os.getcwd(), "..", "ComputerScripts"),
        os.path.join(os.path.expanduser("~"), "ComputerScripts"),
        "/var/mnt/shared-drive/2027/ComputerScripts",
    ]
    for d in base_dirs:
        p = os.path.join(d, "PrinterAccounting.py")
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _find_accounting_history_file() -> str | None:
    script_path = _find_printer_accounting_script()
    if script_path:
        h = os.path.join(os.path.dirname(script_path), "history.json")
        if os.path.isfile(h):
            return h
    home_history = os.path.expanduser("~/.fuji_printer_history.json")
    if os.path.isfile(home_history):
        return home_history
    return None