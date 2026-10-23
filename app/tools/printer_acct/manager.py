# app/tools/printer_acct/manager.py
import os
import json
import contextlib
from typing import Any
from PySide6.QtWidgets import (
    QWidget,
    QLineEdit,
    QComboBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QCheckBox,
    QProgressBar,
    QScrollArea,
    QTabWidget,
)
from PySide6.QtCore import Qt

from .utils import (
    ensure_preferences_file, PREFERENCES_FILE, HISTORY_FILE,
    TOKYO_NIGHT_STYLE, get_os_info
)
from .manager_ui import build_manager_ui
from .manager_preview import ManagerPreviewMixin
from .manager_config import ManagerConfigMixin
from .manager_print import ManagerPrintMixin


class FujiAccountingManager(ManagerPreviewMixin, ManagerConfigMixin, ManagerPrintMixin, QWidget):
    # UI components attached dynamically by build_manager_ui
    file_input: QLineEdit
    dest_mode_combo: QComboBox
    ip_lbl: QLabel
    dest_ip_input: QLineEdit
    test_conn_btn: QPushButton
    cups_queue_combo: QComboBox
    acct_banner_lbl: QLabel
    page_scope_combo: QComboBox
    custom_range_input: QLineEdit
    duplex_combo: QComboBox
    paper_combo: QComboBox
    color_combo: QComboBox
    copies_spin: QSpinBox
    chk_collate: QCheckBox
    print_progress: QProgressBar
    send_pdf_btn: QPushButton
    doc_name_lbl: QLabel
    prev_btn: QPushButton
    page_indicator_lbl: QLabel
    next_btn: QPushButton
    printer_combo: QComboBox
    ip_input: QLineEdit
    convert_chk: QCheckBox
    user_input: QLineEdit
    pass_input: QLineEdit
    btn_toggle_pin: QPushButton
    account_input: QLineEdit
    sudo_input: QLineEdit
    btn_toggle_sudo: QPushButton
    test_socket_btn: QPushButton
    save_btn: QPushButton
    status_badge: QLabel
    tabs: QTabWidget
    tab_preview: QWidget
    tab_setup: QWidget
    scroll_area: QScrollArea | None
    preview_lbl: QLabel | None
    pdf_doc: Any
    pdf_view: Any
    nav: Any

    def __init__(self, initial_pdf: str | None = None):
        super().__init__()
        self.os_title = get_os_info()
        self.setWindowTitle(f"PDF Print — [{self.os_title}]")
        self.setStyleSheet(TOKYO_NIGHT_STYLE)
        
        self.total_pages = 0
        self.current_page = 1
        self.initial_pdf = initial_pdf
        self.print_thread = None
        self.temp_slice_path = None

        self.scroll_area = None
        self.preview_lbl = None
        self.pdf_doc = None
        self.pdf_view = None
        self.nav = None

        self.setAcceptDrops(True)
        build_manager_ui(self)
        self.load_printer_list()
        self.load_preferences()

        if self.initial_pdf and os.path.isfile(self.initial_pdf):
            self.file_input.setText(os.path.abspath(self.initial_pdf))

        self.setWindowState(Qt.WindowState.WindowMaximized)

    def load_preferences(self):
        target_file = ensure_preferences_file()
        if not os.path.exists(target_file):
            self.update_acct_banner()
            return
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "duplex" in data:
                idx = self.duplex_combo.findText(data["duplex"])
                if idx >= 0:
                    self.duplex_combo.setCurrentIndex(idx)
            if "paper_size" in data:
                idx = self.paper_combo.findText(data["paper_size"])
                if idx >= 0:
                    self.paper_combo.setCurrentIndex(idx)
            if "color_mode" in data:
                idx = self.color_combo.findText(data["color_mode"])
                if idx >= 0:
                    self.color_combo.setCurrentIndex(idx)
            if "copies" in data:
                self.copies_spin.setValue(int(data["copies"]))
            if "collate" in data:
                self.chk_collate.setChecked(bool(data["collate"]))
            if "page_scope" in data:
                self.page_scope_combo.setCurrentIndex(int(data["page_scope"]))
            if "custom_range" in data:
                self.custom_range_input.setText(data["custom_range"])
            if "dest_mode" in data:
                self.dest_mode_combo.setCurrentIndex(int(data["dest_mode"]))
            if "printer_queue" in data and data["printer_queue"]:
                idx = self.printer_combo.findText(data["printer_queue"])
                if idx >= 0:
                    self.printer_combo.setCurrentIndex(idx)
                else:
                    self.printer_combo.addItem(data["printer_queue"])
                    self.printer_combo.setCurrentText(data["printer_queue"])
            if "printer_ip" in data and data["printer_ip"]:
                self.ip_input.setText(data["printer_ip"])
                self.dest_ip_input.setText(data["printer_ip"])
            if "convert_chk" in data:
                self.convert_chk.setChecked(data["convert_chk"])
            if "user_id" in data:
                self.user_input.setText(data["user_id"])
            if "passcode" in data:
                self.pass_input.setText(data["passcode"])
            if "account_id" in data:
                self.account_input.setText(data["account_id"])

            if "last_pdf_path" in data and os.path.exists(data["last_pdf_path"]):
                self.file_input.setText(data["last_pdf_path"])

            self.update_acct_banner()
        except Exception as e:
            print(f"Failed to read preferences: {e}")

    def save_preferences(self):
        data = {
            "dest_mode": self.dest_mode_combo.currentIndex(),
            "printer_queue": self.printer_combo.currentText(),
            "printer_ip": self.dest_ip_input.text().strip(),
            "convert_chk": self.convert_chk.isChecked(),
            "user_id": self.user_input.text().strip(),
            "passcode": self.pass_input.text().strip(),
            "account_id": self.account_input.text().strip(),
            "last_pdf_path": self.file_input.text().strip(),
            "page_scope": self.page_scope_combo.currentIndex(),
            "custom_range": self.custom_range_input.text().strip(),
            "duplex": self.duplex_combo.currentText(),
            "paper_size": self.paper_combo.currentText(),
            "color_mode": self.color_combo.currentText(),
            "copies": self.copies_spin.value(),
            "collate": self.chk_collate.isChecked(),
        }
        for path in (PREFERENCES_FILE, HISTORY_FILE):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception:
                pass

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(".pdf") and os.path.isfile(path):
                self.file_input.setText(path)
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
        if self.print_thread and self.print_thread.isRunning():
            self.print_thread.cancel()
            self.print_thread.wait(1000)
        if self.temp_slice_path and os.path.exists(self.temp_slice_path):
            with contextlib.suppress(Exception):
                os.unlink(self.temp_slice_path)
        self.save_preferences()
        super().closeEvent(event)