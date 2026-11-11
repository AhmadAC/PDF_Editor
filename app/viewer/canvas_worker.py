# app\viewer\canvas_worker.py
from __future__ import annotations

"""PDF_Editor – Background worker and data structures for canvas page rendering and character-level text extraction."""

import os
from typing import Any
import fitz
from PySide6.QtCore import QObject, QRunnable, Signal

_PAGE_GAP       = 4    # px between pages
_BUFFER_PGS     = 2    # extra pages to pre-render outside the visible area
_MAX_THREADS    = max(2, min(4, os.cpu_count() or 2))   # simultaneous render workers
_NOTE_ICON_SIZE = 22   # note icon size in pixels
_MAX_RENDER_DIM = 8192 # Unifies Normal View high-res crispness with Presentation Mode


class _RenderSignals(QObject):
    page_ready = Signal(int, int, object, object)  # gen, idx, QPixmap, lines


class _PageEntry:
    __slots__ = ("y_off", "w", "h", "pixmap", "prev_pixmap", "words", "lines", "annots", "src_page")

    def __init__(self, y_off: int, w: int, h: int, src_page: int = 0):
        self.y_off       = y_off
        self.w           = w
        self.h           = h
        self.src_page    = src_page
        self.pixmap      = None   # QPixmap | None — filled by worker
        self.prev_pixmap = None   # QPixmap | None — retained during zoom for smooth scaling
        self.words       = None   # backward-compatibility slot
        self.lines       = None   # list[dict] | None — visual reading lines with character bounding boxes
        self.annots      = None   # list | None   — [(rect, text), ...]


def extract_page_lines(page, rx0: float = 0.0, ry0: float = 0.0) -> list[dict]:
    """Extract text organized by visual reading lines with character-level bounding boxes.
    Applies page.rotation_matrix to map unrotated PyMuPDF text into the visual screen space."""
    rot = page.rotation
    rot_mat = page.rotation_matrix
    print(f"[DEBUG] extract_page_lines started for page {page.number} (rotation={rot}, rect={page.rect})")
    try:
        words = page.get_text("words")
    except Exception as e:
        print(f"[DEBUG] get_text failed: {e}")
        words = []

    if not words:
        return []

    # 1. Transform each word bounding box to the visual rotated space of page.rect
    visual_words = []
    for w in words:
        # w = (x0, y0, x1, y1, word_str, block_no, line_no, word_no)
        word_str = w[4]
        if not word_str:
            continue
        rect = (fitz.Rect(w[0], w[1], w[2], w[3]) * rot_mat).normalize()
        vx0 = rect.x0 - rx0
        vy0 = rect.y0 - ry0
        vx1 = rect.x1 - rx0
        vy1 = rect.y1 - ry0
        visual_words.append((vx0, vy0, vx1, vy1, word_str))

    if not visual_words:
        return []

    # 2. Sort words by vertical center to cluster into horizontal lines
    visual_words.sort(key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))

    lines: list[list[tuple[float, float, float, float, str]]] = []
    for w in visual_words:
        w_h = max(6.0, w[3] - w[1])
        w_mid_y = (w[1] + w[3]) / 2.0
        placed = False
        for line in lines:
            line_mid_y = sum((lw[1] + lw[3]) / 2.0 for lw in line) / len(line)
            line_h = max(6.0, max(lw[3] for lw in line) - min(lw[1] for lw in line))
            if abs(w_mid_y - line_mid_y) < min(w_h, line_h) * 0.55:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])

    # 3. Sort words in each line left-to-right, and sort lines top-to-bottom
    for line in lines:
        line.sort(key=lambda w: w[0])
    lines.sort(key=lambda line: sum((lw[1] + lw[3]) / 2.0 for lw in line) / len(line))

    # 4. Construct character-level bounding boxes for accurate text selection
    lines_raw = []
    for line_words in lines:
        chars: list[tuple[float, float, float, float, str]] = []
        for w_idx, w in enumerate(line_words):
            if w_idx > 0:
                prev_w = line_words[w_idx - 1]
                gap = max(1.5, w[0] - prev_w[2])
                chars.append((prev_w[2], prev_w[1], prev_w[2] + gap, prev_w[3], " "))

            word_str = w[4]
            word_w = max(1.0, w[2] - w[0])
            cw = word_w / len(word_str)
            for ci, ch in enumerate(word_str):
                cx0 = w[0] + ci * cw
                cy0 = w[1]
                cx1 = w[0] + (ci + 1) * cw
                cy1 = w[3]
                chars.append((cx0, cy0, cx1, cy1, ch))

        if not chars:
            continue

        real_x0 = min(c[0] for c in chars)
        real_x1 = max(c[2] for c in chars)
        real_y0 = min(c[1] for c in chars)
        real_y1 = max(c[3] for c in chars)

        lines_raw.append({
            "bbox": (real_x0, real_y0, real_x1, real_y1),
            "y0": real_y0,
            "y1": real_y1,
            "chars": chars,
            "text": "".join(c[4] for c in chars),
        })

    print(f"[DEBUG] extract_page_lines produced {len(lines_raw)} lines for page {page.number}")
    if lines_raw:
        sample = lines_raw[0]
        print(f"[DEBUG] Sample line 0: bbox={sample['bbox']} (w={sample['bbox'][2]-sample['bbox'][0]:.1f}, h={sample['bbox'][3]-sample['bbox'][1]:.1f}) text={sample['text'][:30]!r}")
    return lines_raw


class _PageJob(QRunnable):
    """Renders a fitz page in a background thread with generation check and high-resolution vector sharpness."""

    def __init__(self, path: str, password: str, idx: int,
                 zoom: float, dpr: float, gen: int, signals: _RenderSignals,
                 night_mode: bool = False, rotation: int = 0,
                 crop: tuple[float, float, float, float] | None = None,
                 pos: int | None = None,
                 is_valid: Any = None):
        super().__init__()
        self._path       = path
        self._password   = password
        self._idx        = idx
        self._pos        = pos if pos is not None else idx
        self._zoom       = zoom
        self._dpr        = dpr
        self._gen        = gen
        self._night_mode = night_mode
        self._rotation   = rotation
        self._crop       = crop
        self.signals     = signals
        self._is_valid   = is_valid
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
            import fitz
            from PySide6.QtGui import QImage, QPixmap as QP

            doc = fitz.open(self._path)
            if self._password:
                doc.authenticate(self._password)

            if self.is_cancelled():
                return

            page = doc[self._idx]
            if self._crop:
                crop_rect = fitz.Rect(self._crop) & page.mediabox
                if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
                    page.set_cropbox(crop_rect)

            rot = self._rotation % 360
            if rot:
                page.set_rotation((page.rotation + rot) % 360)

            rz = self._zoom * self._dpr
            max_d = max(page.rect.width, page.rect.height)
            if max_d * rz > _MAX_RENDER_DIM:
                rz = float(_MAX_RENDER_DIM) / max_d

            if self.is_cancelled():
                return

            pix = None
            for attempt in range(3):
                try:
                    mat = fitz.Matrix(rz, rz)
                    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True)
                    break
                except (MemoryError, Exception) as exc:
                    if attempt < 2 and rz > 0.4:
                        rz = rz * 0.55
                    else:
                        raise exc

            if self.is_cancelled() or pix is None:
                return

            if self._night_mode:
                pix.invert_irect()

            rx0 = page.rect.x0
            ry0 = page.rect.y0
            lines = extract_page_lines(page, rx0, ry0)

            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)

            if self.is_cancelled():
                return

            try:
                qi = QImage(pix.samples, pix.width, pix.height,
                            pix.stride, QImage.Format.Format_RGB888).copy()
            except MemoryError:
                down_mat = fitz.Matrix(0.5, 0.5)
                pix2 = page.get_pixmap(matrix=down_mat, alpha=False, annots=True)
                if pix2.n != 3:
                    pix2 = fitz.Pixmap(fitz.csRGB, pix2)
                qi = QImage(pix2.samples, pix2.width, pix2.height,
                            pix2.stride, QImage.Format.Format_RGB888).copy()
                pix2 = None

            pix = None

            qp = QP.fromImage(qi)
            qp.setDevicePixelRatio(self._dpr)

            if not self.is_cancelled():
                self.signals.page_ready.emit(self._gen, self._pos, qp, lines)
        except Exception:
            import logging, traceback
            logging.error("Page render failed:\n%s", traceback.format_exc())
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass