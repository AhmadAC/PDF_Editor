# app/workspace_bar.py
"""PDF_Editor – WorkspaceBar: top toolbar for workspace actions and navigation using SVG icons."""
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QLabel, QPushButton, QLineEdit, QSizePolicy, QToolTip
)

from app.constants import TEXT_PRI, ACCENT, _LP
from app.i18n import t
from app.utils import (
    SVG_ARROW_CIRCLE_UP,
    SVG_BARS,
    SVG_BOOKMARK,
    SVG_CHEVRON_LEFT,
    SVG_CHEVRON_RIGHT,
    SVG_COLUMNS,
    SVG_COMPRESS,
    SVG_DOUBLE_LEFT,
    SVG_DOUBLE_RIGHT,
    SVG_FOLDER,
    SVG_MOON,
    SVG_PRINT,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    SVG_SEARCH,
    SVG_SUN,
    SVG_TH_LARGE,
    SVG_TV,
    SVG_ZOOM_IN,
    SVG_ZOOM_OUT,
    _paint_bg,
    svg_icon,
)


class ElidedLabel(QLabel):
    """QLabel that elides text in the middle when horizontal space is constrained and copies path on single left-click."""

    clicked = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self._copy_text = ""
        self.setMinimumWidth(0)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str):
        self._full_text = text
        super().setText(text)
        self._update_elide()

    def setCopyText(self, text: str):
        self._copy_text = text or ""
        if not self._copy_text:
            self.unsetCursor()

    def copyText(self) -> str:
        return self._copy_text

    def _is_over_text(self, pos_x: float) -> bool:
        if not self._copy_text or not self.text():
            return False
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        return 0.0 <= pos_x <= float(text_w + 12)

    def mouseMoveEvent(self, event):
        pos_x = event.position().x() if hasattr(event, "position") else float(event.pos().x())
        if self._is_over_text(pos_x):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos_x = event.position().x() if hasattr(event, "position") else float(event.pos().x())
            if self._is_over_text(pos_x):
                cb = QApplication.clipboard()
                if cb is not None:
                    cb.setText(self._copy_text)
                gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                QToolTip.showText(gpos, "Copied to clipboard!", self, self.rect(), 1500)
                win = self.window()
                set_status = getattr(win, "_set_status", None)
                if callable(set_status):
                    set_status(f"Copied path to clipboard: {self._copy_text}")
                self.clicked.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self):
        fm = self.fontMetrics()
        avail_w = max(10, self.width())
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, avail_w)
        super().setText(elided)

    def minimumSizeHint(self) -> QSize:
        return QSize(20, self.fontMetrics().height())


class WorkspaceBar(QWidget):
    """Top bar containing sidebar toggle, clickable full-path title, viewer tools, zoom and page navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workspace_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        def _a11y(btn, tip):
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)

        # ── Left Side: App Sidebar Toggle & Pages Sidebar Toggle ──────
        self._sidebar_toggle_btn = QPushButton()
        self._ico_bars = svg_icon(SVG_BARS, color=TEXT_PRI, size=15)
        self._sidebar_toggle_btn.setIcon(self._ico_bars)
        self._sidebar_toggle_btn.setObjectName("viewer_nav_btn")
        self._sidebar_toggle_btn.setFixedSize(28, 28)
        self._sidebar_toggle_btn.setIconSize(QSize(15, 15))
        _a11y(self._sidebar_toggle_btn, t("sidebar.collapse_expand"))
        layout.addWidget(self._sidebar_toggle_btn)

        self._pages_toggle_btn = QPushButton()
        self._ico_pages = svg_icon(SVG_TH_LARGE, color=TEXT_PRI, size=15)
        self._pages_toggle_btn.setIcon(self._ico_pages)
        self._pages_toggle_btn.setObjectName("viewer_nav_btn")
        self._pages_toggle_btn.setFixedSize(28, 28)
        self._pages_toggle_btn.setIconSize(QSize(15, 15))
        _a11y(self._pages_toggle_btn, t("viewer.sidebar.pages"))
        layout.addWidget(self._pages_toggle_btn)

        self._breadcrumb = ElidedLabel("")
        self._breadcrumb.setObjectName("workspace_title")
        layout.addWidget(self._breadcrumb, 1)

        # ── Viewer Action Buttons ─────────────────────────────────────
        self._open_pdf_btn = QPushButton()
        self._open_pdf_btn.setIcon(svg_icon(SVG_FOLDER, color=TEXT_PRI, size=15))
        self._open_pdf_btn.setObjectName("viewer_nav_btn")
        self._open_pdf_btn.setFixedSize(28, 28)
        self._open_pdf_btn.setIconSize(QSize(15, 15))
        _a11y(self._open_pdf_btn, t("btn.open_pdf"))
        layout.addWidget(self._open_pdf_btn)

        self._toc_top_btn = QPushButton()
        self._toc_top_btn.setIcon(svg_icon(SVG_BOOKMARK, color=TEXT_PRI, size=15))
        self._toc_top_btn.setObjectName("viewer_nav_btn")
        self._toc_top_btn.setFixedSize(28, 28)
        self._toc_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._toc_top_btn, t("viewer.toc"))
        self._toc_top_btn.setVisible(False)
        layout.addWidget(self._toc_top_btn)

        self._night_top_btn = QPushButton()
        self._night_top_btn.setIcon(svg_icon(SVG_MOON, color=TEXT_PRI, size=15))
        self._night_top_btn.setObjectName("viewer_nav_btn")
        self._night_top_btn.setFixedSize(28, 28)
        self._night_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._night_top_btn, t("viewer.night_mode"))
        self._night_top_btn.setCheckable(True)
        layout.addWidget(self._night_top_btn)

        self._print_top_btn = QPushButton()
        self._print_top_btn.setIcon(svg_icon(SVG_PRINT, color=TEXT_PRI, size=15))
        self._print_top_btn.setObjectName("viewer_nav_btn")
        self._print_top_btn.setFixedSize(28, 28)
        self._print_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._print_top_btn, t("viewer.print"))
        layout.addWidget(self._print_top_btn)

        self._present_btn = QPushButton()
        self._present_btn.setIcon(svg_icon(SVG_TV, color=TEXT_PRI, size=15))
        self._present_btn.setObjectName("viewer_nav_btn")
        self._present_btn.setFixedSize(28, 28)
        self._present_btn.setIconSize(QSize(15, 15))
        _a11y(self._present_btn, t("viewer.presentation") + " (Ctrl+1 / F5)")
        layout.addWidget(self._present_btn)

        self._search_top_btn = QPushButton()
        self._search_top_btn.setIcon(svg_icon(SVG_SEARCH, color=TEXT_PRI, size=15))
        self._search_top_btn.setObjectName("viewer_nav_btn")
        self._search_top_btn.setFixedSize(28, 28)
        self._search_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._search_top_btn, t("search.placeholder") + " (Ctrl+F)")
        layout.addWidget(self._search_top_btn)

        # Zoom widget
        self._zoom_widget = QWidget()
        zw_h = QHBoxLayout(self._zoom_widget)
        zw_h.setContentsMargins(0, 0, 0, 0)
        zw_h.setSpacing(2)

        self._zm_btn = QPushButton()
        self._zm_btn.setIcon(svg_icon(SVG_ZOOM_OUT, color=TEXT_PRI, size=15))
        self._zm_btn.setFixedSize(28, 28)
        self._zm_btn.setIconSize(QSize(15, 15))
        self._zm_btn.setObjectName("viewer_nav_btn")
        _a11y(self._zm_btn, t("zoom.out"))

        self._lbl_zoom = QLabel("100%")
        self._lbl_zoom.setMinimumWidth(38)
        self._lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._zp_btn = QPushButton()
        self._zp_btn.setIcon(svg_icon(SVG_ZOOM_IN, color=TEXT_PRI, size=15))
        self._zp_btn.setFixedSize(28, 28)
        self._zp_btn.setIconSize(QSize(15, 15))
        self._zp_btn.setObjectName("viewer_nav_btn")
        _a11y(self._zp_btn, t("zoom.in"))

        self._z0_btn = QPushButton()
        self._z0_btn.setIcon(svg_icon(SVG_COMPRESS, color=TEXT_PRI, size=15))
        self._z0_btn.setObjectName("viewer_nav_btn")
        self._z0_btn.setFixedSize(28, 28)
        self._z0_btn.setIconSize(QSize(15, 15))
        _a11y(self._z0_btn, t("zoom.reset_tip"))

        zw_h.addWidget(self._zm_btn)
        zw_h.addWidget(self._lbl_zoom)
        zw_h.addWidget(self._zp_btn)
        zw_h.addWidget(self._z0_btn)
        self._zoom_widget.setVisible(False)
        layout.addWidget(self._zoom_widget)

        # Page navigation widget
        self._page_nav_widget = QWidget()
        pn_h = QHBoxLayout(self._page_nav_widget)
        pn_h.setContentsMargins(0, 0, 0, 0)
        pn_h.setSpacing(2)

        self._first_pg_btn = QPushButton()
        self._first_pg_btn.setIcon(svg_icon(SVG_DOUBLE_LEFT, color=TEXT_PRI, size=15))
        self._first_pg_btn.setFixedSize(28, 28)
        self._first_pg_btn.setIconSize(QSize(15, 15))
        self._first_pg_btn.setObjectName("viewer_nav_btn")
        first_tip = t("nav.first_page") if t("nav.first_page") != "nav.first_page" else "First page"
        _a11y(self._first_pg_btn, first_tip)

        self._prev_pg_btn = QPushButton()
        self._prev_pg_btn.setIcon(svg_icon(SVG_CHEVRON_LEFT, color=TEXT_PRI, size=15))
        self._prev_pg_btn.setFixedSize(28, 28)
        self._prev_pg_btn.setIconSize(QSize(15, 15))
        self._prev_pg_btn.setObjectName("viewer_nav_btn")
        _a11y(self._prev_pg_btn, t("nav.prev_page"))

        self._page_input = QLineEdit("1")
        self._page_input.setFixedWidth(34)
        self._page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_input.setObjectName("page_input")

        self._page_total_lbl = QLabel(t("nav.page_total"))
        self._page_total_lbl.setMinimumWidth(26)

        self._next_pg_btn = QPushButton()
        self._next_pg_btn.setIcon(svg_icon(SVG_CHEVRON_RIGHT, color=TEXT_PRI, size=15))
        self._next_pg_btn.setFixedSize(28, 28)
        self._next_pg_btn.setIconSize(QSize(15, 15))
        self._next_pg_btn.setObjectName("viewer_nav_btn")
        _a11y(self._next_pg_btn, t("nav.next_page"))

        self._last_pg_btn = QPushButton()
        self._last_pg_btn.setIcon(svg_icon(SVG_DOUBLE_RIGHT, color=TEXT_PRI, size=15))
        self._last_pg_btn.setFixedSize(28, 28)
        self._last_pg_btn.setIconSize(QSize(15, 15))
        self._last_pg_btn.setObjectName("viewer_nav_btn")
        last_tip = t("nav.last_page") if t("nav.last_page") != "nav.last_page" else "Last page"
        _a11y(self._last_pg_btn, last_tip)

        pn_h.addWidget(self._first_pg_btn)
        pn_h.addWidget(self._prev_pg_btn)
        pn_h.addWidget(self._page_input)
        pn_h.addWidget(self._page_total_lbl)
        pn_h.addWidget(self._next_pg_btn)
        pn_h.addWidget(self._last_pg_btn)
        self._page_nav_widget.setVisible(False)
        layout.addWidget(self._page_nav_widget)

        # Undo/redo top buttons
        self._undo_top_btn = QPushButton()
        self._undo_top_btn.setIcon(svg_icon(SVG_ROTATE_LEFT, color=TEXT_PRI, size=15))
        self._undo_top_btn.setObjectName("viewer_nav_btn")
        self._undo_top_btn.setFixedSize(28, 28)
        self._undo_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._undo_top_btn, "Undo (Ctrl+Z)")
        self._undo_top_btn.setVisible(False)
        layout.addWidget(self._undo_top_btn)

        self._redo_top_btn = QPushButton()
        self._redo_top_btn.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=TEXT_PRI, size=15))
        self._redo_top_btn.setObjectName("viewer_nav_btn")
        self._redo_top_btn.setFixedSize(28, 28)
        self._redo_top_btn.setIconSize(QSize(15, 15))
        _a11y(self._redo_top_btn, "Redo (Ctrl+Y)")
        self._redo_top_btn.setVisible(False)
        layout.addWidget(self._redo_top_btn)

        self._help_btn = QPushButton("?")
        self._help_btn.setObjectName("theme_btn")
        _a11y(self._help_btn, t("help.tip"))
        self._help_btn.setFixedSize(28, 28)
        layout.addWidget(self._help_btn)

        _lang_labels = {"en": "EN", "pt": "PT", "es": "ES", "fr": "FR", "de": "DE", "zh": "ZH", "it": "IT", "nl": "NL"}
        from app.i18n import get_language
        self._lang_btn = QPushButton(_lang_labels.get(get_language(), "EN"))
        self._lang_btn.setObjectName("theme_btn")
        _a11y(self._lang_btn, t("lang.selector"))
        self._lang_btn.setFixedSize(28, 28)
        layout.addWidget(self._lang_btn)

        self._theme_btn = QPushButton()
        self._theme_btn.setIcon(svg_icon(SVG_SUN, color="#FBBF24", size=15))
        self._theme_btn.setObjectName("theme_btn")
        _a11y(self._theme_btn, t("theme.toggle"))
        self._theme_btn.setFixedSize(28, 28)
        self._theme_btn.setIconSize(QSize(15, 15))
        layout.addWidget(self._theme_btn)

        self._update_btn = QPushButton()
        self._update_btn.setIcon(svg_icon(SVG_ARROW_CIRCLE_UP, color=ACCENT, size=15))
        self._update_btn.setObjectName("viewer_nav_btn")
        self._update_btn.setFixedSize(28, 28)
        self._update_btn.setIconSize(QSize(15, 15))
        _a11y(self._update_btn, t("update.check"))
        self._update_btn.setVisible(False)
        self._update_btn.setStyleSheet(
            f"QPushButton {{ border: 1.5px solid {ACCENT}; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: rgba(20,184,166,0.15); }}"
        )
        layout.addWidget(self._update_btn)

        # ── Far Right: Tool Pane Toggle ──────────────────────────────
        self._right_pane_toggle_btn = QPushButton()
        self._right_pane_toggle_btn.setIcon(svg_icon(SVG_COLUMNS, color=TEXT_PRI, size=15))
        self._right_pane_toggle_btn.setObjectName("viewer_nav_btn")
        self._right_pane_toggle_btn.setFixedSize(28, 28)
        self._right_pane_toggle_btn.setIconSize(QSize(15, 15))
        _a11y(self._right_pane_toggle_btn, t("sidebar.collapse_expand"))
        self._right_pane_toggle_btn.setVisible(False)
        layout.addWidget(self._right_pane_toggle_btn)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 36)

    def paintEvent(self, event):
        _paint_bg(self)

    def update_theme(self, dark: bool, sidebar_collapsed: bool = False):
        bar_color = TEXT_PRI if dark else _LP
        self._ico_bars = svg_icon(SVG_BARS, color=bar_color, size=15)
        self._ico_pages = svg_icon(SVG_TH_LARGE, color=bar_color, size=15)

        self._sidebar_toggle_btn.setIcon(self._ico_bars)
        self._pages_toggle_btn.setIcon(self._ico_pages)
        self._right_pane_toggle_btn.setIcon(svg_icon(SVG_COLUMNS, color=bar_color, size=15))
        self._open_pdf_btn.setIcon(svg_icon(SVG_FOLDER, color=bar_color, size=15))
        self._toc_top_btn.setIcon(svg_icon(SVG_BOOKMARK, color=bar_color, size=15))

        is_night = self._night_top_btn.isChecked()
        self._night_top_btn.setIcon(svg_icon(SVG_SUN if is_night else SVG_MOON, color=ACCENT if is_night else bar_color, size=15))
        self._print_top_btn.setIcon(svg_icon(SVG_PRINT, color=bar_color, size=15))
        self._present_btn.setIcon(svg_icon(SVG_TV, color=bar_color, size=15))
        self._search_top_btn.setIcon(svg_icon(SVG_SEARCH, color=bar_color, size=15))
        self._undo_top_btn.setIcon(svg_icon(SVG_ROTATE_LEFT, color=bar_color, size=15))
        self._redo_top_btn.setIcon(svg_icon(SVG_ROTATE_RIGHT, color=bar_color, size=15))
        self._zm_btn.setIcon(svg_icon(SVG_ZOOM_OUT, color=bar_color, size=15))
        self._zp_btn.setIcon(svg_icon(SVG_ZOOM_IN, color=bar_color, size=15))
        self._z0_btn.setIcon(svg_icon(SVG_COMPRESS, color=bar_color, size=15))
        self._first_pg_btn.setIcon(svg_icon(SVG_DOUBLE_LEFT, color=bar_color, size=15))
        self._prev_pg_btn.setIcon(svg_icon(SVG_CHEVRON_LEFT, color=bar_color, size=15))
        self._next_pg_btn.setIcon(svg_icon(SVG_CHEVRON_RIGHT, color=bar_color, size=15))
        self._last_pg_btn.setIcon(svg_icon(SVG_DOUBLE_RIGHT, color=bar_color, size=15))

        self._theme_btn.setIcon(svg_icon(SVG_SUN if dark else SVG_MOON, color="#FBBF24" if dark else ACCENT, size=15))