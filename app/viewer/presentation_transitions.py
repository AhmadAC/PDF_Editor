# app/viewer/presentation_transitions.py
"""PDF_Editor – Presentation transitions, tick animations, and page drawing paintEvent mixin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    _Base = QWidget
else:
    _Base = object


class PresentationTransitionsMixin(_Base):
    """Mixin managing animated page transitions and the fullscreen paintEvent."""

    if TYPE_CHECKING:
        _current: int
        _total: int
        _pan_x: int
        _pan_y: int
        _pixmap: QPixmap | None
        _prev_pixmap: QPixmap | None
        _prev_pw: float
        _prev_ph: float
        _prev_pan_x: float
        _prev_pan_y: float
        _align_bottom_next_render: bool
        _trans_style: str | None
        _trans_progress: float
        _trans_step: float
        _trans_direction: int
        _trans_timer: QTimer
        _auto_advance_timer: QTimer

        def _render(self, transition: bool = False) -> None: ...

    def _go_to_page(self, new_page: int, align_bottom: bool = False):
        if new_page == self._current or not (0 <= new_page < self._total):
            return
        self._prev_pixmap = self._pixmap
        self._prev_pw = getattr(self, "_rendered_pw", float(self.width()))
        self._prev_ph = getattr(self, "_rendered_ph", float(self.height()))
        self._prev_pan_x = float(self._pan_x)
        self._prev_pan_y = float(self._pan_y)

        self._current = new_page
        self._pan_x = 0
        self._pan_y = 0
        self._align_bottom_next_render = align_bottom
        self._render(transition=True)

    def _on_auto_advance(self):
        if self._current < self._total - 1:
            self._go_to_page(self._current + 1)

    def _on_trans_tick(self):
        self._trans_progress += self._trans_step
        if self._trans_progress >= 1.0:
            self._trans_progress = 1.0
            self._trans_timer.stop()
            self._prev_pixmap = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        pixmap = self._pixmap
        if pixmap is not None and not pixmap.isNull():
            pw = getattr(self, "_rendered_pw", float(self.width()))
            ph = getattr(self, "_rendered_ph", float(self.height()))

            x = float(self._pan_x) if pw > self.width() else (self.width() - pw) / 2.0
            y = float(self._pan_y) if ph > self.height() else (self.height() - ph) / 2.0

            rect_new = QRect(int(round(x)), int(round(y)), int(round(pw)), int(round(ph)))

            t_val = self._trans_progress
            prev_pixmap = self._prev_pixmap

            if prev_pixmap is not None and not prev_pixmap.isNull() and t_val < 1.0 and self._trans_style:
                old_w = getattr(self, "_prev_pw", float(self.width()))
                old_h = getattr(self, "_prev_ph", float(self.height()))
                old_pan_x = getattr(self, "_prev_pan_x", 0.0)
                old_pan_y = getattr(self, "_prev_pan_y", 0.0)

                old_x = float(old_pan_x) if old_w > self.width() else (self.width() - old_w) / 2.0
                old_y = float(old_pan_y) if old_h > self.height() else (self.height() - old_h) / 2.0

                rect_old = QRect(int(round(old_x)), int(round(old_y)), int(round(old_w)), int(round(old_h)))

                style = self._trans_style
                if style == "Push":
                    di = self._trans_direction
                    if di == 90:
                        dy = int(self.height() * t_val)
                        p.drawPixmap(rect_old.translated(0, -dy), prev_pixmap)
                        p.drawPixmap(rect_new.translated(0, self.height() - dy), pixmap)
                    elif di == 180:
                        dx = int(self.width() * t_val)
                        p.drawPixmap(rect_old.translated(dx, 0), prev_pixmap)
                        p.drawPixmap(rect_new.translated(-self.width() + dx, 0), pixmap)
                    elif di == 270:
                        dy = int(self.height() * t_val)
                        p.drawPixmap(rect_old.translated(0, dy), prev_pixmap)
                        p.drawPixmap(rect_new.translated(0, -self.height() + dy), pixmap)
                    else:
                        dx = int(self.width() * t_val)
                        p.drawPixmap(rect_old.translated(-dx, 0), prev_pixmap)
                        p.drawPixmap(rect_new.translated(self.width() - dx, 0), pixmap)

                elif style in ("Wipe", "Cover"):
                    p.drawPixmap(rect_old, prev_pixmap)
                    di = self._trans_direction
                    p.save()
                    if di == 90:
                        split_y = int(self.height() * (1.0 - t_val))
                        p.setClipRect(QRect(0, split_y, self.width(), self.height() - split_y))
                    elif di == 180:
                        split_x = int(self.width() * (1.0 - t_val))
                        p.setClipRect(QRect(split_x, 0, self.width() - split_x, self.height()))
                    elif di == 270:
                        split_y = int(self.height() * t_val)
                        p.setClipRect(QRect(0, 0, self.width(), split_y))
                    else:
                        split_x = int(self.width() * t_val)
                        p.setClipRect(QRect(0, 0, split_x, self.height()))
                    p.drawPixmap(rect_new, pixmap)
                    p.restore()

                elif style in ("Fade", "Dissolve"):
                    p.setOpacity(max(0.0, 1.0 - t_val))
                    p.drawPixmap(rect_old, prev_pixmap)
                    p.setOpacity(min(1.0, t_val))
                    p.drawPixmap(rect_new, pixmap)
                    p.setOpacity(1.0)

                elif style == "Box":
                    p.drawPixmap(rect_old, prev_pixmap)
                    bw = int(self.width() * t_val)
                    bh = int(self.height() * t_val)
                    bx = (self.width() - bw) // 2
                    by = (self.height() - bh) // 2
                    p.save()
                    p.setClipRect(QRect(bx, by, bw, bh))
                    p.drawPixmap(rect_new, pixmap)
                    p.restore()

                else:
                    p.setOpacity(max(0.0, 1.0 - t_val))
                    p.drawPixmap(rect_old, prev_pixmap)
                    p.setOpacity(min(1.0, t_val))
                    p.drawPixmap(rect_new, pixmap)
                    p.setOpacity(1.0)
            else:
                p.drawPixmap(rect_new, pixmap)

        p.end()