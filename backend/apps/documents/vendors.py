"""Vendor list: matching invoices to vendors and learning vendors from approvals."""
from typing import Optional

from apps.processing.normalize import normalize_tax_id, normalize_vendor
from apps.processing.validation import VendorRecord

from .models import ExtractedData, Vendor


def match_vendor(organization_id: int, name: str, tax_id: str = "") -> Optional[Vendor]:
    """Tax ID first (most reliable), then the name or a known alias, ignoring legal suffixes."""
    vendors = Vendor.objects.filter(organization_id=organization_id)
    key = normalize_tax_id(tax_id)
    if key:
        by_tax = vendors.filter(tax_id=key).first()
        if by_tax:
            return by_tax
    name_key = normalize_vendor(name)
    if not name_key:
        return None  # an unusable name ("—") never matches a vendor
    by_name = vendors.filter(name_key=name_key).first()
    if by_name:
        return by_name
    for vendor in vendors.exclude(aliases=[]):
        if name_key in {normalize_vendor(a) for a in vendor.aliases or []}:
            return vendor
    return None


def vendor_record(vendor: Optional[Vendor], tax_id: str = "") -> Optional[VendorRecord]:
    if vendor is None:
        return None
    matched_by = "tax_id" if tax_id and normalize_tax_id(tax_id) == vendor.tax_id else "name"
    return VendorRecord(id=vendor.id, name=vendor.name, tax_id=vendor.tax_id,
                        default_currency=vendor.default_currency, is_blocked=vendor.is_blocked, matched_by=matched_by,
                        bank_account=vendor.bank_account, bank_code=vendor.bank_code)


def learn_from_approval(data: ExtractedData) -> Optional[Vendor]:
    """
    A person approved this invoice: add its vendor to the list, or fill in what the list does
    not know yet (tax ID, currency). Existing values are never overwritten.

    Where money is paid is never learned: a bank account seen on the invoice is only stored
    as a proposal, which an owner or admin confirms on the Vendors page. Other names are not
    added automatically either (a tax ID match with a different name may be an impostor).
    """
    if not normalize_vendor(data.vendor_name):
        return None
    org_id = data.document.organization_id
    vendor = match_vendor(org_id, data.vendor_name, data.vendor_tax_id)
    if vendor is None:
        vendor, _ = Vendor.objects.get_or_create(
            organization_id=org_id, name_key=normalize_vendor(data.vendor_name),
            defaults={"name": data.vendor_name, "tax_id": data.vendor_tax_id, "default_currency": data.currency},
        )
    changed = False
    if not vendor.tax_id and data.vendor_tax_id:
        vendor.tax_id, changed = data.vendor_tax_id, True
    if not vendor.default_currency and data.currency:
        vendor.default_currency, changed = data.currency, True
    if data.bank_account and data.bank_account != vendor.bank_account and not vendor.proposed_bank_account:
        vendor.proposed_bank_account, vendor.proposed_bank_code = data.bank_account, data.bank_code
        vendor.proposed_bank_document_id = data.document_id
        changed = True
    if changed:
        vendor.save()
    if data.vendor_id != vendor.id:
        data.vendor = vendor
        data.save(update_fields=["vendor"])
    return vendor


def confirm_proposed_bank(vendor: Vendor) -> Vendor:
    vendor.bank_account, vendor.bank_code = vendor.proposed_bank_account, vendor.proposed_bank_code
    vendor.proposed_bank_account, vendor.proposed_bank_code, vendor.proposed_bank_document = "", "", None
    vendor.save()
    return vendor
