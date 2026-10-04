"""
Offline extractor: regular expressions over text, with Tesseract OCR for images
when it is installed. No API key, no cost. Use it for local development and as
the baseline in the benchmark. It is not accurate enough for production.
"""
import io
import re
from typing import List, Optional

from ..errors import PermanentProcessingError
from ..ingest import DocumentInput
from ..schema import InvoiceExtraction, LineItem, parse_money
from .base import FAST, ExtractionProvider, ProviderResult

_AMOUNT = r"[-(]?[$€£]?\s?\d(?:[\d,.]*\d)?\)?"
_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\.\d{1,2}\.\d{4}|[A-Z][a-z]{2,8} \d{1,2}, \d{4})"
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


def ocr_images(doc: DocumentInput) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    texts = []
    for part in doc.images:
        try:
            texts.append(pytesseract.image_to_string(Image.open(io.BytesIO(part.data))))
        except pytesseract.TesseractNotFoundError as exc:
            raise PermanentProcessingError("Tesseract is not installed, so images cannot be read offline") from exc
    return "\n".join(texts)


def _find(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None


def _line_items(lines: List[str]) -> List[LineItem]:
    items = []
    row = re.compile(rf"^(?P<desc>[A-Za-z].*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>{_AMOUNT})\s+(?P<amt>{_AMOUNT})\s*$")
    for line in lines:
        m = row.match(line.strip())
        if m and not re.match(r"(sub)?total|tax|vat|shipping|discount", m.group("desc"), re.IGNORECASE):
            items.append(LineItem(description=m.group("desc"), quantity=m.group("qty"),
                                  unit_price=m.group("unit"), amount=m.group("amt")))
    return items


_PAGE_MARKER = re.compile(r"^--- page \d+ ---$")


def extract_from_text(text: str) -> InvoiceExtraction:
    lines = [ln for ln in text.splitlines() if ln.strip() and not _PAGE_MARKER.match(ln.strip())]
    text = "\n".join(lines)
    # The first line is usually the vendor, sometimes followed by the title on the same line.
    vendor = re.sub(r"\s+(tax\s+)?(invoice|bill|rechnung|facture|factura|quotation|purchase order|credit note)\s*$",
                    "", lines[0].strip(), flags=re.IGNORECASE) if lines else None
    currency = _find(r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|MXN|BRL)\b", text)
    if not currency:
        currency = next((code for sym, code in _SYMBOLS.items() if sym in text), None)
    return InvoiceExtraction(
        is_invoice=bool(re.search(r"invoice|bill\b|factura|rechnung", text, re.IGNORECASE)),
        vendor_name=vendor,
        invoice_number=_find(r"invoice[ \t]*(?:no\.?|number|num\.?|#)[ \t]*[:#]?[ \t]*([A-Z0-9][A-Z0-9\-/]{2,})", text)
        or _find(r"\b(INV[-\s]?\d{2,})\b", text),
        purchase_order=_find(r"\b(?:P\.?O\.?|purchase[ \t]*order)[ \t]*(?:no\.?|number|#)?[ \t]*[:#]?[ \t]*([A-Z0-9\-]{3,})", text),
        invoice_date=_find(rf"(?:invoice\s*date|date\s*of\s*issue|^date)[ \t]*[:\-]?[ \t]*{_DATE}", text),
        due_date=_find(rf"(?:due\s*date|payment\s*due|due)[ \t]*[:\-]?[ \t]*{_DATE}", text),
        currency=currency,
        subtotal=_find(rf"(?:sub\s*-?total|net\s+(?:amount|total))[ \t]*[:\-]?[ \t]*({_AMOUNT})", text),
        # "VAT 19%: 1,615.78": skip the rate, take the amount.
        tax_amount=_find(rf"(?:tax|vat|gst)(?:[ \t]*\([^)]*\))?(?:[ \t]*\d+(?:[.,]\d+)?[ \t]*%)?[ \t]*[:\-]?[ \t]*({_AMOUNT})(?![\d.,]*[ \t]*%)", text),
        discount_amount=_find(rf"discount(?:[ \t]*\([^)]*\))?[ \t]*[:\-]?[ \t]*({_AMOUNT})", text),
        shipping_amount=_find(rf"(?:shipping|freight|delivery)[ \t]*[:\-]?[ \t]*({_AMOUNT})", text),
        total_amount=_find(rf"(?:grand\s*total|total\s*due|amount\s*due|^total)[ \t]*[:\-]?[ \t]*({_AMOUNT})", text),
        line_items=_line_items(lines),
    )


class HeuristicProvider(ExtractionProvider):
    name = "heuristic"
    supports_escalation = False

    def extract(self, doc: DocumentInput, tier: str = FAST) -> ProviderResult:
        text = "\n".join(t for t in (doc.text, ocr_images(doc) if doc.images else "") if t)
        return ProviderResult(
            extraction=extract_from_text(text or ""),
            provider=self.name, model="regex-v2", tier=tier, cost_usd=parse_money(0),
        )
