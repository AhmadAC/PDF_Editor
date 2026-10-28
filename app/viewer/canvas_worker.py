# app/viewer/canvas_worker.py
from __future__ import annotations

"""PDF_Editor – Background worker and data structures for canvas page rendering and character-level text extraction."""

import os
from typing import Any
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
    """Extract text organized by visual reading lines with character-level bounding boxes."""
    try:
        raw = page.get_text("rawdict")
    except Exception:
        raw = {}

    lines_raw: list[dict] = []
    blocks = raw.get("blocks", []) if isinstance(raw, dict) else []

    for b in blocks:
        if not isinstance(b, dict) or b.get("type", 0) != 0:
            continue
        for ln in b.get("lines", []):
            if not isinstance(ln, dict):
                continue

            chars: list[tuple[float, float, float, float, str]] = []
            for s in ln.get("spans", []):
                if not isinstance(s, dict):
                    continue
                s_chars = s.get("chars", [])
                if s_chars:
                    for ch_dict in s_chars:
                        ch = ch_dict.get("c", "")
                        if not ch:
                            continue
                        c_bb = ch_dict.get("bbox", (0, 0, 0, 0))
                        cx0 = float(c_bb[0] - rx0)
                        cy0 = float(c_bb[1] - ry0)
                        cx1 = float(c_bb[2] - rx0)
                        cy1 = float(c_bb[3] - ry0)
                        chars.append((cx0, cy0, cx1, cy1, ch))
                else:
                    stxt = s.get("text", "")
                    if stxt:
                        s_bb = s.get("bbox", (0, 0, 0, 0))
                        sx0 = float(s_bb[0] - rx0)
                        sy0 = float(s_bb[1] - ry0)
                        sx1 = float(s_bb[2] - rx0)
                        sy1 = float(s_bb[3] - ry0)
                        cw = (sx1 - sx0) / max(1, len(stxt))
                        for ci, ch_char in enumerate(stxt):
                            chars.append((sx0 + ci * cw, sy0, sx0 + (ci + 1) * cw, sy1, ch_char))

            if not chars:
                continue

            chars.sort(key=lambda c: c[0])

            merged_chars = [chars[0]]
            for i in range(1, len(chars)):
                prev_c = merged_chars[-1]
                curr_c = chars[i]
                if not prev_c[4].isspace() and not curr_c[4].isspace():
                    gap = curr_c[0] - prev_c[2]
                    char_h = max(6.0, prev_c[3] - prev_c[1])
                    if gap >= max(1.8, char_h * 0.16):
                        merged_chars.append((prev_c[2], prev_c[1], curr_c[0], prev_c[3], " "))
                merged_chars.append(curr_c)

            real_x0 = merged_chars[0][0]
            real_x1 = merged_chars[-1][2]
            real_y0 = min(c[1] for c in merged_chars)
            real_y1 = max(c[3] for c in merged_chars)

            lines_raw.append({
                "bbox": (real_x0, real_y0, real_x1, real_y1),
                "y0": real_y0,
                "y1": real_y1,
                "chars": merged_chars,
                "text": "".join(c[4] for c in merged_chars),
            })

    if not lines_raw:
        try:
            raw_words = page.get_text("words")
            if raw_words:
                sorted_words = sort_words_in_reading_order(raw_words)
                by_line: dict[int, list] = {}
                for w in sorted_words:
                    by_line.setdefault(w[6], []).append(w)
                for line_idx in sorted(by_line.keys()):
                    line_words = by_line[line_idx]
                    chars = []
                    for w_idx, w in enumerate(line_words):
                        if w_idx > 0:
                            prev_w = line_words[w_idx - 1]
                            chars.append((prev_w[2] - rx0, prev_w[1] - ry0, w[0] - rx0, prev_w[3] - ry0, " "))
                        txt = w[4]
                        cw = ((w[2] - w[0]) / max(1, len(txt)))
                        for ci, ch_char in enumerate(txt):
                            chars.append((
                                (w[0] - rx0) + ci * cw,
                                w[1] - ry0,
                                (w[0] - rx0) + (ci + 1) * cw,
                                w[3] - ry0,
                                ch_char,
                            ))
                    if chars:
                        lines_raw.append({
                            "bbox": (chars[0][0], min(c[1] for c in chars), chars[-1][2], max(c[3] for c in chars)),
                            "y0": min(c[1] for c in chars),
                            "y1": max(c[3] for c in chars),
                            "chars": chars,
                            "text": "".join(c[4] for c in chars),
                        })
        except Exception:
            pass

    if not lines_raw:
        return []

    sorted_by_y = sorted(lines_raw, key=lambda l: (l["y0"], l["bbox"][0]))
    reading_lines: list[dict] = []
    line_groups: list[list[dict]] = []

    for l in sorted_by_y:
        placed = False
        l_h = max(6.0, l["y1"] - l["y0"])
        l_mid = (l["y0"] + l["y1"]) / 2.0
        for grp in line_groups:
            grp_y0 = min(item["y0"] for item in grp)
            grp_y1 = max(item["y1"] for item in grp)
            grp_mid = (grp_y0 + grp_y1) / 2.0
            grp_h = max(6.0, grp_y1 - grp_y0)
            overlap = min(l["y1"], grp_y1) - max(l["y0"], grp_y0)
            if overlap > 0.45 * min(l_h, grp_h) or abs(l_mid - grp_mid) < min(l_h, grp_h) * 0.40:
                grp.append(l)
                placed = True
                break
        if not placed:
            line_groups.append([l])

    for grp in line_groups:
        grp.sort(key=lambda item: item["bbox"][0])
        reading_lines.extend(grp)

    return reading_lines


def sort_words_in_reading_order(raw_words: list) -> list:
    """Sort words into visual natural reading order (top-to-bottom, left-to-right lines)."""
    if not raw_words:
        return []

    lines: list[list] = []
    sorted_by_y = sorted(raw_words, key=lambda w: (w[1], w[0]))

    for w in sorted_by_y:
        placed = False
        w_h = max(6.0, w[3] - w[1])
        w_mid_y = (w[1] + w[3]) / 2.0

        for line in lines:
            line_y0 = min(lw[1] for lw in line)
            line_y1 = max(lw[3] for lw in line)
            line_mid_y = (line_y0 + line_y1) / 2.0
            line_h = max(6.0, line_y1 - line_y0)

            overlap = min(w[3], line_y1) - max(w[1], line_y0)
            if overlap > 0.40 * min(w_h, line_h) or abs(w_mid_y - line_mid_y) < min(w_h, line_h) * 0.40:
                line.append(w)
                placed = True
                break

        if not placed:
            lines.append([w])

    for line in lines:
        line.sort(key=lambda w: w[0])

    lines.sort(key=lambda line: sum((w[1] + w[3]) / 2.0 for w in line) / len(line))

    result = []
    for line_idx, line in enumerate(lines):
        for word_idx, w in enumerate(line):
            result.append((
                w[0], w[1], w[2], w[3],
                w[4],
                w[5] if len(w) > 5 else 0,
                line_idx,
                word_idx,
            ))
    return result


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