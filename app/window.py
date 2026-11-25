# app/window.py
"""PDF_Editor – MainWindow: application main window facade with Alt+P Command Palette, History, and Selection Pane integration."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QStackedWidget, QStatusBar, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget
)
from shiboken6 import isValid

from app.base import BasePage
from app.command_palette import CommandPalette
from app.constants import ACCENT, APP_VERSION, DESKTOP, TEXT_PRI, TEXT_SEC, _LQ
from app.i18n import _CONFIG_PATH, t
from app.nav_config import NAV_ITEMS, _NAV_GROUPS, _NAV_KEYS
from app.single_instance import SingleInstanceServer
from app.styles import STYLE, STYLE_LIGHT
from app.update_controller import UpdateController
from app.utils import _make_palette, resource_path, svg_for_name
from app.viewer.canvas_1 import _SelectCanvas
from app.viewer.panel import PdfViewerPanel
from app.viewer.panel_page_ops import cleanup_page_clipboard
from app.viewer.thumbnails import ThumbnailPanel
from app.widgets import DropFileEdit
from app.window_actions import (
    WindowActionsMixin as _WindowActionsMixin,
    _has_dirty_user_pending,
)
from app.window_pipeline import WindowPipelineMixin as _WindowPipelineMixin
from app.window_tabs import _ViewerTabBar, WindowTabsMixin as _WindowTabsMixin
from app.workspace_bar import WorkspaceBar

_NAV_ICON_CACHE: dict[tuple[str, str], QIcon] = {}


def _get_nav_icon(icon_name: str, color: str) -> QIcon:
    key = (icon_name, color)
    ico = _NAV_ICON_CACHE.get(key)
    if ico is None:
        ico = svg_for_name(icon_name, color=color, size=20)
        _NAV_ICON_CACHE[key] = ico
    return ico


class _LazyPlaceholderWidget(QWidget):
    """Placeholder widget used in QStackedWidget until a tool is activated."""
    _is_lazy_placeholder: bool = True


class NavItemDelegate(QStyledItemDelegate):
    """Delegate for sidebar navigation list ensuring SVG icons and selection boxes
    are perfectly centered horizontally when the sidebar is collapsed."""

    def __init__(self, parent_nav: QListWidget, is_dark_fn=None):
        super().__init__(parent_nav)
        self._nav = parent_nav
        self._is_dark_fn = is_dark_fn

    def _is_collapsed(self) -> bool:
        sidebar = self._nav.parent()
        if isinstance(sidebar, QWidget):
            if sidebar.maximumWidth() <= 60 or sidebar.width() <= 60:
                return True
        return self._nav.width() <= 60

    def sizeHint(self, option, index: QModelIndex):
        if self._is_collapsed():
            idx = index.data(Qt.ItemDataRole.UserRole)
            if idx is not None and idx < 0:
                return QSize(0, 0)
            return QSize(self._nav.width(), 42)
        return super().sizeHint(option, index)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        if not self._is_collapsed():
            super().paint(painter, option, index)
            return

        idx = index.data(Qt.ItemDataRole.UserRole)
        if idx is not None and idx < 0:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        box_size = 36
        bx = rect.x() + (rect.width() - box_size) // 2
        by = rect.y() + (rect.height() - box_size) // 2
        box_rect = QRect(bx, by, box_size, box_size)

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        dark = self._is_dark_fn() if callable(self._is_dark_fn) else True

        if is_selected:
            bg_col = QColor("#264F78") if dark else QColor("#D6E8FA")
            border_col = QColor(ACCENT) if dark else QColor("#70A7DB")
            painter.setBrush(bg_col)
            painter.setPen(QPen(border_col, 1))
            painter.drawRoundedRect(box_rect, 6, 6)
        elif is_hovered:
            bg_col = QColor("#333333") if dark else QColor("#E0E7FF")
            painter.setBrush(bg_col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(box_rect, 6, 6)

        icon_size = self._nav.iconSize()
        if not icon_size.isValid() or icon_size.width() <= 0:
            icon_size = QSize(20, 20)
        ix = box_rect.x() + (box_rect.width() - icon_size.width()) // 2
        iy = box_rect.y() + (box_rect.height() - icon_size.height()) // 2
        icon_rect = QRect(ix, iy, icon_size.width(), icon_size.height())

        tool_idx = index.data(Qt.ItemDataRole.UserRole)
        if tool_idx is not None and 0 <= tool_idx < len(_NAV_KEYS):
            icon_name = _NAV_KEYS[tool_idx][1]
            col = ACCENT if is_selected else (TEXT_SEC if dark else _LQ)
            ico = _get_nav_icon(icon_name, col)
            ico.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
        else:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon):
                icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
            elif isinstance(icon, QPixmap):
                painter.drawPixmap(icon_rect, icon)

        painter.restore()


class MainWindow(_WindowTabsMixin, _WindowPipelineMixin, _WindowActionsMixin, QMainWindow):
    """Main Application Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("app.name"))
        _ico_path = resource_path("icon.ico")
        if os.path.exists(_ico_path):
            self.setWindowIcon(QIcon(_ico_path))
        else:
            _svg = resource_path("PDF_Editor.svg")
            if os.path.exists(_svg):
                self.setWindowIcon(QIcon(_svg))
        self.resize(1220, 700)
        self.setMinimumSize(480, 360)

        self._sb = QStatusBar()
        self.setStatusBar(self._sb)
        self._sb.showMessage(t("app.ready"))
        self.setAcceptDrops(True)

        central = QWidget()
        central.setObjectName("central_widget")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_v = QVBoxLayout(central)
        root_v.setContentsMargins(0, 0, 0, 0)
        root_v.setSpacing(0)

        self._workspace_bar = WorkspaceBar(self)
        wb = self._workspace_bar
        self._sidebar_toggle_btn = wb._sidebar_toggle_btn
        self._pages_toggle_btn = wb._pages_toggle_btn
        self._manage_tabs_btn = wb._manage_tabs_btn
        self._right_pane_toggle_btn = wb._right_pane_toggle_btn
        self._breadcrumb = wb._breadcrumb
        self._open_pdf_btn = wb._open_pdf_btn
        self._toc_top_btn = wb._toc_top_btn
        self._night_top_btn = wb._night_top_btn
        self._print_top_btn = wb._print_top_btn
        self._present_btn = wb._present_btn
        self._search_top_btn = wb._search_top_btn
        self._zoom_widget = wb._zoom_widget
        self._zm_btn = wb._zm_btn
        self._lbl_zoom = wb._lbl_zoom
        self._zp_btn = wb._zp_btn
        self._z0_btn = wb._z0_btn
        self._page_nav_widget = wb._page_nav_widget
        self._first_pg_btn = wb._first_pg_btn
        self._prev_pg_btn = wb._prev_pg_btn
        self._page_input = wb._page_input
        self._page_total_lbl = wb._page_total_lbl
        self._next_pg_btn = wb._next_pg_btn
        self._last_pg_btn = wb._last_pg_btn
        self._undo_top_btn = wb._undo_top_btn
        self._redo_top_btn = wb._redo_top_btn
        self._help_btn = wb._help_btn
        self._lang_btn = wb._lang_btn
        self._theme_btn = wb._theme_btn
        self._update_btn = wb._update_btn

        self._sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self._pages_toggle_btn.clicked.connect(self._toggle_pages_sidebar)
        self._manage_tabs_btn.clicked.connect(self._show_manage_tabs_dialog)
        self._right_pane_toggle_btn.clicked.connect(self._toggle_right_pane)
        self._open_pdf_btn.clicked.connect(self._open_pdf)
        self._open_pdf_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._open_pdf_btn.customContextMenuRequested.connect(lambda _: self._show_recent_files_menu())
        self._toc_top_btn.clicked.connect(self._toggle_pages_sidebar)
        self._night_top_btn.clicked.connect(self._toggle_night_mode_top)
        self._print_top_btn.clicked.connect(lambda: self._viewer._print_pdf() if self._viewer else None)
        self._present_btn.clicked.connect(self._start_presentation)
        self._search_top_btn.clicked.connect(lambda: self._viewer._toggle_search() if self._viewer else None)
        self._first_pg_btn.clicked.connect(self._goto_first_page)
        self._prev_pg_btn.clicked.connect(self._goto_prev_page)
        self._next_pg_btn.clicked.connect(self._goto_next_page)
        self._last_pg_btn.clicked.connect(self._goto_last_page)
        self._page_input.returnPressed.connect(self._goto_input_page)
        self._undo_top_btn.clicked.connect(self._handle_global_undo)
        self._redo_top_btn.clicked.connect(self._handle_global_redo)
        self._help_btn.clicked.connect(lambda: __import__('webbrowser').open("https://pdf-apps.com/docs#first-steps"))
        self._lang_btn.clicked.connect(self._show_language_menu)
        self._theme_btn.clicked.connect(self._toggle_theme)

        self._update_controller = UpdateController(self, self._update_btn)
        self._update_btn.clicked.connect(self._update_controller.show_update_dialog)
        QTimer.singleShot(2000, lambda: self._update_controller.check_async() if isValid(self) else None)

        root_v.addWidget(self._workspace_bar)

        body = QWidget()
        body.setObjectName("workspace_shell")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        main_h = QHBoxLayout(body)
        main_h.setContentsMargins(10, 10, 10, 0)
        main_h.setSpacing(0)

        self._sidebar = QWidget()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sidebar.setFixedWidth(228)
        sb_lay = QVBoxLayout(self._sidebar)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(0)

        brand = QWidget()
        brand.setObjectName("brand_area")
        self._brand_layout = bh = QHBoxLayout(brand)
        bh.setContentsMargins(12, 10, 10, 10)
        bh.setSpacing(8)
        ico_lbl = QLabel()
        _svg_path = resource_path("PDF_Editor.svg")
        _png_path = resource_path("icon_512.png")
        _h = 36
        dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else 1.0
        if dpr <= 0:
            dpr = 1.0
        if os.path.exists(_png_path):
            _app_pix = QPixmap(_png_path).scaled(
                int(_h * dpr), int(_h * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            _app_pix.setDevicePixelRatio(dpr)
            _w = _h
        elif os.path.exists(_svg_path):
            renderer = QSvgRenderer(_svg_path)
            vb = renderer.viewBox()
            ratio = vb.width() / vb.height() if vb.height() else 1.0
            _w = int(_h * ratio)
            img = QImage(int(_w * dpr), int(_h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            p = QPainter(img)
            renderer.render(p)
            p.end()
            _app_pix = QPixmap.fromImage(img)
            _app_pix.setDevicePixelRatio(dpr)
        else:
            _w = _h
            _ico_path = resource_path("icon.ico")
            _app_pix = QPixmap(_ico_path).scaled(
                int(_w * dpr), int(_w * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            _app_pix.setDevicePixelRatio(dpr)
        ico_lbl.setPixmap(_app_pix)
        ico_lbl.setObjectName("app_icon")
        ico_lbl.setFixedSize(_w, _h)
        ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bh.addWidget(ico_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self._brand_text_w = QWidget()
        brand_text = QVBoxLayout(self._brand_text_w)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(1)
        self._brand_title = QLabel(t("app.name"))
        self._brand_title.setObjectName("app_title")
        self._brand_sub = QLabel(t("app.subtitle"))
        self._brand_sub.setObjectName("app_sub")
        brand_text.addWidget(self._brand_title)
        brand_text.addWidget(self._brand_sub)
        bh.addWidget(self._brand_text_w, 1)
        sb_lay.addWidget(brand)

        sep = QFrame()
        sep.setObjectName("nav_sep")
        sep.setFixedHeight(1)
        sb_lay.addWidget(sep)

        self._nav_search = QLineEdit()
        self._nav_search.setPlaceholderText(t("nav.search"))
        self._nav_search.setClearButtonEnabled(True)
        self._nav_search.setObjectName("nav_search")
        self._nav_search.textChanged.connect(self._filter_nav)
        sb_lay.addWidget(self._nav_search)

        self.nav = QListWidget()
        self.nav.setObjectName("nav_list")
        self.nav.setSpacing(0)
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setMouseTracking(True)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if self.nav.verticalScrollBar():
            self.nav.verticalScrollBar().setFixedWidth(0)
            self.nav.verticalScrollBar().setMaximumWidth(0)
            self.nav.verticalScrollBar().setStyleSheet("width: 0px; max-width: 0px; border: none; background: transparent;")

        self._nav_delegate = NavItemDelegate(self.nav, is_dark_fn=lambda: self._dark_mode)
        self.nav.setItemDelegate(self._nav_delegate)

        tool_idx = 0
        for group_key, tools in _NAV_GROUPS:
            if tool_idx > 0:
                sep_item = QListWidgetItem()
                sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
                sep_item.setData(Qt.ItemDataRole.UserRole, -1)
                sep_item.setSizeHint(QSize(0, 24))
                self.nav.addItem(sep_item)
                sep_line = QFrame()
                sep_line.setObjectName("nav_group_sep")
                sep_line.setFixedHeight(1)
                self.nav.setItemWidget(sep_item, sep_line)

            hdr = QListWidgetItem(t(group_key).upper())
            hdr.setFlags(Qt.ItemFlag.NoItemFlags)
            hdr.setData(Qt.ItemDataRole.UserRole, -1)
            hdr.setForeground(QColor(TEXT_SEC))
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
            hdr.setFont(f)
            hdr.setSizeHint(QSize(0, 26))
            self.nav.addItem(hdr)
            for key, icon_name, _ in tools:
                item = QListWidgetItem(_get_nav_icon(icon_name, TEXT_SEC), t(key))
                item.setData(Qt.ItemDataRole.UserRole, tool_idx)
                desc_key = key.replace("nav.", "tool.") + ".desc"
                tip = t(desc_key)
                if tip != desc_key:
                    item.setToolTip(tip)
                self.nav.addItem(item)
                tool_idx += 1
        sb_lay.addWidget(self.nav, 1)

        self._footer_w = QWidget()
        self._footer_w.setObjectName("sidebar")
        footer_h = QHBoxLayout(self._footer_w)
        footer_h.setContentsMargins(14, 8, 14, 10)
        footer_h.setSpacing(0)
        footer_lbl = QLabel(f"v{APP_VERSION}  -  {t('app.credits')}")
        footer_lbl.setObjectName("sidebar_footer")
        footer_h.addWidget(footer_lbl, 1)
        sb_lay.addWidget(self._footer_w)
        self._sidebar_collapsed = False

        self._dark_mode = True
        self._tool_usage: dict[str, int] = {}
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as _cf:
                _cfg_data = json.load(_cf)
                self._dark_mode = _cfg_data.get("dark_mode", True)
                self._tool_usage = _cfg_data.get("tool_usage", {})
        except Exception:
            pass
        self._qapp: QApplication = cast(QApplication, QApplication.instance())

        self.stack = QStackedWidget()
        self.stack.setObjectName("content_area")
        for _ in NAV_ITEMS:
            self.stack.addWidget(_LazyPlaceholderWidget())
        self.stack.setVisible(False)

        self._right_tool_container = QWidget()
        self._right_tool_container.setObjectName("right_tool_container")
        rc_lay = QVBoxLayout(self._right_tool_container)
        rc_lay.setContentsMargins(0, 0, 0, 0)
        rc_lay.setSpacing(0)
        rc_lay.addWidget(self.stack, 1)
        self._right_tool_container.setVisible(False)
        self._saved_right_width = 380

        self._tab_container = QWidget()
        tc_lay = QVBoxLayout(self._tab_container)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.setSpacing(0)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        self._tab_bar = _ViewerTabBar()
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setMovable(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setObjectName("viewer_tabs")
        self._tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        self._current_tool = -1
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)
        self._tab_bar.active_tab_clicked.connect(self._on_active_tab_clicked)
        self._tab_bar.setVisible(False)
        tab_row.addWidget(self._tab_bar, 1)

        self._viewer_stack = QStackedWidget()
        self._viewers: list[PdfViewerPanel] = []
        self._add_viewer_tab()
        tc_lay.addWidget(self._viewer_stack, 1)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(1)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._tab_container)
        self._splitter.addWidget(self._right_tool_container)
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)
        self._splitter.setSizes([800, 0])

        self._content_area = QWidget()
        content_v = QVBoxLayout(self._content_area)
        content_v.setContentsMargins(0, 0, 0, 0)
        content_v.setSpacing(0)
        content_v.addLayout(tab_row)
        content_v.addWidget(self._splitter, 1)

        main_h.addWidget(self._sidebar)
        main_h.addWidget(self._content_area, 1)
        root_v.addWidget(body, 1)
        self.setCentralWidget(central)

        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as _cf:
                _saved = json.load(_cf)
            mode = _saved.get("sidebar_mode")
            if mode == "icons":
                self._toggle_sidebar()
            elif mode == "hidden":
                self._toggle_sidebar()
                self._toggle_sidebar()
            pages_pref = _saved.get("pages_sidebar_open")
            if pages_pref is not None:
                PdfViewerPanel._pages_sidebar_visible_pref = bool(pages_pref)
            saved_w = _saved.get("sidebar_panel_width")
            if saved_w is not None:
                PdfViewerPanel._saved_sidebar_width_pref = max(70, min(600, int(saved_w)))
            saved_thumb_scale = _saved.get("thumbnail_scale")
            if saved_thumb_scale is not None:
                ThumbnailPanel._thumb_scale_pref = max(0.5, min(2.5, float(saved_thumb_scale)))
            saved_zoom = _saved.get("viewer_zoom_factor")
            if saved_zoom is not None:
                _SelectCanvas._saved_zoom_factor_pref = max(0.2, min(8.0, float(saved_zoom)))
        except Exception:
            pass

        self._current_tool = -1
        self.nav.itemClicked.connect(self._on_nav_clicked)
        self._pipeline_state: dict[int, dict] = {}

        # ── Global Keyboard Shortcuts ─────────────────────────────────────
        QShortcut(QKeySequence("Alt+P"), self, self._show_command_palette)
        QShortcut(QKeySequence("Ctrl+Shift+P"), self, self._show_command_palette)
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, self._show_manage_tabs_dialog)
        QShortcut(QKeySequence("F5"), self, self._start_presentation)
        sc_alt1 = QShortcut(QKeySequence("Alt+1"), self, self._start_presentation)
        sc_alt1.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("F11"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+O"), self, self._open_pdf)
        sc_hist = QShortcut(QKeySequence("Ctrl+H"), self, self._show_recent_files_menu)
        sc_hist.setContext(Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence("Ctrl+P"), self, lambda: self._viewer._print_pdf() if self._viewer else None)

        sc_zi1 = QShortcut(QKeySequence("Ctrl++"), self, self._zoom_in_current)
        sc_zi2 = QShortcut(QKeySequence("Ctrl+="), self, self._zoom_in_current)
        sc_zo1 = QShortcut(QKeySequence("Ctrl+-"), self, self._zoom_out_current)
        sc_zo2 = QShortcut(QKeySequence("Ctrl+_"), self, self._zoom_out_current)
        sc_zr = QShortcut(QKeySequence("Ctrl+0"), self, lambda: self._handle_ctrl_digit(0))
        for sc in (sc_zi1, sc_zi2, sc_zo1, sc_zo2):
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_zr.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self, self._handle_global_undo)
        sc_redo1 = QShortcut(QKeySequence("Ctrl+Y"), self, self._handle_global_redo)
        sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._handle_global_redo)
        sc_close = QShortcut(QKeySequence("Ctrl+W"), self, self._close_current_tab)
        sc_save = QShortcut(QKeySequence("Ctrl+S"), self, self._save_current_tool)
        sc_save_as = QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._save_as_current_doc)
        sc_pgup = QShortcut(QKeySequence("PgUp"), self, self._goto_prev_page)
        sc_pgdn = QShortcut(QKeySequence("PgDown"), self, self._goto_next_page)
        sc_sel_all = QShortcut(QKeySequence("Ctrl+A"), self, self._handle_global_select_all)
        for sc in (sc_undo, sc_redo1, sc_redo2, sc_close, sc_save, sc_save_as, sc_pgup, sc_pgdn, sc_sel_all):
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        assert len(NAV_ITEMS) <= 18, f"Max 18 tool shortcuts defined but NAV_ITEMS has {len(NAV_ITEMS)}."
        for idx in range(len(NAV_ITEMS)):
            if idx < 9:
                sc_d = QShortcut(QKeySequence(f"Ctrl+{idx+1}"), self, lambda d=idx + 1: self._handle_ctrl_digit(d))
                sc_d.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            elif idx < 18:
                QShortcut(QKeySequence(f"Ctrl+Shift+{idx-8}"), self, lambda i=idx: self._activate_tool(i))
        self._fullscreen = False

        if not self._dark_mode:
            self._apply_theme()

        self._setup_zoom_bar(True, canvas=self._viewer._canvas if self._viewer else None)
        self._update_undo_redo_buttons()

        self._instance_server = SingleInstanceServer(self)
        self._instance_server.new_paths.connect(self._on_second_instance)

    def _handle_ctrl_digit(self, digit: int) -> None:
        """In Edit mode, Ctrl+1..0 apply color shortcuts to selected text (or all text boxes if none selected)."""
        edit_idx = self._edit_tool_idx()
        if self._current_tool == edit_idx and edit_idx >= 0:
            edit_w: Any = self._get_or_create_tool(edit_idx)
            apply_fn = getattr(edit_w, "_apply_color_shortcut", None)
            if callable(apply_fn) and apply_fn(digit):
                return

        if digit == 0:
            self._zoom_reset_current()
        else:
            tool_idx = digit - 1
            if 0 <= tool_idx < len(NAV_ITEMS):
                self._activate_tool(tool_idx)

    def _open_selection_pane(self):
        """Open the Selection Pane inspector for the active PDF in Edit mode."""
        edit_idx = self._edit_tool_idx()
        if self._current_tool != edit_idx:
            self._open_tool_by_name(t("nav.edit"))
        edit_w: Any = self._get_or_create_tool(edit_idx)
        if edit_w and hasattr(edit_w, "open_selection_pane"):
            return edit_w.open_selection_pane()
        return None

    def _show_command_palette(self):
        """Build actions list and present the Alt+P Command Palette."""
        commands: list[dict[str, Any]] = [
            {
                "name": "Manage & Switch Tabs... (Filter, Reorder, Duplicate & Jump)",
                "category": "Tabs",
                "action": self._show_manage_tabs_dialog,
                "shortcut": "Ctrl+Shift+T",
                "icon": svg_for_name("manage-tabs", ACCENT, 18),
            },
            {
                "name": "Selection Pane (Manage Layers, Opacity, Lock & Visibility)",
                "category": "Edit",
                "action": self._open_selection_pane,
                "shortcut": "Alt+P",
                "icon": svg_for_name("fa5s.th-large", ACCENT, 18),
            },
            {
                "name": "Edit PDF (Visual Objects, Text, Drawing & Fill)",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.edit")),
                "shortcut": "Ctrl+15",
                "icon": svg_for_name("fa5s.edit", ACCENT, 18),
            },
            {
                "name": "Open PDF Document...",
                "category": "File",
                "action": self._open_pdf,
                "shortcut": "Ctrl+O",
                "icon": svg_for_name("fa5s.folder-open", ACCENT, 18),
            },
            {
                "name": "Recent Files History...",
                "category": "File",
                "action": self._show_recent_files_menu,
                "shortcut": "Ctrl+H",
                "icon": svg_for_name("fa5s.history", ACCENT, 18),
            },
            {
                "name": "Save Changes",
                "category": "File",
                "action": self._save_current_tool,
                "shortcut": "Ctrl+S",
                "icon": svg_for_name("fa5s.save", ACCENT, 18),
            },
            {
                "name": "Save As...",
                "category": "File",
                "action": self._save_as_current_doc,
                "shortcut": "Ctrl+Shift+S",
                "icon": svg_for_name("fa5s.save", ACCENT, 18),
            },
            {
                "name": "Print PDF...",
                "category": "File",
                "action": lambda: self._viewer._print_pdf() if self._viewer else None,
                "shortcut": "Ctrl+P",
                "icon": svg_for_name("fa5s.print", ACCENT, 18),
            },
            {
                "name": "Presentation Mode (Full Screen Slideshow)",
                "category": "View",
                "action": self._start_presentation,
                "shortcut": "F5 / Alt+1",
                "icon": svg_for_name("fa5s.tv", ACCENT, 18),
            },
            {
                "name": "Toggle Fullscreen",
                "category": "View",
                "action": self._toggle_fullscreen,
                "shortcut": "F11",
                "icon": svg_for_name("fa5s.expand", ACCENT, 18),
            },
            {
                "name": "Toggle Dark / Light Theme",
                "category": "View",
                "action": self._toggle_theme,
                "shortcut": "",
                "icon": svg_for_name("fa5s.moon", ACCENT, 18),
            },
            {
                "name": "Toggle Main Sidebar",
                "category": "View",
                "action": self._toggle_sidebar,
                "shortcut": "",
                "icon": svg_for_name("fa5s.bars", ACCENT, 18),
            },
            {
                "name": "Toggle Page Thumbnails Sidebar",
                "category": "View",
                "action": self._toggle_pages_sidebar,
                "shortcut": "",
                "icon": svg_for_name("fa5s.th-large", ACCENT, 18),
            },
            {
                "name": "Toggle Night Mode (Color Inversion)",
                "category": "View",
                "action": self._toggle_night_mode_top,
                "shortcut": "",
                "icon": svg_for_name("fa5s.sun", ACCENT, 18),
            },
            {
                "name": "Zoom In",
                "category": "View",
                "action": self._zoom_in_current,
                "shortcut": "Ctrl++",
                "icon": svg_for_name("fa5s.search-plus", ACCENT, 18),
            },
            {
                "name": "Zoom Out",
                "category": "View",
                "action": self._zoom_out_current,
                "shortcut": "Ctrl+-",
                "icon": svg_for_name("fa5s.search-minus", ACCENT, 18),
            },
            {
                "name": "Reset Zoom (Fit Page)",
                "category": "View",
                "action": self._zoom_reset_current,
                "shortcut": "Ctrl+0",
                "icon": svg_for_name("fa5s.compress-arrows-alt", ACCENT, 18),
            },
            {
                "name": "Search in Document...",
                "category": "View",
                "action": lambda: self._viewer._toggle_search() if self._viewer else None,
                "shortcut": "Ctrl+F",
                "icon": svg_for_name("fa5s.search", ACCENT, 18),
            },
            {
                "name": "Select All Text",
                "category": "Edit",
                "action": self._handle_global_select_all,
                "shortcut": "Ctrl+A",
                "icon": svg_for_name("fa5s.mouse-pointer", ACCENT, 18),
            },
            {
                "name": "Undo Action",
                "category": "Edit",
                "action": self._handle_global_undo,
                "shortcut": "Ctrl+Z",
                "icon": svg_for_name("fa5s.undo", ACCENT, 18),
            },
            {
                "name": "Redo Action",
                "category": "Edit",
                "action": self._handle_global_redo,
                "shortcut": "Ctrl+Y",
                "icon": svg_for_name("fa5s.redo", ACCENT, 18),
            },
            {
                "name": "Close Current Tab",
                "category": "Document",
                "action": self._close_current_tab,
                "shortcut": "Ctrl+W",
                "icon": svg_for_name("fa5s.times", "#EF4444", 18),
            },
            {
                "name": "Crop Pages Tool",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.crop")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.crop-alt", ACCENT, 18),
            },
            {
                "name": "Rotate Pages Tool",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.rotate")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.sync-alt", ACCENT, 18),
            },
            {
                "name": "Compress PDF Tool",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.compress")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.compress-arrows-alt", ACCENT, 18),
            },
            {
                "name": "Add Page Numbers Tool",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.page_numbers")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.list-ol", ACCENT, 18),
            },
            {
                "name": "N-Up Multiple Pages Per Sheet",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.nup")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.th", ACCENT, 18),
            },
            {
                "name": "Merge Multiple PDFs",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.merge")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.object-group", ACCENT, 18),
            },
            {
                "name": "Split PDF by Ranges",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.split")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.cut", ACCENT, 18),
            },
            {
                "name": "Extract PDF Pages",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.extract")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.file-export", ACCENT, 18),
            },
            {
                "name": "Reorder PDF Pages",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.reorder")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.sort", ACCENT, 18),
            },
            {
                "name": "Watermark PDF (Stamps & Overlays)",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.watermark")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.stamp", ACCENT, 18),
            },
            {
                "name": "Encrypt / Password Protect PDF",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.encrypt")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.lock", ACCENT, 18),
            },
            {
                "name": "OCR Text Recognition",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.ocr")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.search", ACCENT, 18),
            },
            {
                "name": "Convert PDF (DOCX, Images, PPTX, XLSX, HTML, EPUB)",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.convert")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.exchange-alt", ACCENT, 18),
            },
            {
                "name": "Import Office & Image Files to PDF",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.import")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.file-import", ACCENT, 18),
            },
            {
                "name": "Document Information & Metadata",
                "category": "Tools",
                "action": lambda: self._open_tool_by_name(t("nav.info")),
                "shortcut": "",
                "icon": svg_for_name("fa5s.info-circle", ACCENT, 18),
            },
        ]

        dlg = CommandPalette(commands, parent=self)
        dlg.exec()

    def _get_or_create_tool(self, index: int) -> QWidget | None:
        if not (0 <= index < len(NAV_ITEMS)):
            return None
        w = self.stack.widget(index)
        if w is None:
            return None
        if isinstance(w, _LazyPlaceholderWidget) or getattr(w, "_is_lazy_placeholder", False):
            _, __, loader = NAV_ITEMS[index]
            real_widget = loader(self._set_status)
            self._connect_tool_signals(real_widget)
            fn = getattr(real_widget, "update_theme", None)
            if callable(fn):
                fn(self._dark_mode)
            self.stack.removeWidget(w)
            w.deleteLater()
            self.stack.insertWidget(index, real_widget)
            return real_widget
        return w

    def _connect_tool_signals(self, w: Any) -> None:
        for dfe in w.findChildren(DropFileEdit):
            if not getattr(dfe, "_save", False) and not getattr(dfe, "_skip_window_track", False):
                dfe.path_changed.connect(lambda p: self._load_and_track(p) if (p and os.path.isfile(p)) else None)

        pipeline_done = getattr(w, "pipeline_done", None)
        if pipeline_done is not None and hasattr(pipeline_done, "connect"):
            pipeline_done.connect(self._on_pipeline_done)

        pipeline_save = getattr(w, "pipeline_save_requested", None)
        if pipeline_save is not None and hasattr(pipeline_save, "connect"):
            pipeline_save.connect(lambda: self._save_pipeline(prompt_dialog=True))

        for sig_name, handler in (
            ("rotations_changed", self._on_rotations_changed),
            ("crop_changed", self._on_crop_changed),
            ("crops_changed", self._on_crops_changed),
            ("crop_mode_toggled", self._on_crop_mode_toggled),
            ("order_changed", self._on_order_changed),
            ("numbers_preview_changed", self._on_numbers_preview_changed),
        ):
            sig = getattr(w, sig_name, None)
            if sig is not None and hasattr(sig, "connect"):
                sig.connect(handler)

    def closeEvent(self, event):
        for v in self._viewers:
            if self._viewer_has_unsaved(v):
                ans = QMessageBox.question(
                    self, t("msg.warning"), t("pipeline.unsaved_prompt"),
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel
                )
                if ans == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if ans == QMessageBox.StandardButton.Save:
                    self._save_pipeline(prompt_dialog=False)
                break
        edit_idx = self._edit_tool_idx()
        if edit_idx >= 0:
            edit_w: Any = self.stack.widget(edit_idx)
            if edit_w and not isinstance(edit_w, _LazyPlaceholderWidget):
                has_cur_dirty = bool(
                    getattr(edit_w, "_user_pending", None)
                    or (hasattr(edit_w, "_history") and edit_w._history.has_page_ops())
                )
                dirty_cached = [
                    st_path for st_path, st in getattr(edit_w, "_doc_states", {}).items()
                    if _has_dirty_user_pending(st.get("pending", []))
                    or any(act.get("kind") == "page_doc" for act in st.get("undo_stack", []))
                ]
                if has_cur_dirty or dirty_cached:
                    ans = QMessageBox.question(
                        self, t("msg.warning"), t("pipeline.unsaved_prompt"),
                        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel
                    )
                    if ans == QMessageBox.StandardButton.Cancel:
                        event.ignore()
                        return
                    if ans == QMessageBox.StandardButton.Save:
                        if has_cur_dirty:
                            edit_w._run()
                        for st_path in dirty_cached:
                            if os.path.isfile(st_path) and os.path.abspath(st_path) != os.path.abspath(getattr(edit_w, "_doc_path", "") or ""):
                                edit_w.auto_load(st_path)
                                if getattr(edit_w, "_user_pending", None) or (hasattr(edit_w, "_history") and edit_w._history.has_page_ops()):
                                    edit_w._run()
        for v in list(self._viewers):
            self._cleanup_pipeline(id(v))
        cleanup_page_clipboard()
        self._wait_for_workers_on_all_pages()
        self._wipe_all_pdf_passwords()
        self._update_controller.release_worker()
        try:
            from app.i18n import _update_config
            sizes = self._splitter.sizes()
            mode = "hidden" if self._sidebar_collapsed else ("icons" if self._sidebar.width() <= 60 else "full")
            pages_open = getattr(PdfViewerPanel, "_pages_sidebar_visible_pref", True)
            sidebar_w = getattr(PdfViewerPanel, "_saved_sidebar_width_pref", 220)
            thumb_scale = getattr(ThumbnailPanel, "_thumb_scale_pref", 1.0)
            zoom_factor = getattr(_SelectCanvas, "_saved_zoom_factor_pref", 1.0)

            def _mutate(cfg: dict) -> None:
                cfg["splitter_sizes"] = sizes
                cfg["sidebar_mode"] = mode
                cfg["pages_sidebar_open"] = bool(pages_open)
                cfg["sidebar_panel_width"] = int(sidebar_w)
                cfg["thumbnail_scale"] = float(thumb_scale)
                cfg["viewer_zoom_factor"] = float(zoom_factor)
            _update_config(_mutate)
        except Exception:
            pass
        super().closeEvent(event)