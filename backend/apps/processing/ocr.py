import os
from typing import Optional

from PIL import Image
import pytesseract


def run_ocr(file_path: str) -> str:
    """
    Basic OCR using pytesseract for images. Returns empty string on failure.
    """
    if not file_path or not os.path.exists(file_path):
        return ""
    try:
        with Image.open(file_path) as img:
            text = pytesseract.image_to_string(img)
            return text or ""
    except Exception:
        return ""
