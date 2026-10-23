# app/tools/_import_base.py
"""PDF_Editor – Base constants and utilities for the import engine."""

import os
import re
import sys
import importlib.util

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".ico", ".heic", ".heif", ".svg")
DOC_EXTS = (".docx", ".doc")
PPT_EXTS = (".pptx", ".ppt", ".pptm")
EXCEL_EXTS = (".xlsx", ".xls", ".xlsm")
TEXT_EXTS = (".json", ".txt", ".csv", ".xml", ".yaml", ".yml", ".ini", ".log", ".py", ".js", ".html", ".css", ".md", ".htm")
EBOOK_EXTS = (".epub", ".xps", ".oxps", ".cbz", ".fb2")

_ALL_SUPPORTED_FILTER = (
    "All Files (*);;"
    "All Supported Files (*.txt *.md *.markdown *.docx *.doc *.pptx *.ppt *.xlsx *.xls *.html *.htm *.epub "
    "*.png *.jpg *.jpeg *.bmp *.tiff *.webp *.gif *.pdf);;"
    "Office Documents (*.docx *.doc *.pptx *.ppt *.xlsx *.xls);;"
    "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.webp *.gif *.svg);;"
    "Text & Markdown (*.txt *.md *.markdown *.json *.csv *.xml *.log *.py);;"
    "Web & eBooks (*.html *.htm *.epub *.xps);;"
    "PDF Files (*.pdf)"
)

_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _clean(text: str) -> str:
    """Strip control characters that break XML-based formats."""
    return _CTRL_RE.sub('', text)

def _get_shared_tmp_dir() -> str | None:
    if os.environ.get("FLATPAK_ID"):
        cache_dir = os.path.expanduser("~/.cache/PDF_Editor_tmp")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir
    return None

def _has_win32com() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("win32com") is not None

class _NoContent:
    """Sentinel result: a converter produced a zero-page document."""
    __slots__ = ("skipped",)

    def __init__(self, skipped: int = 0):
        self.skipped = skipped