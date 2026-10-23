
import sys
import os
import json
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

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFERENCES_FILE = os.path.join(SCRIPT_DIR, "printer_preferences.json")
HISTORY_FILE = os.path.join(SCRIPT_DIR, "history.json")

DEFAULT_PREFERENCES = {
    "dest_mode": 0,
    "printer_queue": "",
    "printer_ip": "",
    "convert_chk": True,
    "user_id": "",
    "passcode": "",
    "account_id": "",
    "last_pdf_path": "",
    "page_scope": 0,
    "custom_range": "",
    "duplex": "2-Sided (Flip on Long Edge)",
    "paper_size": "A4",
    "color_mode": "Color",
    "copies": 1,
    "collate": True
}

def ensure_preferences_file() -> str:
    if os.path.isfile(PREFERENCES_FILE):
        return PREFERENCES_FILE
    if os.path.isfile(HISTORY_FILE):
        return HISTORY_FILE

    target_path = PREFERENCES_FILE
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PREFERENCES, f, indent=4)
        return target_path
    except Exception:
        fallback_path = os.path.expanduser("~/.PDF_Editor_printer_preferences.json")
        try:
            if not os.path.isfile(fallback_path):
                with open(fallback_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_PREFERENCES, f, indent=4)
            return fallback_path
        except Exception:
            return target_path

def get_os_info() -> str:
    if sys.platform == "linux":
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    data = {}
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            data[k] = v.strip('"')
                    name = data.get("NAME", "Linux")
                    ver = data.get("VERSION_ID", "")
                    if "fedora" in data.get("ID", "").lower():
                        return f"Fedora Linux {ver}".strip()
                    return f"{name} {ver}".strip()
        except Exception:
            pass
        return "Linux CUPS"
    elif sys.platform == "win32":
        return "Windows Spooler"
    elif sys.platform == "darwin":
        return "macOS"
    return str(sys.platform)

def _is_linux() -> bool:
    return sys.platform.startswith("linux")

def _get_os_badge() -> str:
    return get_os_info()

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

TOKYO_NIGHT_STYLE = """
    QWidget { background-color: #1a1b26; color: #c0caf5; font-family: Segoe UI, Roboto, Helvetica, sans-serif; font-size: 10pt; }
    QTabWidget::pane { border: 1px solid #414868; background-color: #1a1b26; border-radius: 6px; }
    QTabBar::tab { background-color: #24283b; color: #c0caf5; padding: 9px 18px; margin-right: 4px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; }
    QTabBar::tab:selected { background-color: #7aa2f7; color: #1a1b26; }
    QGroupBox { border: 1px solid #414868; border-radius: 8px; margin-top: 8px; padding-top: 12px; font-weight: bold; color: #7aa2f7; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLineEdit, QComboBox, QSpinBox { background-color: #24283b; border: 1px solid #414868; border-radius: 6px; padding: 5px 8px; color: #c0caf5; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #7aa2f7; }
    QPushButton { background-color: #24283b; border: 1px solid #414868; border-radius: 6px; color: #c0caf5; padding: 6px 12px; font-weight: bold; }
    QPushButton:hover { background-color: #292e42; border-color: #7aa2f7; }
    QPushButton#PrimaryBtn { background-color: #7aa2f7; color: #1a1b26; border: none; }
    QPushButton#PrimaryBtn:hover { background-color: #89b4fa; }
    QPushButton#ActionBtn { background-color: #9ece6a; color: #1a1b26; border: none; padding: 10px; font-size: 11pt; }
    QPushButton#ActionBtn:hover { background-color: #b9f27c; }
    QPushButton#NavBtn { padding: 4px 10px; font-size: 9pt; }
    QProgressBar { border: 1px solid #414868; border-radius: 4px; text-align: center; background-color: #24283b; color: #c0caf5; font-weight: bold; }
    QProgressBar::chunk { background-color: #7aa2f7; border-radius: 3px; }
"""