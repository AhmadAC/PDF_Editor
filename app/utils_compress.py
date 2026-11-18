# app/utils_compress.py
"""PDF_Editor – Ghostscript and PyMuPDF compression engine and process execution."""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, cast

from app.i18n import t


class CancelledError(Exception):
    """Raised when the user cancels a long-running operation."""


class WrongPasswordError(Exception):
    """Raised when an encrypted PDF cannot be unlocked with the supplied password."""


_COMPRESS_LEVELS = {
    "extreme":     {"dpi": 72,  "quality": 40, "grayscale": True},
    "recommended": {"dpi": 150, "quality": 65, "grayscale": False},
    "low":         {"dpi": 300, "quality": 80, "grayscale": False},
}

_GS_CACHE: tuple[bool, str | None] = (False, None)


def _find_gs() -> str | None:
    """Find Ghostscript executable (cached)."""
    global _GS_CACHE
    if _GS_CACHE[0]:
        return _GS_CACHE[1]
    names = ["gswin64c", "gswin32c", "gs"] if platform.system() == "Windows" else ["gs"]
    for n in names:
        p = shutil.which(n)
        if p and os.path.isfile(p):
            _GS_CACHE = (True, os.path.abspath(p))
            return _GS_CACHE[1]
    if platform.system() == "Windows":
        import glob
        for pattern in [
            r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
            r"C:\Program Files\gs\gs*\bin\gswin32c.exe",
            r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
        ]:
            matches = sorted(glob.glob(pattern), reverse=True)
            for p in matches:
                if os.path.isfile(p):
                    _GS_CACHE = (True, os.path.abspath(p))
                    return _GS_CACHE[1]
    _GS_CACHE = (True, None)
    return None


def _win_short_path(path: str) -> str:
    """On Windows, map path to its 8.3 short form if possible."""
    if sys.platform != "win32" or not path:
        return path
    if not os.path.exists(path):
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 512)
        if n and buf.value:
            return buf.value
    except Exception:
        pass
    return path


def _is_valid_pdf(path: str) -> bool:
    """Return True if path is a readable, non-empty PDF with pages."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
    except OSError:
        return False
    try:
        import fitz
        doc = fitz.open(path)
        try:
            if doc.needs_pass:
                return False
            return doc.page_count > 0
        finally:
            doc.close()
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        if r.is_encrypted:
            return False
        return len(r.pages) > 0
    except Exception:
        pass
    return False


def _compress_pdf(
    src: str, dst: str, level: str = "recommended", progress_fn=None, password: str | None = None
) -> tuple[int, int]:
    cfg = _COMPRESS_LEVELS.get(level, _COMPRESS_LEVELS["recommended"])
    dpi = cfg["dpi"]
    quality = cfg["quality"]
    gray = cfg["grayscale"]
    before = os.path.getsize(src)
    temps: list[str] = []

    encrypted = False
    authed = False
    try:
        import fitz
        from app.pdf_password import authenticate_fitz
        probe = fitz.open(src)
        try:
            encrypted = probe.needs_pass
            if encrypted and password:
                winner = authenticate_fitz(probe, password)
                authed = winner is not None
                if winner is not None:
                    password = winner
        finally:
            probe.close()
    except Exception:
        try:
            from app.pdf_password import decrypt_pypdf
            from pypdf import PdfReader
            pr = PdfReader(src)
            encrypted = pr.is_encrypted
            if encrypted and password:
                winner = decrypt_pypdf(pr, password)
                authed = winner is not None
                if winner is not None:
                    password = winner
        except Exception:
            encrypted = False

    if encrypted and not authed:
        raise WrongPasswordError(t("tool.err.wrong_password"))
    if not encrypted:
        password = None

    def _prog(stage: str, cur: int = 0, tot: int = 0):
        if progress_fn and progress_fn(stage, cur, tot) is False:
            for _p in temps:
                with contextlib.suppress(Exception):
                    os.unlink(_p)
            raise CancelledError()

    # Pass A: Ghostscript
    _prog("passA")
    gs = _find_gs()
    p = None
    if gs:
        try:
            presets = {"extreme": "/screen", "recommended": "/ebook", "low": "/printer"}
            fd, p = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            cmd = [
                gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={presets[level]}",
                "-dNOPAUSE", "-dQUIET", "-dBATCH",
                "-dDownsampleColorImages=true", "-dDownsampleGrayImages=true", "-dDownsampleMonoImages=true",
                f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}", f"-dMonoImageResolution={max(dpi, 150)}",
                "-dColorImageDownsampleThreshold=1.0", "-dGrayImageDownsampleThreshold=1.0",
                "-dColorImageDownsampleType=/Bicubic", "-dGrayImageDownsampleType=/Bicubic",
            ]
            if gray:
                cmd += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray", "-dOverrideICC"]
            if password:
                cmd += [f"-sPDFPassword={password}"]
            cmd += [f"-sOutputFile={_win_short_path(p)}", _win_short_path(src)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 120
            cancelled = False
            try:
                while True:
                    if proc.poll() is not None:
                        break
                    if progress_fn and progress_fn("passA", 0, 0) is False:
                        cancelled = True
                        break
                    if time.monotonic() > deadline:
                        break
                    time.sleep(0.2)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    with contextlib.suppress(Exception):
                        proc.wait(timeout=2)
            if cancelled:
                with contextlib.suppress(Exception):
                    os.unlink(p)
                raise CancelledError()
            if proc.returncode == 0 and _is_valid_pdf(p):
                temps.append(p)
            else:
                with contextlib.suppress(Exception):
                    os.unlink(p)
        except CancelledError:
            raise
        except Exception:
            if p:
                with contextlib.suppress(Exception):
                    os.unlink(p)

    # Pass B: PyMuPDF
    _prog("passB_setup")
    doc = None
    p = None
    try:
        import fitz
        doc = fitz.open(src)
        if doc.needs_pass and not (password and doc.authenticate(password)):
            raise WrongPasswordError(t("tool.err.wrong_password"))
        with contextlib.suppress(Exception):
            doc.scrub(metadata=True, xml_metadata=True, thumbnails=True, attached_files=True)
            doc.subset_fonts()

        _prog("passB_images", 0, 1)
        with contextlib.suppress(Exception):
            doc.rewrite_images(
                dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality,
                lossy=True, lossless=True, bitonal=True, color=True, gray=True, set_to_gray=gray,
            )
        _prog("passB_images", 1, 1)

        _prog("passB_save")
        fd, p = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        save_kw = dict(garbage=4, deflate=True, deflate_fonts=True, clean=True)
        try:
            cast(Any, doc).save(p, **save_kw, use_objstms=True)
        except TypeError:
            cast(Any, doc).save(p, **save_kw)
        if _is_valid_pdf(p):
            temps.append(p)
            p = None
    except CancelledError:
        raise
    except WrongPasswordError:
        raise
    except Exception:
        pass
    finally:
        if doc is not None:
            with contextlib.suppress(Exception):
                doc.close()
        if p:
            with contextlib.suppress(Exception):
                os.unlink(p)

    if not temps:
        raise RuntimeError(t("tool.compress.deps_missing"))

    best = min(temps, key=lambda f_path: os.path.getsize(f_path))
    best_size = os.path.getsize(best)
    for t_path in temps:
        if t_path != best:
            with contextlib.suppress(Exception):
                os.unlink(t_path)

    if best_size >= before:
        with contextlib.suppress(Exception):
            os.unlink(best)
        raise ValueError(t("tool.compress.no_gain_detail", before=f"{before/1024:.0f}", after=f"{best_size/1024:.0f}"))

    dst_dir = os.path.dirname(dst) or "."
    try:
        os.replace(best, dst)
    except OSError:
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=dst_dir)
        os.close(fd)
        try:
            shutil.copyfile(best, tmp)
            os.replace(tmp, dst)
        finally:
            with contextlib.suppress(Exception):
                os.unlink(tmp)
                os.unlink(best)
    return before, best_size