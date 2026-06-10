import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple


_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}

_INVOICE_NUM_PATTERNS = [
    # "Invoice No. 0123456789" / "Invoice #001" — value must contain a digit
    r"invoice\s*(?:no\.?|number|#|num\.?)\s*[:\-]?\s*([A-Z0-9\-\/]*\d[A-Z0-9\-\/]*)",
    # "INV-001" / "INV #123" — require explicit separator so we don't match "Invoice"
    r"\binv[-#:\s]+([A-Z0-9\-]{3,})",
    # "Invoice 2022435" (bare number after word Invoice)
    r"invoice\s+(\d{4,})",
    r"reference\s*[:\-]?\s*([A-Z0-9\-\/]*\d[A-Z0-9\-\/]*)",
]

_DATE_FORMATS = [
    # ISO: 2023-06-20
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "ymd"),
    # D/M/YYYY or M/D/YYYY: 19/7/2022
    (r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b", "dmy"),
    # Month DD, YYYY: June 20, 2023
    (r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", "mdy_text"),
    # DD Month YYYY: 20 June 2023
    (r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", "dmy_text"),
]

_DATE_LABEL = r"(?:invoice\s*date|issue\s*date|date\s*issued|date)\s*[:\-]?\s*"
_DUE_LABEL  = r"(?:due\s*date|payment\s*due|due)\s*[:\-]?\s*"

_TOTAL_LABEL = (
    r"(?:total\s*(?:due|amount|price)?|amount\s*due|grand\s*total)"
    r"\s*(?:\([A-Z]{3}\))?\s*[:\-]?\s*"
)
_AMOUNT_RE = r"[\$\£\€]?\s*([\d,]+(?:\.\d{1,2})?)"

_CURRENCY_CODES_RE = r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|MXN)\b"
_CURRENCY_SYMBOLS  = {"$": "USD", "£": "GBP", "€": "EUR"}

_VENDOR_SKIP = re.compile(
    r"^(invoice|receipt|tax|a\s+tax|bill\s+to|billed\s+to|date|page|\d+\s*$|to:|from:|attn)",
    re.IGNORECASE,
)
_MOSTLY_NOISE = re.compile(r"^[\W_]{3,}$")


def extract_fields(doc_type: str, text: str) -> Dict[str, Any]:
    invoice_number = _extract_invoice_number(text)
    invoice_date   = _extract_labeled_date(text, _DATE_LABEL)
    due_date       = _extract_labeled_date(text, _DUE_LABEL)
    vendor_name    = _extract_vendor(text)
    total_amount, currency = _extract_total_and_currency(text)
    line_items     = _extract_line_items(text)

    field_confidences = {
        "vendor_name":    0.75 if vendor_name else 0.2,
        "invoice_number": 0.85 if invoice_number else 0.2,
        "total_amount":   0.85 if total_amount else 0.2,
        "currency":       0.90 if currency else 0.2,
        "invoice_date":   0.80 if invoice_date else 0.2,
        "due_date":       0.70 if due_date else 0.2,
    }
    overall_confidence = sum(field_confidences.values()) / len(field_confidences)

    return {
        "invoice_number":    invoice_number or "",
        "invoice_date":      invoice_date or "",
        "due_date":          due_date or "",
        "vendor_name":       vendor_name or "",
        "total_amount":      total_amount or "",
        "currency":          currency or "",
        "line_items":        line_items,
        "overall_confidence": overall_confidence,
        "field_confidences": field_confidences,
        "raw_extraction":    {"extractor": "regex_v2"},
    }


# ── Invoice number ────────────────────────────────────────────────────────────

def _extract_invoice_number(text: str) -> str:
    for pattern in _INVOICE_NUM_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if len(val) >= 3:
                return val
    # Fallback: "Invoice Number" on one line, value on the next
    m = re.search(r"invoice\s*number\s*[\r\n]+\s*([A-Z0-9\-]{3,})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


# ── Dates ─────────────────────────────────────────────────────────────────────

def _extract_labeled_date(text: str, label_pattern: str) -> str:
    for m in re.finditer(label_pattern, text, re.IGNORECASE):
        # Check up to 50 chars after the label (handles same-line and next-line)
        snippet = text[m.end(): m.end() + 50]
        date = _parse_date(snippet)
        if date:
            return date
    return ""


def _parse_date(snippet: str) -> Optional[str]:
    for pattern, fmt in _DATE_FORMATS:
        m = re.search(pattern, snippet, re.IGNORECASE)
        if not m:
            continue
        try:
            if fmt == "ymd":
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif fmt == "dmy":
                d, mo, y = m.group(1), m.group(2), m.group(3)
                return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
            elif fmt == "mdy_text":
                mo = _MONTHS.get(m.group(1).lower())
                if mo:
                    return f"{m.group(3)}-{mo}-{m.group(2).zfill(2)}"
            elif fmt == "dmy_text":
                mo = _MONTHS.get(m.group(2).lower())
                if mo:
                    return f"{m.group(3)}-{mo}-{m.group(1).zfill(2)}"
        except (IndexError, ValueError):
            continue
    return None


# ── Vendor ────────────────────────────────────────────────────────────────────

def _extract_vendor(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[:10]:
        if len(line) < 3:
            continue
        if _VENDOR_SKIP.match(line):
            continue
        if _MOSTLY_NOISE.match(line):
            continue
        # Strip trailing punctuation/noise (e.g. "COMPANY ————")
        return re.sub(r"[\s\W]+$", "", line)
    return lines[0] if lines else ""


# ── Total & currency ──────────────────────────────────────────────────────────

def _extract_total_and_currency(text: str) -> Tuple[str, str]:
    currency = _detect_currency(text)

    # Total label followed by an amount on the same line
    same_line = re.search(_TOTAL_LABEL + _AMOUNT_RE, text, re.IGNORECASE)
    if same_line:
        val = _clean_amount(same_line.group(1))
        if val:
            return val, currency

    # Total label, then amount on the next line
    next_line = re.search(
        _TOTAL_LABEL + r"[\r\n]+\s*" + _AMOUNT_RE, text, re.IGNORECASE
    )
    if next_line:
        val = _clean_amount(next_line.group(1))
        if val:
            return val, currency

    # Fallback: largest currency-symbol amount in the document
    amounts = re.findall(r"[\$\£\€]\s*([\d,]+(?:\.\d{1,2})?)", text)
    parsed = []
    for a in amounts:
        v = _clean_amount(a)
        if v:
            try:
                parsed.append(Decimal(v))
            except InvalidOperation:
                pass
    if parsed:
        return str(max(parsed)), currency

    return "", currency


def _detect_currency(text: str) -> str:
    m = re.search(_CURRENCY_CODES_RE, text)
    if m:
        return m.group(1)
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return "USD"


def _clean_amount(raw: str) -> str:
    val = raw.replace(",", "").strip()
    try:
        Decimal(val)
        return val
    except InvalidOperation:
        return ""


# ── Line items ────────────────────────────────────────────────────────────────

def _extract_line_items(text: str) -> List[Dict]:
    items = []
    # Matches: description  qty  unit_price  amount  (all on one line)
    row_re = re.compile(
        r"^(.{3,60}?)\s+"
        r"(\d+(?:\.\d+)?)\s+"
        r"[\$\£\€]?\s*([\d,]+(?:\.\d{1,2})?)\s+"
        r"[\$\£\€]?\s*([\d,]+(?:\.\d{1,2})?)$",
        re.MULTILINE,
    )
    for m in row_re.finditer(text):
        desc = m.group(1).strip()
        # Skip header rows
        if re.search(r"description|qty|quantity|amount|price|unit", desc, re.IGNORECASE):
            continue
        try:
            items.append({
                "description": desc,
                "quantity":    float(m.group(2)),
                "unit_price":  _clean_amount(m.group(3)),
                "amount":      _clean_amount(m.group(4)),
                "confidence":  0.75,
            })
        except (ValueError, IndexError):
            continue
    return items
