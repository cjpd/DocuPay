import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# Feature flag — set ENHANCED_EXTRACTION=0 to fall back to the legacy extractor
_ENHANCED = os.getenv("ENHANCED_EXTRACTION", "1").strip() not in ("0", "false", "no")

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}

_INVOICE_NUM_PATTERNS = [
    r"invoice\s*(?:no\.?|number|#|num\.?)\s*[:\-]?\s*([A-Z0-9\-\/]*\d[A-Z0-9\-\/]*)",
    r"\binv[-#:\s]+([A-Z0-9\-]{3,})",
    r"invoice\s+(\d{4,})",
    r"reference\s*[:\-]?\s*([A-Z0-9\-\/]*\d[A-Z0-9\-\/]*)",
]

_DATE_FORMATS = [
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "ymd"),
    (r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b", "dmy"),
    (r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", "mdy_text"),
    (r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", "dmy_text"),
]

_DATE_LABEL = r"(?:invoice\s*date|issue\s*date|date\s*issued|date)\s*[:\-]?\s*"
_DUE_LABEL  = r"(?:due\s*date|payment\s*due|due)\s*[:\-]?\s*"

_TOTAL_LABEL = (
    r"(?:\btotal\s*(?:due|amount|price)?|\bamount\s*due|\bgrand\s*total|\bbalance\s*due)"
    r"\s*(?:\([A-Z]{3}\))?\s*[:\-]?\s*"
)
_CURRENCY_SYM_RE = r"[\$\£\€]"
_AMOUNT_NUM_RE   = r"([\d,]+(?:\.\d{1,2})?)"
_AMOUNT_RE       = _CURRENCY_SYM_RE + r"?\s*" + _AMOUNT_NUM_RE

_CURRENCY_CODES_RE = r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|MXN)\b"
_CURRENCY_SYMBOLS  = {"$": "USD", "£": "GBP", "€": "EUR"}

_VENDOR_SKIP   = re.compile(
    r"^(invoice|receipt|tax\s+invoice|tax|a\s+tax|\w+\s+tax\s+invoice|bill\s+to|billed\s+to|date|page|\d+\s*$|to:|from:|attn)",
    re.IGNORECASE,
)
_MOSTLY_NOISE  = re.compile(r"^[\W_]{3,}$")
_TOTAL_SKIP    = re.compile(r"subtotal|tax|gst|vat|discount|shipping|fee", re.IGNORECASE)


# ── Public entry point ────────────────────────────────────────────────────────

def extract_fields(doc_type: str, text: str) -> Dict[str, Any]:
    if _ENHANCED:
        return _extract_enhanced(doc_type, text)
    return _extract_legacy(doc_type, text)


# ── Enhanced extractor ────────────────────────────────────────────────────────

def _extract_enhanced(doc_type: str, text: str) -> Dict[str, Any]:
    inv_num,   conf_inv  = _invoice_number(text)
    inv_date,  conf_idat = _labeled_date(text, _DATE_LABEL)
    due_date,  conf_ddat = _labeled_date(text, _DUE_LABEL)
    vendor,    conf_ven  = _vendor(text)
    total,     conf_tot, currency = _total_and_currency(text)
    line_items           = _line_items(text)

    field_confidences = {
        "invoice_number": conf_inv,
        "invoice_date":   conf_idat,
        "due_date":       conf_ddat,
        "vendor_name":    conf_ven,
        "total_amount":   conf_tot,
        "currency":       0.95 if currency else 0.2,
    }
    overall_confidence = sum(field_confidences.values()) / len(field_confidences)

    return {
        "invoice_number":    inv_num,
        "invoice_date":      inv_date,
        "due_date":          due_date,
        "vendor_name":       vendor,
        "total_amount":      total,
        "currency":          currency,
        "line_items":        line_items,
        "overall_confidence": overall_confidence,
        "field_confidences": field_confidences,
        "raw_extraction":    {"extractor": "signal_v1", "enhanced": True},
    }


# ── Invoice number ────────────────────────────────────────────────────────────

def _invoice_number(text: str) -> Tuple[str, float]:
    # Labeled pattern → high confidence
    for pattern in _INVOICE_NUM_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if len(val) >= 3:
                return val, 0.90

    # Label on one line, value on the next
    m = re.search(r"invoice\s*number\s*[\r\n]+\s*([A-Z0-9\-]{3,})", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), 0.80

    # Standalone signal: # followed by digits (e.g. "#00001")
    m = re.search(r"#\s*([0-9]{4,})\b", text)
    if m:
        return m.group(1).strip(), 0.65

    return "", 0.2


# ── Dates ─────────────────────────────────────────────────────────────────────

def _labeled_date(text: str, label_pattern: str) -> Tuple[str, float]:
    # Label found → high confidence
    for m in re.finditer(label_pattern, text, re.IGNORECASE):
        snippet = text[m.end(): m.end() + 50]
        date = _parse_date(snippet)
        if date:
            return date, 0.90
    return "", 0.2


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

def _vendor(text: str) -> Tuple[str, float]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[:10]:
        if len(line) < 3:
            continue
        if _VENDOR_SKIP.match(line):
            continue
        if _MOSTLY_NOISE.match(line):
            continue
        return re.sub(r"[\s\W]+$", "", line), 0.70
    return (lines[0] if lines else ""), 0.40


# ── Total & currency ──────────────────────────────────────────────────────────

def _total_and_currency(text: str) -> Tuple[str, float, str]:
    currency = _detect_currency(text)

    # Strongest: label + currency symbol on same line
    m = re.search(_TOTAL_LABEL + _CURRENCY_SYM_RE + r"\s*" + _AMOUNT_NUM_RE, text, re.IGNORECASE)
    if m:
        val = _clean_amount(m.group(1))
        if val:
            return val, 0.95, currency

    # Strong: label + amount (symbol optional) on same line
    m = re.search(_TOTAL_LABEL + _AMOUNT_RE, text, re.IGNORECASE)
    if m:
        val = _clean_amount(m.group(1))
        if val:
            return val, 0.85, currency

    # Strong: label then amount on next line
    m = re.search(_TOTAL_LABEL + r"[\r\n]+\s*" + _AMOUNT_RE, text, re.IGNORECASE)
    if m:
        val = _clean_amount(m.group(1))
        if val:
            return val, 0.80, currency

    # Medium: any currency-symbol amount — take the largest (skip subtotals)
    symbol_amounts = _all_symbol_amounts(text)
    if symbol_amounts:
        return str(max(symbol_amounts)), 0.65, currency

    return "", 0.2, currency


def _all_symbol_amounts(text: str) -> List[Decimal]:
    results = []
    for m in re.finditer(_CURRENCY_SYM_RE + r"\s*" + _AMOUNT_NUM_RE, text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line = text[line_start: text.find("\n", m.end())].strip()
        if _TOTAL_SKIP.search(line):
            continue
        val = _clean_amount(m.group(1))
        if val:
            try:
                d = Decimal(val)
                if d > 0:
                    results.append(d)
            except InvalidOperation:
                pass
    return results


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

def _line_items(text: str) -> List[Dict]:
    items = []

    # Pattern A: desc  qty  unit_price  amount  (strict 4-column table)
    strict = re.compile(
        r"^(.{3,60}?)\s+"
        r"(\d+(?:\.\d+)?)\s+"
        r"[\$\£\€]?\s*([\d,]+(?:\.\d{1,2})?)\s+"
        r"[\$\£\€]?\s*([\d,]+(?:\.\d{1,2})?)$",
        re.MULTILINE,
    )
    for m in strict.finditer(text):
        desc = m.group(1).strip()
        if re.search(r"description|qty|quantity|amount|price|unit|total|subtotal", desc, re.IGNORECASE):
            continue
        if _TOTAL_SKIP.search(desc):
            continue
        unit = _clean_amount(m.group(3))
        amt  = _clean_amount(m.group(4))
        if not unit and not amt:
            continue
        try:
            items.append({
                "description": desc,
                "quantity":    float(m.group(2)),
                "unit_price":  unit,
                "amount":      amt,
                "confidence":  0.85,  # both unit + total present
            })
        except ValueError:
            continue

    if items:
        return items

    # Pattern B: fallback — any line ending with a currency-symbol amount
    # (catches simpler invoices where only the line total is present)
    loose = re.compile(
        r"^(.{3,60}?)\s+" + _CURRENCY_SYM_RE + r"?\s*([\d,]+\.\d{2})$",
        re.MULTILINE,
    )
    for m in loose.finditer(text):
        desc = m.group(1).strip()
        if re.search(r"total|subtotal|tax|gst|vat|discount|amount|due|balance", desc, re.IGNORECASE):
            continue
        amt = _clean_amount(m.group(2))
        if not amt:
            continue
        items.append({
            "description": desc,
            "quantity":    1.0,
            "unit_price":  amt,
            "amount":      amt,
            "confidence":  0.65,  # amount present but no qty/unit split
        })

    return items


# ── Legacy extractor (kept for flag-off fallback) ─────────────────────────────

def _extract_legacy(doc_type: str, text: str) -> Dict[str, Any]:
    lines  = text.splitlines()
    vendor = _first_non_empty(lines[:5])

    invoice_number = _regex_first(r"(INV[\s\-:]?\s*\d+)", text)
    total_amount   = _regex_first(r"total\s*[:\-]?\s*\$?\s*([\d.,]+)", text, group=1)
    currency       = _regex_first(r"\b(USD|EUR|GBP|CAD|AUD|JPY)\b", text, group=1) or "USD"
    invoice_date   = _regex_first(
        r"(?:invoice\s*date|date)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, group=1
    )
    due_date = _regex_first(
        r"(?:due\s*date|due)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, group=1
    )

    field_confidences = {
        "vendor_name":    0.7 if vendor else 0.3,
        "invoice_number": 0.7 if invoice_number else 0.3,
        "total_amount":   0.7 if total_amount else 0.3,
        "currency":       0.8 if currency else 0.3,
        "invoice_date":   0.6 if invoice_date else 0.3,
        "due_date":       0.6 if due_date else 0.3,
    }

    return {
        "invoice_number":    invoice_number or "",
        "invoice_date":      invoice_date or "",
        "due_date":          due_date or "",
        "vendor_name":       vendor or "",
        "total_amount":      total_amount or "",
        "currency":          currency or "",
        "line_items":        [],
        "overall_confidence": sum(field_confidences.values()) / len(field_confidences),
        "field_confidences": field_confidences,
        "raw_extraction":    {"extractor": "legacy"},
    }


def _first_non_empty(lines: List[str]) -> str:
    for line in lines:
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def _regex_first(pattern: str, text: str, group: int = 0) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(group).strip() if m else ""
