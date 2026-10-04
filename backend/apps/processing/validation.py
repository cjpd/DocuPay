"""
Confidence from checks that can be proved, not from what the model says about itself.

Each check passes, fails or is skipped (not enough data). A document can
auto-approve only when:
- no critical check fails,
- the amounts are proved (the line items or the totals add up), and
- the score is at or above the organization's threshold.
"""
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, List, Optional

from .normalize import normalize_vendor
from .schema import ISO_CURRENCIES, InvoiceExtraction

PASS, FAIL, SKIP = "pass", "fail", "skip"
CRITICAL, MAJOR, MINOR = "critical", "major", "minor"
WEIGHTS = {CRITICAL: 3.0, MAJOR: 2.0, MINOR: 1.0}

REQUIRED_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "total_amount")
# Largest amount the database can store (DecimalField max_digits=14, decimal_places=2).
MAX_STORABLE_AMOUNT = Decimal("999999999999.99")
AMOUNT_FIELDS = ("subtotal", "discount_amount", "tax_amount", "shipping_amount", "total_amount")
# Failures that a stronger model can fix by reading the document better. Others
# (duplicate, not an invoice, amount limit) are facts about the document, so a
# second model call would only cost money.
EXTRACTION_QUALITY_CHECKS = frozenset({
    "required_fields", "totals_math", "line_items_sum", "line_item_math", "date_order", "currency", "model_uncertain",
    "vendor_history", "vendor_not_self", "amounts_verified",
})
# The line items must add up to auto-approve. "Subtotal + tax = total" alone is not enough:
# a model that misses the line items (or a document whose lines do not add up) would pass it.
AMOUNT_PROOFS = ("line_items_sum",)


@dataclass
class VendorHistory:
    """What this organization has approved before from the same vendor (last 50 invoices)."""
    count: int
    currencies: frozenset
    median_total: Decimal


# A total more than HISTORY_RANGE times above or below the vendor's median needs a person.
# This catches a decimal point read in the wrong place (x10, x100). The median, not the
# min/max, so one wrong approval cannot widen the range. Needs HISTORY_MIN_COUNT invoices.
HISTORY_RANGE = Decimal("5")
HISTORY_MIN_COUNT = 3


@dataclass
class Check:
    name: str
    status: str
    severity: str
    message: str = ""
    fields: tuple = ()


@dataclass
class ValidationReport:
    checks: List[Check]
    score: float
    amounts_proved: bool

    @property
    def critical_failures(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL and c.severity == CRITICAL]

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def can_auto_approve(self, threshold: float) -> bool:
        """Fail closed: any critical or major failure blocks, whatever the score.
        (A score alone can pass with one major failure when many checks pass, e.g. 22/24 = 0.917.)"""
        blocking = [c for c in self.failures if c.severity in (CRITICAL, MAJOR)]
        return not blocking and self.amounts_proved and self.score >= threshold

    @property
    def worth_escalating(self) -> bool:
        return any(c.name in EXTRACTION_QUALITY_CHECKS for c in self.failures)

    def field_confidences(self, extraction: InvoiceExtraction) -> dict:
        """1.0 = proved by a passed check, 0.6 = present but not proved, 0.0 = missing or in a failed check."""
        proved, failed = set(), set()
        for c in self.checks:
            (proved if c.status == PASS else failed if c.status == FAIL else set()).update(c.fields)
        result = {}
        for name in ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency",
                     "subtotal", "tax_amount", "total_amount", "line_items"):
            value = getattr(extraction, name)
            if value in (None, [], ""):
                score = 0.0
            elif name in failed:
                score = 0.0
            elif name in proved:
                score = 1.0
            else:
                score = 0.6
            if name in extraction.uncertain_fields:
                score = min(score, 0.3)
            result[name] = score
        return result

    def to_json(self) -> list:
        return [asdict(c) | {"fields": list(c.fields)} for c in self.checks]


def _close(a: Decimal, b: Decimal) -> bool:
    """Equal within one cent per 1,000 of value, at least 0.02 (rounding on printed lines)."""
    tolerance = max(Decimal("0.02"), abs(b) * Decimal("0.00001"))
    return abs(a - b) <= tolerance


def check_required(ex: InvoiceExtraction) -> Check:
    missing = [f for f in REQUIRED_FIELDS if getattr(ex, f) in (None, "")]
    if missing:
        return Check("required_fields", FAIL, CRITICAL, "Missing: " + ", ".join(missing), tuple(missing))
    return Check("required_fields", PASS, CRITICAL, fields=REQUIRED_FIELDS)


def check_is_invoice(ex: InvoiceExtraction) -> Check:
    if ex.is_invoice:
        return Check("is_invoice", PASS, CRITICAL)
    return Check("is_invoice", FAIL, CRITICAL, "The document does not look like an invoice")


def check_line_items_sum(ex: InvoiceExtraction) -> Check:
    amounts = [li.amount for li in ex.line_items]
    if not amounts or any(a is None for a in amounts):
        return Check("line_items_sum", SKIP, MAJOR, "Line item amounts are missing")
    target_name = "subtotal" if ex.subtotal is not None else "total_amount"
    target = getattr(ex, target_name)
    if target is None:
        return Check("line_items_sum", SKIP, MAJOR, "No subtotal or total to compare")
    total = sum(amounts, Decimal("0"))
    if target_name == "total_amount":
        # No subtotal printed. The lines can prove the total in two ways:
        # - tax-inclusive prices, only when the document says so ("incl. VAT"). Without that
        #   statement, lines = total with tax > 0 is also what a model produces when it reports
        #   the net amount as the total, so it proves nothing;
        # - lines + tax + charges - discount = total.
        with_charges = (total - abs(ex.discount_amount or 0) + (ex.tax_amount or 0) + (ex.shipping_amount or 0))
        no_tax = not ex.tax_amount
        if _close(with_charges, target) or (_close(total, target) and (no_tax or ex.prices_include_tax)):
            return Check("line_items_sum", PASS, MAJOR, fields=("line_items", "total_amount", "tax_amount"))
        return Check("line_items_sum", FAIL, MAJOR,
                     f"Line items add up to {total} ({with_charges} with tax and charges), but the total is {target}",
                     ("line_items", "total_amount"))
    if _close(total, target):
        return Check("line_items_sum", PASS, MAJOR, fields=("line_items", target_name))
    return Check("line_items_sum", FAIL, MAJOR,
                 f"Line items add up to {total}, but {target_name} is {target}", ("line_items", target_name))


def check_line_item_math(ex: InvoiceExtraction) -> Check:
    rows = [li for li in ex.line_items if None not in (li.quantity, li.unit_price, li.amount)]
    if not rows:
        return Check("line_item_math", SKIP, MAJOR, "No line has quantity, unit price and amount")
    bad = [i for i, li in enumerate(rows, 1)
           if abs(li.quantity) > MAX_STORABLE_AMOUNT
           or not _close((li.quantity * li.unit_price).quantize(Decimal("0.01")), li.amount)]
    if bad:
        return Check("line_item_math", FAIL, MAJOR,
                     "Quantity x unit price does not equal the amount on line(s) " + ", ".join(map(str, bad)),
                     ("line_items",))
    return Check("line_item_math", PASS, MAJOR, fields=("line_items",))


def check_totals_math(ex: InvoiceExtraction) -> Check:
    if ex.subtotal is None or ex.total_amount is None:
        return Check("totals_math", SKIP, CRITICAL, "Subtotal or total is missing")
    expected = (ex.subtotal - abs(ex.discount_amount or 0) + (ex.tax_amount or 0) + (ex.shipping_amount or 0))
    fields = ("subtotal", "tax_amount", "total_amount")
    if _close(expected, ex.total_amount):
        return Check("totals_math", PASS, CRITICAL, fields=fields)
    return Check("totals_math", FAIL, CRITICAL,
                 f"Subtotal - discount + tax + shipping = {expected}, but the total is {ex.total_amount}", fields)


def check_sign(ex: InvoiceExtraction) -> Check:
    """Credit notes, refunds and zero invoices change money in the other direction (or not at all):
    a person must look at them."""
    if ex.total_amount is None:
        return Check("positive_total", SKIP, CRITICAL, "No total")
    if ex.total_amount <= 0:
        return Check("positive_total", FAIL, CRITICAL,
                     f"The total is {ex.total_amount}: credit notes and zero invoices need a person", ("total_amount",))
    return Check("positive_total", PASS, CRITICAL, fields=("total_amount",))


def check_dates(ex: InvoiceExtraction, today: date) -> Check:
    if ex.invoice_date is None:
        return Check("date_order", SKIP, MAJOR, "No invoice date")
    if ex.invoice_date > today + timedelta(days=1):
        return Check("date_order", FAIL, MAJOR, f"The invoice date {ex.invoice_date} is in the future", ("invoice_date",))
    if ex.invoice_date.year < 2000:
        return Check("date_order", FAIL, MAJOR, f"The invoice date {ex.invoice_date} is not plausible", ("invoice_date",))
    if ex.due_date and ex.due_date < ex.invoice_date:
        return Check("date_order", FAIL, MAJOR, "The due date is before the invoice date", ("invoice_date", "due_date"))
    return Check("date_order", PASS, MAJOR, fields=("invoice_date", "due_date") if ex.due_date else ("invoice_date",))


def check_currency(ex: InvoiceExtraction) -> Check:
    if ex.currency is None:
        return Check("currency", FAIL, MAJOR, "No currency", ("currency",))
    if ex.currency not in ISO_CURRENCIES:
        return Check("currency", FAIL, MAJOR, f"Unknown currency code {ex.currency}", ("currency",))
    return Check("currency", PASS, MAJOR, fields=("currency",))


def check_uncertain(ex: InvoiceExtraction) -> Check:
    if ex.uncertain_fields:
        return Check("model_uncertain", FAIL, MAJOR,
                     "The model could not read: " + ", ".join(ex.uncertain_fields), tuple(ex.uncertain_fields))
    return Check("model_uncertain", PASS, MAJOR)


def check_amount_limits(ex: InvoiceExtraction, max_amount: Optional[Decimal]) -> Check:
    too_big = [f for f in AMOUNT_FIELDS if getattr(ex, f) is not None and abs(getattr(ex, f)) > MAX_STORABLE_AMOUNT]
    too_big += [f"line_items[{i}]" for i, li in enumerate(ex.line_items, 1)
                if any(v is not None and abs(v) > MAX_STORABLE_AMOUNT for v in (li.unit_price, li.amount))]
    if too_big:
        return Check("amount_limit", FAIL, CRITICAL, "Amount out of range: " + ", ".join(too_big), tuple(too_big))
    if max_amount is not None and ex.total_amount is not None and abs(ex.total_amount) > max_amount:
        return Check("amount_limit", FAIL, CRITICAL,
                     f"The total {ex.total_amount} is above the auto-approve limit of {max_amount}", ("total_amount",))
    return Check("amount_limit", PASS, CRITICAL)


def check_duplicate(ex: InvoiceExtraction, is_duplicate: Optional[Callable[[InvoiceExtraction], bool]]) -> Check:
    if is_duplicate is None or not (ex.vendor_name and ex.invoice_number):
        return Check("duplicate", SKIP, CRITICAL, "Not enough data to look for duplicates")
    if is_duplicate(ex):
        return Check("duplicate", FAIL, CRITICAL,
                     f"Invoice {ex.invoice_number} from {ex.vendor_name} was already received",
                     ("invoice_number", "vendor_name"))
    return Check("duplicate", PASS, CRITICAL)


def check_vendor_history(ex: InvoiceExtraction, history: Optional[VendorHistory]) -> Check:
    if history is None or history.count == 0:
        return Check("vendor_history", SKIP, MAJOR, "No approved invoices from this vendor yet")
    if ex.currency and history.currencies and ex.currency not in history.currencies:
        return Check("vendor_history", FAIL, MAJOR,
                     f"This vendor has always billed in {', '.join(sorted(history.currencies))}, not {ex.currency}",
                     ("currency",))
    if (history.count >= HISTORY_MIN_COUNT and ex.total_amount is not None and ex.total_amount > 0
            and history.median_total > 0
            and not history.median_total / HISTORY_RANGE <= ex.total_amount <= history.median_total * HISTORY_RANGE):
        return Check("vendor_history", FAIL, MAJOR,
                     f"The total {ex.total_amount} is far from this vendor's usual amount ({history.median_total})",
                     ("total_amount",))
    return Check("vendor_history", PASS, MAJOR)


def check_vendor_not_self(ex: InvoiceExtraction, own_names) -> Check:
    """The vendor must not be the organization itself: that means the model read the bill-to block.
    Names are compared without legal suffixes, so "Globex" matches "Globex Corporation"."""
    names = {normalize_vendor(n) for n in (own_names or ()) if normalize_vendor(n)}
    if not names or not ex.vendor_name:
        return Check("vendor_not_self", SKIP, CRITICAL, "Organization name unknown")
    if normalize_vendor(ex.vendor_name) in names:
        return Check("vendor_not_self", FAIL, CRITICAL,
                     "The vendor name is your own company: the customer block was probably read as the vendor",
                     ("vendor_name",))
    return Check("vendor_not_self", PASS, CRITICAL)


def check_new_vendor(ex: InvoiceExtraction, is_new_vendor: Optional[Callable[[InvoiceExtraction], bool]]) -> Check:
    """Optional accounts payable control: the first invoice from a vendor is always checked by a person."""
    if is_new_vendor is None:
        return Check("new_vendor", SKIP, CRITICAL, "Rule not enabled for this organization")
    if not ex.vendor_name:
        return Check("new_vendor", SKIP, CRITICAL, "No vendor name")
    if is_new_vendor(ex):
        return Check("new_vendor", FAIL, CRITICAL,
                     f"First invoice from {ex.vendor_name}: a person must approve it", ("vendor_name",))
    return Check("new_vendor", PASS, CRITICAL)


def validate(ex: InvoiceExtraction, today: Optional[date] = None,
             is_duplicate: Optional[Callable[[InvoiceExtraction], bool]] = None,
             max_amount: Optional[Decimal] = None,
             is_new_vendor: Optional[Callable[[InvoiceExtraction], bool]] = None,
             vendor_history: Optional[Callable[[InvoiceExtraction], Optional[VendorHistory]]] = None,
             own_names: tuple = ()) -> ValidationReport:
    today = today or date.today()
    checks = [
        check_is_invoice(ex),
        check_required(ex),
        check_sign(ex),
        check_totals_math(ex),
        check_line_items_sum(ex),
        check_line_item_math(ex),
        check_dates(ex, today),
        check_currency(ex),
        check_uncertain(ex),
        check_amount_limits(ex, max_amount),
        check_duplicate(ex, is_duplicate),
        check_new_vendor(ex, is_new_vendor),
        check_vendor_history(ex, vendor_history(ex) if vendor_history and ex.vendor_name else None),
        check_vendor_not_self(ex, own_names),
    ]
    # Always give the reviewer a reason: when nothing proved the total, that is the reason.
    proved = any(c.name in AMOUNT_PROOFS and c.status == PASS for c in checks)
    if proved:
        checks.append(Check("amounts_verified", PASS, CRITICAL, fields=("total_amount",)))
    else:
        checks.append(Check("amounts_verified", FAIL, CRITICAL,
                            "No line items were found that add up to the total", ("total_amount", "line_items")))
    evaluated = [c for c in checks if c.status != SKIP]
    total = sum(WEIGHTS[c.severity] for c in evaluated)
    passed = sum(WEIGHTS[c.severity] for c in evaluated if c.status == PASS)
    score = round(passed / total, 4) if total else 0.0
    return ValidationReport(checks=checks, score=score, amounts_proved=proved)
