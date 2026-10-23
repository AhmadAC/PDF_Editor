# app/tools/_import_parsers.py
"""PDF_Editor – Native fallback parsers for rendering documents without dependencies."""

import contextlib
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

import fitz
from PIL import Image, ImageOps

from app.utils import check_image_size
from app.tools._import_base import _clean

_log = logging.getLogger(__name__)


def md_to_lines(md: str) -> list[tuple[str, int, bool]]:
    result = []
    for line in md.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            result.append((stripped[2:], 18, True))
        elif stripped.startswith("## "):
            result.append((stripped[3:], 15, True))
        elif stripped.startswith("### "):
            result.append((stripped[4:], 13, True))
        elif stripped.startswith("#### "):
            result.append((stripped[5:], 12, True))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            result.append(("  \u2022  " + stripped[2:], 10, False))
        elif stripped.startswith("```"):
            continue
        elif stripped in ("---", "***"):
            result.append(("\u2500" * 60, 8, False))
        elif stripped == "":
            result.append(("", 10, False))
        else:
            clean = stripped.replace("**", "").replace("__", "")
            clean = clean.replace("*", "").replace("_", "")
            clean = clean.replace("`", "")
            result.append((clean, 10, False))
    return result

def html_to_lines(soup: Any) -> list[tuple[str, int, bool]]:
    lines = []
    tag_map = {
        "h1": (18, True), "h2": (15, True), "h3": (13, True),
        "h4": (12, True), "h5": (11, True), "h6": (11, True),
    }
    for el in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "td", "th", "blockquote"]
    ):
        text = el.get_text(strip=True)
        if not text:
            continue
        tag = el.name
        if tag in tag_map:
            size, bold = tag_map[tag]
            lines.append((text, size, bold))
        elif tag == "li":
            lines.append(("  \u2022  " + text, 10, False))
        elif tag == "pre":
            for ln in text.split("\n"):
                lines.append((ln, 9, False))
        elif tag == "blockquote":
            lines.append(("  \u201c " + text + " \u201d", 10, False))
        elif tag in ("th",):
            lines.append((text, 10, True))
        else:
            lines.append((text, 10, False))
        if tag in tag_map:
            lines.append(("", 10, False))
    return lines

def docx_xml_to_lines(docx_path: str) -> list[tuple[str, int, bool]]:
    lines: list[tuple[str, int, bool]] = []
    try:
        with zipfile.ZipFile(docx_path) as z:
            if "word/document.xml" not in z.namelist():
                return lines
            xml_bytes = z.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for p in root.iter(f"{{{ns['w']}}}p"):
            texts = [t.text for t in p.iter(f"{{{ns['w']}}}t") if t.text]
            p_text = "".join(texts).strip()
            if p_text:
                is_bold = any(p.iter(f"{{{ns['w']}}}b"))
                p_style = ""
                for s in p.iter(f"{{{ns['w']}}}pStyle"):
                    val = s.attrib.get(f"{{{ns['w']}}}val", "")
                    if val:
                        p_style = val.lower()
                size = 10
                if "heading1" in p_style or "heading 1" in p_style:
                    size = 18; is_bold = True
                elif "heading2" in p_style or "heading 2" in p_style:
                    size = 15; is_bold = True
                elif "heading3" in p_style or "heading 3" in p_style:
                    size = 13; is_bold = True
                elif "heading" in p_style:
                    size = 12; is_bold = True
                lines.append((p_text, size, is_bold))
            else:
                lines.append(("", 10, False))
    except Exception as exc:
        _log.warning("docx_xml_to_lines fallback error: %s", exc)
    return lines

def pptx_xml_to_lines(pptx_path: str) -> list[tuple[str, int, bool]]:
    lines: list[tuple[str, int, bool]] = []
    try:
        with zipfile.ZipFile(pptx_path) as z:
            slide_files = sorted([name for name in z.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")])
            for s_file in slide_files:
                xml_bytes = z.read(s_file)
                root = ET.fromstring(xml_bytes)
                ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                for p in root.iter(f"{{{ns['a']}}}p"):
                    texts = [t.text for t in p.iter(f"{{{ns['a']}}}t") if t.text]
                    p_text = "".join(texts).strip()
                    if p_text:
                        lines.append((p_text, 12, False))
                lines.append(("", 10, False))
    except Exception as exc:
        _log.warning("pptx_xml_to_lines fallback error: %s", exc)
    return lines

def xlsx_xml_to_lines(xlsx_path: str) -> list[tuple[str, int, bool]]:
    lines: list[tuple[str, int, bool]] = []
    try:
        with zipfile.ZipFile(xlsx_path) as z:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                ss_bytes = z.read("xl/sharedStrings.xml")
                ss_root = ET.fromstring(ss_bytes)
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in ss_root.iter(f"{{{ns['s']}}}si"):
                    texts = [t.text for t in si.iter(f"{{{ns['s']}}}t") if t.text]
                    shared_strings.append("".join(texts))

            sheet_files = sorted([name for name in z.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")])
            ns_s = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for sf in sheet_files:
                xml_bytes = z.read(sf)
                root = ET.fromstring(xml_bytes)
                for row in root.iter(f"{{{ns_s['s']}}}row"):
                    row_vals = []
                    for c in row.iter(f"{{{ns_s['s']}}}c"):
                        cell_type = c.attrib.get("t", "")
                        v_el = c.find(f"{{{ns_s['s']}}}v")
                        val = v_el.text if v_el is not None and v_el.text else ""
                        if cell_type == "s" and val.isdigit():
                            idx = int(val)
                            val = shared_strings[idx] if idx < len(shared_strings) else val
                        row_vals.append(val)
                    if any(row_vals):
                        lines.append(("  |  ".join(row_vals), 9, False))
                lines.append(("", 10, False))
    except Exception as exc:
        _log.warning("xlsx_xml_to_lines fallback error: %s", exc)
    return lines

def render_lines_to_doc(doc: fitz.Document, lines: list[tuple[str, int, bool]]) -> None:
    page = None
    y = 50.0
    margin_x = 50.0
    max_w = 495.0
    max_y = 792.0
    for text, size, bold in lines:
        if not text:
            y += size * 0.8
            continue
        font = "hebo" if bold else "helv"
        line_height = size * 1.35
        if page is None or y + line_height > max_y:
            page = doc.new_page(width=595, height=842)
            y = 50.0
        rect = fitz.Rect(margin_x, y, margin_x + max_w, max_y)
        rc = page.insert_textbox(rect, text, fontsize=size, fontname=font)
        if rc < 0:
            page = doc.new_page(width=595, height=842)
            y = 50.0
            rect = fitz.Rect(margin_x, y, margin_x + max_w, max_y)
            page.insert_textbox(rect, text, fontsize=size, fontname=font)
        est_chars_per_line = max(10, int(max_w / (size * 0.52)))
        est_lines = max(1, (len(text) + est_chars_per_line - 1) // est_chars_per_line)
        y += est_lines * line_height + 4.0

def process_image(doc: fitz.Document, img_path: str) -> bool:
    ok, _w, _h = check_image_size(img_path)
    if not ok:
        return False
    try:
        img = fitz.open(img_path)
        try:
            if img.page_count == 0: return False
            rect = img[0].rect
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, filename=img_path)
            return True
        finally:
            with contextlib.suppress(Exception):
                if not getattr(img, "is_closed", False):
                    img.close()
    except Exception:
        try:
            with Image.open(img_path) as pimg:
                pimg = ImageOps.exif_transpose(pimg)
                w, h = pimg.size
            page = doc.new_page(width=w, height=h)
            page.insert_image(page.rect, filename=img_path)
            return True
        except Exception:
            return False

def process_txt(doc: fitz.Document, txt_path: str) -> None:
    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    lines = content.split("\n")
    page = None
    y = 50.0
    fontsize = 10.0
    line_height = fontsize * 1.4
    margin_x = 50.0
    max_y = 792.0
    max_width = 495.0
    for line in lines:
        if page is None or y + fontsize > max_y:
            page = doc.new_page(width=595, height=842)
            y = 50.0
        if not line.strip():
            y += line_height
            continue
        rect = fitz.Rect(margin_x, y, margin_x + max_width, max_y)
        used = page.insert_textbox(rect, line, fontsize=fontsize, fontname="helv")
        if used < 0:
            page = doc.new_page(width=595, height=842)
            y = 50.0
            rect = fitz.Rect(margin_x, y, margin_x + max_width, max_y)
            used = page.insert_textbox(rect, line, fontsize=fontsize, fontname="helv")
        est_lines = max(1, len(line) * fontsize * 0.5 / max_width + 1)
        y += line_height * est_lines

def process_md(doc: fitz.Document, md_path: str) -> None:
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        md_text = f.read()
    lines = md_to_lines(md_text)
    render_lines_to_doc(doc, lines)

def process_html(doc: fitz.Document, html_path: str) -> None:
    try:
        from bs4 import BeautifulSoup
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        lines = html_to_lines(soup)
        render_lines_to_doc(doc, lines)
    except Exception:
        fallback_lines = []
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        clean = re.sub(r"<[^>]+>", " ", raw).strip()
        for line in clean.split("\n"):
            s = line.strip()
            if s: fallback_lines.append((s, 10, False))
        if fallback_lines:
            render_lines_to_doc(doc, fallback_lines)
        else:
            raise

def process_epub(doc: fitz.Document, epub_path: str) -> None:
    converted = False
    try:
        epub_doc = fitz.open(epub_path)
        try:
            pdf_bytes = epub_doc.convert_to_pdf()
            pdf_part = fitz.open("pdf", pdf_bytes)
            try: doc.insert_pdf(pdf_part)
            finally:
                with contextlib.suppress(Exception):
                    if not getattr(pdf_part, "is_closed", False): pdf_part.close()
            converted = True
        finally:
            with contextlib.suppress(Exception):
                if not getattr(epub_doc, "is_closed", False): epub_doc.close()
    except Exception:
        converted = False
    if not converted:
        try:
            epub_doc = fitz.open(epub_path)
            try:
                lines = []
                for page in epub_doc:
                    raw_text = page.get_text("text")
                    text = str(raw_text or "")
                    for line in text.split("\n"):
                        if line.strip(): lines.append((line.strip(), 10, False))
                        else: lines.append(("", 10, False))
                if lines: render_lines_to_doc(doc, lines)
            finally:
                with contextlib.suppress(Exception):
                    if not getattr(epub_doc, "is_closed", False): epub_doc.close()
        except Exception: pass

def process_pdf(doc: fitz.Document, pdf_path: str) -> None:
    pdf_part = fitz.open(pdf_path)
    try:
        doc.insert_pdf(pdf_part)
    finally:
        with contextlib.suppress(Exception):
            if not getattr(pdf_part, "is_closed", False):
                pdf_part.close()