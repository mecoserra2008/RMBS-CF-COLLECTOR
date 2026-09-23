"""PDF -> text with an on-disk cache (<pdf>.txt). OCR fallback (ocrmypdf / pytesseract) for scanned accounts."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def pdf_text(pdf: Path, use_cache: bool = True, ocr: bool = True) -> tuple[str, str]:
    """Return (text, method). method in {cache, pdfplumber, pdftotext, ocr, empty}."""
    pdf = Path(pdf)
    txt_path = pdf.with_suffix(".txt")
    if use_cache and txt_path.exists() and txt_path.stat().st_mtime >= pdf.stat().st_mtime:
        return txt_path.read_text(encoding="utf-8"), "cache"
    text, method = "", "empty"
    try:
        import pdfplumber
        with pdfplumber.open(pdf) as doc:
            text = "\n".join((p.extract_text() or "") for p in doc.pages)
        method = "pdfplumber"
    except Exception:
        if shutil.which("pdftotext"):
            text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
            method = "pdftotext"
    if ocr and len(text.strip()) < 200:
        o = _ocr(pdf)
        if o:
            text, method = o, "ocr"
    txt_path.write_text(text, encoding="utf-8")
    return text, method


def _ocr(pdf: Path) -> str:   # pragma: no cover - depends on system OCR packages
    if shutil.which("ocrmypdf"):
        out = pdf.with_suffix(".ocr.pdf")
        r = subprocess.run(["ocrmypdf", "-l", "spa+por+eng", "--force-ocr", str(pdf), str(out)], capture_output=True)
        if r.returncode == 0:
            import pdfplumber
            with pdfplumber.open(out) as doc:
                return "\n".join((p.extract_text() or "") for p in doc.pages)
    try:
        import pytesseract
        from pdf2image import convert_from_path
        return "\n".join(pytesseract.image_to_string(im, lang="spa") for im in convert_from_path(str(pdf), dpi=300))
    except Exception:
        return ""
