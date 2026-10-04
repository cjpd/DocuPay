"""
CSV export of extracted invoices.

Opens directly in Excel and Google Sheets: UTF-8 with a byte order mark (so accents
show correctly), plain decimal amounts with a dot, ISO dates. Text that a spreadsheet
would run as a formula (= + - @ at the start) is prefixed with an apostrophe, because
invoice text comes from outside the company.
"""
import csv
import io
from typing import Iterable, Iterator

from .models import Document

INVOICE_COLUMNS = [
    ("document_id", "Document ID"),
    ("status", "Status"),
    ("approved_by", "Approved by"),
    ("approved_at", "Approved at"),
    ("vendor_name", "Vendor"),
    ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date"),
    ("due_date", "Due date"),
    ("purchase_order", "PO number"),
    ("customer_name", "Billed to"),
    ("currency", "Currency"),
    ("subtotal", "Subtotal"),
    ("tax_amount", "Tax"),
    ("total_amount", "Total"),
    ("amount_due", "Amount due"),
    ("bank_account", "Bank account"),
    ("bank_code", "Bank code"),
    ("line_item_count", "Line items"),
    ("failed_checks", "Failed checks"),
    ("file_name", "File"),
    ("uploaded_at", "Uploaded at"),
]

LINE_COLUMNS = [
    ("document_id", "Document ID"),
    ("vendor_name", "Vendor"),
    ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date"),
    ("currency", "Currency"),
    ("line_number", "Line"),
    ("description", "Description"),
    ("quantity", "Quantity"),
    ("unit_price", "Unit price"),
    ("amount", "Amount"),
]

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Text for one cell. Numbers stay numbers; text that looks like a formula is neutralized."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _date(value) -> str:
    return value.isoformat() if value else ""


def _datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def invoice_row(doc: Document) -> dict:
    data = getattr(doc, "extracted_data", None)
    approved_by = ""
    if doc.status == Document.Status.APPROVED:
        if (doc.processing_meta or {}).get("decision") == "auto_approve":
            approved_by = "DocuPay (all checks passed)"
        else:
            reviewer = next((t.reviewed_by for t in doc.review_tasks.all() if t.status == "approved" and t.reviewed_by), None)
            approved_by = reviewer.get_username() if reviewer else "Person"
    failed = [c["name"] for c in (data.validation if data else []) if c.get("status") == "fail"]
    return {
        "document_id": doc.id,
        "status": doc.get_status_display(),
        "approved_by": approved_by,
        "approved_at": _datetime(doc.approved_at),
        "vendor_name": data.vendor_name if data else "",
        "invoice_number": data.invoice_number if data else "",
        "invoice_date": _date(data.invoice_date) if data else "",
        "due_date": _date(data.due_date) if data else "",
        "purchase_order": data.purchase_order if data else "",
        "customer_name": data.customer_name if data else "",
        "currency": data.currency if data else "",
        "subtotal": data.subtotal if data else None,
        "tax_amount": data.tax_amount if data else None,
        "total_amount": data.total_amount if data else None,
        "amount_due": data.amount_due if data else None,
        "bank_account": data.bank_account if data else "",
        "bank_code": data.bank_code if data else "",
        "line_item_count": len(data.line_items) if data else 0,
        "failed_checks": ", ".join(failed),
        "file_name": doc.file.name.rsplit("/", 1)[-1] if doc.file else "",
        "uploaded_at": _datetime(doc.created_at),
    }


def line_rows(doc: Document) -> Iterator[dict]:
    data = getattr(doc, "extracted_data", None)
    if not data:
        return
    for number, item in enumerate(data.line_items or [], 1):
        yield {
            "document_id": doc.id,
            "vendor_name": data.vendor_name,
            "invoice_number": data.invoice_number,
            "invoice_date": _date(data.invoice_date),
            "currency": data.currency,
            "line_number": number,
            "description": item.get("description"),
            "quantity": item.get("quantity"),
            "unit_price": item.get("unit_price"),
            "amount": item.get("amount"),
        }


def stream_csv(documents: Iterable[Document], level: str = "invoice") -> Iterator[str]:
    """Yield the CSV text in chunks, so a large export never sits in memory at once."""
    columns = LINE_COLUMNS if level == "line" else INVOICE_COLUMNS
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return text

    buffer.write("﻿")  # byte order mark: Excel then reads the file as UTF-8
    writer.writerow([label for _, label in columns])
    yield flush()
    for doc in documents:
        rows = line_rows(doc) if level == "line" else [invoice_row(doc)]
        for row in rows:
            writer.writerow([safe_cell(row[key]) for key, _ in columns])
        yield flush()
