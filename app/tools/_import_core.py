# app/tools/_import_core.py
"""PDF_Editor – Central coordinator and format routing for PDF conversion."""

import contextlib
import html
import json
import logging
import os
import tempfile

import fitz
from PIL import Image, ImageOps
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from app.pdf_io import atomic_pdf_write
from app.tools._import_base import (
    IMAGE_EXTS, DOC_EXTS, PPT_EXTS, EXCEL_EXTS, TEXT_EXTS, EBOOK_EXTS,
    _get_shared_tmp_dir, _clean
)
from app.tools._import_office import convert_docx_to_pdf, convert_pptx_to_pdf, convert_xlsx_to_pdf
from app.tools._import_parsers import process_image, process_txt

_log = logging.getLogger(__name__)

def convert_image_to_pdf(image_path: str, pdf_path: str) -> None:
    abs_img = os.path.abspath(image_path)
    abs_pdf = os.path.abspath(pdf_path)

    try:
        with Image.open(abs_img) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    background.paste(img, mask=img.split()[3])
                elif img.mode == "LA":
                    background.paste(img, mask=img.split()[1])
                else:
                    rgba = img.convert("RGBA")
                    background.paste(rgba, mask=rgba.split()[3])
                img = background
            else:
                img = img.convert("RGB")
            img.save(abs_pdf, "PDF", resolution=150.0)
            if os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
                return
    except Exception as pil_err:
        _log.warning("Pillow image conversion failed for %s: %s; falling back to PyMuPDF.", image_path, pil_err)

    pdf_doc = fitz.open()
    try:
        if process_image(pdf_doc, image_path):
            atomic_pdf_write(pdf_doc, abs_pdf, close_writer=True)
            return
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_doc, "is_closed", False):
                pdf_doc.close()

    raise RuntimeError(f"Could not convert image: {image_path}")

def convert_text_to_pdf(text_path: str, pdf_path: str) -> None:
    with open(text_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    ext = os.path.splitext(text_path)[1].lower()
    doc = QTextDocument()

    if ext == ".json":
        is_html_json = False
        html_content = ""
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("metadata", {}).get("format") == "html" and "data" in parsed:
                is_html_json = True
                html_content = parsed["data"]
            else:
                content = json.dumps(parsed, indent=4)
        except Exception:
            pass
        if is_html_json:
            doc.setHtml(html_content)
        else:
            escaped = html.escape(content)
            doc.setHtml(
                f'<html><body style="font-family: Consolas, \'Courier New\', monospace; font-size: 9pt; white-space: pre-wrap;">{escaped}</body></html>'
            )
    elif ext in (".html", ".htm"):
        doc.setHtml(content)
    elif ext in (".md", ".markdown"):
        doc.setMarkdown(content)
    else:
        escaped = html.escape(content)
        doc.setHtml(
            f'<html><body style="font-family: Consolas, \'Courier New\', monospace; font-size: 9.5pt; white-space: pre-wrap; line-height: 1.4;">{escaped}</body></html>'
        )

    try:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(pdf_path)

        layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(15, 15, 15, 15),
            QPageLayout.Unit.Millimeter,
        )
        printer.setPageLayout(layout)
        doc.print_(printer)
        if os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0:
            return
    except Exception as q_err:
        _log.warning("QTextDocument print failed for %s: %s; falling back to PyMuPDF.", text_path, q_err)

    pdf_doc = fitz.open()
    try:
        process_txt(pdf_doc, text_path)
        if pdf_doc.page_count > 0:
            atomic_pdf_write(pdf_doc, pdf_path, close_writer=True)
            return
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_doc, "is_closed", False):
                pdf_doc.close()

    raise RuntimeError(f"Could not convert text document: {text_path}")

def convert_file_to_pdf(file_path: str, out_pdf_path: str | None = None) -> str:
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    is_pdf = file_path.lower().endswith(".pdf")
    if not is_pdf:
        try:
            with open(file_path, "rb") as f:
                header = f.read(1024)
                if b"%PDF-" in header:
                    return file_path
        except Exception:
            pass
    else:
        return file_path

    if out_pdf_path is None:
        fd, out_pdf_path = tempfile.mkstemp(prefix="PDF_Editor_conv_", suffix=".pdf", dir=_get_shared_tmp_dir())
        os.close(fd)

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in DOC_EXTS: convert_docx_to_pdf(file_path, out_pdf_path)
        elif ext in PPT_EXTS: convert_pptx_to_pdf(file_path, out_pdf_path)
        elif ext in EXCEL_EXTS: convert_xlsx_to_pdf(file_path, out_pdf_path)
        elif ext in IMAGE_EXTS: convert_image_to_pdf(file_path, out_pdf_path)
        elif ext in EBOOK_EXTS:
            converted = False
            try:
                src_doc = fitz.open(file_path)
                try:
                    if hasattr(src_doc, "convert_to_pdf"):
                        pdf_bytes = src_doc.convert_to_pdf()
                        pdf_part = fitz.open("pdf", pdf_bytes)
                        try:
                            atomic_pdf_write(pdf_part, out_pdf_path, close_writer=True)
                            converted = True
                        finally:
                            with contextlib.suppress(Exception):
                                if not getattr(pdf_part, "is_closed", False): pdf_part.close()
                finally:
                    with contextlib.suppress(Exception):
                        if not getattr(src_doc, "is_closed", False): src_doc.close()
            except Exception as e_conv:
                _log.warning("PyMuPDF convert_to_pdf failed for %s: %s", file_path, e_conv)
            if not converted: convert_text_to_pdf(file_path, out_pdf_path)
        elif ext in TEXT_EXTS:
            convert_text_to_pdf(file_path, out_pdf_path)
        else:
            try:
                convert_text_to_pdf(file_path, out_pdf_path)
            except Exception:
                doc = fitz.open()
                try:
                    process_txt(doc, file_path)
                    atomic_pdf_write(doc, out_pdf_path, close_writer=True)
                finally:
                    with contextlib.suppress(Exception):
                        if not getattr(doc, "is_closed", False): doc.close()

        if not os.path.isfile(out_pdf_path) or os.path.getsize(out_pdf_path) == 0:
            raise ValueError(f"No pages could be generated from '{file_path}'")

    except Exception:
        if os.path.exists(out_pdf_path):
            with contextlib.suppress(Exception):
                os.unlink(out_pdf_path)
        raise

    return out_pdf_path