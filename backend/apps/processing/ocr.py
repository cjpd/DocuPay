import os
from pathlib import Path
from typing import List

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

_TESSERACT_CONFIG = "--psm 6 --oem 3"

# Tesseract accuracy degrades below ~150 DPI; target 300 DPI equivalent (~2400px for A4 width).
_MIN_WIDTH = 2400


def run_ocr(file_path: str) -> str:
    if not file_path or not os.path.exists(file_path):
        return ""
    try:
        if Path(file_path).suffix.lower() == ".pdf":
            return _ocr_pdf(file_path)
        return _ocr_image(file_path)
    except Exception:
        return ""


def _ocr_pdf(file_path: str) -> str:
    from pdf2image import convert_from_path

    # 300 DPI gives ~2480px wide for A4 — no further upscaling needed
    pages: List[Image.Image] = convert_from_path(file_path, dpi=300)
    parts = []
    for page in pages:
        text = pytesseract.image_to_string(_preprocess(page), config=_TESSERACT_CONFIG)
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _ocr_image(file_path: str) -> str:
    with Image.open(file_path) as img:
        text = pytesseract.image_to_string(_preprocess(img), config=_TESSERACT_CONFIG)
        return text.strip()


def _preprocess(img: Image.Image) -> Image.Image:
    # 1. Flatten transparency (RGBA/P → RGB) before grayscale conversion
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGBA").convert("RGB")

    # 2. Grayscale
    img = img.convert("L")

    # 3. Upscale if too small — preserves detail Tesseract needs
    w, h = img.size
    if w < _MIN_WIDTH:
        scale = _MIN_WIDTH / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # 4. Normalise contrast dynamically (handles under/over-exposed scans)
    img = ImageOps.autocontrast(img, cutoff=2)

    # 5. Median filter removes scanner noise / compression artifacts
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # 6. Unsharp mask sharpens character edges without ringing
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

    # 7. Final contrast nudge to push dark ink toward black
    img = ImageEnhance.Contrast(img).enhance(1.5)

    return img
