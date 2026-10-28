
# app/tools/printer_acct/manager_print.py
import os
import tempfile
import subprocess
import contextlib
from typing import TYPE_CHECKING
from PySide6.QtWidgets import (
    QMessageBox, QWidget, QLineEdit, QComboBox, QSpinBox, 
    QCheckBox, QRadioButton, QPushButton, QProgressBar, QLabel,
    QFileDialog
)

from .worker import _FastPrintWorker

try:
    import fitz
    HAS_FITZ = True
except Exception:
    fitz = None
    HAS_FITZ = False

try:
    from pypdf import PdfReader, PdfWriter
    HAS_PYPDF = True
except Exception:
    PdfReader, PdfWriter = None, None
    HAS_PYPDF = False


if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object

class ManagerPrintMixin(_Base):
    """Mixin processing page ranges, slicing temporary PDFs and launching worker threads."""

    file_input: QLineEdit
    dest_mode_combo: QComboBox
    dest_ip_input: QLineEdit
    cups_queue_combo: QComboBox
    cmb_printer: QComboBox
    page_scope_combo: QComboBox
    total_pages: int
    cmb_duplex: QComboBox
    cmb_paper: QComboBox
    cmb_color: QComboBox
    spin_copies: QSpinBox
    chk_collate: QCheckBox
    cmb_orient: QComboBox
    user_input: QLineEdit
    pass_input: QLineEdit
    account_input: QLineEdit
    doc_path: str
    temp_slice_path: str | None
    password: str
    rad_custom: QRadioButton
    rad_current: QRadioButton
    send_pdf_btn: QPushButton
    print_progress: QProgressBar
    status_badge: QLabel
    print_thread: _FastPrintWorker | None

    def parse_pages(self, scope_idx: int, total_pages: int) -> list[int]: ...
    def save_preferences(self) -> None: ...

    def extract_pdf_pages_fast(self, input_pdf: str, page_list: list[int]) -> str:
        tmp_fd, out_path = tempfile.mkstemp(suffix=".pdf")
        os.close(tmp_fd)

        if HAS_FITZ and fitz is not None:
            doc = fitz.open(input_pdf)
            new_doc = fitz.open()
            for p in page_list:
                if 1 <= p <= doc.page_count:
                    new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)
            new_doc.save(out_path)
            new_doc.close()
            doc.close()
            return out_path

        if HAS_PYPDF and PdfReader is not None and PdfWriter is not None:
            reader = PdfReader(input_pdf)
            writer = PdfWriter()
            for p in page_list:
                if 1 <= p <= len(reader.pages):
                    writer.add_page(reader.pages[p-1])
            with open(out_path, "wb") as f:
                writer.write(f)
            return out_path

        page_str = ",".join(str(p) for p in page_list)
        try:
            res = subprocess.run(["qpdf", "--empty", "--pages", input_pdf, page_str, "--", out_path], capture_output=True)
            if res.returncode == 0:
                return out_path
        except FileNotFoundError:
            pass

        try:
            temp_dir = tempfile.mkdtemp()
            chunk_files = []
            for p in page_list:
                single_page = os.path.join(temp_dir, f"pg_{p}.pdf")
                subprocess.run(["pdfseparate", "-f", str(p), "-l", str(p), input_pdf, single_page], check=True)
                chunk_files.append(single_page)

            subprocess.run(["pdfunite"] + chunk_files + [out_path], check=True)
            for f in chunk_files:
                os.remove(f)
            os.rmdir(temp_dir)
            return out_path
        except Exception:
            if os.path.exists(out_path):
                os.remove(out_path)
            raise RuntimeError("Could not slice pages. Please install 'pymupdf' or 'qpdf'.")

    def execute_print_job(self):
        pdf_path = self.file_input.text().strip()
        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, "Invalid File", "Please select a valid PDF file first.")
            return

        is_direct = (self.dest_mode_combo.currentIndex() == 0)
        ip = self.dest_ip_input.text().strip()
        queue = self.cups_queue_combo.currentText().strip() or self.cmb_printer.currentText().strip()

        if is_direct and not ip:
            QMessageBox.warning(self, "Missing IP", "Printer IP Address is required for Direct Port 9100.")
            return
        if not is_direct and not queue:
            QMessageBox.warning(self, "Missing Queue", "No CUPS queue selected.")
            return

        scope_idx = self.page_scope_combo.currentIndex()
        try:
            pages_to_print = self.parse_pages(scope_idx, self.total_pages or 9999)
        except Exception as e:
            QMessageBox.warning(self, "Page Range Error", str(e))
            return

        if not pages_to_print:
            QMessageBox.warning(self, "Page Range Error", "No pages selected to print.")
            return

        self.save_preferences()

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

        user = self.user_input.text().strip() or "none"
        pin = self.pass_input.text().strip()
        acct = self.account_input.text().strip()

        target_pdf = pdf_path

        need_temp = (is_grayscale or scope_idx != 0 or len(pages_to_print) != self.total_pages)
        if need_temp:
            try:
                if HAS_FITZ and fitz is not None:
                    doc = fitz.open(pdf_path)
                    if self.password and doc.needs_pass:
                        doc.authenticate(self.password)
                    sliced_doc = fitz.open()

                    csGRAY = getattr(fitz, "csGRAY")

                    for p in pages_to_print:
                        p_idx = p - 1
                        if 0 <= p_idx < doc.page_count:
                            page = doc[p_idx]
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
                                sliced_doc.insert_pdf(doc, from_page=p_idx, to_page=p_idx)

                    doc.close()
                    fd, tmp_out = tempfile.mkstemp(prefix="PDF_Editor_print_", suffix=".pdf")
                    os.close(fd)
                    sliced_doc.save(tmp_out, garbage=4, deflate=True)
                    sliced_doc.close()
                    self.temp_slice_path = tmp_out
                    target_file = tmp_out
                else:
                    if scope_idx != 0 or len(pages_to_print) != self.total_pages:
                        target_pdf = self.extract_pdf_pages_fast(pdf_path, pages_to_print)
                        target_file = target_pdf
                    else:
                        target_file = target_pdf
            except Exception as e:
                QMessageBox.critical(self, "Page Processing Error", f"Failed to prepare pages for printing:\n{e}")
                return
        else:
            target_file = target_pdf

        if p_data == "socket":
            try:
                with open(target_file, "rb") as f:
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
                if self.temp_slice_path and os.path.exists(self.temp_slice_path):
                    with contextlib.suppress(Exception):
                        os.unlink(self.temp_slice_path)
                QMessageBox.critical(self, "Setup Error", str(e))
                return

        elif isinstance(p_data, tuple) and p_data[0] == "cups":
            self._dispatch_worker(
                "cups",
                queue,
                target_file,
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
            if any(k in (printer_name or "").lower() for k in ("pdf", "xps", "document writer", "print to file")):
                base, _ = os.path.splitext(os.path.basename(pdf_path))
                filt = "PDF Files (*.pdf)" if "xps" not in printer_name.lower() else "XPS Files (*.xps)"
                def_ext = ".xps" if "xps" in printer_name.lower() else ".pdf"
                default_name = f"{base}_printed{def_ext}"
                start_dir = os.path.dirname(pdf_path) if pdf_path else ""
                output_file, _ = QFileDialog.getSaveFileName(self, "Save Print Output As", os.path.join(start_dir, default_name), filt)
                if not output_file:
                    if self.temp_slice_path and os.path.exists(self.temp_slice_path):
                        with contextlib.suppress(Exception):
                            os.unlink(self.temp_slice_path)
                    return

            self._dispatch_worker(
                "qprinter",
                printer_name,
                target_file,
                {
                    "copies": copies_val,
                    "collate": collate_val,
                    "duplex": duplex_val,
                    "paper": paper_val,
                    "color": color_val,
                    "orient": orient_setting,
                    "page_indices": pages_to_print if not self.temp_slice_path else None,
                    "output_file": output_file,
                },
            )

    def _dispatch_worker(self, job_type: str, target: str, payload_or_pdf, kwargs):
        self.send_pdf_btn.setEnabled(False)
        self.print_progress.setValue(0)
        self.print_progress.setVisible(True)

        self.print_thread = _FastPrintWorker(job_type, target, payload_or_pdf, kwargs, parent=self)

        def on_prog(pct, msg):
            self.print_progress.setValue(pct)
            self.status_badge.setText(msg)

        def on_done(success, msg):
            self.send_pdf_btn.setEnabled(True)
            self.print_progress.setVisible(False)
            self.status_badge.setText("Engine: Ready")

            if self.temp_slice_path and os.path.exists(self.temp_slice_path):
                with contextlib.suppress(Exception):
                    os.unlink(self.temp_slice_path)
                self.temp_slice_path = None

            if success:
                QMessageBox.information(self, "Success", msg)
            else:
                QMessageBox.critical(self, "Print Error", msg)

        self.print_thread.progress.connect(on_prog)
        self.print_thread.finished.connect(on_done)
        self.print_thread.start()
