# app/tools/watermark.py
"""PDF_Editor – TabMarcaDagua facade: stamps PDF A onto PDF B with independent opacities, open-tab selectors, omitted-page retention, and new-tab output."""

from __future__ import annotations

from app.tools.watermark_engine import (
    EmptyPdfError,
    WatermarkJobConfig,
    WatermarkPreflight,
    copy_file_atomic,
    execute_watermark,
    preflight_watermark,
)
from app.tools.watermark_page import TabMarcaDagua
from app.tools.watermark_widgets import (
    _OpacitySpinBox,
    _OpenTabComboBox,
    _PdfSourceCard,
    _WatermarkResultBox,
)

__all__ = [
    "EmptyPdfError",
    "TabMarcaDagua",
    "WatermarkJobConfig",
    "WatermarkPreflight",
    "_OpacitySpinBox",
    "_OpenTabComboBox",
    "_PdfSourceCard",
    "_WatermarkResultBox",
    "copy_file_atomic",
    "execute_watermark",
    "preflight_watermark",
]