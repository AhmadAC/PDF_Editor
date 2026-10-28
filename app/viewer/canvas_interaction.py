# app/viewer/canvas_interaction.py

"""PDF_Editor – Interaction handler for _SelectCanvas (Mouse, Keyboard, Context Menus, Signatures, Shortcuts)."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Any

import fitz
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QProxyStyle, QStyle
import qtawesome as qta

from app.constants import ACCENT, TEXT_SEC
from app.i18n import t
from app.viewer.canvas_worker import _NOTE_ICON_SIZE, extract_page_lines

if TYPE_CHECKING:
    from app.viewer.canvas_1 import _SelectCanvas

# Safe fallback for PyMuPDF annotation constant to satisfy Pylance
_PDF_ANNOT_TEXT: int = getattr(fitz, "PDF_ANNOT_TEXT", 0)


class _ScrollableMenuStyle(QProxyStyle):
    """Enforce single-column scrollable QMenu across all platforms."""
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


def is_cjk(ch: str) -> bool:
    """Return True if character is a CJK ideograph, kana, hangul, or fullwidth punctuation."""
    if not ch:
        return False
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF or      # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or      # CJK Unified Ideographs Extension A
        0x20000 <= cp <= 0x2A6DF or    # Extension B
        0xF900 <= cp <= 0xFAFF or      # CJK Compatibility Ideographs
        0x3000 <= cp <= 0x303F or      # CJK Symbols and Punctuation
        0xFF00 <= cp <= 0xFFEF or      # Halfwidth and Fullwidth Forms
        0x3040 <= cp <= 0x309F or      # Hiragana
        0x30A0 <= cp <= 0x30FF or      # Katakana
        0xAC00 <= cp <= 0xD7AF         # Hangul Syllables
    )


class CanvasInteractionHandler:
    """Handles text selection, note balloon interaction, shortcuts, signatures, and context actions."""

    def __init__(self, canvas: _SelectCanvas):
        self.canvas = canvas
        self._is_dragging_text: bool = False

    def clear_selection(self):
        c = self.canvas
        c._drag_start = None
        c._drag_end   = None
        c._sel_rects  = []
        c._sel_text   = ""
        self._is_dragging_text = False

    def ensure_entry_lines(self, entry) -> list[dict]:
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

    def select_all(self):
        """Select all text across all pages in the open document, copy it, and display highlights."""
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
            if lines and any(l.get("chars") for l in lines):
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

        self._select_char_range(first_p, first_l, first_c, last_p, last_l, last_c)
        if c._sel_text:
            QApplication.clipboard().setText(c._sel_text)
        c.text_copied.emit(c._sel_text)
        c.update()

        win: Any = c.window()
        set_status = getattr(win, "_set_status", None)
        if callable(set_status) and c._sel_text:
            char_count = len(c._sel_text)
            page_count = last_p - first_p + 1
            set_status(f"✔ Selected all text across {page_count} page(s) ({char_count:,} characters) — copied to clipboard")

    def _find_char_at(self, lines: list[dict], px: float, py: float) -> tuple[int, int]:
        """Find the (line_idx, char_idx) boundary closest to page coordinates (px, py)."""
        if not lines:
            return (-1, -1)

        # Check if py is vertically inside any line
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
            return (best_li, self._find_char_in_line(best_ln["chars"], px))

        # If py is above all lines
        if py < lines[0]["y0"]:
            return (0, 0 if px <= lines[0]["bbox"][0] else self._find_char_in_line(lines[0]["chars"], px))

        # If py is below all lines
        if py > lines[-1]["y1"]:
            last_line = lines[-1]
            return (len(lines) - 1, len(last_line["chars"]) if px >= last_line["bbox"][2] else self._find_char_in_line(last_line["chars"], px))

        # In between lines: pick line with minimal vertical distance
        best_li = 0
        best_dist = float("inf")
        for li, ln in enumerate(lines):
            mid_y = (ln["y0"] + ln["y1"]) / 2.0
            d = abs(py - mid_y)
            if d < best_dist:
                best_dist = d
                best_li = li

        chosen_ln = lines[best_li]
        return (best_li, self._find_char_in_line(chosen_ln["chars"], px))

    @staticmethod
    def _find_char_in_line(chars: list[tuple[float, float, float, float, str]], px: float) -> int:
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

    def _select_char_range(
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
                rects.append(QRect(rx0, ry0, max(1, rx1 - rx0), max(1, ry1 - ry0)))

            if line_strings:
                page_texts.append("\n".join(line_strings))

        c._sel_rects = rects
        c._sel_text = "\n\n".join(page_texts)

    def compute_selection(self):
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

        l1, c1 = self._find_char_at(lines1, p1_x, p1_y)
        l2, c2 = self._find_char_at(lines2, p2_x, p2_y)

        if l1 < 0 and l2 < 0:
            c._sel_rects = []
            c._sel_text = ""
            return

        if l1 < 0:
            l1, c1 = 0, 0
        if l2 < 0:
            l2 = max(0, len(lines2) - 1)
            c2 = len(lines2[l2]["chars"]) if lines2 else 0

        self._select_char_range(p1_page, l1, c1, p2_page, l2, c2)

    def note_icon_at(self, pos: QPoint) -> tuple[int, int] | None:
        c = self.canvas
        z = c._zoom
        margin = 8
        for page_idx, entry in enumerate(c._entries):
            annots = getattr(entry, "annots", None)
            if not annots:
                continue
            if pos.y() < entry.y_off - margin or pos.y() > entry.y_off + entry.h + margin:
                continue
            x_off = c.page_x_offset(entry)
            for annot_idx, (rect, _txt) in enumerate(annots):
                px = x_off + int(round(rect.x0 * z))
                py = entry.y_off + int(round(rect.y0 * z))
                hit_r = QRect(px - margin, py - margin,
                              _NOTE_ICON_SIZE + margin * 2, _NOTE_ICON_SIZE + margin * 2)
                if hit_r.contains(pos):
                    return (page_idx, annot_idx)
        return None

    def get_sig_handle_at(self, pos: QPoint) -> int:
        c = self.canvas
        sig = c._active_sig
        if not sig:
            return c.HANDLE_NONE
        page_idx = sig["page"]
        if not (0 <= page_idx < len(c._entries)):
            return c.HANDLE_NONE
        entry = c._entries[page_idx]
        x_off = c.page_x_offset(entry)
        z = c._zoom
        r = sig["rect"]
        sx0 = x_off + int(round(r.x0 * z))
        sy0 = entry.y_off + int(round(r.y0 * z))
        sx1 = x_off + int(round(r.x1 * z))
        sy1 = entry.y_off + int(round(r.y1 * z))
        hs = 10
        if QRect(sx0 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_TL
        if QRect(sx1 - hs, sy0 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_TR
        if QRect(sx0 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_BL
        if QRect(sx1 - hs, sy1 - hs, hs * 2, hs * 2).contains(pos):
            return c.HANDLE_BR
        return c.HANDLE_NONE

    def is_pos_inside_active_sig(self, pos: QPoint) -> bool:
        c = self.canvas
        sig = c._active_sig
        if not sig:
            return False
        page_idx = sig["page"]
        if not (0 <= page_idx < len(c._entries)):
            return False
        entry = c._entries[page_idx]
        x_off = c.page_x_offset(entry)
        z = c._zoom
        r = sig["rect"]
        sx0 = x_off + int(round(r.x0 * z))
        sy0 = entry.y_off + int(round(r.y0 * z))
        sx1 = x_off + int(round(r.x1 * z))
        sy1 = entry.y_off + int(round(r.y1 * z))
        return QRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0)).contains(pos)

    def mouse_press(self, e):
        c = self.canvas
        pos = e.position().toPoint()
        page_idx = c.page_at_y(pos.y())
        c._active_page_idx = page_idx

        # ── 1. Placing Signature from cursor ──────────────────────────
        if c._placing_signature:
            if e.button() == Qt.MouseButton.RightButton:
                c.cancel_signature_placement()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                if 0 <= page_idx < len(c._entries):
                    entry = c._entries[page_idx]
                    x_off = c.page_x_offset(entry)
                    z = c._zoom
                    px = (pos.x() - x_off) / z
                    py = (pos.y() - entry.y_off) / z
                    pix = c._placing_sig_pixmap
                    aspect = (pix.height() / pix.width()) if pix and pix.width() > 0 else 0.35
                    sig_w = min(180.0, (entry.w / z) * 0.6)
                    sig_h = sig_w * aspect
                    x0 = max(0.0, min(entry.w / z - sig_w, px - sig_w / 2.0))
                    y0 = max(0.0, min(entry.h / z - sig_h, py - sig_h / 2.0))
                    rect = fitz.Rect(x0, y0, x0 + sig_w, y0 + sig_h)
                    c._active_sig = {
                        "page": page_idx,
                        "rect": rect,
                        "path": c._placing_sig_path,
                        "pixmap": c._placing_sig_pixmap,
                        "resizing": False,
                        "moving": False,
                        "handle": c.HANDLE_NONE,
                        "drag_start": pos,
                        "orig_rect": fitz.Rect(rect),
                    }
                    c._placing_signature = False
                    c._placing_sig_path = ""
                    c._sig_cursor_pos = None
                    c.setCursor(Qt.CursorShape.ArrowCursor)
                    c.update()
                e.accept()
                return

        # ── 2. Interacting with placed Active Signature ────────────────
        sig = c._active_sig
        if sig is not None:
            handle = self.get_sig_handle_at(pos)
            if handle != c.HANDLE_NONE and e.button() == Qt.MouseButton.LeftButton:
                sig["resizing"] = True
                sig["handle"] = handle
                sig["drag_start"] = pos
                sig["orig_rect"] = fitz.Rect(sig["rect"])
                e.accept()
                return
            if self.is_pos_inside_active_sig(pos) and e.button() == Qt.MouseButton.LeftButton:
                sig["moving"] = True
                sig["drag_start"] = pos
                sig["orig_rect"] = fitz.Rect(sig["rect"])
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                c.commit_active_signature()

        # ── 3. Crop mode ──────────────────────────────────────────────
        if c._crop_mode and e.button() == Qt.MouseButton.LeftButton:
            c.setFocus()
            c._crop_active_page = page_idx
            c._crop_drag_start = pos
            c._crop_drag_cur = pos
            c.update()
            e.accept()
            return

        # ── 4. Standard text selection ────────────────────────────────
        if e.button() == Qt.MouseButton.LeftButton:
            c.setFocus()
            self.clear_selection()
            c.text_copied.emit("")
            c._drag_start = pos
            c._drag_end   = None
            self._is_dragging_text = False
            c.update()
            e.accept()

    def mouse_move(self, e):
        c = self.canvas
        pos = e.position().toPoint()

        if c._placing_signature:
            c._sig_cursor_pos = pos
            c.update()
            e.accept()
            return

        sig = c._active_sig
        if sig is not None:
            z = c._zoom
            orig = sig.get("orig_rect")
            start = sig.get("drag_start", pos)
            page_idx = sig["page"]
            entry = c._entries[page_idx] if 0 <= page_idx < len(c._entries) else None
            max_w = entry.w / z if entry else 1000.0
            max_h = entry.h / z if entry else 1000.0

            if sig.get("resizing") and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                handle = sig.get("handle", c.HANDLE_NONE)
                r = fitz.Rect(orig)
                min_s = 20.0

                if handle == c.HANDLE_BR:
                    r.x1 = max(r.x0 + min_s, min(max_w, orig.x1 + dx))
                    r.y1 = max(r.y0 + min_s, min(max_h, orig.y1 + dy))
                elif handle == c.HANDLE_BL:
                    r.x0 = min(r.x1 - min_s, max(0.0, orig.x0 + dx))
                    r.y1 = max(r.y0 + min_s, min(max_h, orig.y1 + dy))
                elif handle == c.HANDLE_TR:
                    r.x1 = max(r.x0 + min_s, min(max_w, orig.x1 + dx))
                    r.y0 = min(r.y1 - min_s, max(0.0, orig.y0 + dy))
                elif handle == c.HANDLE_TL:
                    r.x0 = min(r.x1 - min_s, max(0.0, orig.x0 + dx))
                    r.y0 = min(r.y1 - min_s, max(0.0, orig.y0 + dy))

                sig["rect"] = r
                c.update()
                e.accept()
                return

            if sig.get("moving") and orig:
                dx = (pos.x() - start.x()) / z
                dy = (pos.y() - start.y()) / z
                w = orig.width
                h = orig.height
                new_x0 = max(0.0, min(max_w - w, orig.x0 + dx))
                new_y0 = max(0.0, min(max_h - h, orig.y0 + dy))
                sig["rect"] = fitz.Rect(new_x0, new_y0, new_x0 + w, new_y0 + h)
                c.update()
                e.accept()
                return

            h_id = self.get_sig_handle_at(pos)
            if h_id in (c.HANDLE_TL, c.HANDLE_BR):
                c.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if h_id in (c.HANDLE_TR, c.HANDLE_BL):
                c.setCursor(Qt.CursorShape.SizeBDiagCursor)
                return
            if self.is_pos_inside_active_sig(pos):
                c.setCursor(Qt.CursorShape.SizeAllCursor)
                return
            c.setCursor(Qt.CursorShape.ArrowCursor)

        if c._crop_mode and c._crop_drag_start:
            c._crop_drag_cur = pos
            c.update()
            e.accept()
            return

        if c._drag_start and (e.buttons() & Qt.MouseButton.LeftButton):
            if not self._is_dragging_text:
                dx = pos.x() - c._drag_start.x()
                dy = pos.y() - c._drag_start.y()
                if abs(dx) >= 3 or abs(dy) >= 3:
                    self._is_dragging_text = True
            if self._is_dragging_text:
                c._drag_end = pos
                self.compute_selection()
                c.update()
                e.accept()

    def mouse_double_click(self, e):
        c = self.canvas
        if c._active_sig is not None and e.button() == Qt.MouseButton.LeftButton:
            c.commit_active_signature()
            e.accept()
            return
        if c._crop_mode and e.button() == Qt.MouseButton.LeftButton:
            c.crop_applied.emit()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            page_idx = c.page_at_y(pos.y())
            c._active_page_idx = page_idx
            if 0 <= page_idx < len(c._entries):
                entry = c._entries[page_idx]
                lines = self.ensure_entry_lines(entry)
                if lines:
                    x_off = c.page_x_offset(entry)
                    z = c._zoom or 1.0
                    px = (pos.x() - x_off) / z
                    py = (pos.y() - entry.y_off) / z
                    line_idx, char_idx = self._find_char_at(lines, px, py)
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
                                self._select_char_range(page_idx, line_idx, sc, page_idx, line_idx, ec)
                                if c._sel_text:
                                    QApplication.clipboard().setText(c._sel_text)
                                c.text_copied.emit(c._sel_text)
                                c.update()
                                e.accept()

    def mouse_release(self, e):
        c = self.canvas

        sig = c._active_sig
        if sig is not None:
            if sig.get("resizing") or sig.get("moving"):
                sig["resizing"] = False
                sig["moving"] = False
                sig["handle"] = c.HANDLE_NONE
                c.update()
                e.accept()
                return

        if c._crop_mode and c._crop_drag_start:
            start = c._crop_drag_start
            end = e.position().toPoint()
            page_idx = c._crop_active_page
            c._crop_drag_start = None
            c._crop_drag_cur = None
            if (abs(end.x() - start.x()) > 5 and abs(end.y() - start.y()) > 5
                    and 0 <= page_idx < len(c._entries)):
                entry = c._entries[page_idx]
                x_off = c.page_x_offset(entry)
                z = c._zoom
                p_x0 = max(0.0, min(start.x() - x_off, end.x() - x_off) / z)
                p_y0 = max(0.0, min(start.y() - entry.y_off, end.y() - entry.y_off) / z)
                p_x1 = min(entry.w / z, max(start.x() - x_off, end.x() - x_off) / z)
                p_y1 = min(entry.h / z, max(start.y() - entry.y_off, end.y() - entry.y_off) / z)
                pw = entry.w / z
                ph = entry.h / z
                c.crop_selected.emit(page_idx, (p_x0, p_y0, p_x1, p_y1, pw, ph))
            c.update()
            e.accept()
            return

        if e.button() != Qt.MouseButton.LeftButton or not c._drag_start:
            return

        drag_start = c._drag_start
        pos = e.position().toPoint()
        was_dragging = self._is_dragging_text
        c._drag_start = None
        c._drag_end   = None
        self._is_dragging_text = False

        if was_dragging:
            c._drag_end = pos
            c._drag_start = drag_start
            self.compute_selection()
            c._drag_start = None
            c._drag_end = None
            if c._sel_text:
                QApplication.clipboard().setText(c._sel_text)
                c.text_copied.emit(c._sel_text)
            c.update()
            e.accept()
            return

        # It was a single click without dragging
        hit = self.note_icon_at(pos)
        if hit is not None:
            c._open_note = None if c._open_note == hit else hit
            c.update()
            e.accept()
            return

        if c._open_note is not None:
            c._open_note = None
            c.update()

        self.clear_selection()
        c.text_copied.emit("")
        c.update()
        e.accept()

    def key_press(self, e) -> bool:
        c = self.canvas
        key = e.key()
        modifiers = e.modifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                c.zoom_in()
                return True
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                c.zoom_out()
                return True
            if key == Qt.Key.Key_0:
                c.zoom_reset()
                return True

        if c._placing_signature:
            if key == Qt.Key.Key_Escape:
                c.cancel_signature_placement()
                return True

        if c._active_sig is not None:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                c.commit_active_signature()
                return True
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                c.delete_active_signature()
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = 15.0 if (modifiers & Qt.KeyboardModifier.ShiftModifier) else (1.0 if (modifiers & Qt.KeyboardModifier.AltModifier) else 4.0)
                dx = 0.0
                dy = 0.0
                if key == Qt.Key.Key_Left:
                    dx = -step
                elif key == Qt.Key.Key_Right:
                    dx = step
                elif key == Qt.Key.Key_Up:
                    dy = -step
                elif key == Qt.Key.Key_Down:
                    dy = step
                z = c._zoom or 1.0
                page_idx = c._active_sig.get("page", 0)
                entry = c._entries[page_idx] if 0 <= page_idx < len(c._entries) else None
                max_w = entry.w / z if entry else 1000.0
                max_h = entry.h / z if entry else 1000.0
                r = c._active_sig["rect"]
                w = r.width
                h = r.height
                new_x0 = max(0.0, min(max_w - w, r.x0 + dx))
                new_y0 = max(0.0, min(max_h - h, r.y0 + dy))
                c._active_sig["rect"] = fitz.Rect(new_x0, new_y0, new_x0 + w, new_y0 + h)
                c._active_sig["orig_rect"] = fitz.Rect(c._active_sig["rect"])
                c.update()
                return True

        if c._crop_mode and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            c.crop_applied.emit()
            return True
        if c._crop_mode and (modifiers & Qt.KeyboardModifier.ControlModifier):
            if key == Qt.Key.Key_Z:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    c.crop_redo_requested.emit()
                else:
                    c.crop_undo_requested.emit()
                return True
            elif key == Qt.Key.Key_Y:
                c.crop_redo_requested.emit()
                return True

        if not (modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            if key == Qt.Key.Key_Left:
                cur_p = getattr(c, "_active_page_idx", None)
                if cur_p is None:
                    sa = c._get_scroll_area()
                    sb_val = sa.verticalScrollBar().value() if sa else 0
                    cur_p = c.page_at_y(sb_val)
                if cur_p > 0:
                    target = cur_p - 1
                    c._active_page_idx = target
                    c.page_action_requested.emit("go_to_page", target)
                return True
            if key == Qt.Key.Key_Right:
                cur_p = getattr(c, "_active_page_idx", None)
                if cur_p is None:
                    sa = c._get_scroll_area()
                    sb_val = sa.verticalScrollBar().value() if sa else 0
                    cur_p = c.page_at_y(sb_val)
                if cur_p < len(c._entries) - 1:
                    target = cur_p + 1
                    c._active_page_idx = target
                    c.page_action_requested.emit("go_to_page", target)
                return True

        if c._active_sig is None and not c._placing_signature:
            if not (modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End):
                    if c._sel_text and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                        self.clear_selection()
                        c.text_copied.emit("")
                        c.update()
                    sa = c._get_scroll_area()
                    if sa:
                        sb = sa.verticalScrollBar()
                        if sb:
                            step = 60
                            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                                step = 150
                            if key == Qt.Key.Key_Down:
                                sb.setValue(min(sb.maximum(), sb.value() + step))
                                return True
                            elif key == Qt.Key.Key_Up:
                                sb.setValue(max(0, sb.value() - step))
                                return True
                            elif key == Qt.Key.Key_PageDown:
                                sb.setValue(min(sb.maximum(), sb.value() + sb.pageStep()))
                                return True
                            elif key == Qt.Key.Key_PageUp:
                                sb.setValue(max(0, sb.value() - sb.pageStep()))
                                return True
                            elif key == Qt.Key.Key_Home:
                                sb.setValue(0)
                                return True
                            elif key == Qt.Key.Key_End:
                                sb.setValue(sb.maximum())
                                return True

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            sa = c._get_scroll_area()
            sb_val = sa.verticalScrollBar().value() if sa else 0
            idx = c.page_at_y(sb_val)
            if key == Qt.Key.Key_Z:
                action = "redo" if (modifiers & Qt.KeyboardModifier.ShiftModifier) else "undo"
                c.page_action_requested.emit(action, [idx])
                return True
            elif key == Qt.Key.Key_Y:
                c.page_action_requested.emit("redo", [idx])
                return True
            elif key == Qt.Key.Key_Left:
                c.page_action_requested.emit("rotate_left", [idx])
                return True
            elif key == Qt.Key.Key_Right:
                c.page_action_requested.emit("rotate_right", [idx])
                return True
            elif key == Qt.Key.Key_C and c._sel_text:
                QApplication.clipboard().setText(c._sel_text)
                win: Any = c.window()
                set_status = getattr(win, "_set_status", None)
                if callable(set_status):
                    set_status(f"✔ Copied {len(c._sel_text):,} characters to clipboard")
                return True
            elif key == Qt.Key.Key_A:
                self.select_all()
                return True

        if key == Qt.Key.Key_Escape and c._sel_text:
            self.clear_selection()
            c.text_copied.emit("")
            c.update()
            return True

        if not c._crop_mode and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            sa = c._get_scroll_area()
            sb_val = sa.verticalScrollBar().value() if sa else 0
            idx = c.page_at_y(sb_val)
            c.page_action_requested.emit("delete", [idx])
            return True

        return False

    def context_menu(self, e):
        c = self.canvas
        pos = e.pos()

        if c._placing_signature:
            c.cancel_signature_placement()
            return

        sig = c._active_sig
        if sig is not None and self.is_pos_inside_active_sig(pos):
            menu = QMenu(c)
            self._menu_style = _ScrollableMenuStyle(menu.style())
            menu.setStyle(self._menu_style)
            menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
            act_apply = menu.addAction(qta.icon("fa5s.check", color=ACCENT), t("btn.apply"))
            act_apply.triggered.connect(c.commit_active_signature)
            act_del = menu.addAction(qta.icon("fa5s.trash-alt", color="#EF4444"), t("btn.delete"))
            act_del.triggered.connect(c.delete_active_signature)
            menu.exec(e.globalPos())
            return

        hit = self.note_icon_at(pos)
        if hit is not None:
            menu = QMenu(c)
            self._menu_style = _ScrollableMenuStyle(menu.style())
            menu.setStyle(self._menu_style)
            menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
            delete_action = menu.addAction(t("viewer.delete_comment"))
            action = menu.exec(e.globalPos())
            if action == delete_action:
                page_idx, annot_idx = hit
                entry = c._entries[page_idx]
                annots = getattr(entry, "annots", None)
                if annots and annot_idx < len(annots):
                    rect, txt = annots[annot_idx]
                    reply = QMessageBox.question(
                        c, t("msg.confirm"),
                        t("viewer.confirm_delete_comment"),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                    if c._doc:
                        backup_path = None
                        if c._path and os.path.isfile(c._path):
                            try:
                                src_dir = os.path.dirname(c._path) or "."
                                fd, backup_path = tempfile.mkstemp(
                                    prefix=".PDF_Editor_backup_",
                                    suffix=".pdf.bak",
                                    dir=src_dir,
                                )
                                os.close(fd)
                                shutil.copy2(c._path, backup_path)
                            except Exception:
                                if backup_path:
                                    with contextlib.suppress(Exception):
                                        os.unlink(backup_path)
                                    backup_path = None
                                return
                        target_annot = None
                        try:
                            page = c._doc[page_idx]
                            for annot in page.annots() or []:
                                if annot.type[0] != _PDF_ANNOT_TEXT and annot.type[1] != "Text":
                                    continue
                                content = annot.info.get("content", "") or ""
                                if content.strip() != txt.strip():
                                    continue
                                ar = annot.rect
                                if abs(ar.x0 - rect.x0) < 1 and abs(ar.y0 - rect.y0) < 1:
                                    target_annot = annot
                                    break
                            if target_annot is None:
                                for annot in page.annots() or []:
                                    if annot.type[0] != _PDF_ANNOT_TEXT and annot.type[1] != "Text":
                                        continue
                                    content = annot.info.get("content", "") or ""
                                    if content.strip() == txt.strip():
                                        target_annot = annot
                                        break
                            if target_annot is not None:
                                page.delete_annot(target_annot)
                            else:
                                if backup_path:
                                    with contextlib.suppress(Exception):
                                        os.unlink(backup_path)
                                QMessageBox.warning(c, t("msg.warning"), t("viewer.delete_no_match"))
                                return
                        except Exception:
                            if backup_path:
                                with contextlib.suppress(Exception):
                                    os.unlink(backup_path)
                            return
                        if c._path:
                            c._prepare_for_save()
                            try:
                                c._doc.saveIncr()
                            except Exception:
                                if backup_path:
                                    with contextlib.suppress(Exception):
                                        shutil.move(backup_path, c._path)
                                    backup_path = None
                                new_doc = c._reopen_document()
                                if new_doc is not None:
                                    c._schedule_visible()
                                return
                        if backup_path:
                            with contextlib.suppress(Exception):
                                os.unlink(backup_path)
                    annots.pop(annot_idx)
                    if c._open_note is not None:
                        open_page, open_idx = c._open_note
                        if open_page == page_idx:
                            if open_idx == annot_idx:
                                c._open_note = None
                            elif open_idx > annot_idx:
                                c._open_note = (open_page, open_idx - 1)
                    c._schedule_visible()
                    c.update()
            return

        menu = QMenu(c)
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        if c._sel_text:
            act_copy = menu.addAction(
                qta.icon("fa5s.copy", color=TEXT_SEC),
                t("viewer.copy_chars", n=len(c._sel_text)),
            )
            act_copy.triggered.connect(lambda: QApplication.clipboard().setText(c._sel_text))
            menu.addSeparator()

        act_sel_all = menu.addAction(
            qta.icon("fa5s.object-group", color=TEXT_SEC),
            t("Select All Text", default="Select All\tCtrl+A"),
        )
        act_sel_all.triggered.connect(self.select_all)
        menu.addSeparator()

        act_sig = menu.addAction(
            qta.icon("fa5s.signature", color=ACCENT),
            t("viewer.add_signature"),
        )
        act_sig.triggered.connect(lambda: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())