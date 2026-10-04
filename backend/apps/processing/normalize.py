"""Name and number normalization shared by duplicate detection and vendor checks."""
import re

_LEGAL_SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|ag|sa|sas|sarl|srl|"
    r"spa|bv|nv|plc|pty|oy|ab|as|kg)\b\.?", re.IGNORECASE)
_INVOICE_PREFIX = re.compile(r"^(invoice|inv|bill|no|nr|num|number|#)+", re.IGNORECASE)


def normalize_vendor(name: str) -> str:
    """"ACME Supplies, L.L.C." and "Acme Supplies" give the same key."""
    name = _LEGAL_SUFFIXES.sub(" ", (name or "").lower())
    return re.sub(r"[^0-9a-z]", "", name)


def normalize_invoice_number(number: str) -> str:
    """"INV-001", "inv 1", "#0001" and "INV-OO1" (letter O read for zero) give the same key."""
    text = re.sub(r"[^0-9a-z]", "", (number or "").lower())
    text = _INVOICE_PREFIX.sub("", text) or text
    text = text.replace("o", "0") if re.fullmatch(r"[0-9o]+", text) else text
    return text.lstrip("0") or "0"


def normalize_tax_id(tax_id: str) -> str:
    """"DE 811 234 567" and "de811234567" give the same key."""
    return re.sub(r"[^0-9A-Z]", "", (tax_id or "").upper())


def normalize_bank(value) -> str:
    """"DE89 3704 0044 0532 0130 00" -> "DE89370400440532013000"."""
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def looks_like_iban(account: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", account or ""))


def iban_is_valid(iban: str) -> bool:
    """ISO 13616 mod-97 checksum: catches almost every misread or mistyped character."""
    if not looks_like_iban(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def mask_account(account: str) -> str:
    return f"…{account[-4:]}" if account and len(account) > 4 else (account or "")
