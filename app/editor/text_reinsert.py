# app/editor/text_reinsert.py

"""PDF_Editor – pure typographic-fidelity helpers for text-edit reinsertion with multi-color runs and custom system font embedding."""

import html
import logging
import os

from app.utils import find_system_font_file

_log = logging.getLogger(__name__)

_EMBED_FAMILY = "PDF_EditorEmbeddedFont"
_MIN_LEGIBLE_SCALE = 0.6
_FONT_STYLE_SUFFIXES = (
    "regular", "book", "roman", "medium", "semibold", "demibold",
    "bold", "italic", "oblique", "light", "black", "heavy", "condensed",
    "mt", "ps",
)


def _font_core(name: str) -> str:
    """Reduce a font name to a comparable lowercase alphanumeric core."""
    s = "".join(c for c in (name or "").split("+")[-1].lower() if c.isalnum())
    changed = True
    while changed:
        changed = False
        for suf in _FONT_STYLE_SUFFIXES:
            if s.endswith(suf) and len(s) - len(suf) >= 3:
                s = s[:-len(suf)]
                changed = True
    return s


def _font_matches(core: str, basefont: str, name: str) -> bool:
    if not core:
        return False
    for cand in (_font_core(basefont), _font_core(name)):
        if cand and (core == cand or core in cand or cand in core):
            return True
    return False


def _text_edit_size(edit: dict) -> float:
    """Original or user-modified span font size (points)."""
    size = float(edit.get("font_size") or 0) or float(edit.get("size") or 0)
    return size if size >= 1.0 else 4.0


def _color_value_to_hex(c) -> str:
    if isinstance(c, bool):
        c = 0
    if isinstance(c, int):
        return "#%06x" % (c & 0xFFFFFF)
    if isinstance(c, float):
        return "#%06x" % (int(c) & 0xFFFFFF)
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            r, g, b = c[0], c[1], c[2]
            return "#%02x%02x%02x" % (
                max(0, min(255, int(round(float(r) * 255)))),
                max(0, min(255, int(round(float(g) * 255)))),
                max(0, min(255, int(round(float(b) * 255)))),
            )
        except Exception:
            pass
    return "#000000"


def _color_value_to_rgb(c) -> tuple[float, float, float]:
    if isinstance(c, bool):
        c = 0
    if isinstance(c, int):
        return (((c >> 16) & 0xFF) / 255.0, ((c >> 8) & 0xFF) / 255.0, (c & 0xFF) / 255.0)
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            return (float(c[0]), float(c[1]), float(c[2]))
        except Exception:
            pass
    return (0.0, 0.0, 0.0)


def _text_edit_color_hex(edit: dict) -> str:
    return _color_value_to_hex(edit.get("color", 0))


def _text_edit_color_rgb(edit: dict) -> tuple[float, float, float]:
    return _color_value_to_rgb(edit.get("color", 0))


def _text_edit_style(edit: dict) -> tuple[bool, bool, bool, bool]:
    """Return (serif, mono, bold, italic) from flags and font name."""
    flags = int(edit.get("flags", 0) or 0)
    fname = (edit.get("font", "") or "").lower()
    serif = bool(flags & 4) or any(
        k in fname for k in ("times", "serif", "roman", "georgia", "garamond", "minion"))
    mono = bool(flags & 8) or any(
        k in fname for k in ("mono", "courier", "consol"))
    bold = bool(flags & 16) or any(
        k in fname for k in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or any(
        k in fname for k in ("italic", "oblique"))
    return serif, mono, bold, italic


def _base14_fontname(edit: dict) -> str:
    """Best-matching base-14 font code for defensive insert_text fallback."""
    serif, mono, bold, italic = _text_edit_style(edit)
    if mono:
        tbl = {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"}
    elif serif:
        tbl = {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"}
    else:
        tbl = {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"}
    return tbl[(int(bold), int(italic))]


def _generic_font_style(edit: dict) -> tuple[str, str, str]:
    """CSS (family, weight, style) for the generic-family htmlbox fallback."""
    serif, mono, bold, italic = _text_edit_style(edit)
    font_spec = edit.get("font", "").strip()
    if font_spec:
        clean_spec = font_spec.split("+")[-1].strip()
        family = f"'{clean_spec}', '{font_spec}', sans-serif"
    else:
        family = "monospace" if mono else ("serif" if serif else "sans-serif")
    return family, ("bold" if bold else "normal"), ("italic" if italic else "normal")


def _build_embed_archive(fitz, doc, page, edit: dict, new_txt: str):
    """Return (archive, ref) embedding the span's font (either from system or PDF), or (None, None)."""
    font_spec = edit.get("font", "").strip()
    serif, mono, bold, italic = _text_edit_style(edit)

    # 1. Prefer matching system installed font
    if font_spec:
        clean_name = font_spec.split("+")[-1].strip()
        sys_ttf = find_system_font_file(clean_name, bold=bold, italic=italic)
        if not sys_ttf and clean_name != font_spec:
            sys_ttf = find_system_font_file(font_spec, bold=bold, italic=italic)

        if sys_ttf and os.path.isfile(sys_ttf):
            try:
                with open(sys_ttf, "rb") as f_in:
                    content = f_in.read()
                if content and len(content) >= 256:
                    ext_str = os.path.splitext(sys_ttf)[1].lstrip(".") or "ttf"
                    ref = f"PDF_Editor_custom.{ext_str}"
                    arch = fitz.Archive()
                    arch.add(content, ref)
                    return arch, ref
            except Exception:
                pass

    if edit.get("_font_changed"):
        return None, None

    # 2. Extract embedded font from original PDF
    core = _font_core(font_spec)
    if not core:
        return None, None
    try:
        fonts = page.get_fonts(full=False)
    except Exception:
        return None, None

    xref = None
    for f in fonts:
        fxref, basefont, name = f[0], f[3], f[4]
        if "+" in (basefont or ""):
            continue
        if fxref and int(fxref) > 0 and _font_matches(core, basefont, name):
            xref = int(fxref)
            break

    if not xref:
        return None, None

    try:
        _n, ext_, _t, content = doc.extract_font(xref)
    except Exception:
        return None, None

    if not content or len(content) < 256:
        return None, None

    try:
        probe = fitz.Font(fontbuffer=content)
        for ch in new_txt:
            if ch.isspace():
                continue
            if not probe.has_glyph(ord(ch)):
                return None, None
    except Exception:
        return None, None

    ref = "PDF_Editor_embed." + ((ext_ or "ttf").lstrip(".") or "ttf")
    try:
        arch = fitz.Archive()
        arch.add(content, ref)
    except Exception:
        return None, None

    return arch, ref


def _text_edit_redaction_rect(fitz, edit: dict, size: float):
    """Vertically tight redaction rectangle for removing original span at its original location."""
    bbox = fitz.Rect(edit.get("_orig_rect") or edit["bbox"])
    origin = edit.get("_orig_origin") or edit.get("origin") or (bbox.x0, bbox.y1)
    asc = float(edit.get("ascender") or 0)
    desc = float(edit.get("descender") or 0)
    span = asc - desc
    if size > 0 and asc > 0 and desc < 0 and span > 1e-3:
        y1 = float(origin[1]) - size * desc / span
        y0 = y1 - size
        if (y1 - y0) >= size * 0.5 and y0 >= bbox.y0 - 0.5 and y1 <= bbox.y1 + 0.5:
            return fitz.Rect(bbox.x0, y0, bbox.x1, y1)
    return bbox


def _text_edit_layout_rect(fitz, page, edit: dict, size: float):
    """Layout rectangle for insert_htmlbox at the target position."""
    bbox = fitz.Rect(edit["bbox"])
    origin = edit.get("origin") or (bbox.x0, bbox.y1)
    asc = float(edit.get("ascender") or 0) or 0.9
    x0 = float(origin[0])
    top = float(origin[1]) - asc * size
    right = page.rect.x1 - 2.0
    if right <= x0 + size:
        right = min(page.rect.x1, x0 + size * 8)
    bottom = max(top + 3.0 * size, page.rect.y1 - 2.0)
    return fitz.Rect(x0, top, right, bottom)


def _warn_if_downscaled(htmlbox_result, edit: dict, warn_fn) -> None:
    """Inspect insert_htmlbox's (spare_height, scale) output."""
    try:
        spare_height, scale = htmlbox_result
        scale = float(scale)
    except Exception:
        return
    if scale < _MIN_LEGIBLE_SCALE or (spare_height is not None and spare_height < 0):
        _log.warning(
            "edited text did not fit its box at the original size "
            "(scale=%.2f); it was reduced to fit. old=%r",
            scale, (edit.get("old_text") or "")[:40])
        if warn_fn is not None:
            try:
                warn_fn(edit)
            except Exception:
                _log.exception("text-fit warn_fn raised")


def _build_html_body(edit: dict, new_txt: str) -> str:
    runs = edit.get("runs")
    if runs:
        parts: list[str] = []
        for run in runs:
            rt = run.get("text", "")
            if not rt:
                continue
            r_hex = _color_value_to_hex(run.get("color", edit.get("color", 0)))
            r_w = "bold" if run.get("bold") else "normal"
            r_s = "italic" if run.get("italic") else "normal"
            parts.append(
                f'<span style="color:{r_hex}; font-weight:{r_w}; font-style:{r_s};">{html.escape(rt)}</span>'
            )
        if parts:
            return "<div>%s</div>" % "".join(parts)
    return "<div>%s</div>" % html.escape(new_txt)


def _reinsert_edited_text(fitz, doc, page, edit: dict, warn_fn=None, *, redact: bool = True, prebuilt_archive=None) -> bool:
    """Redact original span (if redact=True) and reinsert replacement text with custom typography, multi-color runs, and system font support."""
    bbox = fitz.Rect(edit["bbox"])
    new_txt = (edit.get("new_text") or "").strip()
    size = _text_edit_size(edit)

    if prebuilt_archive is not None:
        arch, ref = prebuilt_archive
    else:
        arch = ref = None
        if new_txt:
            arch, ref = _build_embed_archive(fitz, doc, page, edit, new_txt)

    if redact:
        redact_rect = _text_edit_redaction_rect(fitz, edit, size)
        try:
            page.add_redact_annot(redact_rect, fill=False, cross_out=False)
            page.apply_redactions(
                images=getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0),
                graphics=getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0),
                text=getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0),
            )
        except Exception:
            page.add_redact_annot(redact_rect, fill=(1, 1, 1))
            page.apply_redactions()

    if not new_txt:
        return False

    color_hex = _text_edit_color_hex(edit)
    rect = _text_edit_layout_rect(fitz, page, edit, size)
    body = _build_html_body(edit, new_txt)

    try:
        if arch is not None:
            css = (
                f"@font-face {{ font-family: {_EMBED_FAMILY}; src: url({ref}); }}\n"
                f"* {{ margin:0; padding:0; white-space:pre-wrap;"
                f" font-family:{_EMBED_FAMILY}; font-size:{size}pt; color:{color_hex}; }}"
            )
            _warn_if_downscaled(
                page.insert_htmlbox(rect, body, css=css, archive=arch),
                edit, warn_fn)
            return True

        family, weight, style = _generic_font_style(edit)
        css = (
            f"* {{ margin:0; padding:0; white-space:pre-wrap;"
            f" font-family:{family}; font-size:{size}pt; color:{color_hex};"
            f" font-weight:{weight}; font-style:{style}; }}"
        )
        _warn_if_downscaled(page.insert_htmlbox(rect, body, css=css), edit, warn_fn)
        return False
    except Exception:
        _log.exception("htmlbox reinsertion failed; using fontfile/base-14 insert_text")
        try:
            origin = edit.get("origin") or (bbox.x0, bbox.y1)
            serif, mono, bold, italic = _text_edit_style(edit)
            font_req = edit.get("font", "").split("+")[-1].strip()
            sys_ttf = find_system_font_file(font_req, bold=bold, italic=italic)

            pt_x = float(origin[0])
            pt_y = float(origin[1])
            runs = edit.get("runs") or [{"text": new_txt, "color": edit.get("color", 0)}]
            b14 = _base14_fontname(edit)
            f_probe = fitz.Font(fontfile=sys_ttf) if (sys_ttf and os.path.isfile(sys_ttf)) else fitz.Font(b14)

            for run in runs:
                rt = run.get("text", "")
                if not rt:
                    continue
                col = _color_value_to_rgb(run.get("color", edit.get("color", 0)))
                if sys_ttf and os.path.isfile(sys_ttf):
                    page.insert_text(fitz.Point(pt_x, pt_y), rt, fontsize=size, fontfile=sys_ttf, fontname="custom", color=col)
                else:
                    page.insert_text(fitz.Point(pt_x, pt_y), rt, fontsize=size, fontname=b14, color=col)
                pt_x += f_probe.text_length(rt, fontsize=size)
            return bool(sys_ttf and os.path.isfile(sys_ttf))
        except Exception:
            _log.exception("insert_text fallback also failed")
            return False