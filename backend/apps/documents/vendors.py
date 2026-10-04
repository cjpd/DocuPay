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
        return None
    by_name = vendors.filter(name_key=name_key).first()
    if by_name:
        return by_name
    for vendor in vendors.exclude(aliases=[]).only("id", "aliases", "name", "tax_id", "default_currency", "is_blocked"):
        if name_key in {normalize_vendor(a) for a in vendor.aliases or []}:
            return vendor
    return None


def vendor_record(vendor: Optional[Vendor], tax_id: str = "") -> Optional[VendorRecord]:
    if vendor is None:
        return None
    matched_by = "tax_id" if tax_id and normalize_tax_id(tax_id) == vendor.tax_id else "name"
    return VendorRecord(id=vendor.id, name=vendor.name, tax_id=vendor.tax_id,
                        default_currency=vendor.default_currency, is_blocked=vendor.is_blocked, matched_by=matched_by)


def learn_from_approval(data: ExtractedData) -> Optional[Vendor]:
    """
    A person approved this invoice: add its vendor to the list, or fill in what the list
    does not know yet (tax ID, currency, another name). Existing values are never
    overwritten: a different tax ID is a check failure for a person to resolve, not an update.
    """
    if not data.vendor_name:
        return None
    org_id = data.document.organization_id
    vendor = match_vendor(org_id, data.vendor_name, data.vendor_tax_id)
    if vendor is None:
        vendor, _ = Vendor.objects.get_or_create(
            organization_id=org_id, name_key=normalize_vendor(data.vendor_name),
            defaults={"name": data.vendor_name, "tax_id": data.vendor_tax_id, "default_currency": data.currency},
        )
    else:
        changed = False
        if not vendor.tax_id and data.vendor_tax_id:
            vendor.tax_id, changed = data.vendor_tax_id, True
        if not vendor.default_currency and data.currency:
            vendor.default_currency, changed = data.currency, True
        known = {vendor.name_key, *(normalize_vendor(a) for a in vendor.aliases or [])}
        if normalize_vendor(data.vendor_name) not in known:
            vendor.aliases = [*(vendor.aliases or []), data.vendor_name]
            changed = True
        if changed:
            vendor.save()
    if data.vendor_id != vendor.id:
        data.vendor = vendor
        data.save(update_fields=["vendor"])
    return vendor
