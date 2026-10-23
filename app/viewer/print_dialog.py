# app/viewer/print_dialog.py
"""PDF_Editor – High-speed custom print dialog integrating the worker and UI setup."""
import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtWidgets import (
    QDialog, QMessageBox, QScrollArea, QLabel, QPushButton, QTabWidget,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit, QSpinBox, QCheckBox,
    QProgressBar, QFileDialog
)

from app.utils import parse_pages
from app.viewer.print_utils import _find_accounting_history_file, _is_linux
from app.viewer.print_worker import _FastPrintWorker
from app.viewer.print_dialog_ui import setup_print_dialog_ui, apply_dialog_stylesheet

try:
    import fitz
    HAS_FITZ = True
except Exception:
    fitz = None
    HAS_FITZ = False

_log = logging.getLogger(__name__)

PW_FILE = os.path.join(os.path.dirname(__file__), "pw.json")


class _PdfPrintDialog(QDialog):
    # Type hints to satisfy Pylance for attributes dynamically added by setup_print_dialog_ui
    preview_scroll: QScrollArea
    lbl_preview_img: QLabel
    btn_first_page: QPushButton
    btn_prev_page: QPushButton
    lbl_page_count: QLabel
    btn_next_page: QPushButton
    btn_last_page: QPushButton
    tab_widget: QTabWidget
    cmb_printer: QComboBox
    lbl_direct_socket_hint: QLabel
    rad_all: QRadioButton
    rad_current: QRadioButton
    rad_custom: QRadioButton
    bg_range: QButtonGroup
    edit_range: QLineEdit
    spin_copies: QSpinBox
    chk_collate: QCheckBox
    cmb_duplex: QComboBox
    cmb_paper: QComboBox
    cmb_color: QComboBox
    cmb_orient: QComboBox
    edit_ip: QLineEdit
    btn_test_ip: QPushButton
    edit_acct_user: QLineEdit
    edit_acct_pin: QLineEdit
    btn_toggle_pin: QPushButton
    edit_acct_id: QLineEdit
    prog_bar: QProgressBar
    lbl_status: QLabel
    btn_cancel: QPushButton
    btn_print: QPushButton
    
    # State attributes
    acct_user: str
    acct_pin: str
    acct_id: str
    saved_ip: str
    saved_printer: str
    saved_duplex: str
    saved_paper: str
    saved_color: str
    saved_copies: int
    saved_collate: bool

    def __init__(self, doc_path: str, password: str = "", initial_pages: list[int] | None = None, parent=None):
        super().__init__(parent)
        self.doc_path = doc_path
        self.password = password
        self.initial_pages = initial_pages
        self.total_pages = 0
        self.current_preview_page = 0
        self.temp_slice_path = None
        self.worker = None

        self.setObjectName("print_dialog")
        self.setWindowTitle(f"Print — {os.path.basename(doc_path)}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        self.setMinimumSize(960, 620)
        self.setModal(True)

        self._load_accounting_history()
        self._init_doc_metrics()

        if self.initial_pages and len(self.initial_pages) > 0:
            p0 = self.initial_pages[0]
            if 0 <= p0 < self.total_pages:
                self.current_preview_page = p0
        elif parent and hasattr(parent, "_canvas") and parent._canvas:
            active_p = getattr(parent._canvas, "_active_page_idx", 0)
            if 0 <= active_p < self.total_pages:
                self.current_preview_page = active_p

        self._build_ui()
        self._populate_printers()

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._update_preview)

        QTimer.singleShot(0, self.showMaximized)
        QTimer.singleShot(0, self._update_preview)

    def _build_ui(self):
        setup_print_dialog_ui(self)
        apply_dialog_stylesheet(self)

    def _init_doc_metrics(self):
        try:
            if fitz is not None:
                doc = fitz.open(self.doc_path)
                if self.password and doc.needs_pass:
                    doc.authenticate(self.password)
                self.total_pages = doc.page_count
                doc.close()
            else:
                self.total_pages = 1
        except Exception:
            self.total_pages = 1

    def _load_accounting_history(self):
        # Empty fallbacks in case no JSON files exist
        self.acct_user = ""
        self.acct_pin = ""
        self.acct_id = ""
        self.saved_ip = ""
        self.saved_printer = ""
        self.saved_duplex = "2-Sided (Flip on Long Edge)"
        self.saved_paper = "A4"
        self.saved_color = "Color"
        self.saved_copies = 1
        self.saved_collate = True

        # 1. Load credentials from pw.json next to the script
        if os.path.isfile(PW_FILE):
            try:
                with open(PW_FILE, "r", encoding="utf-8") as f:
                    pw_data = json.load(f)
                    self.acct_user = pw_data.get("user_id", self.acct_user)
                    self.acct_pin = pw_data.get("passcode", self.acct_pin)
                    self.acct_id = pw_data.get("account_id", self.acct_id)
                    self.saved_ip = pw_data.get("printer_ip", self.saved_ip)
            except Exception as e:
                _log.warning("Could not load credentials from pw.json: %s", e)

        # 2. Layer printer history if present
        h_file = _find_accounting_history_file()
        if h_file and os.path.isfile(h_file):
            try:
                with open(h_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.acct_user = data.get("user_id", self.acct_user)
                    self.acct_pin = data.get("passcode", self.acct_pin)
                    self.acct_id = data.get("account_id", self.acct_id)
                    self.saved_ip = data.get("printer_ip", self.saved_ip)
                    self.saved_printer = data.get("printer_queue", self.saved_printer)
                    self.saved_duplex = data.get("duplex", self.saved_duplex)
                    self.saved_paper = data.get("paper_size", self.saved_paper)
                    self.saved_color = data.get("color_mode", self.saved_color)
                    self.saved_copies = data.get("copies", self.saved_copies)
                    self.saved_collate = data.get("collate", self.saved_collate)
            except Exception:
                pass

    def _save_accounting_history(self):
        user = self.edit_acct_user.text().strip()
        pin = self.edit_acct_pin.text().strip()
        acct_id = self.edit_acct_id.text().strip()
        ip = self.edit_ip.text().strip()

        # Update pw.json next to the script
        pw_data = {
            "user_id": user,
            "passcode": pin,
            "account_id": acct_id,
            "printer_ip": ip,
        }
        try:
            with open(PW_FILE, "w", encoding="utf-8") as f:
                json.dump(pw_data, f, indent=4)
        except Exception as e:
            _log.warning("Could not save to pw.json: %s", e)

        # Update global printer history
        h_file = _find_accounting_history_file()
        if not h_file:
            home_history = os.path.expanduser("~/.fuji_printer_history.json")
            h_file = home_history

        data = {
            "user_id": user,
            "passcode": pin,
            "account_id": acct_id,
            "printer_ip": ip,
            "printer_queue": self.cmb_printer.currentText(),
            "duplex": self.cmb_duplex.currentText(),
            "paper_size": self.cmb_paper.currentText(),
            "color_mode": self.cmb_color.currentText(),
            "copies": self.spin_copies.value(),
            "collate": self.chk_collate.isChecked()
        }
        try:
            with open(h_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass

    def _populate_printers(self):
        self.cmb_printer.clear()
        self.cmb_printer.addItem("Direct Network Printer (Port 9100)", "socket")

        printers = QPrinterInfo.availablePrinters()
        for p in printers:
            p_name = p.printerName()
            self.cmb_printer.addItem(p_name, ("cups" if _is_linux() else "qprinter", p_name))

        if _is_linux():
            try:
                res = subprocess.run(["lpstat", "-e"], capture_output=True, text=True)
                for q in res.stdout.splitlines():
                    q = q.strip()
                    if q and not any(q in self.cmb_printer.itemText(i) for i in range(self.cmb_printer.count())):
                        self.cmb_printer.addItem(f"{q} (CUPS)", ("cups", q))
            except Exception:
                pass

        if self.saved_printer:
            idx = self.cmb_printer.findText(self.saved_printer)
            if idx >= 0:
                self.cmb_printer.setCurrentIndex(idx)

    def _on_printer_changed(self, idx: int):
        data = self.cmb_printer.itemData(idx)
        is_direct_socket = (data == "socket")
        if is_direct_socket:
            self.btn_print.setText("Print")
        else:
            self.btn_print.setText("Send Print Job")

    def _test_printer_ip(self):
        ip = self.edit_ip.text().strip()
        if not ip:
            QMessageBox.warning(self, "Validation", "Please enter an IP address.")
            return
        try:
            with socket.create_connection((ip, 9100), timeout=1.5):
                QMessageBox.information(self, "Online", f"Printer at {ip}:9100 is ONLINE and ready for Port 9100 printing.")
        except Exception as e:
            QMessageBox.critical(self, "Offline / Refused", f"Cannot connect to {ip}:9100:\n{e}")

    def _set_range_odd(self):
        self.rad_custom.setChecked(True)
        odds = [str(p) for p in range(1, self.total_pages + 1, 2)]
        self.edit_range.setText(", ".join(odds))

    def _set_range_even(self):
        self.rad_custom.setChecked(True)
        evens = [str(p) for p in range(2, self.total_pages + 1, 2)]
        self.edit_range.setText(", ".join(evens))

    def _first_preview_page(self):
        if self.current_preview_page > 0:
            self.current_preview_page = 0
            self._update_preview()

    def _prev_preview_page(self):
        if self.current_preview_page > 0:
            self.current_preview_page -= 1
            self._update_preview()

    def _next_preview_page(self):
        if self.current_preview_page < self.total_pages - 1:
            self.current_preview_page += 1
            self._update_preview()

    def _last_preview_page(self):
        if self.current_preview_page < self.total_pages - 1:
            self.current_preview_page = self.total_pages - 1
            self._update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self._update_preview)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start(80)

    def _update_preview(self):
        self.rad_current.setText(f"Current Page (Page {self.current_preview_page + 1})")
        self.btn_first_page.setEnabled(self.current_preview_page > 0)
        self.btn_prev_page.setEnabled(self.current_preview_page > 0)
        self.btn_next_page.setEnabled(self.current_preview_page < self.total_pages - 1)
        self.btn_last_page.setEnabled(self.current_preview_page < self.total_pages - 1)

        doc = None
        try:
            if fitz is None:
                raise ImportError("PyMuPDF (fitz) is not available.")
                
            doc = fitz.open(self.doc_path)
            if self.password and doc.needs_pass:
                doc.authenticate(self.password)

            if 0 <= self.current_preview_page < doc.page_count:
                page = doc[self.current_preview_page]
                vp = self.preview_scroll.viewport()
                avail_w = max(100, vp.width() - 32)
                avail_h = max(100, vp.height() - 32)

                orient = self.cmb_orient.currentText()
                is_natural_landscape = (page.rect.width > page.rect.height)
                if "Landscape" in orient:
                    effective_is_landscape = True
                elif "Portrait" in orient:
                    effective_is_landscape = False
                else:
                    effective_is_landscape = is_natural_landscape

                orient_rot = 0
                if effective_is_landscape and not is_natural_landscape:
                    orient_rot = 90
                elif not effective_is_landscape and is_natural_landscape:
                    orient_rot = 90

                duplex_text = self.cmb_duplex.currentText()
                is_duplex = "2-Sided" in duplex_text

                try:
                    target_pages = self._resolve_target_pages()
                except Exception:
                    target_pages = list(range(self.total_pages))

                if self.current_preview_page in target_pages:
                    sheet_slot = target_pages.index(self.current_preview_page)
                else:
                    sheet_slot = self.current_preview_page

                is_back_page = is_duplex and (sheet_slot % 2 == 1)

                duplex_rot = 0
                if is_back_page:
                    if "Short Edge" in duplex_text:
                        duplex_rot = 0 if effective_is_landscape else 180
                    elif "Long Edge" in duplex_text:
                        duplex_rot = 180 if effective_is_landscape else 0

                total_rot = (orient_rot + duplex_rot) % 360
                new_rotation = (page.rotation + total_rot) % 360
                page.set_rotation(new_rotation)

                page_desc = f"Page {self.current_preview_page + 1} of {self.total_pages}"
                if is_duplex:
                    if is_back_page:
                        status_tag = "Back — Inverted" if duplex_rot == 180 else "Back"
                    else:
                        status_tag = "Front"
                    page_desc += f" ({status_tag})"
                self.lbl_page_count.setText(page_desc)

                pw = page.rect.width
                ph = page.rect.height

                zoom = min(avail_w / max(1.0, pw), avail_h / max(1.0, ph))
                zoom = max(0.05, zoom * 0.95)

                mat = getattr(fitz, "Matrix")(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                if "Black & White" in self.cmb_color.currentText() and pix.n != 1:
                    pix = getattr(fitz, "Pixmap")(getattr(fitz, "csGRAY"), pix)
                elif pix.n != 3:
                    pix = getattr(fitz, "Pixmap")(getattr(fitz, "csRGB"), pix)

                fmt = QImage.Format.Format_Grayscale8 if pix.n == 1 else QImage.Format.Format_RGB888
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
                self.lbl_preview_img.setPixmap(QPixmap.fromImage(img))
                return
        except Exception as e:
            _log.warning("Print dialog preview generation failed: %s", e)
        finally:
            if doc is not None:
                with contextlib.suppress(Exception):
                    doc.close()

        self.lbl_page_count.setText(f"Page {self.current_preview_page + 1} of {self.total_pages}")
        self.lbl_preview_img.setText(f"Preview unavailable for Page {self.current_preview_page + 1}")

    def _resolve_target_pages(self) -> list[int]:
        if self.rad_all.isChecked():
            return list(range(self.total_pages))
        elif self.rad_current.isChecked():
            return [self.current_preview_page]
        else:
            txt = self.edit_range.text().strip()
            if not txt:
                raise ValueError("Custom page range is empty.")
            return parse_pages(txt, self.total_pages)

    @staticmethod
    def _is_file_printer(printer_name: str) -> bool:
        name = (printer_name or "").lower()
        return any(k in name for k in ("pdf", "xps", "document writer", "print to file"))

    def _start_print_job(self):
        try:
            pages = self._resolve_target_pages()
        except Exception as e:
            QMessageBox.warning(self, "Invalid Range", str(e))
            return

        if not pages:
            QMessageBox.warning(self, "Invalid Range", "No pages selected to print.")
            return

        self._save_accounting_history()

        p_idx = self.cmb_printer.currentIndex()
        p_data = self.cmb_printer.itemData(p_idx)

        duplex_val = self.cmb_duplex.currentText()
        paper_val = self.cmb_paper.currentText()
        color_val = self.cmb_color.currentText()
        is_color = ("Color" in color_val and "Black" not in color_val and "Mono" not in color_val)
        is_grayscale = not is_color

        copies_val = self.spin_copies.value()
        collate_val = self.chk_collate.isChecked()
        orient_setting = self.cmb_orient.currentText()

        user = self.edit_acct_user.text().strip() or "none"
        pin = self.edit_acct_pin.text().strip()
        acct = self.edit_acct_id.text().strip()

        target_pdf = self.doc_path

        need_temp = (is_grayscale or self.rad_custom.isChecked() or self.rad_current.isChecked() or orient_setting != "Auto (Match PDF)")
        if need_temp:
            try:
                if fitz is not None:
                    doc = fitz.open(self.doc_path)
                    if self.password and doc.needs_pass:
                        doc.authenticate(self.password)
                    sliced_doc = fitz.open()

                    csGRAY = getattr(fitz, "csGRAY")

                    for p in pages:
                        if 0 <= p < doc.page_count:
                            page = doc[p]
                            page_rot_delta = 0
                            is_nat_land = (page.rect.width > page.rect.height)
                            if "Landscape" in orient_setting and not is_nat_land:
                                page_rot_delta = 90
                            elif "Portrait" in orient_setting and is_nat_land:
                                page_rot_delta = 90

                            if page_rot_delta:
                                page.set_rotation((page.rotation + page_rot_delta) % 360)

                            if is_grayscale:
                                pix = page.get_pixmap(dpi=300, colorspace=csGRAY, alpha=False)
                                new_page = sliced_doc.new_page(width=page.rect.width, height=page.rect.height)
                                new_page.insert_image(page.rect, stream=pix.tobytes("jpeg"))
                            else:
                                sliced_doc.insert_pdf(doc, from_page=p, to_page=p)

                    doc.close()
                    fd, tmp_out = tempfile.mkstemp(prefix="PDF_Editor_print_", suffix=".pdf")
                    os.close(fd)
                    sliced_doc.save(tmp_out, garbage=4, deflate=True)
                    sliced_doc.close()
                    self.temp_slice_path = tmp_out
                    target_pdf = tmp_out
                else:
                    QMessageBox.warning(self, "Missing Dependency", "PyMuPDF (fitz) is required for advanced printing options.")
                    return
            except Exception as ex:
                QMessageBox.critical(self, "Page Processing Error", f"Failed to prepare pages for printing:\n{ex}")
                return

        if p_data == "socket":
            ip = self.edit_ip.text().strip()
            if not ip:
                QMessageBox.warning(self, "Missing IP", "Printer IP Address is required for Direct Port 9100.")
                return

            try:
                with open(target_pdf, "rb") as f:
                    pdf_bytes = f.read()

                pjl = [
                    "\x1b%-12345X@PJL",
                    f'@PJL SET JOBATTR = "@JOAU={user}"',
                    f'@PJL SET JOBATTR = "@JOAP={pin}"',
                ]
                if acct:
                    pjl.append(f'@PJL SET JOBATTR = "@DAID={acct}"')
                pjl.append(f'@PJL SET JOBNAME = "{os.path.basename(self.doc_path)}"')
                
                if collate_val:
                    pjl.append(f"@PJL SET QTY = {copies_val}")
                else:
                    pjl.append(f"@PJL SET COPIES = {copies_val}")
                    
                pjl.append(f"@PJL SET PAPER = {paper_val}")

                if "Landscape" in orient_setting:
                    pjl.append("@PJL SET ORIENTATION = LANDSCAPE")
                elif "Portrait" in orient_setting:
                    pjl.append("@PJL SET ORIENTATION = PORTRAIT")

                if is_color:
                    pjl.append("@PJL SET COLORMODE = COLOR")
                    pjl.append("@PJL SET RENDERMODE = COLOR")
                else:
                    pjl.append("@PJL SET COLORMODE = MONO")
                    pjl.append("@PJL SET RENDERMODE = GRAYSCALE")
                    pjl.append("@PJL SET DATAMODE = GRAYSCALE")
                    pjl.append("@PJL SET COLOR = OFF")
                    pjl.append("@PJL SET PROCESSCOLOR = OFF")

                if "Long Edge" in duplex_val:
                    pjl.append("@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = LONGEDGE")
                elif "Short Edge" in duplex_val:
                    pjl.append("@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = SHORTEDGE")
                else:
                    pjl.append("@PJL SET DUPLEX = OFF")

                pjl.append("@PJL ENTER LANGUAGE = PDF\r\n")
                payload = "\r\n".join(pjl).encode("latin-1") + pdf_bytes + b"\r\n\x1b%-12345X"

                self._dispatch_worker("socket", ip, payload, {"port": 9100})
            except Exception as e:
                self._cleanup_temp_slice()
                QMessageBox.critical(self, "Setup Error", str(e))
                return

        elif isinstance(p_data, tuple) and p_data[0] == "cups":
            queue_name = p_data[1]
            self._dispatch_worker(
                "cups",
                queue_name,
                target_pdf,
                {
                    "copies": copies_val,
                    "collate": collate_val,
                    "duplex": duplex_val,
                    "paper": paper_val,
                    "color": color_val,
                    "user": user,
                    "orient": orient_setting,
                },
            )
        else:
            printer_name = p_data[1] if isinstance(p_data, tuple) else self.cmb_printer.currentText()
            output_file = None
            if self._is_file_printer(printer_name):
                base, _ = os.path.splitext(os.path.basename(self.doc_path))
                filt = "PDF Files (*.pdf)" if "xps" not in printer_name.lower() else "XPS Files (*.xps)"
                def_ext = ".xps" if "xps" in printer_name.lower() else ".pdf"
                default_name = f"{base}_printed{def_ext}"
                start_dir = os.path.dirname(self.doc_path) if self.doc_path else ""
                output_file, _ = QFileDialog.getSaveFileName(self, "Save Print Output As", os.path.join(start_dir, default_name), filt)
                if not output_file:
                    self._cleanup_temp_slice()
                    return

            self._dispatch_worker(
                "qprinter",
                printer_name,
                target_pdf,
                {
                    "copies": copies_val,
                    "collate": collate_val,
                    "color": color_val,
                    "orient": orient_setting,
                    "page_indices": pages if not self.temp_slice_path else None,
                    "output_file": output_file,
                },
            )

    def _dispatch_worker(self, job_type: str, target: str, payload_or_pdf, kwargs):
        self.btn_print.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.prog_bar.setValue(0)
        self.prog_bar.setVisible(True)

        self.worker = _FastPrintWorker(job_type, target, payload_or_pdf, kwargs, parent=self)

        def on_prog(pct, msg):
            self.prog_bar.setValue(pct)
            self.lbl_status.setText(msg)

        def on_done(success, msg):
            self.btn_print.setEnabled(True)
            self.btn_cancel.setEnabled(True)
            self.prog_bar.setVisible(False)
            self._cleanup_temp_slice()

            if success:
                self.accept()
            else:
                QMessageBox.critical(self, "Print Error", msg)

        self.worker.progress.connect(on_prog)
        self.worker.finished.connect(on_done)
        self.worker.start()

    def _cleanup_temp_slice(self):
        if self.temp_slice_path and os.path.exists(self.temp_slice_path):
            with contextlib.suppress(Exception):
                os.unlink(self.temp_slice_path)
            self.temp_slice_path = None

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)
        self._cleanup_temp_slice()
        super().closeEvent(event)