# app/manage_tabs_list.py
"""PDF_Editor – Custom item delegate and drag-drop list widget for ManageTabsDialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
)

from app.constants import ACCENT, TEXT_PRI, TEXT_SEC
from app.manage_tabs_preview import (
    ROLE_DISPLAY_NAME,
    ROLE_IS_ACTIVE,
    ROLE_PAGE_RANGE,
    ROLE_THUMBNAIL,
)
from app.utils import SVG_FILE_PDF, svg_pixmap

if TYPE_CHECKING:
    from app.manage_tabs_dialog import ManageTabsDialog


class _ManageTabItemDelegate(QStyledItemDelegate):
    """Custom delegate painting each tab row: PDF icon, filename (elided), page range badge, and Page 1 thumbnail."""

    def __init__(self, dialog: ManageTabsDialog, parent=None):
        super().__init__(parent)
        self._dialog = dialog

    def sizeHint(self, option, index: QModelIndex) -> QSize:
        return QSize(0, 56)

    @staticmethod
    def get_thumb_box(rect: QRect) -> QRect:
        thumb_w = 34
        thumb_h = 46
        thumb_x = rect.right() - 10 - thumb_w
        thumb_y = rect.top() + (rect.height() - thumb_h) // 2
        return QRect(thumb_x - 3, thumb_y - 3, thumb_w + 6, thumb_h + 6)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = option.rect
        dark = self._dialog._dark

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        display_name = str(index.data(ROLE_DISPLAY_NAME) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        page_range_text = str(index.data(ROLE_PAGE_RANGE) or "")
        thumb_pix = index.data(ROLE_THUMBNAIL)
        is_active = bool(index.data(ROLE_IS_ACTIVE))

        # 1. Row selection / hover background
        if is_selected:
            bg_col = QColor("#264F78") if dark else QColor("#D6E8FA")
            border_col = QColor(ACCENT) if dark else QColor("#70A7DB")
            painter.setBrush(bg_col)
            painter.setPen(QPen(border_col, 1.2))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
        elif is_hovered:
            bg_col = QColor("#333333") if dark else QColor("#F1F5F9")
            painter.setBrush(bg_col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

        # 2. Right side: Page 1 Thumbnail preview card
        thumb_w = 34
        thumb_h = 46
        thumb_x = rect.right() - 10 - thumb_w
        thumb_y = rect.top() + (rect.height() - thumb_h) // 2
        thumb_box = QRect(thumb_x, thumb_y, thumb_w, thumb_h)

        # Paper background and border
        painter.setBrush(QColor("#FFFFFF"))
        border_col = QColor(ACCENT) if (is_active and not is_selected) else (QColor("#555555") if dark else QColor("#CBD5E1"))
        painter.setPen(QPen(border_col, 1))
        painter.drawRoundedRect(thumb_box, 3, 3)

        if isinstance(thumb_pix, QPixmap) and not thumb_pix.isNull():
            inner = thumb_box.adjusted(1, 1, -1, -1)
            dpr = thumb_pix.devicePixelRatio() or 1.0
            scaled = thumb_pix.scaled(
                int(inner.width() * dpr),
                int(inner.height() * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            sw = int(round(scaled.width() / dpr))
            sh = int(round(scaled.height() / dpr))
            sx = inner.x() + (inner.width() - sw) // 2
            sy = inner.y() + (inner.height() - sh) // 2
            painter.drawPixmap(sx, sy, scaled)
        else:
            painter.setPen(QPen(QColor("#9CA3AF" if dark else "#94A3B8"), 1))
            f_p = QFont("Segoe UI", 7, QFont.Weight.Bold)
            painter.setFont(f_p)
            painter.drawText(thumb_box, Qt.AlignmentFlag.AlignCenter, "P. 1")

        # 3. Middle-Right: Total Page Range Badge
        if page_range_text:
            f_badge = QFont("Segoe UI", 8, QFont.Weight.Bold)
            painter.setFont(f_badge)
            fm_badge = painter.fontMetrics()
            badge_text_w = fm_badge.horizontalAdvance(page_range_text)
            badge_w = badge_text_w + 16
            badge_h = 22
            badge_x = thumb_x - 12 - badge_w
            badge_y = rect.top() + (rect.height() - badge_h) // 2
            badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)

            if is_selected:
                badge_bg = QColor(255, 255, 255, 40) if dark else QColor(0, 120, 212, 35)
                badge_border = QColor(255, 255, 255, 75) if dark else QColor(0, 120, 212, 90)
                badge_fg = QColor("#FFFFFF") if dark else QColor(ACCENT)
            else:
                if dark:
                    badge_bg = QColor("#1E3A5F") if is_active else QColor("#2A2D34")
                    badge_border = QColor(ACCENT) if is_active else QColor("#3E4249")
                    badge_fg = QColor("#93C5FD") if is_active else QColor("#9CA3AF")
                else:
                    badge_bg = QColor("#D6E8FA") if is_active else QColor("#F1F5F9")
                    badge_border = QColor("#70A7DB") if is_active else QColor("#CBD5E1")
                    badge_fg = QColor(ACCENT) if is_active else QColor("#64748B")

            painter.setBrush(badge_bg)
            painter.setPen(QPen(badge_border, 1))
            painter.drawRoundedRect(badge_rect, 4, 4)

            painter.setPen(badge_fg)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, page_range_text)
        else:
            badge_x = thumb_x

        # 4. Left side: PDF Document Icon
        ico_w = 20
        ico_h = 20
        ico_x = rect.left() + 12
        ico_y = rect.top() + (rect.height() - ico_h) // 2

        icon_color = ACCENT if is_active else (TEXT_SEC if dark else "#6B7280")
        ico_pixmap = svg_pixmap(SVG_FILE_PDF, color=icon_color, size=18)
        painter.drawPixmap(ico_x + 1, ico_y + 1, ico_pixmap)

        # 5. Filename text (cleanly elided so it never collides with the badge / thumbnail)
        text_x = ico_x + ico_w + 10
        avail_w = max(40, badge_x - 12 - text_x)

        font = QFont("Segoe UI", 10)
        if is_active:
            font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()

        elided_title = fm.elidedText(display_name, Qt.TextElideMode.ElideMiddle, avail_w)

        if is_selected:
            text_color = QColor("#FFFFFF") if dark else QColor("#1A2B28")
        else:
            if is_active:
                text_color = QColor(ACCENT) if not dark else QColor("#FFFFFF")
            else:
                text_color = QColor(TEXT_PRI) if dark else QColor("#1A2B28")

        painter.setPen(text_color)
        text_rect = QRect(text_x, rect.top(), avail_w, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)

        painter.restore()


class _ManageTabsListWidget(QListWidget):
    """QListWidget supporting internal drag-and-drop reordering, thumbnail clicking for popup previews, and hover states."""

    def __init__(self, dialog: ManageTabsDialog, parent=None) -> None:
        super().__init__(parent)
        self._dialog = dialog
        self._drag_start_pos: QPoint = QPoint()
        self._pressed_on_thumb: bool = False

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(False)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._drag_start_pos = pos
        self._pressed_on_thumb = False

        if self._dialog._dismiss_preview_if_open():
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(pos)
            if idx.isValid():
                rect = self.visualRect(idx)
                thumb_box = _ManageTabItemDelegate.get_thumb_box(rect)
                if thumb_box.contains(pos):
                    self._pressed_on_thumb = True
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()

        # Update cursor when hovering over the thumbnail pane
        idx = self.indexAt(pos)
        if idx.isValid():
            rect = self.visualRect(idx)
            thumb_box = _ManageTabItemDelegate.get_thumb_box(rect)
            if thumb_box.contains(pos):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                super().mouseMoveEvent(event)
                return

        self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()

        if event.button() == Qt.MouseButton.LeftButton and self._pressed_on_thumb:
            self._pressed_on_thumb = False
            idx = self.indexAt(pos)
            if idx.isValid():
                rect = self.visualRect(idx)
                thumb_box = _ManageTabItemDelegate.get_thumb_box(rect)
                if thumb_box.contains(pos) and (pos - self._drag_start_pos).manhattanLength() < 8:
                    self._dialog._show_thumbnail_preview_popup(idx)
                    event.accept()
                    return

        self._pressed_on_thumb = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def dropEvent(self, event):
        super().dropEvent(event)
        self._dialog._on_list_dropped()