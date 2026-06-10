import os
from pathlib import Path
from typing import List

from PIL import Image, ImageEnhance
import pytesseract

_TESSERACT_CONFIG = "--psm 6 --oem 3"


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
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    return img
