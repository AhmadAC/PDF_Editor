# app/tools/_import_office.py
"""PDF_Editor – Native office integration, format handlers, and layout improvements."""

import contextlib
import logging
import os
import zipfile

import fitz

from app.pdf_io import atomic_pdf_write
from app.tools._import_base import _has_win32com, _clean
from app.tools._import_external import _convert_via_onlyoffice, _convert_via_libreoffice, _convert_via_abiword
from app.tools._import_parsers import (
    docx_xml_to_lines, pptx_xml_to_lines, xlsx_xml_to_lines, render_lines_to_doc
)

_log = logging.getLogger(__name__)


def process_docx(doc: fitz.Document, docx_path: str) -> None:
    lines: list[tuple[str, int, bool]] = []
    try:
        import docx
        from docx.oxml.text.paragraph import CT_P
        from docx.oxml.table import CT_Tbl
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        
        dx = docx.Document(docx_path)
        
        for child in dx.element.body.iterchildren():
            if isinstance(child, CT_P):
                para = Paragraph(child, dx)
                text = para.text.strip()
                p_style = para.style
                style = (p_style.name or "").lower() if p_style is not None else ""
                if "heading 1" in style:
                    lines.append((text, 18, True))
                elif "heading 2" in style:
                    lines.append((text, 15, True))
                elif "heading 3" in style:
                    lines.append((text, 13, True))
                elif "heading" in style:
                    lines.append((text, 12, True))
                elif text:
                    bold = any(r.bold for r in para.runs if r.bold)
                    lines.append((text, 10, bold))
                else:
                    lines.append(("", 10, False))
            elif isinstance(child, CT_Tbl):
                table = Table(child, dx)
                lines.append(("", 10, False))
                for row in table.rows:
                    cells = [c.text.strip().replace('\n', ' ') for c in row.cells]
                    lines.append(("  |  ".join(cells), 9, False))
                lines.append(("", 10, False))
    except Exception as exc:
        _log.warning("python-docx processing failed for %s: %s; using XML fallback.", docx_path, exc)
        lines = []

    if not lines or not any(txt for txt, _, _ in lines):
        fallback_lines = docx_xml_to_lines(docx_path)
        if fallback_lines:
            lines = fallback_lines

    if lines and any(txt for txt, _, _ in lines):
        render_lines_to_doc(doc, lines)

    if doc.page_count == 0:
        try:
            with zipfile.ZipFile(docx_path) as z:
                media_files = sorted([m for m in z.namelist() if m.startswith("word/media/")])
                for mf in media_files:
                    img_bytes = z.read(mf)
                    try:
                        ext = os.path.splitext(mf)[1].lower().lstrip(".")
                        img = fitz.open(stream=img_bytes, filetype=ext)
                        if img.page_count > 0:
                            r = img[0].rect
                            page = doc.new_page(width=r.width, height=r.height)
                            page.insert_image(page.rect, stream=img_bytes)
                        img.close()
                    except Exception:
                        pass
        except Exception:
            pass

    if doc.page_count == 0:
        doc.new_page(width=595, height=842)

def process_pptx(doc: fitz.Document, pptx_path: str) -> None:
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
        slide_w = prs.slide_width.pt if prs.slide_width else 960
        slide_h = prs.slide_height.pt if prs.slide_height else 540
        for slide in prs.slides:
            page = doc.new_page(width=slide_w, height=slide_h)
            y = 40.0
            for shape in slide.shapes:
                if not getattr(shape, "has_text_frame", False):
                    continue
                tf = getattr(shape, "text_frame", None)
                if tf is None: continue
                for para in tf.paragraphs:
                    text = para.text.strip()
                    if not text: continue
                    size = 10.0; bold = False
                    if para.runs:
                        r = para.runs[0]
                        font = getattr(r, "font", None)
                        if font is not None and getattr(font, "size", None):
                            size = min(36.0, float(font.size.pt))
                        if font is not None:
                            bold = bool(getattr(font, "bold", False))
                    font_name = "hebo" if bold else "helv"
                    if y + size > slide_h - 20: break
                    try: page.insert_text(fitz.Point(40, y), text, fontsize=size, fontname=font_name)
                    except Exception: page.insert_text(fitz.Point(40, y), text, fontsize=size, fontname="helv")
                    y += size * 1.4
    except Exception:
        fallback_lines = pptx_xml_to_lines(pptx_path)
        if fallback_lines:
            render_lines_to_doc(doc, fallback_lines)
    if doc.page_count == 0:
        doc.new_page(width=960, height=540)

def process_xlsx(doc: fitz.Document, xlsx_path: str) -> None:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx_path, data_only=True)
        for ws in wb.worksheets:
            lines = []
            lines.append((f"— {ws.title} —", 12, True))
            lines.append(("", 10, False))
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                lines.append(("  |  ".join(cells), 8, False))
            lines.append(("", 10, False))
            render_lines_to_doc(doc, lines)
    except Exception:
        fallback_lines = xlsx_xml_to_lines(xlsx_path)
        if fallback_lines:
            render_lines_to_doc(doc, fallback_lines)
    if doc.page_count == 0:
        doc.new_page(width=595, height=842)

def convert_docx_to_pdf(doc_path: str, pdf_path: str) -> None:
    abs_doc = os.path.abspath(doc_path)
    abs_pdf = os.path.abspath(pdf_path)

    if _has_win32com():
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
                word = win32com.client.Dispatch("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0 
                try:
                    doc = word.Documents.Open(abs_doc, ReadOnly=True, ConfirmConversions=False)
                    doc.SaveAs2(abs_pdf, FileFormat=17) 
                    doc.Close(SaveChanges=0) 
                    if os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
                        return
                finally:
                    word.Quit()
                    pythoncom.CoUninitialize()
            except Exception as com_err:
                _log.warning("Word COM conversion failed for %s: %s; falling back to internal converter.", doc_path, com_err)
        except ImportError:
            pass

    if _convert_via_onlyoffice(doc_path, pdf_path): return
    if _convert_via_libreoffice(doc_path, pdf_path): return
    if _convert_via_abiword(doc_path, pdf_path): return

    pdf_doc = fitz.open()
    try:
        process_docx(pdf_doc, doc_path)
        if pdf_doc.page_count > 0:
            atomic_pdf_write(pdf_doc, abs_pdf, close_writer=True)
            return
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_doc, "is_closed", False):
                pdf_doc.close()

    raise RuntimeError(f"Could not convert Word document: {doc_path}")

def convert_pptx_to_pdf(ppt_path: str, pdf_path: str) -> None:
    abs_ppt = os.path.abspath(ppt_path)
    abs_pdf = os.path.abspath(pdf_path)

    if _has_win32com():
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
                powerpoint = win32com.client.Dispatch("PowerPoint.Application")
                try:
                    try: presentation = powerpoint.Presentations.Open(abs_ppt, ReadOnly=True, WithWindow=False)
                    except Exception: presentation = powerpoint.Presentations.Open(abs_ppt, ReadOnly=True)
                    presentation.SaveAs(abs_pdf, FileFormat=32) 
                    presentation.Close()
                    if os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
                        return
                finally:
                    powerpoint.Quit()
                    pythoncom.CoUninitialize()
            except Exception as com_err:
                _log.warning("PowerPoint COM conversion failed for %s: %s; falling back to internal converter.", ppt_path, com_err)
        except ImportError:
            pass

    if _convert_via_onlyoffice(ppt_path, pdf_path): return
    if _convert_via_libreoffice(ppt_path, pdf_path): return

    pdf_doc = fitz.open()
    try:
        process_pptx(pdf_doc, ppt_path)
        if pdf_doc.page_count > 0:
            atomic_pdf_write(pdf_doc, abs_pdf, close_writer=True)
            return
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_doc, "is_closed", False):
                pdf_doc.close()

    raise RuntimeError(f"Could not convert PowerPoint presentation: {ppt_path}")

def convert_xlsx_to_pdf(xlsx_path: str, pdf_path: str) -> None:
    abs_xlsx = os.path.abspath(xlsx_path)
    abs_pdf = os.path.abspath(pdf_path)

    if _has_win32com():
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
                excel = win32com.client.Dispatch("Excel.Application")
                excel.Visible = False
                excel.DisplayAlerts = False
                try:
                    wb = excel.Workbooks.Open(abs_xlsx, ReadOnly=True, UpdateLinks=0)
                    wb.ExportAsFixedFormat(0, abs_pdf)
                    wb.Close(SaveChanges=False)
                    if os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
                        return
                finally:
                    excel.Quit()
                    pythoncom.CoUninitialize()
            except Exception as com_err:
                _log.warning("Excel COM conversion failed for %s: %s; falling back to internal converter.", xlsx_path, com_err)
        except ImportError:
            pass

    if _convert_via_onlyoffice(xlsx_path, pdf_path): return
    if _convert_via_libreoffice(xlsx_path, pdf_path): return

    pdf_doc = fitz.open()
    try:
        process_xlsx(pdf_doc, xlsx_path)
        if pdf_doc.page_count > 0:
            atomic_pdf_write(pdf_doc, abs_pdf, close_writer=True)
            return
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_doc, "is_closed", False):
                pdf_doc.close()

    raise RuntimeError(f"Could not convert Excel spreadsheet: {xlsx_path}")