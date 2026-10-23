# app/tools/_import_external.py
"""PDF_Editor – External CLI wrapper tools for document conversion."""

import logging
import os
import shutil
import subprocess
import sys
import tempfile

_log = logging.getLogger(__name__)

def _find_onlyoffice_x2t() -> list[str] | None:
    """Locate ONLYOFFICE x2t binary or flatpak command on Linux/macOS/Windows."""
    bin_x2t = shutil.which("x2t")
    if bin_x2t:
        return [bin_x2t]

    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        try:
            res = subprocess.run(["flatpak-spawn", "--host", "which", "x2t"], capture_output=True, timeout=2)
            if res.returncode == 0:
                return ["flatpak-spawn", "--host", "x2t"]
        except Exception:
            pass

    candidates = [
        "/opt/onlyoffice/desktopeditors/converter/x2t",
        "/opt/onlyoffice/desktopeditors/x2t",
        "/usr/bin/x2t",
        "/usr/lib/onlyoffice/desktopeditors/converter/x2t",
        "/usr/lib64/onlyoffice/desktopeditors/converter/x2t",
        "/usr/local/bin/x2t",
        "/var/lib/flatpak/app/org.onlyoffice.desktopeditors/current/active/files/bin/x2t",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return [p]
            
    flatpak_cmd = ["flatpak-spawn", "--host", "flatpak"] if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn") else ["flatpak"]
    if shutil.which(flatpak_cmd[0]) or flatpak_cmd[0] == "flatpak-spawn":
        try:
            res = subprocess.run(
                flatpak_cmd + ["info", "org.onlyoffice.desktopeditors"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                return flatpak_cmd + ["run", "--command=x2t", "org.onlyoffice.desktopeditors"]
        except Exception:
            pass
    return None

def _convert_via_onlyoffice(doc_path: str, pdf_path: str) -> bool:
    cmd_base = _find_onlyoffice_x2t()
    if not cmd_base:
        return False
    try:
        abs_doc = os.path.abspath(doc_path)
        abs_pdf = os.path.abspath(pdf_path)
        
        if "flatpak-spawn" in cmd_base or os.environ.get("FLATPAK_ID"):
            tmp_base = os.path.expanduser("~/.cache/PDF_Editor_oo_tmp")
            os.makedirs(tmp_base, exist_ok=True)
            tmp_dir = tempfile.mkdtemp(dir=tmp_base)
        else:
            tmp_dir = tempfile.mkdtemp()
            
        try:
            temp_out = os.path.join(tmp_dir, "output.pdf")
            cmd = list(cmd_base) + [abs_doc, temp_out]
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            if os.path.isfile(temp_out) and os.path.getsize(temp_out) > 0:
                shutil.copy2(temp_out, abs_pdf)
                return True
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as exc:
        _log.warning("ONLYOFFICE conversion failed for %s: %s", doc_path, exc)
    return False

def _find_libreoffice() -> list[str] | None:
    lo_bin = shutil.which("libreoffice") or shutil.which("soffice") or shutil.which("libreoffice24.2") or shutil.which("libreoffice7.6")
    if lo_bin:
        return [lo_bin]

    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        for bin_name in ["libreoffice", "soffice"]:
            try:
                res = subprocess.run(["flatpak-spawn", "--host", "which", bin_name], capture_output=True, timeout=2)
                if res.returncode == 0:
                    return ["flatpak-spawn", "--host", bin_name]
            except Exception:
                pass

    flatpak_cmd = ["flatpak-spawn", "--host", "flatpak"] if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn") else ["flatpak"]
    if shutil.which(flatpak_cmd[0]) or flatpak_cmd[0] == "flatpak-spawn":
        try:
            res = subprocess.run(
                flatpak_cmd + ["info", "org.libreoffice.LibreOffice"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                return flatpak_cmd + ["run", "org.libreoffice.LibreOffice"]
        except Exception:
            pass

    if sys.platform == "darwin":
        mac_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if os.path.exists(mac_path):
            return [mac_path]

    if sys.platform == "win32":
        import glob
        for pattern in [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]:
            matches = glob.glob(pattern)
            if matches:
                return [matches[0]]
    return None

def _convert_via_libreoffice(doc_path: str, pdf_path: str) -> bool:
    cmd_base = _find_libreoffice()
    if not cmd_base:
        return False
    try:
        abs_doc = os.path.abspath(doc_path)
        abs_pdf = os.path.abspath(pdf_path)
        
        if "flatpak-spawn" in cmd_base or os.environ.get("FLATPAK_ID"):
            tmp_base = os.path.expanduser("~/.cache/PDF_Editor_lo_tmp")
            os.makedirs(tmp_base, exist_ok=True)
            tmp_dir = tempfile.mkdtemp(dir=tmp_base)
        else:
            tmp_dir = tempfile.mkdtemp()
            
        try:
            cmd = list(cmd_base) + [
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmp_dir,
                abs_doc,
            ]
            if sys.platform != "win32" and "flatpak" not in cmd_base[0]:
                lo_profile = os.path.join(tmp_dir, "lo_profile")
                cmd.insert(1, f"-env:UserInstallation=file://{lo_profile}")
            
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            
            stem = os.path.splitext(os.path.basename(abs_doc))[0]
            generated = os.path.join(tmp_dir, f"{stem}.pdf")
            if os.path.isfile(generated) and os.path.getsize(generated) > 0:
                shutil.copy2(generated, abs_pdf)
                return True
            for fname in os.listdir(tmp_dir):
                if fname.lower().endswith(".pdf"):
                    found = os.path.join(tmp_dir, fname)
                    if os.path.isfile(found) and os.path.getsize(found) > 0:
                        shutil.copy2(found, abs_pdf)
                        return True
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as exc:
        _log.warning("LibreOffice conversion failed for %s: %s", doc_path, exc)
    return False

def _find_abiword() -> list[str] | None:
    abi_bin = shutil.which("abiword")
    if abi_bin:
        return [abi_bin]
    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        try:
            res = subprocess.run(["flatpak-spawn", "--host", "which", "abiword"], capture_output=True, timeout=2)
            if res.returncode == 0:
                return ["flatpak-spawn", "--host", "abiword"]
        except Exception:
            pass
    return None

def _convert_via_abiword(doc_path: str, pdf_path: str) -> bool:
    cmd_base = _find_abiword()
    if not cmd_base:
        return False
    try:
        abs_doc = os.path.abspath(doc_path)
        abs_pdf = os.path.abspath(pdf_path)
        cmd = cmd_base + ["--to=pdf", "-o", abs_pdf, abs_doc]
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
            return True
    except Exception as exc:
        _log.warning("AbiWord conversion failed for %s: %s", doc_path, exc)
    return False