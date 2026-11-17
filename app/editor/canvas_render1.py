# app/editor/canvas_render1.py

"""PDF canvas rendering worker, SVG icon cursors, and pixmap caching."""

from __future__ import annotations

import os
from typing import Any
import fitz
from PySide6.QtCore import Qt, Signal, QObject, QRunnable
from PySide6.QtGui import QPixmap, QPainter, QCursor

from app.utils import ICON_SVGS, SVG_FILE_PDF, svg_pixmap

_NOTE_ICON_SIZE = 22
_PAGE_GAP = 4
_BUFFER_PGS = 2
_MAX_THREADS = max(2, min(4, os.cpu_count() or 2))
_MAX_RENDER_DIM = 4096

_ICON_CURSORS: dict = {}
_OVERLAY_PIXMAP_CACHE: dict[tuple[str, float], QPixmap] = {}
_OVERLAY_PIXMAP_CACHE_MAX = 64


def _load_overlay_pixmap(path: str, mtime: float) -> QPixmap:
    key = (path, mtime)
    pix = _OVERLAY_PIXMAP_CACHE.get(key)
    if pix is None:
        if path.lower().endswith((".svg", ".svgz")):
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(path)
            if renderer.isValid():
                ds = renderer.defaultSize()
                w = max(ds.width(), 320)
                h = max(ds.height(), 120)
                pix = QPixmap(w, h)
                pix.fill(Qt.GlobalColor.transparent)
                p = QPainter(pix)
                renderer.render(p)
                p.end()
            else:
                pix = QPixmap(path)
        else:
            pix = QPixmap(path)
        if len(_OVERLAY_PIXMAP_CACHE) >= _OVERLAY_PIXMAP_CACHE_MAX:
            _OVERLAY_PIXMAP_CACHE.pop(next(iter(_OVERLAY_PIXMAP_CACHE)))
        _OVERLAY_PIXMAP_CACHE[key] = pix
    return pix


def clear_overlay_pixmap_cache() -> None:
    _OVERLAY_PIXMAP_CACHE.clear()


def _get_icon_cursor(icon_name: str, hx: int, hy: int,
                     size: int = 28, rotate: float = 0.0) -> QCursor:
    key = (icon_name, hx, hy, size, rotate)
    cur = _ICON_CURSORS.get(key)
    if cur is not None:
        return cur
    svg_src = ICON_SVGS.get(icon_name, SVG_FILE_PDF)
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    pad = (size - 24) // 2
    halo = svg_pixmap(svg_src, color="white", size=24, dpr=1.0, rotate=rotate)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        p.drawPixmap(pad + dx, pad + dy, halo)
    body = svg_pixmap(svg_src, color="black", size=24, dpr=1.0, rotate=rotate)
    p.drawPixmap(pad, pad, body)
    p.end()
    cur = QCursor(pix, hx, hy)
    _ICON_CURSORS[key] = cur
    return cur


class _EditRenderSignals(QObject):
    page_ready = Signal(int, int, object)  # gen, idx, QPixmap


class _EditPageJob(QRunnable):
    """Renders a single page in a background thread with memory-safe dimensions."""

    def __init__(self, path: str, idx: int, zoom: float, dpr: float,
                 gen: int, signals: _EditRenderSignals, password: str = "",
                 is_valid: Any = None):
        super().__init__()
        self._path = path
        self._idx = idx
        self._zoom = zoom
        self._dpr = dpr
        self._gen = gen
        self._password = password
        self.signals = signals
        self._is_valid = is_valid
        self.setAutoDelete(True)

    def is_cancelled(self) -> bool:
        if callable(self._is_valid):
            try:
                return not self._is_valid()
            except Exception:
                return False
        return False

    def run(self):
        if self.is_cancelled():
            return
        doc = None
        try:
            from PySide6.QtGui import QPixmap as QP, QImage
            doc = fitz.open(self._path)
            if doc.needs_pass and self._password:
                doc.authenticate(self._password)
            if self.is_cancelled():
                return
            page = doc[self._idx]
            rz = self._zoom * self._dpr
            max_d = max(page.rect.width, page.rect.height)
            if max_d * rz > _MAX_RENDER_DIM:
                rz = float(_MAX_RENDER_DIM) / max_d

            pix = None
            for attempt in range(3):
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(rz, rz), annots=True)
                    break
                except (MemoryError, Exception) as exc:
                    if attempt < 2 and rz > 0.4:
                        rz = rz * 0.55
                    else:
                        raise exc

            if self.is_cancelled() or pix is None:
                return

            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            qi = QImage(pix.samples, pix.width, pix.height,
                        pix.stride, QImage.Format.Format_RGB888).copy()
            pix = None
            qp = QP.fromImage(qi)
            qp.setDevicePixelRatio(self._dpr)
            if not self.is_cancelled():
                self.signals.page_ready.emit(self._gen, self._idx, qp)
        except Exception:
            import traceback, logging
            logging.error("Edit page render failed (idx=%d):\n%s",
                          self._idx, traceback.format_exc())
        finally:
            if doc is not None:
                try: doc.close()
                except Exception: pass