import re
from typing import Dict

# Each doc type maps to a list of (pattern, weight) tuples.
# Higher weight = stronger signal for that type.
_SIGNALS: Dict[str, list] = {
    "invoice": [
        (r"\binvoice\b", 3),
        (r"\binvoice\s*(no|number|#)", 4),
        (r"\bbill\s*to\b", 3),
        (r"\bdue\s*date\b", 2),
        (r"\bamount\s*due\b", 3),
        (r"\btotal\s*due\b", 3),
        (r"\bvendor\b", 2),
        (r"\bpayment\s*terms\b", 2),
        (r"\bsubtotal\b", 1),
    ],
    "receipt": [
        (r"\breceipt\b", 4),
        (r"\btransaction\b", 2),
        (r"\bpayment\s*received\b", 3),
        (r"\bchange\s*due\b", 3),
        (r"\bcash\b", 1),
        (r"\bthank\s*you\s*for\s*your\s*(purchase|business)\b", 4),
        (r"\bcard\s*(number|ending)\b", 2),
    ],
    "purchase_order": [
        (r"\bpurchase\s*order\b", 5),
        (r"\bp\.?\s*o\.?\s*(#|number)\b", 5),
        (r"\bship\s*to\b", 3),
        (r"\border\s*date\b", 2),
        (r"\border\s*number\b", 3),
        (r"\bdelivery\s*date\b", 2),
        (r"\bordered\s*by\b", 2),
        (r"\brequisition\b", 3),
    ],
    "contract": [
        (r"\bagreement\b", 3),
        (r"\bcontract\b", 3),
        (r"\bhereby\b", 3),
        (r"\bwhereas\b", 4),
        (r"\bterms\s*and\s*conditions\b", 3),
        (r"\bshall\b", 2),
        (r"\bhereinafter\b", 4),
        (r"\bjurisdiction\b", 3),
        (r"\bindemnif", 3),
        (r"\bliabilit", 2),
    ],
    "resume": [
        (r"\bresume\b", 5),
        (r"\bcurriculum\s*vitae\b", 5),
        (r"\b(work\s*)?experience\b", 2),
        (r"\beducation\b", 2),
        (r"\bskills\b", 2),
        (r"\bemployment\b", 2),
        (r"\breferences\b", 2),
        (r"\bobjective\b", 2),
        (r"\bqualifications\b", 2),
    ],
}

_MIN_SCORE = 3  # below this threshold we fall back to "invoice"


def classify_document(text: str) -> str:
    if not text or not text.strip():
        return "invoice"

    scores: Dict[str, int] = {doc_type: 0 for doc_type in _SIGNALS}
    for doc_type, signals in _SIGNALS.items():
        for pattern, weight in signals:
            if re.search(pattern, text, re.IGNORECASE):
                scores[doc_type] += weight

    best_type = max(scores, key=lambda t: scores[t])
    return best_type if scores[best_type] >= _MIN_SCORE else "invoice"
