# app/tools/crop.py
"""PDF_Editor – TabCortar facade: crop PDF pages tool with SVG icons."""

from __future__ import annotations

from app.tools.crop_engine import (
    compute_page_crops,
    compute_ref_dimensions,
    map_margins_for_rotation,
    resolve_target_pages,
    save_cropped_pdf,
)
from app.tools.crop_i18n import _CROP_I18N, register_crop_translations
from app.tools.crop_page import TabCortar
from app.tools.crop_widgets import (
    CropMarginsGroup,
    CropOptionsGroup,
    FocusComboBox,
    FocusSpinBox,
)

__all__ = [
    "CropMarginsGroup",
    "CropOptionsGroup",
    "FocusComboBox",
    "FocusSpinBox",
    "TabCortar",
    "_CROP_I18N",
    "compute_page_crops",
    "compute_ref_dimensions",
    "map_margins_for_rotation",
    "register_crop_translations",
    "resolve_target_pages",
    "save_cropped_pdf",
]