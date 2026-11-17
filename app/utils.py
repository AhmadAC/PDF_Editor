# app/utils.py

"""PDF_Editor – utility functions, SVG icon helpers, and reusable UI factory helpers."""

import contextlib
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import traceback
from typing import Any, cast

from PySide6.QtCore import Qt, QSize, QByteArray, QRectF
from PySide6.QtGui import QPalette, QColor, QPainter, QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QFileDialog, QApplication, QProgressBar,
)

from app.i18n import t
from app.constants import (
    ACCENT, DESKTOP,
    BG_BASE, BG_CARD, BG_INPUT,
    TEXT_PRI, TEXT_SEC,
    SUCCESS_DARK, SUCCESS_LIGHT,
    _LA, _LC, _LI, _LN, _LO, _LP, _LQ,
)

# ── Inline SVG Assets ─────────────────────────────────────────────────────────

SVG_STAMP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 20h14"/><path d="M5 17h14v-2a3 3 0 0 0-3-3h-1.5l1-4.5a3.5 3.5 0 1 0-7 0L9.5 12H8a3 3 0 0 0-3 3v2z"/></svg>"""
SVG_FILE_PDF = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>"""
SVG_UPLOAD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>"""
SVG_CLOSE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>"""
SVG_CHECK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>"""
SVG_LIGHTBULB = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/></svg>"""
SVG_ARROW_LEFT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>"""
SVG_FOLDER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>"""
SVG_TAB = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M9 4v6"/></svg>"""
SVG_SAVE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>"""
SVG_REFRESH = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>"""

SVG_COPY = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>"""
SVG_CUT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>"""
SVG_PASTE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>"""
SVG_CLONE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/></svg>"""
SVG_TRASH = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>"""
SVG_ZOOM_IN = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>"""
SVG_ZOOM_OUT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></svg>"""
SVG_ARROWS_H = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 8 22 12 18 16"/><polyline points="6 8 2 12 6 16"/><line x1="2" y1="12" x2="22" y2="12"/></svg>"""
SVG_EXPAND = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>"""
SVG_TH_LARGE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>"""
SVG_TH = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>"""
SVG_FILE_PLUS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>"""
SVG_FILE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>"""
SVG_FILE_EXPORT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M12 18v-6"/><polyline points="9 15 12 12 15 15"/></svg>"""
SVG_SORT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><polyline points="3 6 4 7 6 5"/><polyline points="3 12 4 13 6 11"/><polyline points="3 18 4 19 6 17"/></svg>"""
SVG_EXCHANGE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>"""
SVG_SWAP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>"""
SVG_MOVE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/></svg>"""
SVG_CROP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6.13 1L6 16a2 2 0 0 0 2 2h15"/><path d="M1 6.13L16 6a2 2 0 0 1 2 2v15"/></svg>"""
SVG_ROTATE_RIGHT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>"""
SVG_ROTATE_LEFT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>"""
SVG_SYNC = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>"""
SVG_TV = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="15" rx="2" ry="2"/><polyline points="17 2 12 7 7 2"/></svg>"""
SVG_LIST_OL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="10" y1="6" x2="21" y2="6"/><line x1="10" y1="12" x2="21" y2="12"/><line x1="10" y1="18" x2="21" y2="18"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/></svg>"""
SVG_PRINT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>"""
SVG_INFO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>"""
SVG_COMPRESS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>"""
SVG_LOCK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>"""
SVG_SEARCH = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>"""
SVG_EDIT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>"""
SVG_GROUP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/><path d="M13 7h4a2 2 0 0 1 2 2v2"/><path d="M11 17H7a2 2 0 0 1-2-2v-2"/></svg>"""

SVG_BARS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>"""
SVG_BOOKMARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>"""
SVG_MOON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>"""
SVG_SUN = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>"""
SVG_DOUBLE_LEFT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/></svg>"""
SVG_CHEVRON_LEFT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>"""
SVG_CHEVRON_RIGHT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>"""
SVG_DOUBLE_RIGHT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>"""
SVG_CHEVRON_UP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>"""
SVG_CHEVRON_DOWN = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>"""
SVG_ARROW_UP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>"""
SVG_ARROW_DOWN = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>"""
SVG_COLUMNS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>"""
SVG_ARROW_CIRCLE_UP = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="16 12 12 8 8 12"/><line x1="12" y1="16" x2="12" y2="8"/></svg>"""
SVG_HISTORY = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg>"""
SVG_EYE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>"""
SVG_POINTER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/><path d="M13 13l6 6"/></svg>"""
SVG_FONT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>"""
SVG_IMAGE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>"""
SVG_HIGHLIGHTER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l-6 6v3h9l3-3"/><path d="M22 12l-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/></svg>"""
SVG_VECTOR_SQUARE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="4" height="4"/><rect x="17" y="3" width="4" height="4"/><rect x="3" y="17" width="4" height="4"/><rect x="17" y="17" width="4" height="4"/><line x1="7" y1="5" x2="17" y2="5"/><line x1="7" y1="19" x2="17" y2="19"/><line x1="5" y1="7" x2="5" y2="17"/><line x1="19" y1="7" x2="19" y2="17"/></svg>"""
SVG_STICKY_NOTE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15.5 3H5a2 2 0 0 0-2 2v14c0 1.1.9 2 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M15 3v6h6"/></svg>"""
SVG_CLIPBOARD_LIST = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg>"""
SVG_SIGNATURE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 20h20"/><path d="M5 17c2-6 4-10 6-10 1 0 1 3 0 6s-1 4 0 4 3-5 5-5c1 0 1 3 2 3s2-1 3-2"/></svg>"""
SVG_PEN = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/></svg>"""
SVG_ERASER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20H7L3 16c-.8-.8-.8-2 0-2.8L13.4 2.8c.8-.8 2-.8 2.8 0l5 5c.8.8.8 2 0 2.8L12 20"/></svg>"""
SVG_BOLD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4h8a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/><path d="M6 12h9a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/></svg>"""
SVG_ITALIC = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="4" x2="10" y2="4"/><line x1="14" y1="20" x2="5" y2="20"/><line x1="15" y1="4" x2="9" y2="20"/></svg>"""
SVG_PLUS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>"""
SVG_MINUS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>"""
SVG_PLUS_CIRCLE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>"""
SVG_DOT_CIRCLE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3" fill="{color}"/></svg>"""
SVG_FILE_ALT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>"""
SVG_FILE_CODE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><polyline points="10 13 8 15 10 17"/><polyline points="14 17 16 15 14 13"/></svg>"""
SVG_BOOK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>"""

ICON_SVGS = {
    "fa5s.stamp": SVG_STAMP,
    "fa5s.cut": SVG_CUT,
    "fa5s.object-group": SVG_GROUP,
    "fa5s.sort": SVG_SORT,
    "fa5s.file-export": SVG_FILE_EXPORT,
    "fa5s.crop-alt": SVG_CROP,
    "fa5s.sync-alt": SVG_SYNC,
    "fa5s.compress-arrows-alt": SVG_COMPRESS,
    "fa5s.list-ol": SVG_LIST_OL,
    "fa5s.th": SVG_TH,
    "fa5s.th-large": SVG_TH_LARGE,
    "fa5s.lock": SVG_LOCK,
    "fa5s.eye": SVG_EYE,
    "fa5s.search": SVG_SEARCH,
    "fa5s.search-plus": SVG_ZOOM_IN,
    "fa5s.search-minus": SVG_ZOOM_OUT,
    "fa5s.exchange-alt": SVG_EXCHANGE,
    "fa5s.file-import": SVG_UPLOAD,
    "fa5s.edit": SVG_EDIT,
    "fa5s.info-circle": SVG_INFO,
    "fa5s.bars": SVG_BARS,
    "fa5s.folder-open": SVG_FOLDER,
    "fa5s.bookmark": SVG_BOOKMARK,
    "fa5s.moon": SVG_MOON,
    "fa5s.sun": SVG_SUN,
    "fa5s.print": SVG_PRINT,
    "fa5s.tv": SVG_TV,
    "fa5s.angle-double-left": SVG_DOUBLE_LEFT,
    "fa5s.chevron-left": SVG_CHEVRON_LEFT,
    "fa5s.chevron-right": SVG_CHEVRON_RIGHT,
    "fa5s.angle-double-right": SVG_DOUBLE_RIGHT,
    "fa5s.chevron-up": SVG_CHEVRON_UP,
    "fa5s.chevron-down": SVG_CHEVRON_DOWN,
    "fa5s.arrow-up": SVG_ARROW_UP,
    "fa5s.arrow-down": SVG_ARROW_DOWN,
    "fa5s.undo": SVG_ROTATE_LEFT,
    "fa5s.redo": SVG_ROTATE_RIGHT,
    "fa5s.columns": SVG_COLUMNS,
    "fa5s.arrow-circle-up": SVG_ARROW_CIRCLE_UP,
    "fa5s.history": SVG_HISTORY,
    "fa5s.check": SVG_CHECK,
    "fa5s.times": SVG_CLOSE,
    "fa5s.copy": SVG_COPY,
    "fa5s.paste": SVG_PASTE,
    "fa5s.clone": SVG_CLONE,
    "fa5s.trash-alt": SVG_TRASH,
    "fa5s.trash": SVG_TRASH,
    "fa5s.file-pdf": SVG_FILE_PDF,
    "fa5s.file-alt": SVG_FILE_ALT,
    "fa5s.file-word": SVG_FILE_ALT,
    "fa5s.file-powerpoint": SVG_FILE_ALT,
    "fa5s.file-excel": SVG_TH,
    "fa5s.file-code": SVG_FILE_CODE,
    "fa5s.book": SVG_BOOK,
    "fa5s.eraser": SVG_ERASER,
    "fa5s.font": SVG_FONT,
    "fa5s.image": SVG_IMAGE,
    "fa5s.highlighter": SVG_HIGHLIGHTER,
    "fa5s.vector-square": SVG_VECTOR_SQUARE,
    "fa5s.sticky-note": SVG_STICKY_NOTE,
    "fa5s.clipboard-list": SVG_CLIPBOARD_LIST,
    "fa5s.signature": SVG_SIGNATURE,
    "fa5s.pencil-alt": SVG_PEN,
    "fa5s.pen": SVG_PEN,
    "fa5s.mouse-pointer": SVG_POINTER,
    "fa5s.bold": SVG_BOLD,
    "fa5s.italic": SVG_ITALIC,
    "fa5s.plus": SVG_PLUS,
    "fa5s.minus": SVG_MINUS,
    "fa5s.plus-circle": SVG_PLUS_CIRCLE,
    "fa5s.dot-circle": SVG_DOT_CIRCLE,
}

_TOOL_SVGS = ICON_SVGS


def svg_pixmap(
    svg_xml: str,
    color: str | None = None,
    size: int = 18,
    dpr: float = 1.0,
    rotate: float = 0.0,
) -> QPixmap:
    """Render an inline SVG string into a transparent QPixmap."""
    if color is None:
        color = TEXT_PRI if is_dark() else _LP
    xml = svg_xml.format(color=color) if "{color}" in svg_xml else svg_xml
    renderer = QSvgRenderer(QByteArray(xml.encode("utf-8")))
    px = max(1, int(round(size * dpr)))
    pix = QPixmap(px, px)
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(dpr)
    if renderer.isValid():
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if rotate:
            p.translate(size / 2.0, size / 2.0)
            p.rotate(rotate)
            p.translate(-size / 2.0, -size / 2.0)
        renderer.render(p, QRectF(0.0, 0.0, float(size), float(size)))
        p.end()
    return pix


def svg_icon(svg_xml: str, color: str | None = None, size: int = 18, rotate: float = 0.0) -> QIcon:
    """Create a high-DPI QIcon from an inline SVG string."""
    dpr = 1.0
    app = QApplication.instance()
    if app is not None:
        try:
            scr = app.primaryScreen()
            if scr is not None:
                dpr = scr.devicePixelRatio() or 1.0
        except Exception:
            pass
    return QIcon(svg_pixmap(svg_xml, color=color, size=size, dpr=dpr, rotate=rotate))


def svg_for_name(name: str, color: str | None = None, size: int = 18, rotate: float = 0.0) -> QIcon:
    """Resolve an icon identifier or raw SVG XML into a QIcon using inline SVGs."""
    if name.lstrip().startswith("<svg"):
        return svg_icon(name, color=color, size=size, rotate=rotate)
    svg_src = ICON_SVGS.get(name, SVG_FILE_PDF)
    return svg_icon(svg_src, color=color, size=size, rotate=rotate)


def resource_path(rel):
    """Returns the correct path both in dev and in PyInstaller exe."""
    base = getattr(sys, '_MEIPASS',
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def reveal_file(path: str) -> None:
    """Open the OS file manager and highlight the given file when possible.
    Uses Dolphin on KDE Plasma / Linux when available, falling back to xdg-open."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            abs_path = os.path.abspath(path)
            if shutil.which("dolphin"):
                subprocess.Popen(["dolphin", "--select", abs_path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(abs_path) or "."])
    except OSError:
        pass


def open_folder(path: str) -> None:
    """Open the folder containing the given file (or the folder itself)."""
    try:
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if sys.platform == "win32":
            subprocess.Popen(["explorer", os.path.normpath(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif shutil.which("dolphin"):
            subprocess.Popen(["dolphin", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except OSError:
        pass


def format_size_localized(value: float, decimals: int = 1) -> str:
    """Format a numeric value using the system locale's decimal separator."""
    try:
        from PySide6.QtCore import QLocale
        return QLocale.system().toString(float(value), 'f', decimals)
    except Exception:
        return f"{value:.{decimals}f}"


def _make_palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        p.setColor(QPalette.ColorRole.Window,          QColor(BG_BASE))
        p.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Base,            QColor(BG_INPUT))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_CARD))
        p.setColor(QPalette.ColorRole.Text,            QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Button,          QColor("#1E2235"))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT_PRI))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    else:
        p.setColor(QPalette.ColorRole.Window,          QColor("#CBD5E1"))
        p.setColor(QPalette.ColorRole.WindowText,      QColor(_LP))
        p.setColor(QPalette.ColorRole.Base,            QColor(_LI))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor(_LN))
        p.setColor(QPalette.ColorRole.Text,            QColor(_LP))
        p.setColor(QPalette.ColorRole.Button,          QColor(_LC))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor(_LP))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(_LA))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    return p


def _paint_bg(widget: QWidget) -> None:
    """Makes QWidget subclasses honour 'background:' in the stylesheet."""
    from PySide6.QtWidgets import QStyleOption, QStyle
    opt = QStyleOption()
    opt.initFrom(widget)
    p = QPainter(widget)
    widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, widget)


def parse_pages(text: str, total: int) -> list:
    _MAX_PAGES = 100_000
    pages: list = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = a.strip(); b = b.strip()
            if not a and not b:
                raise ValueError(
                    t("tool.err.bad_page_input", text=part))
            try:
                a_int = int(a) if a else 1
                b_int = int(b) if b else total
            except ValueError as exc:
                raise ValueError(
                    t("tool.err.bad_page_input", text=part)) from exc
        else:
            try:
                a_int = b_int = int(part)
            except ValueError as exc:
                raise ValueError(
                    t("tool.err.bad_page_input", text=part)) from exc
        if "-" in part:
            if b_int - a_int + 1 > _MAX_PAGES:
                raise ValueError(
                    f"Range too large: {a_int}-{b_int} (max {_MAX_PAGES})")
            pages.extend(range(a_int - 1, b_int))
        else:
            pages.append(a_int - 1)
        if len(pages) > _MAX_PAGES:
            raise ValueError(f"Too many pages selected (max {_MAX_PAGES})")
    invalid = [p for p in pages if p < 0 or p >= total]
    if invalid:
        bad = sorted({(p + 1) if p >= 0 else 0 for p in invalid})
        raise ValueError(
            f"Pages out of range: {bad}  (valid: 1-{total})")
    return sorted(set(pages))


_IMAGE_PIXEL_LIMIT = 100_000_000


def check_image_size(path: str) -> tuple[bool, int, int]:
    """Return ``(ok, width, height)`` for the image at ``path``."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
    except Exception:
        return True, 0, 0
    return (w * h) <= _IMAGE_PIXEL_LIMIT, w, h


def pick_pdfs(parent: QWidget) -> list:
    paths, _ = QFileDialog.getOpenFileNames(
        parent, t("btn.select_pdfs"), DESKTOP, t("file_filter.pdf"))
    return paths


def pick_folder(parent: QWidget) -> str:
    return QFileDialog.getExistingDirectory(parent, t("btn.select_folder"))


_SECONDARY_PWD_ATTRS = ("_pwd_map", "_written_pwd")


def wipe_pdf_password(obj) -> None:
    """Best-effort wipe of the cached PDF password attribute on ``obj``."""
    obj._pdf_password = ""
    for name in _SECONDARY_PWD_ATTRS:
        cache = getattr(obj, name, None)
        if isinstance(cache, dict):
            cache.clear()


def prompt_pdf_password(path: str, parent=None) -> tuple[bool, str]:
    """Open the PDF and, if encrypted, prompt the user for a password."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return True, ""
    try:
        if not doc.needs_pass:
            return True, ""
        from app.editor.dialogs import _PdfPasswordDialog
        from app.pdf_password import authenticate_fitz
        from PySide6.QtWidgets import QDialog
        wrong = False
        while True:
            dlg = _PdfPasswordDialog(os.path.basename(path), wrong=wrong, parent=parent)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False, ""
            winner = authenticate_fitz(doc, dlg.password())
            if winner is not None:
                return True, winner
            wrong = True
    finally:
        doc.close()


# ── UI factory helpers ────────────────────────────────────────────────────────

def ToolHeader(icon_name: str, title: str, desc: str) -> QWidget:
    """Fixed header at the top of each tool using inline SVG icons."""
    w = QWidget(); w.setObjectName("tool_header")
    h = QHBoxLayout(w); h.setContentsMargins(20, 12, 20, 12); h.setSpacing(12)
    ico = QPushButton()
    ico.setIcon(svg_for_name(icon_name, color=ACCENT, size=22))
    ico.setIconSize(QSize(22, 22))
    ico.setFixedSize(36, 36)
    ico.setObjectName("th_icon")
    ico.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    col = QVBoxLayout(); col.setSpacing(3)
    t_lbl = QLabel(title); t_lbl.setObjectName("th_title")
    t_lbl.setWordWrap(True)
    d = QLabel(desc);  d.setObjectName("th_desc")
    d.setWordWrap(True)
    col.addWidget(t_lbl); col.addWidget(d)
    h.addWidget(ico, 0); h.addLayout(col, 1)
    w.setMinimumWidth(0)
    return w


def _action_progress_stylesheet(dark: bool) -> str:
    """Theme-aware stylesheet for the ActionBar's thin progress strip."""
    if dark:
        return (
            f"QProgressBar {{ background: {BG_INPUT}; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}"
        )
    return (
        f"QProgressBar {{ background: {_LO}; border-radius: 2px; }}"
        f"QProgressBar::chunk {{ background: {_LA}; border-radius: 2px; }}"
    )


class _ActionBarWidget(QWidget):
    """Action bar container widget holding a progress bar and theme update method."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress: QProgressBar | None = None

    def update_theme(self, dark: bool) -> None:
        if self.progress is not None:
            try:
                self.progress.setStyleSheet(_action_progress_stylesheet(dark))
            except RuntimeError:
                pass


def ActionBar(btn_text: str, slot) -> tuple[_ActionBarWidget, QPushButton]:
    """Compact right-aligned action button row with seamless background."""
    bar = _ActionBarWidget()
    bar.setObjectName("action_bar")
    v = QVBoxLayout(bar)
    v.setContentsMargins(24, 4, 24, 14)
    v.setSpacing(4)

    progress = QProgressBar()
    progress.setVisible(False)
    progress.setFixedHeight(4)
    progress.setTextVisible(False)
    progress.setObjectName("action_progress")
    progress.setStyleSheet(_action_progress_stylesheet(is_dark()))
    v.addWidget(progress)

    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.addStretch()

    btn = QPushButton(btn_text)
    btn.setObjectName("btn_action_small")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(32)
    btn.setMinimumWidth(80)
    btn.clicked.connect(slot)
    h.addWidget(btn)
    v.addLayout(h)
    bar.progress = progress

    return bar, btn


def section(text: str) -> QLabel:
    lbl = QLabel(text.upper()); lbl.setObjectName("section_lbl")
    return lbl


def info_lbl() -> QLabel:
    lbl = QLabel(""); lbl.setObjectName("info_lbl")
    return lbl


def primary_btn(text: str) -> QPushButton:
    b = QPushButton(text); b.setObjectName("btn_primary")
    b.setFixedHeight(38); return b


def danger_btn(text: str) -> QPushButton:
    b = QPushButton(text); b.setObjectName("btn_danger"); return b


def scrolled(widget: QWidget) -> QScrollArea:
    sa = QScrollArea(); sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame); sa.setWidget(widget)
    return sa


# ── Compression helper ────────────────────────────────────────────────────────

class CancelledError(Exception):
    """Raised when the user cancels a long-running operation."""


class WrongPasswordError(Exception):
    """Raised when an encrypted PDF cannot be unlocked with the supplied password."""


_COMPRESS_LEVELS = {
    "extreme":     {"dpi": 72,  "quality": 40, "grayscale": True},
    "recommended": {"dpi": 150, "quality": 65, "grayscale": False},
    "low":         {"dpi": 300, "quality": 80, "grayscale": False},
}

_GS_CACHE: tuple[bool, str | None] = (False, None)


def _find_gs():
    """Find Ghostscript executable."""
    global _GS_CACHE
    if _GS_CACHE[0]:
        return _GS_CACHE[1]
    import shutil as _sh, platform as _pl
    names = (["gswin64c", "gswin32c", "gs"]
             if _pl.system() == "Windows" else ["gs"])
    for n in names:
        p = _sh.which(n)
        if p and os.path.isfile(p):
            _GS_CACHE = (True, os.path.abspath(p))
            return _GS_CACHE[1]
    if _pl.system() == "Windows":
        import glob
        for pattern in [r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
                        r"C:\Program Files\gs\gs*\bin\gswin32c.exe",
                        r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe"]:
            matches = sorted(glob.glob(pattern), reverse=True)
            for p in matches:
                if os.path.isfile(p):
                    _GS_CACHE = (True, os.path.abspath(p))
                    return _GS_CACHE[1]
    _GS_CACHE = (True, None)
    return None


def _win_short_path(path: str) -> str:
    """On Windows, try to map ``path`` to its 8.3 short form."""
    if sys.platform != "win32" or not path:
        return path
    if not os.path.exists(path):
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 512)
        if n and buf.value:
            return buf.value
    except Exception:
        pass
    return path


def _is_valid_pdf(path: str) -> bool:
    """Return True if ``path`` is a readable, non-empty PDF with pages."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
    except OSError:
        return False
    try:
        import fitz
        doc = fitz.open(path)
        try:
            if doc.needs_pass:
                return False
            return doc.page_count > 0
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        if r.is_encrypted:
            return False
        return len(r.pages) > 0
    except Exception:
        pass
    return False


def _compress_pdf(src: str, dst: str, level: str = "recommended",
                  progress_fn=None, password: str | None = None) -> tuple:
    import tempfile, shutil, subprocess, time

    cfg     = _COMPRESS_LEVELS.get(level, _COMPRESS_LEVELS["recommended"])
    dpi     = cfg["dpi"]
    quality = cfg["quality"]
    gray    = cfg["grayscale"]
    before  = os.path.getsize(src)
    temps: list = []

    encrypted = False
    authed = False
    try:
        import fitz
        from app.pdf_password import authenticate_fitz
        probe = fitz.open(src)
        try:
            encrypted = probe.needs_pass
            if encrypted and password:
                winner = authenticate_fitz(probe, password)
                authed = winner is not None
                if winner is not None:
                    password = winner
        finally:
            probe.close()
    except Exception:
        try:
            from app.pdf_password import decrypt_pypdf
            from pypdf import PdfReader
            pr = PdfReader(src)
            encrypted = pr.is_encrypted
            if encrypted and password:
                winner = decrypt_pypdf(pr, password)
                authed = winner is not None
                if winner is not None:
                    password = winner
        except Exception:
            encrypted = False
    if encrypted and not authed:
        raise WrongPasswordError(t("tool.err.wrong_password"))
    if not encrypted:
        password = None

    def _prog(stage, cur=0, tot=0):
        if progress_fn and progress_fn(stage, cur, tot) is False:
            for _p in temps:
                try: os.unlink(_p)
                except Exception: pass
            raise CancelledError()

    # ── Pass A : Ghostscript ─────────────────────────────────────────────
    _prog("passA")
    gs = _find_gs()
    p = None
    if gs:
        try:
            presets = {
                "extreme":     "/screen",
                "recommended": "/ebook",
                "low":         "/printer",
            }
            fd, p = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
            cmd = [
                gs, "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={presets[level]}",
                "-dNOPAUSE", "-dQUIET", "-dBATCH",
                "-dDownsampleColorImages=true",
                "-dDownsampleGrayImages=true",
                "-dDownsampleMonoImages=true",
                f"-dColorImageResolution={dpi}",
                f"-dGrayImageResolution={dpi}",
                f"-dMonoImageResolution={max(dpi, 150)}",
                "-dColorImageDownsampleThreshold=1.0",
                "-dGrayImageDownsampleThreshold=1.0",
                "-dColorImageDownsampleType=/Bicubic",
                "-dGrayImageDownsampleType=/Bicubic",
            ]
            if gray:
                cmd += ["-sColorConversionStrategy=Gray",
                        "-dProcessColorModel=/DeviceGray",
                        "-dOverrideICC"]
            if password:
                cmd += [f"-sPDFPassword={password}"]
            _src_for_gs = _win_short_path(src)
            _out_for_gs = _win_short_path(p)
            cmd += [f"-sOutputFile={_out_for_gs}", _src_for_gs]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            deadline = time.monotonic() + 120
            cancelled = False
            try:
                while True:
                    if proc.poll() is not None:
                        break
                    if progress_fn and progress_fn("passA", 0, 0) is False:
                        cancelled = True
                        break
                    if time.monotonic() > deadline:
                        break
                    time.sleep(0.2)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try: proc.wait(timeout=2)
                        except subprocess.TimeoutExpired: pass
            if cancelled:
                try: os.unlink(p)
                except Exception: pass
                raise CancelledError()
            if proc.returncode == 0 and _is_valid_pdf(p):
                temps.append(p)
            else:
                try: os.unlink(p)
                except Exception: pass
        except CancelledError:
            raise
        except Exception:
            if p:
                try: os.unlink(p)
                except Exception: pass

    # ── Pass B : PyMuPDF ─────────────────────────────────────────────────
    _prog("passB_setup")
    doc = None
    p = None
    try:
        import fitz
        doc = fitz.open(src)
        if doc.needs_pass:
            if not (password and doc.authenticate(password)):
                raise WrongPasswordError(t("tool.err.wrong_password"))

        try:
            doc.scrub(metadata=True, xml_metadata=True,
                      thumbnails=True, attached_files=True)
        except Exception:
            pass

        _prog("passB_setup")

        try:
            doc.subset_fonts()
        except Exception:
            pass

        _prog("passB_images", 0, 1)
        try:
            doc.rewrite_images(
                dpi_threshold=dpi + 10,
                dpi_target=dpi,
                quality=quality,
                lossy=True,
                lossless=True,
                bitonal=True,
                color=True,
                gray=True,
                set_to_gray=gray,
            )
        except Exception:
            pass
        _prog("passB_images", 1, 1)

        _prog("passB_save")
        fd, p = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
        save_kw = dict(garbage=4, deflate=True, deflate_fonts=True, clean=True)
        try:
            cast(Any, doc).save(p, **save_kw, use_objstms=True)
        except TypeError:
            cast(Any, doc).save(p, **save_kw)
        if _is_valid_pdf(p):
            temps.append(p)
            p = None
    except CancelledError:
        raise
    except WrongPasswordError:
        raise
    except Exception:
        pass
    finally:
        if doc is not None:
            try: doc.close()
            except Exception: pass
        if p:
            try: os.unlink(p)
            except Exception: pass

    if not temps:
        raise RuntimeError(t("tool.compress.deps_missing"))

    best      = min(temps, key=lambda p: os.path.getsize(p))
    best_size = os.path.getsize(best)

    for _p in temps:
        if _p != best:
            try: os.unlink(_p)
            except Exception: pass

    if best_size >= before:
        with contextlib.suppress(Exception):
            os.unlink(best)
        raise ValueError(t("tool.compress.no_gain_detail",
                           before=f"{before/1024:.0f}",
                           after=f"{best_size/1024:.0f}"))

    dst_dir = os.path.dirname(dst) or "."
    try:
        os.replace(best, dst)
    except OSError:
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=dst_dir)
        os.close(fd)
        try:
            shutil.copyfile(best, tmp)
            os.replace(tmp, dst)
        except Exception:
            with contextlib.suppress(Exception):
                os.unlink(tmp)
            raise
        with contextlib.suppress(Exception):
            os.unlink(best)
    return before, best_size


def is_dark() -> bool:
    """Return the user's current dark-mode preference."""
    try:
        import json
        from app.i18n import _CONFIG_PATH
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("dark_mode", True))
    except Exception:
        return True


def error_color() -> str:
    return "#F87171" if is_dark() else "#DC2626"


def success_color(dark: bool | None = None) -> str:
    if dark is None:
        dark = is_dark()
    return SUCCESS_DARK if dark else SUCCESS_LIGHT


def result_label_style(dark: bool | None = None) -> str:
    return (f"font-weight:600; font-size:11pt; color:{success_color(dark)}; "
            "background:transparent; padding:10px 4px;")


# ─────────────────────────────────────────────────────────────────────────────
# Logging + user-friendly error dialogs
# ─────────────────────────────────────────────────────────────────────────────

_logging_initialised = False


def _log_path() -> str:
    """Return the path to the rotating log file (next to the user config)."""
    from app.i18n import _CONFIG_PATH
    return os.path.join(os.path.dirname(_CONFIG_PATH), "PDF_Editor.log")


def setup_logging() -> None:
    """Configure a rotating file logger and a console logger (stderr/stdout).
    Idempotent — safe to call multiple times."""
    global _logging_initialised
    if _logging_initialised:
        return
    _logging_initialised = True
    try:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
        root = logging.getLogger()
        level = logging.DEBUG if os.environ.get("PDF_Editor_DEBUG") or os.environ.get("DEBUG") else logging.INFO
        root.setLevel(level)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root.addHandler(console_handler)

        log_path = _log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except Exception:
        pass


def show_error(parent, exc: BaseException) -> None:
    """Show a translated, friendly error dialog with collapsible technical
    details, log the full error with traceback to stderr and log file,
    and automatically copy the complete error details to the PySide6 clipboard."""
    from PySide6.QtWidgets import QMessageBox

    if isinstance(exc, WrongPasswordError):
        logging.warning("Wrong PDF password: %s", exc)
        with contextlib.suppress(Exception):
            sys.stderr.write(f"\n[PDF_Editor WARNING] Wrong PDF password: {exc}\n")
            sys.stderr.flush()

        err_msg = str(exc) or t("tool.err.wrong_password")

        with contextlib.suppress(Exception):
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(err_msg)

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("msg.warning"))
        box.setText(err_msg)
        box.exec()
        return

    logging.error(
        "UI error surfaced: %s: %s",
        type(exc).__name__, exc, exc_info=exc,
    )

    try:
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        err_text = "".join(tb_lines)
    except Exception:
        err_text = f"{type(exc).__name__}: {exc}"

    with contextlib.suppress(Exception):
        sys.stderr.write(f"\n[PDF_Editor ERROR]\n{err_text}\n")
        sys.stderr.flush()

    with contextlib.suppress(Exception):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(err_text)
            sys.stderr.write("Error successfully copied to clipboard.\n")
            sys.stderr.flush()

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(t("msg.error"))
    box.setText(t("msg.unexpected") + "\n\n(Error details have been copied to your clipboard)")
    box.setDetailedText(err_text)
    box.exec()