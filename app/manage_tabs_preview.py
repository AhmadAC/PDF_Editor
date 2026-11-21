# app/manage_tabs_preview.py
"""PDF_Editor – Page 1 preview popup, thumbnail rendering, roles, and natural sorting helpers."""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any

import fitz
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.constants import ACCENT
from app.utils import SVG_FILE_PDF, svg_pixmap

ROLE_VIEWER_ID = Qt.ItemDataRole.UserRole
ROLE_DISPLAY_NAME = Qt.ItemDataRole.UserRole + 1
ROLE_PAGE_RANGE = Qt.ItemDataRole.UserRole + 2
ROLE_THUMBNAIL = Qt.ItemDataRole.UserRole + 3
ROLE_IS_ACTIVE = Qt.ItemDataRole.UserRole + 4
ROLE_IS_DIRTY = Qt.ItemDataRole.UserRole + 5
ROLE_DISK_PATH = Qt.ItemDataRole.UserRole + 6


def _natural_sort_key(s: str) -> list[Any]:
    """Split alphanumeric text into chunks so numbered strings like 'u1', 'u2', 'u10' sort in natural human order."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


def render_high_res_page1(
    widget: QWidget,
    v: Any,
    disk_path: str,
    max_w: int = 500,
    max_h: int = 680,
) -> QPixmap | None:
    """Render high-resolution Page 1 preview pixmap for the centered popup dialog."""
    dpr = widget.devicePixelRatioF() if hasattr(widget, "devicePixelRatioF") else 1.0
    if dpr <= 0:
        dpr = 1.0

    if (
        hasattr(v, "_fitz_doc")
        and v._fitz_doc
        and not getattr(v._fitz_doc, "is_closed", False)
    ):
        try:
            page0 = v._fitz_doc[0]
            r = page0.rect
            scale = min((max_w * dpr) / max(1.0, r.width), (max_h * dpr) / max(1.0, r.height))
            mat = fitz.Matrix(scale, scale)
            pix = page0.get_pixmap(matrix=mat, alpha=False)
            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            qimg = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
            ).copy()
            qpix = QPixmap.fromImage(qimg)
            qpix.setDevicePixelRatio(dpr)
            return qpix
        except Exception:
            pass

    curr_p = v.current_path() if hasattr(v, "current_path") else ""
    file_to_open = curr_p if (curr_p and os.path.isfile(curr_p)) else disk_path
    if file_to_open and os.path.isfile(file_to_open):
        try:
            doc = fitz.open(file_to_open)
            pwd = getattr(v, "_pdf_password", "")
            if pwd and doc.needs_pass:
                doc.authenticate(pwd)
            if doc.page_count > 0:
                page0 = doc[0]
                r = page0.rect
                scale = min((max_w * dpr) / max(1.0, r.width), (max_h * dpr) / max(1.0, r.height))
                mat = fitz.Matrix(scale, scale)
                pix = page0.get_pixmap(matrix=mat, alpha=False)
                if pix.n != 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                qimg = QImage(
                    pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
                ).copy()
                qpix = QPixmap.fromImage(qimg)
                qpix.setDevicePixelRatio(dpr)
                doc.close()
                return qpix
            doc.close()
        except Exception:
            pass

    return None


def get_page1_thumbnail_and_count(
    widget: QWidget,
    v: Any,
    disk_path: str,
    thumb_cache: dict[str, QPixmap],
) -> tuple[QPixmap | None, int]:
    """Fetch or render a mini thumbnail of Page 1 and determine the total page count for viewer `v`."""
    total_pages = 0
    if hasattr(v, "_canvas") and v._canvas:
        total_pages = v._canvas.page_count()
    if (
        total_pages == 0
        and hasattr(v, "_fitz_doc")
        and v._fitz_doc
        and not getattr(v._fitz_doc, "is_closed", False)
    ):
        with contextlib.suppress(Exception):
            total_pages = v._fitz_doc.page_count
    if total_pages == 0 and hasattr(v, "_thumbnails") and v._thumbnails:
        total_pages = v._thumbnails._model.rowCount()

    cache_key = f"{id(v)}_{disk_path}_{total_pages}"
    if cache_key in thumb_cache:
        return thumb_cache[cache_key], total_pages

    dpr = widget.devicePixelRatioF() if hasattr(widget, "devicePixelRatioF") else 1.0
    if dpr <= 0:
        dpr = 1.0

    target_w = int(34 * dpr)
    target_h = int(46 * dpr)

    # 1. Try from v._thumbnails cache
    if hasattr(v, "_thumbnails") and v._thumbnails:
        cached = v._thumbnails._model._cache.get(0)
        if cached is not None and not cached.isNull():
            scaled = cached.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            thumb_cache[cache_key] = scaled
            return scaled, total_pages

    # 2. Try from v._canvas entries
    if hasattr(v, "_canvas") and v._canvas and v._canvas._entries:
        entry0 = v._canvas._entries[0]
        c_pix = getattr(entry0, "pixmap", None) or getattr(entry0, "prev_pixmap", None)
        if c_pix is not None and not c_pix.isNull():
            scaled = c_pix.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            thumb_cache[cache_key] = scaled
            return scaled, total_pages

    # 3. Try from v._fitz_doc
    if (
        hasattr(v, "_fitz_doc")
        and v._fitz_doc
        and not getattr(v._fitz_doc, "is_closed", False)
    ):
        try:
            page0 = v._fitz_doc[0]
            r = page0.rect
            scale = min(target_w / max(1.0, r.width), target_h / max(1.0, r.height))
            mat = fitz.Matrix(scale, scale)
            pix = page0.get_pixmap(matrix=mat, alpha=False)
            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            qimg = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
            ).copy()
            qpix = QPixmap.fromImage(qimg)
            qpix.setDevicePixelRatio(dpr)
            thumb_cache[cache_key] = qpix
            return qpix, total_pages
        except Exception:
            pass

    # 4. Try from file path
    curr_p = v.current_path() if hasattr(v, "current_path") else ""
    file_to_open = curr_p if (curr_p and os.path.isfile(curr_p)) else disk_path
    if file_to_open and os.path.isfile(file_to_open):
        try:
            doc = fitz.open(file_to_open)
            pwd = getattr(v, "_pdf_password", "")
            if pwd and doc.needs_pass:
                doc.authenticate(pwd)
            if doc.page_count > 0:
                if total_pages == 0:
                    total_pages = doc.page_count
                page0 = doc[0]
                r = page0.rect
                scale = min(target_w / max(1.0, r.width), target_h / max(1.0, r.height))
                mat = fitz.Matrix(scale, scale)
                pix = page0.get_pixmap(matrix=mat, alpha=False)
                if pix.n != 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                qimg = QImage(
                    pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
                ).copy()
                qpix = QPixmap.fromImage(qimg)
                qpix.setDevicePixelRatio(dpr)
                doc.close()
                thumb_cache[cache_key] = qpix
                return qpix, total_pages
            doc.close()
        except Exception:
            pass

    return None, total_pages


class _PagePreviewPopup(QDialog):
    """High-resolution floating Page 1 preview popup that closes on click inside, click outside, or after 4 seconds."""

    def __init__(
        self,
        title: str,
        page_range: str,
        pixmap: QPixmap,
        viewer_id: int | None = None,
        dark: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._viewer_id = viewer_id
        self._notified_close = False

        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.setModal(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)

        card = QFrame(self)
        card.setObjectName("popup_preview_card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 10)
        card_lay.setSpacing(8)

        bg_col = "#1E1F22" if dark else "#FFFFFF"
        brd_col = ACCENT
        fg = "#F0F0F0" if dark else "#1A2B28"
        sec_fg = "#9CA3AF" if dark else "#6B7280"

        card.setStyleSheet(
            f"QFrame#popup_preview_card {{"
            f"  background-color: {bg_col};"
            f"  border: 1.5px solid {brd_col};"
            f"  border-radius: 10px;"
            f"}}"
        )

        # Header: Icon + Title + Page count badge + Auto-close notice
        top_h = QHBoxLayout()
        top_h.setContentsMargins(0, 0, 0, 0)
        top_h.setSpacing(8)

        ico_lbl = QLabel()
        ico_lbl.setPixmap(svg_pixmap(SVG_FILE_PDF, color=ACCENT, size=18))
        top_h.addWidget(ico_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {fg}; font-size: 11pt; font-weight: bold; background: transparent;")
        top_h.addWidget(title_lbl)

        if page_range:
            badge_lbl = QLabel(page_range)
            badge_bg = "rgba(0, 120, 212, 0.22)" if dark else "#D6E8FA"
            badge_fg = "#93C5FD" if dark else ACCENT
            badge_lbl.setStyleSheet(
                f"background: {badge_bg}; color: {badge_fg}; border-radius: 4px; "
                f"padding: 2px 8px; font-size: 8.5pt; font-weight: bold;"
            )
            top_h.addWidget(badge_lbl)

        top_h.addStretch(1)

        timer_hint = QLabel("Auto-closing in 4s (click anywhere to dismiss)")
        timer_hint.setStyleSheet(f"color: {sec_fg}; font-size: 9pt; background: transparent;")
        top_h.addWidget(timer_hint)
        card_lay.addLayout(top_h)

        # Center: High-Resolution Page 1 Image Preview clamped to available screen space
        img_lbl = QLabel()
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_lbl.setStyleSheet("border: 1px solid #444444; border-radius: 4px; background: #FFFFFF;")

        screen = QApplication.screenAt(QCursor.pos()) or self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        max_preview_w = min(460, max(240, avail.width() - 80))
        max_preview_h = min(620, max(260, avail.height() - 140))

        dpr = pixmap.devicePixelRatio() or 1.0
        scaled = pixmap.scaled(
            int(max_preview_w * dpr),
            int(max_preview_h * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        img_lbl.setPixmap(scaled)
        card_lay.addWidget(img_lbl, 1)

        # Bottom: Smooth 4-Second Countdown Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 4000)
        self.progress_bar.setValue(4000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {'#333333' if dark else '#CBD5E1'}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 1px; }}"
        )
        card_lay.addWidget(self.progress_bar)

        root.addWidget(card)

        # 4-Second Dismissal Timer
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        self.adjustSize()
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            cx = avail.center().x() - self.width() // 2
            cy = avail.center().y() - self.height() // 2
            x = max(avail.left() + 12, min(cx, avail.right() - self.width() - 12))
            y = max(avail.top() + 12, min(cy, avail.bottom() - self.height() - 12))
            self.move(x, y)

    def _notify_closed(self) -> None:
        self._timer.stop()
        if not self._notified_close:
            self._notified_close = True
            parent = self.parentWidget()
            if parent is not None and hasattr(parent, "_on_preview_popup_closed"):
                parent._on_preview_popup_closed(self._viewer_id)  # type: ignore[attr-defined]

    def _dismiss(self) -> None:
        self._notify_closed()
        self.close()

    def _on_tick(self) -> None:
        self._elapsed += 40
        remain = max(0, 4000 - self._elapsed)
        self.progress_bar.setValue(remain)
        if self._elapsed >= 4000:
            self._dismiss()

    def showEvent(self, event):
        self._notified_close = False
        super().showEvent(event)
        self._center_on_screen()

    def mousePressEvent(self, event):
        self._dismiss()
        event.accept()

    def keyPressEvent(self, event):
        self._dismiss()
        event.accept()

    def hideEvent(self, event):
        self._notify_closed()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._notify_closed()
        super().closeEvent(event)