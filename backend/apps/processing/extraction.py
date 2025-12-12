import re
from typing import Any, Dict, List


def extract_fields(doc_type: str, text: str) -> Dict[str, Any]:
    """
    Lightweight heuristic extractor (no external API).
    """
    lines = text.splitlines()
    vendor = _first_non_empty(lines[:5])
    invoice_number = _regex_first(r"(INV[\s\-:]?\s*\d+)", text)
    total_amount = _regex_first(r"total\s*[:\-]?\s*\$?\s*([\d.,]+)", text, group=1)
    currency = _regex_first(r"\b(USD|EUR|GBP|CAD|AUD|JPY)\b", text, group=1) or "USD"
    invoice_date = _regex_first(r"(?:invoice\s*date|date)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, group=1)
    due_date = _regex_first(r"(?:due\s*date|due)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, group=1)

    field_confidences = {
        "vendor_name": 0.7 if vendor else 0.3,
        "invoice_number": 0.7 if invoice_number else 0.3,
        "total_amount": 0.7 if total_amount else 0.3,
        "currency": 0.8 if currency else 0.3,
        "invoice_date": 0.6 if invoice_date else 0.3,
        "due_date": 0.6 if due_date else 0.3,
    }
    overall_confidence = sum(field_confidences.values()) / len(field_confidences)

    return {
        "invoice_number": invoice_number or "",
        "invoice_date": invoice_date or "",
        "due_date": due_date or "",
        "vendor_name": vendor or "",
        "total_amount": total_amount or "",
        "currency": currency or "",
        "line_items": _simple_line_items(lines),
        "overall_confidence": overall_confidence,
        "field_confidences": field_confidences,
        "raw_extraction": {"note": "heuristic_extractor"},
    }


def _first_non_empty(lines: List[str]) -> str:
    for line in lines:
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def _regex_first(pattern: str, text: str, group: int = 0) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if m:
        return m.group(group).strip()
    return ""


def _simple_line_items(lines: List[str]) -> list:
    items = []
    for line in lines:
        if line.lower().startswith("item"):
            items.append({"description": line.strip(), "quantity": 1, "unit_price": "", "confidence": 0.5})
    return items
