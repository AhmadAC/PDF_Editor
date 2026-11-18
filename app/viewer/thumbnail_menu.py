# app/viewer/thumbnail_menu.py
"""Context menu builder, inline count-spinbox widgets, and action routing for ThumbnailPanel."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QProxyStyle,
    QPushButton,
    QSpinBox,
    QStyle,
    QWidget,
    QWidgetAction,
)

from app.constants import ACCENT, TEXT_PRI, _LQ
from app.utils import (
    SVG_ARROWS_H,
    SVG_CLONE,
    SVG_COPY,
    SVG_CROP,
    SVG_CUT,
    SVG_EXCHANGE,
    SVG_EXPAND,
    SVG_FILE,
    SVG_FILE_EXPORT,
    SVG_FILE_PLUS,
    SVG_FOLDER,
    SVG_INFO,
    SVG_LIST_OL,
    SVG_MOVE,
    SVG_PASTE,
    SVG_PRINT,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    SVG_SORT,
    SVG_SWAP,
    SVG_SYNC,
    SVG_TH,
    SVG_TH_LARGE,
    SVG_TRASH,
    SVG_TV,
    SVG_ZOOM_IN,
    SVG_ZOOM_OUT,
    svg_icon,
)


class _ScrollableMenuStyle(QProxyStyle):
    """Enforce single-column scrollable QMenu across all platforms."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class _SpinEnterFilter(QObject):
    """Trigger the menu row action when Enter/Return is pressed inside its spinbox."""

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self._cb = callback

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self._cb()
            return True
        return super().eventFilter(obj, event)


def _add_counted_action(
    root_menu: QMenu,
    target_menu: QMenu,
    dark: bool,
    icon_svg: str,
    icon_col: str,
    label_text: str,
    shortcut_text: str = "",
    tooltip: str = "Number of copies/pages (1–999)",
):
    wa = QWidgetAction(target_menu)
    row_w = QWidget(target_menu)
    row_w.setObjectName("menu_count_row")
    h = QHBoxLayout(row_w)
    h.setContentsMargins(4, 2, 6, 2)
    h.setSpacing(6)

    fg = TEXT_PRI if dark else "#1A2B28"
    brd = "#444444" if dark else "#D1D5DB"
    inp_bg = "#1E1E1E" if dark else "#FFFFFF"
    sec_fg = "#9CA3AF" if dark else "#6B7280"

    btn = QPushButton(label_text, row_w)
    btn.setIcon(svg_icon(icon_svg, color=icon_col, size=15))
    btn.setIconSize(QSize(15, 15))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFlat(True)
    btn.setStyleSheet(
        f"QPushButton {{ text-align: left; padding: 4px 10px 4px 8px;"
        f" border: none; border-radius: 4px; background: transparent;"
        f" color: {fg}; font-size: 10.5pt; }}"
        f"QPushButton:hover {{ background: {ACCENT}; color: #FFFFFF; }}"
    )

    spin = QSpinBox(row_w)
    spin.setRange(1, 999)
    spin.setValue(1)
    spin.setToolTip(tooltip)
    spin.setFixedWidth(54)
    spin.setFixedHeight(22)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setStyleSheet(
        f"QSpinBox {{ background: {inp_bg}; color: {fg};"
        f" border: 1px solid {brd}; border-radius: 4px;"
        f" padding: 0px 2px; font-size: 9.5pt; }}"
        f"QSpinBox:focus {{ border: 1px solid {ACCENT}; }}"
    )

    h.addWidget(btn, 1)
    if shortcut_text:
        sc_lbl = QLabel(shortcut_text, row_w)
        sc_lbl.setStyleSheet(
            f"color: {sec_fg}; font-size: 9.5pt; background: transparent; padding-right: 4px;"
        )
        h.addWidget(sc_lbl)
    h.addWidget(spin)

    chosen_result = {"triggered": False, "count": 1}

    def _trigger():
        with contextlib.suppress(Exception):
            spin.interpretText()
        chosen_result["triggered"] = True
        chosen_result["count"] = spin.value()
        if target_menu is not root_menu:
            target_menu.close()
        root_menu.close()

    btn.clicked.connect(_trigger)
    le = spin.lineEdit()
    flt = _SpinEnterFilter(_trigger, spin)
    spin.installEventFilter(flt)
    if le is not None:
        le.installEventFilter(flt)

    wa.setDefaultWidget(row_w)
    target_menu.addAction(wa)
    return wa, spin, chosen_result


class ThumbnailMenuMixin:
    """Mixin building and dispatching the Foxit-style thumbnail context menu."""

    if TYPE_CHECKING:
        _model: Any
        _delegate: Any
        _menu_style: _ScrollableMenuStyle | None
        action_requested: Any

        def _enlarge_thumbnails(self) -> None: ...
        def _reduce_thumbnails(self) -> None: ...
        def fit_window_to_thumbnails(self) -> None: ...
        def fit_thumbnails_to_window(self) -> None: ...

    def _show_context_menu(
        self, global_pos: QPoint, selected_pages: list[int]
    ) -> None:
        if self._model.rowCount() <= 0:
            return

        from app.viewer.panel_page_ops import has_cut_pages

        dark = self._delegate._dark
        icon_color = TEXT_PRI if dark else _LQ
        n_sel = len(selected_pages)
        suffix = f" ({n_sel})" if n_sel > 1 else ""

        menu = QMenu(self)  # type: ignore[arg-type]
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        act_cut = menu.addAction(
            svg_icon(SVG_CUT, color=icon_color, size=15), f"Cut Pages{suffix}\tCtrl+X"
        )
        act_copy_pages = menu.addAction(
            svg_icon(SVG_COPY, color=icon_color, size=15), f"Copy Pages{suffix}\tCtrl+C"
        )

        if selected_pages == [0]:
            paste_title = "Paste Pages (Before Page 1)\tCtrl+V"
        elif selected_pages:
            paste_title = f"Paste Pages (After Page {max(selected_pages) + 1})\tCtrl+V"
        else:
            paste_title = "Paste Pages\tCtrl+V"

        act_paste_pages = menu.addAction(
            svg_icon(SVG_PASTE, color=icon_color, size=15), paste_title
        )
        act_paste_pages.setEnabled(has_cut_pages())

        act_copy = menu.addAction(
            svg_icon(SVG_COPY, color=icon_color, size=15), f"Copy Text{suffix}"
        )
        act_paste = menu.addAction(
            svg_icon(SVG_PASTE, color=icon_color, size=15), "Paste Text"
        )
        menu.addSeparator()

        act_enlarge = menu.addAction(
            svg_icon(SVG_ZOOM_IN, color=icon_color, size=15),
            "Enlarge Page Thumbnails",
        )
        act_reduce = menu.addAction(
            svg_icon(SVG_ZOOM_OUT, color=icon_color, size=15),
            "Reduce Page Thumbnails",
        )
        act_fit_window = menu.addAction(
            svg_icon(SVG_ARROWS_H, color=icon_color, size=15),
            "Fit Window to Thumbnails",
        )
        act_fit_width = menu.addAction(
            svg_icon(SVG_EXPAND, color=icon_color, size=15),
            "Fit Thumbnails to Window Width",
        )
        menu.addSeparator()

        act_embed = menu.addAction(
            svg_icon(SVG_TH_LARGE, color=icon_color, size=15),
            "Embed All Page Thumbnails",
        )
        act_rem_embed = menu.addAction(
            svg_icon(SVG_TH, color=icon_color, size=15),
            "Remove Embedded Page Thumbnails",
        )
        menu.addSeparator()

        insert_menu = menu.addMenu(
            svg_icon(SVG_FILE_PLUS, color=icon_color, size=15), "Insert Pages..."
        )
        _, _, ins_blank_res = _add_counted_action(
            menu, insert_menu, dark, SVG_FILE, icon_color,
            "Blank Page(s)...", tooltip="Number of blank pages to insert (1–999)",
        )
        _, _, ins_file_res = _add_counted_action(
            menu, insert_menu, dark, SVG_FOLDER, icon_color,
            "From File...", tooltip="Number of times to insert the selected PDF (1–999)",
        )

        act_delete = menu.addAction(
            svg_icon(SVG_TRASH, color="#EF4444", size=15),
            f"Delete Pages{suffix}...\tDel",
        )
        act_extract = menu.addAction(
            svg_icon(SVG_FILE_EXPORT, color=icon_color, size=15),
            f"Extract Pages{suffix}...",
        )
        act_reverse = menu.addAction(
            svg_icon(SVG_SORT, color=icon_color, size=15),
            f"Reverse Pages{suffix}...",
        )
        act_replace = menu.addAction(
            svg_icon(SVG_EXCHANGE, color=icon_color, size=15), "Replace Pages..."
        )
        act_swap = menu.addAction(
            svg_icon(SVG_SWAP, color=icon_color, size=15), "Swap Pages..."
        )
        _, _, dup_res = _add_counted_action(
            menu, menu, dark, SVG_CLONE, icon_color,
            f"Duplicate Pages{suffix}", shortcut_text="Ctrl+D",
            tooltip="Number of duplicate copies to insert (1–999)",
        )
        act_move = menu.addAction(
            svg_icon(SVG_MOVE, color=icon_color, size=15),
            f"Move Pages{suffix}...",
        )
        act_split = menu.addAction(
            svg_icon(SVG_CUT, color=icon_color, size=15), "Split Document..."
        )
        menu.addSeparator()

        act_crop = menu.addAction(
            svg_icon(SVG_CROP, color=icon_color, size=15), f"Crop Pages{suffix}..."
        )
        act_resize = menu.addAction(
            svg_icon(SVG_EXPAND, color=icon_color, size=15),
            f"Resize pages{suffix}...",
        )

        rotate_menu = menu.addMenu(
            svg_icon(SVG_SYNC, color=icon_color, size=15), f"Rotate Pages{suffix}..."
        )
        act_rot_right = rotate_menu.addAction(
            svg_icon(SVG_ROTATE_RIGHT, color=icon_color, size=15),
            "Rotate Right (90° Clockwise)\tCtrl+Right",
        )
        act_rot_left = rotate_menu.addAction(
            svg_icon(SVG_ROTATE_LEFT, color=icon_color, size=15),
            "Rotate Left (90° Counter-Clockwise)\tCtrl+Left",
        )
        act_rot_180 = rotate_menu.addAction(
            svg_icon(SVG_SYNC, color=icon_color, size=15), "Rotate 180°"
        )
        menu.addSeparator()

        act_transitions = menu.addAction(
            svg_icon(SVG_TV, color=icon_color, size=15), "Page Transitions..."
        )
        act_page_nums = menu.addAction(
            svg_icon(SVG_LIST_OL, color=icon_color, size=15),
            f"Format Page Numbers{suffix}...",
        )
        menu.addSeparator()

        act_print = menu.addAction(
            svg_icon(SVG_PRINT, color=icon_color, size=15), f"Print Pages{suffix}..."
        )
        act_props = menu.addAction(
            svg_icon(SVG_INFO, color=icon_color, size=15), "Properties..."
        )

        selected_action = menu.exec(global_pos)

        if dup_res["triggered"]:
            self.action_requested.emit(
                "duplicate", {"pages": selected_pages, "count": dup_res["count"]}
            )
            return
        if ins_blank_res["triggered"]:
            self.action_requested.emit(
                "insert_blank", {"pages": selected_pages, "count": ins_blank_res["count"]}
            )
            return
        if ins_file_res["triggered"]:
            self.action_requested.emit(
                "insert_file", {"pages": selected_pages, "count": ins_file_res["count"]}
            )
            return

        if not selected_action:
            return

        if selected_action == act_enlarge:
            self._enlarge_thumbnails()
            return
        if selected_action == act_reduce:
            self._reduce_thumbnails()
            return
        if selected_action == act_fit_window:
            self.fit_window_to_thumbnails()
            return
        if selected_action == act_fit_width:
            self.fit_thumbnails_to_window()
            return

        action_map = {
            act_cut: "cut",
            act_copy_pages: "copy_pages",
            act_paste_pages: "paste_pages",
            act_copy: "copy",
            act_paste: "paste",
            act_embed: "embed_thumbnails",
            act_rem_embed: "remove_thumbnails",
            act_delete: "delete",
            act_extract: "extract",
            act_reverse: "reverse",
            act_replace: "replace",
            act_swap: "swap",
            act_move: "move",
            act_split: "split",
            act_crop: "crop",
            act_resize: "resize",
            act_rot_right: "rotate_right",
            act_rot_left: "rotate_left",
            act_rot_180: "rotate_180",
            act_transitions: "transitions",
            act_page_nums: "page_numbers",
            act_print: "print",
            act_props: "properties",
        }
        action_name = action_map.get(selected_action)
        if action_name:
            self.action_requested.emit(action_name, selected_pages)