from datetime import date
from decimal import Decimal

from apps.processing.schema import InvoiceExtraction
from apps.processing.validation import FAIL, PASS, SKIP, VendorHistory, validate

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


def test_subtotal_plus_tax_alone_does_not_approve():
    """Without line items, a wrong subtotal and total that agree with each other would pass."""
    report = validate(_ex(line_items=[]), today=TODAY)
    assert _status(report, "totals_math") == PASS
    assert _status(report, "amounts_verified") == FAIL
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


def test_any_major_failure_blocks_even_with_high_score():
    """22 of 24 weight points pass = 0.917; the old gate approved this at a 0.91 threshold."""
    report = validate(_ex(due_date="2026-08-01"), today=TODAY, is_duplicate=lambda ex: False)
    assert report.score > 0.9
    assert not report.can_auto_approve(0.5)


def test_credit_note_needs_a_person():
    ex = _ex(subtotal=-300.0, tax_amount=-24.0, total_amount=-324.0,
             line_items=[{"description": "Refund", "quantity": 1, "unit_price": -300.0, "amount": -300.0}])
    report = validate(ex, today=TODAY)
    assert _status(report, "positive_total") == FAIL
    assert not report.can_auto_approve(0.0)


def test_vat_inclusive_invoice_can_prove_amounts():
    """Lines include VAT, no subtotal printed, VAT shown as 'of which'."""
    ex = _ex(subtotal=None, tax_amount=54.0, total_amount=324.0, prices_include_tax=True,
             line_items=[{"description": "A", "quantity": 1, "unit_price": 324.0, "amount": 324.0}])
    report = validate(ex, today=TODAY)
    assert _status(report, "line_items_sum") == PASS
    assert report.can_auto_approve(0.92)


def test_net_reported_as_total_does_not_pass():
    """Regression found by the Evaluator: the model drops the subtotal and reports the net (300)
    as the total. Lines = total, but the document never said prices include tax."""
    ex = _ex(subtotal=None, tax_amount=24.0, total_amount=300.0)
    report = validate(ex, today=TODAY)
    assert _status(report, "line_items_sum") == FAIL
    assert not report.can_auto_approve(0.0)


def test_qty_times_price_error_blocks():
    items = [dict(CLEAN_INVOICE["line_items"][0], quantity=3), CLEAN_INVOICE["line_items"][1]]
    assert not validate(_ex(line_items=items), today=TODAY).can_auto_approve(0.0)


def test_ambiguous_date_needs_a_person():
    ex = _ex(invoice_date="01/09/2026", due_date="2026-10-01")
    assert "invoice_date" in ex.uncertain_fields
    assert not validate(ex, today=TODAY).can_auto_approve(0.0)


HISTORY = VendorHistory(count=3, currencies=frozenset({"USD"}), median_total=Decimal("300"))


def test_vendor_history_currency_change():
    report = validate(_ex(currency="EUR"), today=TODAY, vendor_history=lambda ex: HISTORY)
    assert _status(report, "vendor_history") == FAIL
    assert report.worth_escalating


def test_vendor_history_amount_scale():
    """1,234.00 read as 123,400 adds up perfectly, so only history can catch it."""
    ex = _ex(subtotal=30000.0, tax_amount=2400.0, total_amount=32400.0,
             line_items=[{"description": "Paper A4", "quantity": 10, "unit_price": 2000.0, "amount": 20000.0},
                         {"description": "Toner", "quantity": 1, "unit_price": 10000.0, "amount": 10000.0}])
    report = validate(ex, today=TODAY, vendor_history=lambda ex: HISTORY)
    assert _status(report, "totals_math") == PASS
    assert _status(report, "vendor_history") == FAIL
    assert not report.can_auto_approve(0.0)


def test_vendor_history_normal_invoice_passes():
    report = validate(_ex(), today=TODAY, vendor_history=lambda ex: HISTORY)
    assert _status(report, "vendor_history") == PASS
    assert report.can_auto_approve(0.92)


def test_vendor_is_own_company():
    report = validate(_ex(vendor_name="Acme Supplies LLC"), today=TODAY, own_names=("ACME Supplies, LLC",))
    assert _status(report, "vendor_not_self") == FAIL
    assert _status(validate(_ex(), today=TODAY, own_names=("Buyer Corp",)), "vendor_not_self") == PASS
    # Stored without the legal suffix that the invoice prints.
    assert _status(validate(_ex(), today=TODAY, own_names=("Acme Supplies",)), "vendor_not_self") == FAIL


def test_vendor_history_catches_x10():
    ex = _ex(subtotal=3000.0, tax_amount=240.0, total_amount=3240.0,
             line_items=[{"description": "A", "quantity": 1, "unit_price": 3000.0, "amount": 3000.0}])
    assert _status(validate(ex, today=TODAY, vendor_history=lambda e: HISTORY), "vendor_history") == FAIL


def test_vendor_history_needs_three_invoices_for_amounts():
    few = VendorHistory(count=2, currencies=frozenset({"USD"}), median_total=Decimal("30"))
    assert _status(validate(_ex(), today=TODAY, vendor_history=lambda e: few), "vendor_history") == PASS


def test_review_always_has_a_reason():
    """A document that cannot auto-approve always shows at least one failed check to the reviewer."""
    cases = [
        _ex(subtotal=None, line_items=[], tax_amount=None),  # nothing proves the total
        _ex(subtotal=None, tax_amount=24.0, total_amount=300.0),
        _ex(invoice_number=None),
        _ex(currency=None),
    ]
    for ex in cases:
        report = validate(ex, today=TODAY)
        assert not report.can_auto_approve(0.92)
        assert report.failures, ex
