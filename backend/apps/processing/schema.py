"""
Invoice extraction schema.

One schema is shared by every provider: the JSON Schema below is sent to the
LLM as a structured-output contract, and `InvoiceExtraction` normalizes the
result (money as Decimal, dates as ISO, currency as ISO 4217).
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ISO_CURRENCIES = {
    "AED", "ARS", "AUD", "BRL", "CAD", "CHF", "CLP", "CNY", "COP", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PEN", "PHP",
    "PLN", "RON", "SAR", "SEK", "SGD", "THB", "TRY", "TWD", "USD", "VND", "ZAR",
}

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d-%m-%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
)
_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

_nullable_str = {"type": ["string", "null"]}
_nullable_num = {"type": ["number", "null"]}

LINE_ITEM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "description": _nullable_str,
        "quantity": _nullable_num,
        "unit_price": _nullable_num,
        "amount": _nullable_num,
    },
    "required": ["description", "quantity", "unit_price", "amount"],
    "additionalProperties": False,
}

# Strict-mode compatible for both Anthropic and OpenAI: every property is
# required, optional values are nullable, no additional properties.
INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "is_invoice": {"type": "boolean"},
        "vendor_name": _nullable_str,
        "vendor_tax_id": _nullable_str,
        "vendor_address": _nullable_str,
        "customer_name": _nullable_str,
        "invoice_number": _nullable_str,
        "purchase_order": _nullable_str,
        "invoice_date": _nullable_str,
        "due_date": _nullable_str,
        "payment_terms": _nullable_str,
        "currency": _nullable_str,
        "subtotal": _nullable_num,
        "discount_amount": _nullable_num,
        "tax_amount": _nullable_num,
        "shipping_amount": _nullable_num,
        "total_amount": _nullable_num,
        "line_items": {"type": "array", "items": LINE_ITEM_JSON_SCHEMA},
        "uncertain_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "is_invoice", "vendor_name", "vendor_tax_id", "vendor_address", "customer_name",
        "invoice_number", "purchase_order", "invoice_date", "due_date", "payment_terms",
        "currency", "subtotal", "discount_amount", "tax_amount", "shipping_amount",
        "total_amount", "line_items", "uncertain_fields",
    ],
    "additionalProperties": False,
}


def _parse_decimal(value: Any) -> Optional[Decimal]:
    """Parse 1234.5, "1,234.56", "1.234,56", "1.234" (thousands), "$ -5" or "(12.00)"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    text = str(value).strip()
    if not text:
        return None
    # A minus sign or parentheses anywhere before the first digit mean a negative amount.
    prefix = re.match(r"^[^\d]*", text).group(0)
    negative = "-" in prefix or (text.startswith("(") and text.endswith(")"))
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", text) or re.fullmatch(r"\d{1,3}(,\d{3})+", text):
        text = text.replace(".", "").replace(",", "")  # 1.234 or 1,234: thousands separators only
    elif "," in text and ("." not in text or text.rfind(",") > text.rfind(".")):
        text = text.replace(".", "").replace(",", ".")  # decimal comma: 1.234,56 or 12,5
    else:
        text = text.replace(",", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative else number


def parse_money(value: Any) -> Optional[Decimal]:
    number = _parse_decimal(value)
    return number.quantize(Decimal("0.01")) if number is not None else None


def is_ambiguous_date(value: Any) -> bool:
    """01/09/2026 is 9 January in the US and 1 September in Europe."""
    m = _SLASH_DATE.match(str(value).strip()) if isinstance(value, str) else None
    if not m:
        return False
    a, b, _ = (int(g) for g in m.groups())
    return a <= 12 and b <= 12 and a != b


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    m = _SLASH_DATE.match(text)
    if m:
        a, b, year = (int(g) for g in m.groups())
        # US order (month/day) unless the first number cannot be a month (25/08/2026).
        month, day = (b, a) if a > 12 else (a, b)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v):
        return _parse_decimal(v)

    @field_validator("unit_price", "amount", mode="before")
    @classmethod
    def _money(cls, v):
        return parse_money(v)


class InvoiceExtraction(BaseModel):
    is_invoice: bool = True
    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    vendor_address: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    purchase_order: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    payment_terms: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    shipping_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    line_items: List[LineItem] = Field(default_factory=list)
    uncertain_fields: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _flag_ambiguous_dates(cls, data):
        """A day/month order that cannot be decided is a reason for a person to look."""
        if isinstance(data, dict):
            ambiguous = [f for f in ("invoice_date", "due_date") if is_ambiguous_date(data.get(f))]
            if ambiguous:
                data = {**data, "uncertain_fields": list(dict.fromkeys([*(data.get("uncertain_fields") or []), *ambiguous]))}
        return data

    @field_validator(
        "vendor_name", "vendor_tax_id", "vendor_address", "customer_name",
        "invoice_number", "purchase_order", "payment_terms", mode="before",
    )
    @classmethod
    def _strip(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _date(cls, v):
        return parse_date(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v):
        if v is None:
            return None
        code = str(v).strip().upper()
        return code if re.fullmatch(r"[A-Z]{3}", code) else None

    @field_validator(
        "subtotal", "discount_amount", "tax_amount", "shipping_amount", "total_amount", mode="before",
    )
    @classmethod
    def _amount(cls, v):
        return parse_money(v)

    @field_validator("line_items", "uncertain_fields", mode="before")
    @classmethod
    def _list(cls, v):
        return v or []

    def to_json(self) -> dict:
        """JSON-safe dict (Decimal and date as strings) for JSONField storage."""
        return self.model_dump(mode="json")
