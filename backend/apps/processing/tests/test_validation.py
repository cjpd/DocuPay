from datetime import date
from decimal import Decimal

from apps.processing.schema import InvoiceExtraction
from apps.processing.validation import FAIL, PASS, SKIP, validate

from .factories import CLEAN_INVOICE

TODAY = date(2026, 10, 3)


def _ex(**overrides):
    return InvoiceExtraction.model_validate({**CLEAN_INVOICE, **overrides})


def _status(report, name):
    return next(c.status for c in report.checks if c.name == name)


def test_clean_invoice_auto_approves():
    report = validate(_ex(), today=TODAY, is_duplicate=lambda ex: False)
    assert report.failures == []
    assert report.score == 1.0
    assert report.amounts_proved
    assert report.can_auto_approve(0.92)


def test_old_bug_is_fixed_score_can_reach_threshold():
    """Before the revamp the highest possible score was 0.68 and nothing auto-approved."""
    assert validate(_ex(), today=TODAY).score >= 0.92


def test_wrong_total_is_critical():
    report = validate(_ex(total_amount=330.00), today=TODAY)
    assert _status(report, "totals_math") == FAIL
    assert not report.can_auto_approve(0.5)


def test_line_items_must_add_up():
    items = [dict(CLEAN_INVOICE["line_items"][0]), dict(CLEAN_INVOICE["line_items"][1], amount=90.0, unit_price=90.0)]
    report = validate(_ex(line_items=items), today=TODAY)
    assert _status(report, "line_items_sum") == FAIL


def test_line_item_math():
    items = [dict(CLEAN_INVOICE["line_items"][0], quantity=3), CLEAN_INVOICE["line_items"][1]]
    assert _status(validate(_ex(line_items=items), today=TODAY), "line_item_math") == FAIL


def test_discount_and_shipping_in_totals():
    report = validate(_ex(discount_amount=30.0, shipping_amount=6.0, total_amount=300.0), today=TODAY)
    assert _status(report, "totals_math") == PASS


def test_missing_required_field_blocks_approval():
    report = validate(_ex(invoice_number=None), today=TODAY)
    assert _status(report, "required_fields") == FAIL
    assert not report.can_auto_approve(0.0)


def test_amounts_must_be_proved():
    """Without subtotal or line items nothing proves the total, so a person must look."""
    report = validate(_ex(subtotal=None, line_items=[], tax_amount=None), today=TODAY)
    assert _status(report, "totals_math") == SKIP
    assert _status(report, "line_items_sum") == SKIP
    assert not report.amounts_proved
    assert not report.can_auto_approve(0.0)


def test_line_items_prove_total_when_no_tax():
    report = validate(_ex(subtotal=None, tax_amount=None, total_amount=300.0), today=TODAY)
    assert _status(report, "line_items_sum") == PASS
    assert report.amounts_proved


def test_due_before_invoice_date():
    assert _status(validate(_ex(due_date="2026-08-01"), today=TODAY), "date_order") == FAIL


def test_future_invoice_date():
    assert _status(validate(_ex(invoice_date="2027-01-01", due_date=None), today=TODAY), "date_order") == FAIL


def test_unknown_currency():
    assert _status(validate(_ex(currency="XYZ"), today=TODAY), "currency") == FAIL


def test_model_uncertainty_lowers_score_and_field_confidence():
    ex = _ex(uncertain_fields=["total_amount"])
    report = validate(ex, today=TODAY)
    assert _status(report, "model_uncertain") == FAIL
    assert report.score < 1.0
    assert report.field_confidences(ex)["total_amount"] <= 0.3


def test_duplicate_is_critical():
    report = validate(_ex(), today=TODAY, is_duplicate=lambda ex: True)
    assert _status(report, "duplicate") == FAIL
    assert not report.can_auto_approve(0.0)


def test_not_an_invoice():
    assert not validate(_ex(is_invoice=False), today=TODAY).can_auto_approve(0.0)


def test_field_confidences():
    ex = _ex(due_date=None)
    conf = validate(ex, today=TODAY).field_confidences(ex)
    assert conf["total_amount"] == 1.0
    assert conf["due_date"] == 0.0


def test_report_json():
    data = validate(_ex(), today=TODAY).to_json()
    assert {"name", "status", "severity", "message", "fields"} <= set(data[0])


def test_self_consistent_huge_amount_does_not_auto_approve():
    ex = _ex(subtotal=1e15, tax_amount=24, total_amount=1e15 + 24, line_items=[])
    report = validate(ex, today=TODAY)
    assert _status(report, "amount_limit") == FAIL
    assert not report.can_auto_approve(0.0)


def test_org_amount_ceiling():
    assert _status(validate(_ex(), today=TODAY, max_amount=Decimal("1000")), "amount_limit") == PASS
    assert _status(validate(_ex(), today=TODAY, max_amount=Decimal("100")), "amount_limit") == FAIL


def test_nan_quantity_does_not_crash():
    items = [dict(CLEAN_INVOICE["line_items"][0], quantity="nan"), CLEAN_INVOICE["line_items"][1]]
    report = validate(_ex(line_items=items), today=TODAY)
    assert report.score > 0


def test_worth_escalating():
    assert validate(_ex(total_amount=1.0), today=TODAY).worth_escalating
    assert not validate(_ex(), today=TODAY, is_duplicate=lambda ex: True).worth_escalating


def test_new_vendor_check():
    assert _status(validate(_ex(), today=TODAY), "new_vendor") == SKIP
    assert _status(validate(_ex(), today=TODAY, is_new_vendor=lambda ex: True), "new_vendor") == FAIL
    assert _status(validate(_ex(), today=TODAY, is_new_vendor=lambda ex: False), "new_vendor") == PASS
