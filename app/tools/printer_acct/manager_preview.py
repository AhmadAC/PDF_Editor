# app/tools/printer_acct/manager_preview.py
import contextlib
import os
import re
import subprocess
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QUrl, QPointF
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

try:
    import fitz
    HAS_FITZ = True
except Exception:
    fitz = None
    HAS_FITZ = False

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QComboBox,
        QLabel,
        QLineEdit,
        QScrollArea,
    )
    _Base = QWidget
else:
    _Base = object


class ManagerPreviewMixin(_Base):
    """Mixin handling PDF rendering, file browsing, and pagination."""

    if TYPE_CHECKING:
        file_input: QLineEdit
        doc_name_lbl: QLabel
        page_indicator_lbl: QLabel
        total_pages: int
        current_page: int
        pdf_doc: Any
        nav: Any
        duplex_combo: QComboBox
        color_combo: QComboBox
        page_scope_combo: QComboBox
        custom_range_input: QLineEdit
        scroll_area: QScrollArea | None
        preview_lbl: QLabel | None

        def save_preferences(self) -> None: ...

    def browse_pdf(self):
        current_val = self.file_input.text().strip()
        if current_val and os.path.exists(os.path.dirname(current_val)):
            start_dir = os.path.dirname(current_val) if os.path.isfile(current_val) else current_val
        elif os.path.exists("/var/mnt/shared-drive/2027"):
            start_dir = "/var/mnt/shared-drive/2027"
        else:
            start_dir = os.path.expanduser("~")

        dialog = QFileDialog(self, "Select PDF File", start_dir, "PDF Files (*.pdf);;All Files (*)")

        pinned_dirs = [
            os.path.expanduser("~"),
            "/var/mnt/shared-drive",
            "/var/mnt/shared-drive/2027",
            "/var/mnt/shared-drive/2027/MrCooperSuite/0HTMLOfflineQUiz/ahmadac.github.io/0_Quiz/0 Paper Hw",
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~/Documents"),
        ]

        sidebar_urls = [QUrl.fromLocalFile(p) for p in pinned_dirs if os.path.exists(p)]
        dialog.setSidebarUrls(sidebar_urls)

        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self.file_input.setText(files[0])
                self.save_preferences()

    def load_preview(self, file_path: str):
        if not file_path or not os.path.isfile(file_path):
            self.doc_name_lbl.setText("No document loaded.")
            self.page_indicator_lbl.setText("Page: - / -")
            self.total_pages = 0
            self.current_page = 1
            return

        filename = os.path.basename(file_path)
        self.doc_name_lbl.setText(f"<b>{filename}</b>")

        if getattr(self, "pdf_doc", None) is not None:
            self.pdf_doc.load(file_path)
            self.total_pages = self.pdf_doc.pageCount()
            self.current_page = 1
            self.update_page_indicator()
        elif HAS_FITZ and fitz is not None:
            doc = None
            try:
                doc = fitz.open(file_path)
                self.total_pages = doc.page_count
            except Exception:
                self.render_fallback_page(file_path, 1)
                return
            finally:
                if doc is not None:
                    with contextlib.suppress(Exception):
                        doc.close()
            self.current_page = 1
            self.render_fitz_page(file_path, 1)
        else:
            self.render_fallback_page(file_path, 1)

    def on_qtpdf_page_changed(self, page_idx: int):
        self.current_page = page_idx + 1
        self.update_page_indicator()

    def update_page_indicator(self):
        duplex_txt = self.duplex_combo.currentText()
        is_duplex = "2-Sided" in duplex_txt
        is_back = is_duplex and (self.current_page % 2 == 0)
        tag = ""
        if is_duplex:
            if is_back:
                tag = " (Back — Inverted)" if "Short Edge" in duplex_txt else " (Back)"
            else:
                tag = " (Front)"

        self.page_indicator_lbl.setText(f"Viewing: {self.current_page} / {self.total_pages}{tag}")
        if self.page_scope_combo.currentIndex() == 1:
            self.page_scope_combo.setItemText(1, f"Current Page Only (Page {self.current_page})")

    def go_prev_page(self):
        if self.current_page > 1:
            self.jump_to_page(self.current_page - 1)

    def go_next_page(self):
        if self.current_page < self.total_pages:
            self.jump_to_page(self.current_page + 1)

    def jump_to_page(self, page_num: int):
        self.current_page = page_num
        self.update_page_indicator()

        if getattr(self, "nav", None) is not None:
            self.nav.jump(page_num - 1, QPointF(0, 0), self.nav.currentZoom())
        elif HAS_FITZ:
            self.render_fitz_page(self.file_input.text().strip(), page_num)
        else:
            self.render_fallback_page(self.file_input.text().strip(), page_num)

    def render_fitz_page(self, file_path: str, page_num: int):
        if not HAS_FITZ or fitz is None:
            return
        doc = None
        try:
            doc = fitz.open(file_path)
            if 1 <= page_num <= doc.page_count:
                page = doc[page_num - 1]

                duplex_txt = self.duplex_combo.currentText()
                is_duplex = "2-Sided" in duplex_txt
                is_back_page = is_duplex and (page_num % 2 == 0)
                is_natural_landscape = (page.rect.width > page.rect.height)

                duplex_rot = 0
                if is_back_page:
                    if "Short Edge" in duplex_txt:
                        duplex_rot = 0 if is_natural_landscape else 180
                    elif "Long Edge" in duplex_txt:
                        duplex_rot = 180 if is_natural_landscape else 0

                if duplex_rot:
                    page.set_rotation((page.rotation + duplex_rot) % 360)

                target_w = max(400, (self.scroll_area.width() - 40) if self.scroll_area else 400)
                zoom = target_w / max(1.0, page.rect.width)

                Matrix = getattr(fitz, "Matrix")
                Pixmap = getattr(fitz, "Pixmap")
                mat = Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)

                color_txt = self.color_combo.currentText()
                if ("Black" in color_txt or "Mono" in color_txt) and pix.n != 1:
                    csGRAY = getattr(fitz, "csGRAY")
                    pix = Pixmap(csGRAY, pix)
                elif pix.n != 3 and ("Black" not in color_txt and "Mono" not in color_txt):
                    csRGB = getattr(fitz, "csRGB")
                    pix = Pixmap(csRGB, pix)

                fmt = QImage.Format.Format_Grayscale8 if pix.n == 1 else QImage.Format.Format_RGB888
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
                if self.preview_lbl is not None:
                    self.preview_lbl.setPixmap(QPixmap.fromImage(img))
        except Exception:
            pass
        finally:
            if doc is not None:
                with contextlib.suppress(Exception):
                    doc.close()

    def render_fallback_page(self, file_path: str, page_num: int):
        try:
            res = subprocess.run(["pdfinfo", file_path], capture_output=True, text=True)
            m = re.search(r"Pages:\s+([0-9]+)", res.stdout)
            if m:
                self.total_pages = int(m.group(1))
            self.current_page = page_num
            self.update_page_indicator()

            subprocess.run([
                "pdftoppm", "-png", "-r", "120",
                "-f", str(page_num), "-l", str(page_num),
                file_path, "/tmp/preview_pg"
            ], check=True)
            img_path = f"/tmp/preview_pg-{page_num}.png"
            if os.path.exists(img_path) and self.preview_lbl is not None:
                pix = QPixmap(img_path)
                self.preview_lbl.setPixmap(pix.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass

    def on_page_scope_changed(self, idx: int):
        self.custom_range_input.setEnabled(idx == 2)
        if idx == 1:
            self.page_scope_combo.setItemText(1, f"Current Page Only (Page {self.current_page})")
        else:
            self.page_scope_combo.setItemText(1, "Current Page Only")

    def set_range_odd(self):
        self.page_scope_combo.setCurrentIndex(2)
        if self.total_pages > 0:
            odds = [str(p) for p in range(1, self.total_pages + 1, 2)]
            self.custom_range_input.setText(", ".join(odds))

    def set_range_even(self):
        self.page_scope_combo.setCurrentIndex(2)
        if self.total_pages > 0:
            evens = [str(p) for p in range(2, self.total_pages + 1, 2)]
            self.custom_range_input.setText(", ".join(evens))

    def parse_pages(self, scope_idx: int, total_pages: int) -> list[int]:
        if scope_idx == 0:
            return list(range(1, total_pages + 1))
        elif scope_idx == 1:
            return [self.current_page]
        elif scope_idx == 2:
            raw_text = self.custom_range_input.text().strip()
            if not raw_text:
                raise ValueError("Custom page range is empty. Enter pages like '1, 3, 5-8'.")

            pages: set[int] = set()
            for part in raw_text.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    bounds = part.split("-")
                    if len(bounds) == 2 and bounds[0].strip().isdigit() and bounds[1].strip().isdigit():
                        start, end = int(bounds[0]), int(bounds[1])
                        for p in range(min(start, end), max(start, end) + 1):
                            if 1 <= p <= total_pages:
                                pages.add(p)
                elif part.isdigit():
                    p = int(part)
                    if 1 <= p <= total_pages:
                        pages.add(p)

            if not pages:
                raise ValueError(f"No valid pages found in '{raw_text}' for a {total_pages}-page document.")
            return sorted(list(pages))
        return list(range(1, total_pages + 1))