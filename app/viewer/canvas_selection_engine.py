"""PDF_Editor – Character-level text selection engine and line extraction."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from app.viewer.canvas_worker import extract_page_lines

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas


def is_cjk(ch: str) -> bool:
    """Return True if character is a CJK ideograph, kana, hangul, or fullwidth punctuation."""
    if not ch:
        return False
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF or
        0x3400 <= cp <= 0x4DBF or
        0x20000 <= cp <= 0x2A6DF or
        0xF900 <= cp <= 0xFAFF or
        0x3000 <= cp <= 0x303F or
        0xFF00 <= cp <= 0xFFEF or
        0x3040 <= cp <= 0x309F or
        0x30A0 <= cp <= 0x30FF or
        0xAC00 <= cp <= 0xD7AF
    )


class CanvasSelectionEngine:
    """Encapsulates character boundaries, range selection calculation, and line caching."""

    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas
        self.is_dragging_text: bool = False

    def clear_selection(self) -> None:
        c = self.canvas
        c._drag_start = None
        c._drag_end = None
        c._sel_rects = []
        c._sel_text = ""
        self.is_dragging_text = False

    def ensure_entry_lines(self, entry: Any) -> list[dict]:
        """Ensure page lines with character-level details are extracted and cached."""
        lines = getattr(entry, "lines", None)
        if lines is not None:
            return lines
        c = self.canvas
        if not c._doc:
            return []
        src_idx = getattr(entry, "src_page", -1)
        if src_idx < 0 or src_idx >= c._doc.page_count:
            return []
        try:
            page = c._doc[src_idx]
            rot = c._page_rotations.get(src_idx, 0) % 360
            crop = c._page_crops.get(src_idx)
            orig_rot = page.rotation
            orig_crop = page.cropbox
            try:
                if crop:
                    crop_rect = fitz.Rect(crop) & page.mediabox
                    if not crop_rect.is_empty and crop_rect.width >= 10 and crop_rect.height >= 10:
                        page.set_cropbox(crop_rect)
                if rot:
                    page.set_rotation((orig_rot + rot) % 360)
                rx0 = page.rect.x0
                ry0 = page.rect.y0
                lines = extract_page_lines(page, rx0, ry0)
            finally:
                if rot:
                    page.set_rotation(orig_rot)
                if crop:
                    page.set_cropbox(orig_crop)

            entry.lines = lines
            return lines
        except Exception:
            return []

    def select_all(self) -> None:
        """Select all text across all pages in the document, copy it, and display highlights."""
        c = self.canvas
        if not c._entries or not c._doc:
            return

        first_p = -1
        first_l = 0
        first_c = 0
        last_p = -1
        last_l = 0
        last_c = 0

        for pi, entry in enumerate(c._entries):
            lines = self.ensure_entry_lines(entry)
            if lines and any(ln.get("chars") for ln in lines):
                if first_p < 0:
                    first_p = pi
                    first_l = 0
                    first_c = 0
                last_p = pi
                last_l = len(lines) - 1
                last_c = len(lines[-1]["chars"])

        if first_p < 0:
            self.clear_selection()
            c.text_copied.emit("")
            c.update()
            win: Any = c.window()
            set_status = getattr(win, "_set_status", None)
            if callable(set_status):
                set_status("ℹ No text found in document")
            return

        self.select_char_range(first_p, first_l, first_c, last_p, last_l, last_c)
        if c._sel_text:
            QApplication.clipboard().setText(c._sel_text)
        c.text_copied.emit(c._sel_text)
        c.update()

        win = c.window()
        set_status = getattr(win, "_set_status", None)
        if callable(set_status) and c._sel_text:
            char_count = len(c._sel_text)
            page_count = last_p - first_p + 1
            set_status(f"✔ Selected all text across {page_count} page(s) ({char_count:,} characters) — copied to clipboard")

    def find_char_at(self, lines: list[dict], px: float, py: float) -> tuple[int, int]:
        """Find the (line_idx, char_idx) boundary closest to page coordinates (px, py)."""
        if not lines:
            return (-1, -1)

        matching_lines = []
        for li, ln in enumerate(lines):
            if ln["y0"] - 3.0 <= py <= ln["y1"] + 3.0:
                matching_lines.append((li, ln))

        if matching_lines:
            best_li, best_ln = matching_lines[0]
            best_dist = float("inf")
            for li, ln in matching_lines:
                bx0, _, bx1, _ = ln["bbox"]
                if bx0 <= px <= bx1:
                    best_li, best_ln = li, ln
                    break
                d = min(abs(px - bx0), abs(px - bx1))
                if d < best_dist:
                    best_dist = d
                    best_li, best_ln = li, ln
            return (best_li, self.find_char_in_line(best_ln["chars"], px))

        if py < lines[0]["y0"]:
            return (0, 0 if px <= lines[0]["bbox"][0] else self.find_char_in_line(lines[0]["chars"], px))

        if py > lines[-1]["y1"]:
            last_line = lines[-1]
            return (len(lines) - 1, len(last_line["chars"]) if px >= last_line["bbox"][2] else self.find_char_in_line(last_line["chars"], px))

        best_li = 0
        best_dist = float("inf")
        for li, ln in enumerate(lines):
            mid_y = (ln["y0"] + ln["y1"]) / 2.0
            d = abs(py - mid_y)
            if d < best_dist:
                best_dist = d
                best_li = li

        chosen_ln = lines[best_li]
        return (best_li, self.find_char_in_line(chosen_ln["chars"], px))

    @staticmethod
    def find_char_in_line(chars: list[tuple[float, float, float, float, str]], px: float) -> int:
        """Find the character boundary index in a line for coordinate px."""
        if not chars:
            return 0
        if px <= chars[0][0]:
            return 0
        if px >= chars[-1][2]:
            return len(chars)

        for ci, c in enumerate(chars):
            mid_x = (c[0] + c[2]) / 2.0
            if px < mid_x:
                return ci
        return len(chars)

    def select_char_range(
        self,
        p1_page: int, l1: int, c1: int,
        p2_page: int, l2: int, c2: int,
    ) -> None:
        c = self.canvas
        if p1_page < 0 or p2_page < 0:
            c._sel_rects = []
            c._sel_text = ""
            return

        if (p1_page, l1, c1) > (p2_page, l2, c2):
            p_start, l_start, c_start = p2_page, l2, c2
            p_end, l_end, c_end = p1_page, l1, c1
        else:
            p_start, l_start, c_start = p1_page, l1, c1
            p_end, l_end, c_end = p2_page, l2, c2

        rects: list[QRect] = []
        page_texts: list[str] = []
        z = c._zoom or 1.0

        for pi in range(p_start, p_end + 1):
            if pi >= len(c._entries):
                continue
            entry = c._entries[pi]
            lines = self.ensure_entry_lines(entry)
            if not lines:
                continue

            pl_start = l_start if pi == p_start else 0
            pl_end = l_end if pi == p_end else len(lines) - 1
            if pl_start > pl_end or pl_start >= len(lines):
                continue

            x_off = c.page_x_offset(entry)
            line_strings: list[str] = []

            for li in range(pl_start, min(pl_end + 1, len(lines))):
                line = lines[li]
                chars = line.get("chars", [])
                if not chars:
                    continue

                sc = c_start if (pi == p_start and li == pl_start) else 0
                ec = c_end if (pi == p_end and li == pl_end) else len(chars)

                sc = max(0, min(sc, len(chars)))
                ec = max(0, min(ec, len(chars)))

                if sc >= ec:
                    continue

                selected_chars = chars[sc:ec]
                line_text = "".join(ch[4] for ch in selected_chars)
                if line_text:
                    line_strings.append(line_text)

                rx0 = x_off + int(round(selected_chars[0][0] * z))
                ry0 = entry.y_off + int(round(line["y0"] * z))
                rx1 = x_off + int(round(selected_chars[-1][2] * z))
                ry1 = entry.y_off + int(round(line["y1"] * z))

                if rx0 > rx1:
                    rx0, rx1 = rx1, rx0
                if ry0 > ry1:
                    ry0, ry1 = ry1, ry0

                rects.append(QRect(rx0, ry0, max(1, rx1 - rx0), max(1, ry1 - ry0)))

            if line_strings:
                page_texts.append("\n".join(line_strings))

        c._sel_rects = rects
        c._sel_text = "\n\n".join(page_texts)

    def compute_selection(self) -> None:
        c = self.canvas
        start = c._drag_start
        end = c._drag_end
        if start is None or end is None:
            return

        p1_page = c.page_at_y(start.y())
        p2_page = c.page_at_y(end.y())
        if not (0 <= p1_page < len(c._entries)) or not (0 <= p2_page < len(c._entries)):
            return

        e1 = c._entries[p1_page]
        x_off1 = c.page_x_offset(e1)
        z = c._zoom or 1.0
        p1_x = (start.x() - x_off1) / z
        p1_y = (start.y() - e1.y_off) / z

        e2 = c._entries[p2_page]
        x_off2 = c.page_x_offset(e2)
        p2_x = (end.x() - x_off2) / z
        p2_y = (end.y() - e2.y_off) / z

        lines1 = self.ensure_entry_lines(e1)
        lines2 = self.ensure_entry_lines(e2)

        l1, c1 = self.find_char_at(lines1, p1_x, p1_y)
        l2, c2 = self.find_char_at(lines2, p2_x, p2_y)

        if l1 < 0 and l2 < 0:
            c._sel_rects = []
            c._sel_text = ""
            return

        if l1 < 0:
            l1, c1 = 0, 0
        if l2 < 0:
            l2 = max(0, len(lines2) - 1)
            c2 = len(lines2[l2]["chars"]) if lines2 else 0

        self.select_char_range(p1_page, l1, c1, p2_page, l2, c2)

    def select_word_at(self, page_idx: int, pos: QPoint) -> bool:
        """Select a word at coordinates on double-click."""
        c = self.canvas
        if not (0 <= page_idx < len(c._entries)):
            return False
        entry = c._entries[page_idx]
        lines = self.ensure_entry_lines(entry)
        if not lines:
            return False
        x_off = c.page_x_offset(entry)
        z = c._zoom or 1.0
        px = (pos.x() - x_off) / z
        py = (pos.y() - entry.y_off) / z
        line_idx, char_idx = self.find_char_at(lines, px, py)
        if 0 <= line_idx < len(lines):
            line = lines[line_idx]
            chars = line.get("chars", [])
            if chars:
                c_idx = max(0, min(char_idx, len(chars) - 1))
                if not chars[c_idx][4].isspace():
                    sc = c_idx
                    while sc > 0 and not chars[sc - 1][4].isspace():
                        sc -= 1
                    ec = c_idx + 1
                    while ec < len(chars) and not chars[ec][4].isspace():
                        ec += 1
                    self.select_char_range(page_idx, line_idx, sc, page_idx, line_idx, ec)
                    if c._sel_text:
                        QApplication.clipboard().setText(c._sel_text)
                    c.text_copied.emit(c._sel_text)
                    c.update()
                    return True
        return False