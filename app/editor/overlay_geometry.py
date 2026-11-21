# app/editor/overlay_geometry.py
"""Pure geometry, snapshot, preview-ops, background sampling, and vector-item transform helpers for editor overlays."""

from __future__ import annotations

import fitz

from app.editor.canvas_render1 import _NOTE_ICON_SIZE


def rects_close(r1, r2, tol: float = 0.75) -> bool:
    """Return True if two rects match within tol PDF points."""
    if r1 is None or r2 is None:
        return False
    a = fitz.Rect(r1)
    b = fitz.Rect(r2)
    return (
        abs(a.x0 - b.x0) <= tol
        and abs(a.y0 - b.y0) <= tol
        and abs(a.x1 - b.x1) <= tol
        and abs(a.y1 - b.y1) <= tol
    )


def overlay_pdf_rect(ov: dict, zoom: float = 1.0) -> fitz.Rect | None:
    """Return the PDF-space bounding rectangle of any overlay dict."""
    etype = ov.get("type")
    if etype == "group":
        if "rect" in ov and ov["rect"] is not None:
            return fitz.Rect(ov["rect"])
        children = [ch for ch in ov.get("children", []) if not ch.get("_deleted")]
        if children:
            u = None
            for ch in children:
                cr = overlay_pdf_rect(ch, zoom=zoom)
                if cr is not None:
                    u = fitz.Rect(cr) if u is None else (u | cr)
            if u is not None:
                ov["rect"] = u
                return u
        return None
    if "rect" in ov and ov["rect"] is not None:
        return fitz.Rect(ov["rect"])
    if "bbox" in ov and ov["bbox"] is not None:
        return fitz.Rect(ov["bbox"])
    if etype == "highlight" and ov.get("rects"):
        rs = [fitz.Rect(r) for r in ov["rects"]]
        if rs:
            u = fitz.Rect(rs[0])
            for r in rs[1:]:
                u |= r
            return u
    if etype == "draw" and ov.get("points"):
        pts = ov["points"]
        if pts:
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            pad = max(2.0, float(ov.get("width", 2)) * 0.5 + 1.0)
            return fitz.Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    if etype == "note" and "point" in ov:
        pt = ov["point"]
        sz = float(_NOTE_ICON_SIZE) / max(0.1, zoom)
        return fitz.Rect(pt.x, pt.y - sz, pt.x + sz, pt.y)
    if etype == "text" and "point" in ov:
        pt = ov["point"]
        sz = float(ov.get("size", 12))
        txt = ov.get("text", "")
        lines = txt.split("\n") or [""]
        max_len = max((len(ln) for ln in lines), default=1)
        tw = max(16.0, max_len * sz * 0.65)
        raw_ls = ov.get("line_step")
        ls = float(raw_ls) if (raw_ls and float(raw_ls) > 0) else (sz * 1.2)
        th = max(sz * 1.1, len(lines) * ls)
        r = fitz.Rect(pt.x, pt.y - sz * 0.85, pt.x + tw, pt.y - sz * 0.85 + th)
        ov["rect"] = r
        return r
    return None


def recompute_group_rect(ov: dict, zoom: float = 1.0) -> fitz.Rect | None:
    """Recompute and store a group's bounding rectangle from its non-deleted children."""
    if ov.get("type") != "group":
        return overlay_pdf_rect(ov, zoom=zoom)
    u = None
    for ch in ov.get("children", []):
        if ch.get("_deleted"):
            continue
        cr = overlay_pdf_rect(ch, zoom=zoom)
        if cr is not None:
            u = fitz.Rect(cr) if u is None else (u | cr)
    if u is not None:
        ov["rect"] = u
    return ov.get("rect")


def snapshot_overlay(ov: dict) -> dict:
    """Capture mutable geometry/style state of an overlay for undo/redo."""
    snap: dict = {}
    if "rect" in ov and ov["rect"] is not None:
        snap["rect"] = fitz.Rect(ov["rect"])
    if "bbox" in ov and ov["bbox"] is not None:
        snap["bbox"] = list(ov["bbox"])
    if "rects" in ov and ov["rects"] is not None:
        snap["rects"] = [fitz.Rect(r) for r in ov["rects"]]
    if "points" in ov and ov["points"] is not None:
        snap["points"] = [(float(p[0]), float(p[1])) for p in ov["points"]]
    if "point" in ov and ov["point"] is not None:
        snap["point"] = fitz.Point(ov["point"])
    if "origin" in ov and ov["origin"] is not None:
        snap["origin"] = list(ov["origin"])
    if "items" in ov and ov["items"] is not None:
        snap["items"] = list(ov["items"])
    if "_items_rect" in ov and ov["_items_rect"] is not None:
        snap["_items_rect"] = list(ov["_items_rect"])
    if "runs" in ov and ov["runs"] is not None:
        snap["runs"] = [dict(r) for r in ov["runs"]]
    if "children" in ov and ov["children"] is not None:
        snap["children"] = [
            {"ref": ch, "snap": snapshot_overlay(ch)}
            for ch in ov["children"]
        ]
    for k in (
        "size", "font_size", "width", "new_text", "text", "font",
        "color", "flags", "_font_changed", "_deleted", "_dirty",
        "opacity", "rotation", "flip_h", "flip_v", "_z_back",
        "_visible", "_locked", "_ungrouped", "_name", "line_step",
        "shadow_enabled", "shadow_angle", "shadow_opacity",
        "shadow_distance", "shadow_thickness", "shadow_color",
        "outline_enabled", "outline_thickness", "outline_color",
    ):
        if k in ov:
            snap[k] = ov[k]
    return snap


def restore_overlay_snapshot(ov: dict, snap: dict) -> None:
    """Restore a previously captured snapshot onto an overlay dict."""
    mutable_keys = (
        "rect", "bbox", "rects", "points", "point", "origin", "items", "_items_rect",
        "runs", "size", "font_size", "width", "new_text", "text", "font",
        "color", "flags", "_font_changed", "_deleted", "_dirty",
        "opacity", "rotation", "flip_h", "flip_v", "_z_back",
        "_visible", "_locked", "_ungrouped", "_name", "line_step",
        "shadow_enabled", "shadow_angle", "shadow_opacity",
        "shadow_distance", "shadow_thickness", "shadow_color",
        "outline_enabled", "outline_thickness", "outline_color",
    )
    for k in mutable_keys:
        if k in snap:
            val = snap[k]
            if k == "rect":
                ov[k] = fitz.Rect(val)
            elif k in ("bbox", "origin", "_items_rect"):
                ov[k] = list(val)
            elif k == "rects":
                ov[k] = [fitz.Rect(r) for r in val]
            elif k == "points":
                ov[k] = [(float(p[0]), float(p[1])) for p in val]
            elif k == "items":
                ov[k] = list(val)
            elif k == "runs":
                ov[k] = [dict(r) for r in val]
            elif k == "point":
                ov[k] = fitz.Point(val)
            else:
                ov[k] = val
        elif k in ov:
            del ov[k]
    if "children" in snap:
        restored_children: list[dict] = []
        for entry in snap["children"]:
            ch = entry["ref"]
            restore_overlay_snapshot(ch, entry["snap"])
            restored_children.append(ch)
        ov["children"] = restored_children


def get_page_preview_ops(overlays: list[dict], page_idx: int) -> list[dict]:
    """Build single-page (page=0) edit ops for rendering clean page background and _z_back layers."""
    ops: list[dict] = []
    for e in overlays:
        if e.get("page") != page_idx:
            continue

        if e.get("type") == "group":
            group_op = max(0.0, min(1.0, float(e.get("opacity", 1.0))))
            is_group_removed = bool(e.get("_existing") and (e.get("_deleted") or e.get("_dirty")))
            is_group_z_back = bool(not e.get("_deleted") and e.get("_z_back") and e.get("_visible", True))

            for ch in e.get("children", []):
                if is_group_removed and ch.get("_existing") and ch.get("type") != "note":
                    rem = dict(ch)
                    rem["page"] = 0
                    rem["_deleted"] = True
                    rem["_dirty"] = True
                    ops.append(rem)
                if is_group_z_back and not ch.get("_deleted") and ch.get("_visible", True):
                    back_copy = dict(ch)
                    back_copy["page"] = 0
                    back_copy["_existing"] = False
                    back_copy["_deleted"] = False
                    back_copy["_dirty"] = True
                    back_copy["_z_back"] = True
                    back_copy["opacity"] = max(0.0, min(1.0, float(ch.get("opacity", 1.0)) * group_op))
                    if back_copy.get("type") == "drawing" and "_items_rect" not in back_copy and "_orig_rect" in back_copy:
                        back_copy["_items_rect"] = list(back_copy["_orig_rect"])
                    ops.append(back_copy)
            continue

        is_existing_removed = bool(
            e.get("_existing")
            and (e.get("_deleted") or e.get("_dirty"))
            and e.get("type") not in ("note",)
        )
        is_existing_note_deleted = bool(
            e.get("_existing") and e.get("type") == "note" and e.get("_deleted")
        )
        is_non_existing_text_edit = bool(e.get("type") == "text_edit" and not e.get("_existing"))
        is_delete_annot = bool(e.get("type") == "delete_annot")

        if is_existing_removed or is_existing_note_deleted or is_non_existing_text_edit or is_delete_annot:
            rem = dict(e)
            rem["page"] = 0
            if rem.get("type") != "delete_annot":
                rem["_deleted"] = True
                rem["_dirty"] = True
            ops.append(rem)

        if not e.get("_deleted") and e.get("_z_back") and e.get("_visible", True):
            back_copy = dict(e)
            back_copy["page"] = 0
            back_copy["_existing"] = False
            back_copy["_deleted"] = False
            back_copy["_dirty"] = True
            if back_copy.get("type") == "drawing" and "_items_rect" not in back_copy and "_orig_rect" in back_copy:
                back_copy["_items_rect"] = list(back_copy["_orig_rect"])
            ops.append(back_copy)
    return ops


def _sample_bg_color(page, target_rect: fitz.Rect) -> tuple[int, int, int]:
    """Find the filled vector background color behind target_rect, defaulting to white."""
    try:
        bg = (255, 255, 255)
        tr = fitz.Rect(target_rect)
        for d in page.get_drawings() or []:
            fill = d.get("fill")
            if not fill or len(fill) < 3:
                continue
            dr = fitz.Rect(d["rect"]).normalize()
            if rects_close(dr, tr, tol=1.0):
                continue
            if dr.contains(tr) or (dr + fitz.Rect(-1, -1, 1, 1)).contains(tr):
                bg = (
                    max(0, min(255, int(round(fill[0] * 255)))),
                    max(0, min(255, int(round(fill[1] * 255)))),
                    max(0, min(255, int(round(fill[2] * 255)))),
                )
        return bg
    except Exception:
        return (255, 255, 255)


def _serialize_drawing_items(raw_items: list) -> list:
    """Convert PyMuPDF drawing path items into pure Python tuples."""
    out = []
    for item in raw_items or []:
        op = item[0]
        if op == "l":
            p1, p2 = item[1], item[2]
            out.append(("l", (float(p1.x), float(p1.y)), (float(p2.x), float(p2.y))))
        elif op == "re":
            r = fitz.Rect(item[1]).normalize()
            out.append(("re", (float(r.x0), float(r.y0), float(r.x1), float(r.y1))))
        elif op == "qu":
            q = item[1]
            out.append((
                "qu",
                (
                    (float(q.ul.x), float(q.ul.y)),
                    (float(q.ur.x), float(q.ur.y)),
                    (float(q.lr.x), float(q.lr.y)),
                    (float(q.ll.x), float(q.ll.y)),
                ),
            ))
        elif op == "c":
            out.append((
                "c",
                (float(item[1].x), float(item[1].y)),
                (float(item[2].x), float(item[2].y)),
                (float(item[3].x), float(item[3].y)),
                (float(item[4].x), float(item[4].y)),
            ))
    return out


def _transform_pt(x: float, y: float, cx: float, cy: float, mode: str) -> tuple[float, float]:
    dx, dy = x - cx, y - cy
    if mode == "rot90":
        return (cx - dy, cy + dx)
    if mode == "rot270":
        return (cx + dy, cy - dx)
    if mode == "rot180":
        return (cx - dx, cy - dy)
    if mode == "flip_h":
        return (cx - dx, y)
    if mode == "flip_v":
        return (x, cy - dy)
    return (x, y)


def _transform_drawing_items(items: list, cx: float, cy: float, mode: str) -> list:
    out = []
    for item in items:
        op = item[0]
        if op == "l":
            out.append((
                "l",
                _transform_pt(item[1][0], item[1][1], cx, cy, mode),
                _transform_pt(item[2][0], item[2][1], cx, cy, mode),
            ))
        elif op == "re":
            rx0, ry0, rx1, ry1 = item[1]
            p1 = _transform_pt(rx0, ry0, cx, cy, mode)
            p2 = _transform_pt(rx1, ry1, cx, cy, mode)
            out.append((
                "re",
                (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])),
            ))
        elif op == "qu":
            out.append((
                "qu",
                tuple(_transform_pt(pt[0], pt[1], cx, cy, mode) for pt in item[1]),
            ))
        elif op == "c":
            out.append((
                "c",
                _transform_pt(item[1][0], item[1][1], cx, cy, mode),
                _transform_pt(item[2][0], item[2][1], cx, cy, mode),
                _transform_pt(item[3][0], item[3][1], cx, cy, mode),
                _transform_pt(item[4][0], item[4][1], cx, cy, mode),
            ))
    return out