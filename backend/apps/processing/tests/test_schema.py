from datetime import date
from decimal import Decimal

import pytest

from apps.processing.schema import INVOICE_JSON_SCHEMA, InvoiceExtraction, parse_date, parse_money


@pytest.mark.parametrize("raw,expected", [
    (1234.5, Decimal("1234.50")),
    ("1,234.56", Decimal("1234.56")),
    ("$1,234.56", Decimal("1234.56")),
    ("1.234,56", Decimal("1234.56")),
    ("€ 12,50", Decimal("12.50")),
    ("(12.00)", Decimal("-12.00")),
    ("-5", Decimal("-5.00")),
    ("USD -5.00", Decimal("-5.00")),
    ("1.234", Decimal("1234.00")),
    ("1.234.567,89", Decimal("1234567.89")),
    ("1,234", Decimal("1234.00")),
    ("nan", None),
    (float("inf"), None),
    ("", None),
    ("n/a", None),
    (None, None),
    (True, None),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-01", date(2026, 9, 1)),
    ("09/01/2026", date(2026, 9, 1)),
    ("01.09.2026", date(2026, 9, 1)),
    ("Sep 1, 2026", date(2026, 9, 1)),
    ("not a date", None),
    (None, None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_invalid_values_become_null_not_errors():
    ex = InvoiceExtraction.model_validate({
        "vendor_name": "  ", "invoice_date": "yesterday", "currency": "dollars",
        "total_amount": "abc", "line_items": None, "uncertain_fields": None,
    })
    assert ex.vendor_name is None
    assert ex.invoice_date is None
    assert ex.currency is None
    assert ex.total_amount is None
    assert ex.line_items == []


def test_currency_is_uppercased():
    assert InvoiceExtraction(currency="eur").currency == "EUR"


def test_schema_is_strict_mode_compatible():
    """Both providers' strict modes need every property required and no extra properties."""
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])
    walk(INVOICE_JSON_SCHEMA)
    assert set(INVOICE_JSON_SCHEMA["properties"]) == set(InvoiceExtraction.model_fields)


def test_to_json_is_serializable():
    import json

    ex = InvoiceExtraction(total_amount="10.5", invoice_date="2026-01-02", line_items=[{"amount": 1}])
    data = json.loads(json.dumps(ex.to_json()))
    assert data["total_amount"] == "10.50"
    assert data["invoice_date"] == "2026-01-02"


def test_quantity_decimal_comma():
    from apps.processing.schema import LineItem

    assert LineItem(quantity="1,5").quantity == Decimal("1.5")


def test_day_first_slash_date():
    assert parse_date("25/08/2026") == date(2026, 8, 25)
    assert parse_date("31/02/2026") is None
