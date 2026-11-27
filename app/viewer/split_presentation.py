
# app/viewer/split_presentation.py
"""PDF_Editor – Fullscreen side-by-side split presentation widget hosting two independent PDF presentation panes."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSplitter, QWidget
from shiboken6 import isValid

from app.viewer.presentation import PresentationWidget


def _viewer_presentation_params(viewer: Any) -> tuple[str, str, int, int]:
    path = getattr(viewer, "_current_path", "") or ""
    password = getattr(viewer, "_pdf_password", "") or ""
    canvas = getattr(viewer, "_canvas", None)
    sb_scroll = getattr(viewer, "_canvas_scroll", None)
    total_pages = canvas.page_count() if canvas is not None else 1
    sb = sb_scroll.verticalScrollBar() if sb_scroll else None
    start_page = canvas.page_at_y(sb.value()) if (canvas is not None and total_pages > 0 and sb) else 0
    return path, password, start_page, total_pages


class _PaneFocusTracker(QObject):
    """Tracks which side of the split presentation was clicked/focused."""

    def __init__(self, split_pres: SplitPresentationWidget, pane: PresentationWidget, side: str) -> None:
        super().__init__(pane)
        self._split_pres = split_pres
        self._pane = pane
        self._side = side

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            self._split_pres._set_active_side(self._side)
        return False


class SplitPresentationWidget(QWidget):
    """Fullscreen side-by-side presentation viewer displaying two PDFs simultaneously."""

    def __init__(
        self,
        left_viewer: Any,
        right_viewer: Any,
        dark_mode: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background: #000000;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._dark_mode = bool(dark_mode)
        self._active_side = "left"

        root_lay = QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setHandleWidth(3)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #2D2D30; }"
            "QSplitter::handle:hover { background: #0078D4; }"
        )

        l_path, l_pwd, l_start, l_total = _viewer_presentation_params(left_viewer)
        r_path, r_pwd, r_start, r_total = _viewer_presentation_params(right_viewer)

        self._left_pres = PresentationWidget(
            l_path, l_pwd, l_start, l_total,
            dark_mode=self._dark_mode, viewer=left_viewer, embedded=True, parent=self._splitter,
        )
        self._right_pres = PresentationWidget(
            r_path, r_pwd, r_start, r_total,
            dark_mode=self._dark_mode, viewer=right_viewer, embedded=True, parent=self._splitter,
        )

        self._left_pres.close_requested.connect(self.close)
        self._right_pres.close_requested.connect(self.close)

        l_tracker = _PaneFocusTracker(self, self._left_pres, "left")
        self._left_pres.installEventFilter(l_tracker)
        self._left_pres._overlay.installEventFilter(l_tracker)

        r_tracker = _PaneFocusTracker(self, self._right_pres, "right")
        self._right_pres.installEventFilter(r_tracker)
        self._right_pres._overlay.installEventFilter(r_tracker)

        self._splitter.addWidget(self._left_pres)
        self._splitter.addWidget(self._right_pres)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)

        screen = self.screen() or QApplication.primaryScreen()
        half_w = (screen.geometry().width() // 2) if screen else 640
        self._splitter.setSizes([half_w, half_w])

        root_lay.addWidget(self._splitter)
        self._left_pres.setFocus()

        QTimer.singleShot(30, self._relayout_both)

    def _relayout_both(self) -> None:
        for p in (self._left_pres, self._right_pres):
            if isValid(p):
                p._zoom = None
                p._render(transition=False)

    def _set_active_side(self, side: str) -> None:
        if side in ("left", "right"):
            self._active_side = side

    def _active_pane(self) -> PresentationWidget:
        return self._right_pres if self._active_side == "right" else self._left_pres

    def update_theme(self, dark: bool) -> None:
        self._dark_mode = bool(dark)
        if isValid(self._left_pres):
            self._left_pres.update_theme(self._dark_mode)
        if isValid(self._right_pres):
            self._right_pres.update_theme(self._dark_mode)

    def keyPressEvent(self, e):
        pane = self._active_pane()
        if e.key() == Qt.Key.Key_Escape:
            if isValid(pane) and getattr(pane._overlay, "_active_box", None) is not None:
                pane.keyPressEvent(e)
                return
            self.close()
            e.accept()
            return
        if isValid(pane):
            pane.keyPressEvent(e)
            if e.isAccepted():
                return
        super().keyPressEvent(e)

    def closeEvent(self, event):
        for p in (getattr(self, "_left_pres", None), getattr(self, "_right_pres", None)):
            if p is not None and isValid(p):
                p.commit_and_cleanup()
        super().closeEvent(event)

