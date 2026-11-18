# app/utils_fonts.py
"""PDF_Editor – Multi-platform system font discovery, caching, and font file resolution."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys

from PySide6.QtGui import QFontDatabase

_FONT_FILE_CACHE: dict[tuple[str, bool, bool], str | None] = {}


def find_system_font_file(family: str, bold: bool = False, italic: bool = False) -> str | None:
    """Locate a .ttf or .otf file on disk for the requested font family and style (cached)."""
    if not family:
        return None
    cache_key = (family.strip().lower(), bool(bold), bool(italic))
    if cache_key in _FONT_FILE_CACHE:
        return _FONT_FILE_CACHE[cache_key]

    fam_lower = family.strip().lower()

    if fam_lower == "helvetica":
        _FONT_FILE_CACHE[cache_key] = None
        return None

    result_path = None

    if sys.platform == "win32":
        try:
            import winreg
            win_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
            local_fonts_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")

            candidates: list[tuple[int, str]] = []
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            ]
            for hive, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        i = 0
                        while True:
                            try:
                                name, val, _ = winreg.EnumValue(key, i)
                                i += 1
                            except OSError:
                                break
                            if not isinstance(val, str):
                                continue
                            ext = os.path.splitext(val)[1].lower()
                            if ext not in (".ttf", ".otf", ".ttc"):
                                continue
                            name_clean = re.sub(
                                r"\s*\((TrueType|OpenType)\)\s*$", "", name, flags=re.IGNORECASE
                            ).strip()
                            nl = name_clean.lower()
                            if nl != fam_lower and not nl.startswith(fam_lower + " ") and fam_lower not in nl:
                                continue

                            has_bold = "bold" in nl
                            has_italic = "italic" in nl or "oblique" in nl
                            extra_penalty = 0
                            for mod in ("narrow", "condensed", "black", "light", "semibold", "thin", "medium"):
                                if mod in nl and mod not in fam_lower:
                                    extra_penalty += 20

                            score = 0
                            if nl == fam_lower and not bold and not italic:
                                score += 100
                            score += 30 if has_bold == bold else -30
                            score += 30 if has_italic == italic else -30
                            score -= extra_penalty

                            full_p = val if os.path.isabs(val) else os.path.join(win_fonts_dir, val)
                            if not os.path.isfile(full_p) and local_fonts_dir:
                                alt_p = os.path.join(local_fonts_dir, val)
                                if os.path.isfile(alt_p):
                                    full_p = alt_p
                            if os.path.isfile(full_p):
                                candidates.append((score, full_p))
                except OSError:
                    continue

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                result_path = candidates[0][1]

            if not result_path:
                norm_fam = re.sub(r"[^a-zA-Z0-9]", "", fam_lower)
                for fdir in (local_fonts_dir, win_fonts_dir):
                    if not os.path.isdir(fdir):
                        continue
                    with contextlib.suppress(Exception):
                        for fn in os.listdir(fdir):
                            ext = os.path.splitext(fn)[1].lower()
                            if ext in (".ttf", ".otf"):
                                stem = re.sub(r"[^a-zA-Z0-9]", "", os.path.splitext(fn)[0].lower())
                                if stem == norm_fam or stem.startswith(norm_fam):
                                    fp = os.path.join(fdir, fn)
                                    if os.path.isfile(fp):
                                        result_path = fp
                                        break
                    if result_path:
                        break
        except Exception:
            pass
    elif sys.platform == "darwin":
        mac_dirs = [
            os.path.expanduser("~/Library/Fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
        ]
        norm_fam = re.sub(r"[^a-zA-Z0-9]", "", fam_lower)
        for fdir in mac_dirs:
            if not os.path.isdir(fdir):
                continue
            with contextlib.suppress(Exception):
                for fn in os.listdir(fdir):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in (".ttf", ".otf"):
                        stem = re.sub(r"[^a-zA-Z0-9]", "", os.path.splitext(fn)[0].lower())
                        if stem == norm_fam or stem.startswith(norm_fam):
                            fp = os.path.join(fdir, fn)
                            if os.path.isfile(fp):
                                result_path = fp
                                break
            if result_path:
                break
    else:
        fc_bin = shutil.which("fc-match")
        if fc_bin:
            try:
                pattern = family
                if bold:
                    pattern += ":bold"
                if italic:
                    pattern += ":italic"
                res = subprocess.run(
                    [fc_bin, "-f", "%{file}", pattern],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    p = res.stdout.strip()
                    if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in (".ttf", ".otf"):
                        result_path = p
            except Exception:
                pass

    _FONT_FILE_CACHE[cache_key] = result_path
    return result_path


def get_system_font_families() -> list[str]:
    """Return all font family names installed on the system, with standard PDF fonts on top."""
    standard = [
        "Helvetica", "Arial", "Times New Roman", "Courier New",
        "Segoe UI", "Calibri", "Georgia", "Verdana", "Trebuchet MS"
    ]
    try:
        fams = QFontDatabase.families()
        installed = [
            f for f in fams
            if f and not f.startswith("@") and f not in standard
        ]
        installed.sort(key=lambda s: s.lower())
        return standard + installed
    except Exception:
        return standard