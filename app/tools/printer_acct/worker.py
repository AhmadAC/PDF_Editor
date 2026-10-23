
# app/tools/printer_acct/worker.py
import contextlib
import os
import socket
import subprocess
from PySide6.QtCore import QThread, Signal, QRectF
from PySide6.QtGui import QImage, QPainter, QPageLayout
from PySide6.QtPrintSupport import QPrinter


class _FastPrintWorker(QThread):
    """Background worker thread for asynchronous high-speed printing."""
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, job_type: str, target: str, payload_or_pdf, job_kwargs: dict, parent=None):
        super().__init__(parent)
        self.job_type = job_type  # "socket", "cups", "qprinter"
        self.target = target
        self.payload_or_pdf = payload_or_pdf
        self.kwargs = job_kwargs
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self.job_type == "socket":
                self._run_socket()
            elif self.job_type == "cups":
                self._run_cups()
            elif self.job_type == "qprinter":
                self._run_qprinter()
        except Exception as exc:
            self.finished.emit(False, str(exc))

    def _run_socket(self):
        ip = self.target
        port = int(self.kwargs.get("port", 9100))
        if isinstance(self.payload_or_pdf, str):
            if os.path.isfile(self.payload_or_pdf):
                with open(self.payload_or_pdf, "rb") as f:
                    data = f.read()
            else:
                data = self.payload_or_pdf.encode("latin-1")
        else:
            data = self.payload_or_pdf

        total = len(data)
        self.progress.emit(10, f"Connecting to {ip}:{port}...")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            s.settimeout(15)
            s.connect((ip, port))

            chunk_size = 65536
            sent = 0
            self.progress.emit(25, f"Streaming {total / 1024:.1f} KB to printer...")

            while sent < total:
                if self._is_cancelled:
                    raise RuntimeError("Print job cancelled by user.")
                chunk = data[sent:sent + chunk_size]
                s.sendall(chunk)
                sent += len(chunk)
                pct = 25 + int((sent / total) * 70)
                self.progress.emit(pct, f"Sending data: {sent // 1024} / {total // 1024} KB...")

            self.progress.emit(100, "Print job accepted by printer.")
        self.finished.emit(True, f"Successfully sent to {ip}:{port}!")

    def _run_cups(self):
        queue = self.target
        pdf_path = self.payload_or_pdf
        copies = self.kwargs.get("copies", 1)
        collate = self.kwargs.get("collate", True)
        duplex = self.kwargs.get("duplex", "1-Sided")
        paper = self.kwargs.get("paper", "A4")
        color = self.kwargs.get("color", "Color")
        user = self.kwargs.get("user", "")
        orient = self.kwargs.get("orient", "Auto (Match PDF)")
        is_color = (color == "Color")

        self.progress.emit(30, f"Spooling to CUPS queue '{queue}'...")
        cmd = ["lp", "-d", queue, "-n", str(copies)]

        if collate:
            cmd.extend(["-o", "Collate=True"])
        else:
            cmd.extend(["-o", "Collate=False"])

        if "Long Edge" in duplex:
            cmd.extend(["-o", "sides=two-sided-long-edge"])
        elif "Short Edge" in duplex:
            cmd.extend(["-o", "sides=two-sided-short-edge"])
        else:
            cmd.extend(["-o", "sides=one-sided"])

        if paper:
            cmd.extend(["-o", f"media={paper}"])

        if "Landscape" in orient:
            cmd.extend(["-o", "orientation-requested=4", "-o", "landscape"])
        elif "Portrait" in orient:
            cmd.extend(["-o", "orientation-requested=3", "-o", "portrait"])

        if is_color:
            cmd.extend(["-o", "ColorModel=Color", "-o", "print-color-mode=color"])
        else:
            cmd.extend([
                "-o", "ColorModel=Gray", "-o", "ColorModel=Grayscale",
                "-o", "CNColorMode=mono", "-o", "print-color-mode=monochrome",
            ])

        if user:
            cmd.extend(["-o", f"job-originating-user-name={user}"])

        cmd.append(str(pdf_path))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or f"CUPS returned code {res.returncode}")

        self.progress.emit(100, "Dispatched to CUPS spooler.")
        self.finished.emit(True, f"Sent to CUPS printer '{queue}'!\n{res.stdout.strip()}")

    def _run_qprinter(self):
        printer_name = self.target
        pdf_path = self.payload_or_pdf
        page_indices = self.kwargs.get("page_indices", [])
        copies = self.kwargs.get("copies", 1)
        collate = self.kwargs.get("collate", True)
        color = self.kwargs.get("color", "Color")
        orient = self.kwargs.get("orient", "Auto (Match PDF)")
        output_file = self.kwargs.get("output_file")
        is_color = (color == "Color")

        self.progress.emit(15, f"Initializing {printer_name}...")
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(printer_name)
        printer.setDocName(os.path.basename(str(pdf_path)))
        if output_file:
            printer.setOutputFileName(str(output_file))

        if is_color:
            printer.setColorMode(QPrinter.ColorMode.Color)
        else:
            printer.setColorMode(QPrinter.ColorMode.GrayScale)

        if "Landscape" in orient:
            printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        elif "Portrait" in orient:
            printer.setPageOrientation(QPageLayout.Orientation.Portrait)

        # Force software loop to guarantee collation and avoid driver crashes on virtual printers
        job_copies = copies
        printer.setCopyCount(1)

        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError(f"Could not open printer '{printer_name}' for writing.")

        doc = None
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            total_pages = doc.page_count
            pages_to_render = [p for p in page_indices if 0 <= p < total_pages] if page_indices else list(range(total_pages))
            total_steps = len(pages_to_render) * job_copies
            step_count = 0

            target_dpi = min(200, max(120, printer.resolution() // 2))
            zoom = target_dpi / 72.0
            
            Matrix = getattr(fitz, "Matrix")
            mat = Matrix(zoom, zoom)
            cs = getattr(fitz, "csRGB") if is_color else getattr(fitz, "csGRAY")
            fmt = QImage.Format.Format_RGB888 if is_color else QImage.Format.Format_Grayscale8

            first_page = True
            
            def print_page(p_idx):
                nonlocal step_count, first_page
                if self._is_cancelled:
                    raise RuntimeError("Print job cancelled.")

                step_count += 1
                pct = int((step_count / max(1, total_steps)) * 95)
                self.progress.emit(pct, f"Printing page {p_idx + 1} ({step_count}/{total_steps})...")

                if not first_page:
                    printer.newPage()
                first_page = False

                page = doc[p_idx]
                pix = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
                pix = None

                target_rect = QRectF(painter.viewport())
                source_rect = QRectF(0, 0, img.width(), img.height())
                scale = min(target_rect.width() / source_rect.width(), target_rect.height() / source_rect.height())
                w = source_rect.width() * scale
                h = source_rect.height() * scale
                x = target_rect.x() + (target_rect.width() - w) / 2
                y = target_rect.y() + (target_rect.height() - h) / 2
                painter.drawImage(QRectF(x, y, w, h), img, source_rect)

            if collate:
                for _ in range(job_copies):
                    for p_idx in pages_to_render:
                        print_page(p_idx)
            else:
                for p_idx in pages_to_render:
                    for _ in range(job_copies):
                        print_page(p_idx)

        finally:
            if doc is not None:
                with contextlib.suppress(Exception):
                    doc.close()
            painter.end()

        self.progress.emit(100, "Sent to Windows Print Spooler.")
        self.finished.emit(True, f"Successfully spooled to '{printer_name}'.")