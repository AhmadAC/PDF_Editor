

# app/editor/canvas_events_menu.py
"""Right-click context menu handler for PdfEditCanvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QColorDialog, QMenu

from app.constants import ACCENT
from app.editor.canvas_events_helpers import (
    _OpacityAdjustDialog,
    _find_tab_editar,
)
from app.editor.canvas_overlay1 import CanvasOverlayManager
from app.i18n import t
from app.utils import (
    SVG_ARROW_DOWN,
    SVG_ARROW_UP,
    SVG_CHECK,
    SVG_CLONE,
    SVG_CLOSE,
    SVG_COPY,
    SVG_EDIT,
    SVG_ERASER,
    SVG_EYE,
    SVG_FONT,
    SVG_HIGHLIGHTER,
    SVG_MOVE,
    SVG_PEN,
    SVG_ROTATE_LEFT,
    SVG_ROTATE_RIGHT,
    SVG_SIGNATURE,
    SVG_SWAP,
    SVG_SYNC,
    SVG_TRASH,
    SVG_VECTOR_SQUARE,
    SVG_ZOOM_IN,
    SVG_ZOOM_OUT,
    svg_icon,
)
from app.viewer.canvas_action_menus import (
    PRESET_COLORS,
    _ScrollableMenuStyle,
    make_circular_color_icon,
    make_rainbow_wheel_icon,
)


class CanvasContextMenuMixin:
    """Mixin building and executing the right-click context menu for overlays and tools."""

    if TYPE_CHECKING:
        canvas: Any
        _menu_style: _ScrollableMenuStyle | None

        def _begin_inline_edit_for_overlay(self, ov: dict) -> None: ...
        def _is_pos_inside_active_sel(self, pos: QPoint) -> bool: ...

    def _open_opacity_dialog(self, idx: int) -> None:
        c = self.canvas
        if not (0 <= idx < len(c._overlays)):
            return
        cur_op = float(c._overlays[idx].get("opacity", 1.0))
        dlg = _OpacityAdjustDialog(c, idx, int(round(cur_op * 100)), parent=c)
        dlg.exec()

    def handle_context_menu(self, e) -> None:
        c = self.canvas
        pos = e.pos()
        tab = _find_tab_editar(c)

        if c._placing_signature:
            c.cancel_signature_placement()
            return
        if getattr(c, "_placing_stamp", False):
            c.cancel_stamp_placement()
            return

        if not (0 <= c._selected_overlay_idx < len(c._overlays) and c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos)):
            hit_idx = -1
            for idx in reversed(range(len(c._overlays))):
                if c._overlay_mgr.is_pos_inside_overlay(idx, pos):
                    hit_idx = idx
                    break
            if hit_idx >= 0:
                c._selected_overlay_idx = hit_idx
                c.update()
            else:
                c._overlay_mgr.discard_untouched_existing()
                page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
                pdf_pt = c._to_pdf(page_idx, lx, ly)
                el = (
                    c._overlay_mgr.detect_existing_media_at(page_idx, pdf_pt)
                    or c._overlay_mgr.detect_existing_drawing_at(page_idx, pdf_pt)
                    or c._overlay_mgr.detect_existing_text_at(page_idx, pdf_pt)
                )
                if el and el in c._overlays:
                    c._selected_overlay_idx = c._overlays.index(el)
                    c.update()

        menu = QMenu(c)
        self._menu_style = _ScrollableMenuStyle(menu.style())
        menu.setStyle(self._menu_style)
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")

        if 0 <= c._selected_overlay_idx < len(c._overlays) and c._overlay_mgr.is_pos_inside_overlay(c._selected_overlay_idx, pos):
            ov_idx = c._selected_overlay_idx
            ov = c._overlays[ov_idx]
            if ov.get("type") in ("text", "text_edit"):
                act_edit_ov = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
                act_edit_ov.triggered.connect(lambda _=False, o=ov: self._begin_inline_edit_for_overlay(o))

            if ov.get("type") != "note":
                arrange_menu = menu.addMenu(svg_icon(SVG_MOVE, color=ACCENT, size=15), "Arrange (Order)")
                act_front = arrange_menu.addAction(svg_icon(SVG_ARROW_UP, color=ACCENT, size=15), "Bring to Front")
                act_front.triggered.connect(lambda _=False, i=ov_idx: c.arrange_selected_overlay("front", i))
                act_fwd = arrange_menu.addAction(svg_icon(SVG_ARROW_UP, color="#9CA3AF", size=15), "Bring Forward")
                act_fwd.triggered.connect(lambda _=False, i=ov_idx: c.arrange_selected_overlay("forward", i))
                act_bwd = arrange_menu.addAction(svg_icon(SVG_ARROW_DOWN, color="#9CA3AF", size=15), "Send Backward")
                act_bwd.triggered.connect(lambda _=False, i=ov_idx: c.arrange_selected_overlay("backward", i))
                act_back = arrange_menu.addAction(svg_icon(SVG_ARROW_DOWN, color=ACCENT, size=15), "Send to Back")
                act_back.triggered.connect(lambda _=False, i=ov_idx: c.arrange_selected_overlay("back", i))

                rot_menu = menu.addMenu(svg_icon(SVG_SYNC, color=ACCENT, size=15), "Rotate & Flip")
                act_r90 = rot_menu.addAction(svg_icon(SVG_ROTATE_RIGHT, color=ACCENT, size=15), "Rotate Right 90°")
                act_r90.triggered.connect(lambda _=False, i=ov_idx: c.rotate_selected_overlay(90, i))
                act_l90 = rot_menu.addAction(svg_icon(SVG_ROTATE_LEFT, color=ACCENT, size=15), "Rotate Left 90°")
                act_l90.triggered.connect(lambda _=False, i=ov_idx: c.rotate_selected_overlay(270, i))
                act_r180 = rot_menu.addAction(svg_icon(SVG_SYNC, color=ACCENT, size=15), "Rotate 180°")
                act_r180.triggered.connect(lambda _=False, i=ov_idx: c.rotate_selected_overlay(180, i))
                rot_menu.addSeparator()
                act_fh = rot_menu.addAction(svg_icon(SVG_SWAP, color=ACCENT, size=15), "Flip Horizontal")
                act_fh.triggered.connect(lambda _=False, i=ov_idx: c.flip_selected_overlay(True, i))
                act_fv = rot_menu.addAction(svg_icon(SVG_SWAP, color=ACCENT, size=15), "Flip Vertical")
                act_fv.triggered.connect(lambda _=False, i=ov_idx: c.flip_selected_overlay(False, i))

                cur_op_pct = int(round(float(ov.get("opacity", 1.0)) * 100))
                op_menu = menu.addMenu(svg_icon(SVG_EYE, color=ACCENT, size=15), f"Opacity ({cur_op_pct}%)")
                for pct in (100, 80, 60, 40, 20):
                    label = f"{pct}%  (Current)" if abs(cur_op_pct - pct) <= 2 else f"{pct}%"
                    act_op = op_menu.addAction(svg_icon(SVG_CHECK if abs(cur_op_pct - pct) <= 2 else SVG_EYE, color=ACCENT, size=14), label)
                    act_op.triggered.connect(lambda _=False, i=ov_idx, val=pct / 100.0: c.set_selected_overlay_opacity(val, i))
                op_menu.addSeparator()
                act_op_custom = op_menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Custom Opacity...")
                act_op_custom.triggered.connect(lambda _=False, i=ov_idx: self._open_opacity_dialog(i))

            if ov.get("type") in CanvasOverlayManager._RESIZABLE_TYPES:
                menu.addSeparator()
                act_inc = menu.addAction(svg_icon(SVG_ZOOM_IN, color=ACCENT, size=15), "Increase Size (Ctrl+)")
                act_inc.triggered.connect(lambda _=False: c.scale_selected_overlay(1.10))
                act_dec = menu.addAction(svg_icon(SVG_ZOOM_OUT, color=ACCENT, size=15), "Decrease Size (Ctrl-)")
                act_dec.triggered.connect(lambda _=False: c.scale_selected_overlay(0.90))

            menu.addSeparator()
            act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Delete (Del)")
            act_del.triggered.connect(lambda _=False: c.delete_selected_overlay())
            if ov.get("type") != "note":
                act_dup = menu.addAction(svg_icon(SVG_CLONE, color=ACCENT, size=15), "Duplicate")
                act_dup.triggered.connect(lambda _=False: c.duplicate_selected_overlay())
            menu.exec(e.globalPos())
            return

        if getattr(c, "_select_mode", False) and (getattr(c, "_active_sel_rect", None) or self._is_pos_inside_active_sel(pos)):
            act_edit = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
            act_edit.triggered.connect(lambda _=False: c.edit_selection_requested.emit())
            act_del = menu.addAction(svg_icon(SVG_TRASH, color="#EF4444", size=15), "Delete (Del)")
            act_del.triggered.connect(lambda _=False: c.delete_active_selection())
            menu.addSeparator()

            act_copy = menu.addAction(svg_icon(SVG_COPY, color=ACCENT, size=15), t("btn.copy"))
            act_copy.triggered.connect(lambda _=False: QApplication.clipboard().setText(c._sel_text if hasattr(c, "_sel_text") else ""))

            act_hi = menu.addAction(svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15), "Highlight Selection")
            act_hi.triggered.connect(lambda _=False: c.highlight_selection_requested.emit())

            act_redact = menu.addAction(svg_icon(SVG_ERASER, color="#EF4444", size=15), "Redact Selection")
            act_redact.triggered.connect(lambda _=False: c.redact_selection_requested.emit())
            menu.addSeparator()

        page_idx, lx, ly = c._page_and_local(pos.x(), pos.y())
        pdf_pt = c._to_pdf(page_idx, lx, ly)
        span = c.get_span_at(page_idx, pdf_pt, max_dist=8.0)

        def _activate_mode(m_idx: int, submode: str | None = None):
            if tab is not None:
                tab._set_mode_by_idx(m_idx)
                if submode and hasattr(tab, "_set_text_submode"):
                    tab._set_text_submode(submode)
            else:
                c.mode_change_requested.emit(m_idx)

        act_add_text = menu.addAction(svg_icon(SVG_FONT, color=ACCENT, size=15), "Add Text")
        act_add_text.triggered.connect(
            lambda _=False, p=page_idx, pt=pdf_pt: (
                _activate_mode(1, "add"),
                c.set_text_submode("add") if hasattr(c, "set_text_submode") else None,
                c.begin_inline_text_insert(p, pt, 12.0, (0.0, 0.0, 0.0)),
            )
        )

        act_edit_text = menu.addAction(svg_icon(SVG_EDIT, color=ACCENT, size=15), "Edit Text")
        if span:
            act_edit_text.triggered.connect(
                lambda _=False, s=span, p=page_idx: (
                    _activate_mode(1, "edit"),
                    c.set_text_submode("edit") if hasattr(c, "set_text_submode") else None,
                    c.begin_inline_text_edit(s, p),
                )
            )
        else:
            act_edit_text.triggered.connect(
                lambda _=False: (
                    _activate_mode(1, "edit"),
                    c.set_text_submode("edit") if hasattr(c, "set_text_submode") else None,
                )
            )

        menu.addSeparator()

        act_draw_pen = menu.addAction(svg_icon(SVG_PEN, color=ACCENT, size=15), "Draw (Freehand Pen)")
        act_draw_pen.triggered.connect(
            lambda _=False: (
                _activate_mode(8),
                c.set_draw_mode(True, color=c._draw_color),
            )
        )

        act_draw_hi_rect = menu.addAction(svg_icon(SVG_VECTOR_SQUARE, color=ACCENT, size=15), "Draw Highlight (Rectangle)")
        act_draw_hi_rect.triggered.connect(
            lambda _=False: (
                _activate_mode(4),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.CrossCursor),
            )
        )

        act_hi_text = menu.addAction(svg_icon(SVG_HIGHLIGHTER, color=ACCENT, size=15), "Highlight Text")
        act_hi_text.triggered.connect(
            lambda _=False: (
                _activate_mode(3),
                c.set_highlight_mode(True, c._highlight_color, Qt.CursorShape.IBeamCursor),
            )
        )
        menu.addSeparator()

        color_menu = menu.addMenu(make_rainbow_wheel_icon(18), "Highlight & Pen Color")
        act_custom = color_menu.addAction(make_rainbow_wheel_icon(16), "Custom Color...")

        def _apply_chosen_color(c_tup: tuple[float, float, float]):
            c._highlight_color = c_tup
            c._draw_color = c_tup
            if tab is not None and hasattr(tab, "_sync_canvas_color"):
                tab._sync_canvas_color(c_tup)
            if c._highlight_mode:
                c.set_highlight_mode(True, c_tup, c.cursor().shape())
            elif c._draw_mode:
                c.set_draw_mode(True, c_tup)

        def _on_custom_col():
            curr = QColor(
                int(c._draw_color[0] * 255),
                int(c._draw_color[1] * 255),
                int(c._draw_color[2] * 255),
            )
            col = QColorDialog.getColor(curr, c, "Select Color")
            if col.isValid():
                _apply_chosen_color((col.redF(), col.greenF(), col.blueF()))

        act_custom.triggered.connect(lambda _=False: _on_custom_col())
        color_menu.addSeparator()

        for hex_code, label, rgb_tuple in PRESET_COLORS:
            act_c = color_menu.addAction(make_circular_color_icon(hex_code, 16), label)
            act_c.triggered.connect(lambda _=False, rgb=rgb_tuple: _apply_chosen_color(rgb))

        menu.addSeparator()
        act_add_check = menu.addAction(svg_icon(SVG_CHECK, color=ACCENT, size=15), "Add Checkmark")
        act_add_check.triggered.connect(lambda _=False: c.begin_stamp_placement("check"))
        act_add_cross = menu.addAction(svg_icon(SVG_CLOSE, color="#EF4444", size=15), "Add Crossmark")
        act_add_cross.triggered.connect(lambda _=False: c.begin_stamp_placement("cross"))
        menu.addSeparator()
        act_add_sig = menu.addAction(svg_icon(SVG_SIGNATURE, color=ACCENT, size=15), t("viewer.add_signature"))
        act_add_sig.triggered.connect(lambda _=False: c.start_add_signature_flow(pos))

        menu.exec(e.globalPos())

