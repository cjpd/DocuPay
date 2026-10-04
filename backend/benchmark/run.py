"""
DocuPay offline invoice benchmark.

    backend/.venv/bin/python backend/benchmark/generate.py      # once (deterministic)
    backend/.venv/bin/python backend/benchmark/run.py           # heuristic + oracle + corruptions + ingest
    backend/.venv/bin/python backend/benchmark/run.py --provider anthropic   # real LLM run (needs ANTHROPIC_API_KEY)

Sections (all in results.json):
  ingest      how each file is routed (pdf_text / pdf_scan / image / text) or rejected, and content loss
  extractor   end-to-end pipeline with a real extractor (heuristic offline; anthropic/openai with a key):
              field-level accuracy vs ground truth, decisions, wrong-but-approved
  oracle      ground truth fed through FakeProvider: STP rate on clean cases, false-approve on negatives
  corruption  ground truth with typical LLM errors injected: false-approve rate per error type
  escalation  fast model wrong, strong model right: does the pipeline recover?
  threshold   STP / false-approve as a function of the auto-approve threshold
"""
import argparse
import copy
import inspect
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
REPO = BACKEND.parent
CASES = HERE / "cases"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test")

import django  # noqa: E402

django.setup()

from apps.processing import ingest  # noqa: E402
from apps.processing.errors import ProcessingError  # noqa: E402
from apps.processing.ingest import DocumentInput  # noqa: E402
from apps.processing.pipeline import AUTO_APPROVE, run_pipeline  # noqa: E402
from apps.processing.providers.fake import FakeProvider  # noqa: E402
from apps.processing.schema import InvoiceExtraction  # noqa: E402

THRESHOLD = 0.92  # Organization.auto_approve_threshold default
SCALAR_FIELDS = (
    "is_invoice", "vendor_name", "vendor_tax_id", "customer_name", "invoice_number", "purchase_order",
    "invoice_date", "due_date", "currency", "subtotal", "discount_amount", "tax_amount",
    "shipping_amount", "total_amount",
)
CORE_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "total_amount")
MONEY = {"subtotal", "discount_amount", "tax_amount", "shipping_amount", "total_amount"}
_PIPELINE_PARAMS = set(inspect.signature(run_pipeline).parameters)


# ---------------------------------------------------------------- helpers

class LocalFile:
    """Minimal stand-in for Django's FieldFile (open/read/close), as ingest.read_file_bytes expects."""

    def __init__(self, name, data=None, path=None):
        self.name, self._data, self._path, self._fh = name, data, path, None

    def __bool__(self):
        return True

    def open(self, mode="rb"):
        import io
        self._fh = open(self._path, mode) if self._path else io.BytesIO(self._data)

    def read(self, n=-1):
        return self._fh.read(n)

    def close(self):
        self._fh.close()


def manifest():
    return json.loads((CASES / "manifest.json").read_text())


def load_gt(cid):
    return json.loads((CASES / f"{cid}.gt.json").read_text())


def norm_str(v):
    if v is None:
        return None
    s = " ".join(str(v).split()).casefold().rstrip(".,;:")
    return s or None


def norm(field, v):
    if v in (None, ""):
        return None
    if field in MONEY:
        d = Decimal(str(v))
        return abs(d) if field == "discount_amount" else d  # sign convention for discounts is not fixed
    if field == "is_invoice":
        return bool(v)
    if field in ("invoice_date", "due_date", "currency"):
        return str(v)
    return norm_str(v)


def items_key(items, with_qty=True):
    out = []
    for li in items or []:
        amt = Decimal(str(li["amount"])) if li.get("amount") is not None else None
        qty = Decimal(str(li["quantity"])).normalize() if with_qty and li.get("quantity") is not None else None
        out.append((amt, qty))
    return out


def compare(gt: dict, pred: dict) -> dict:
    """Per-field outcome: 'correct' | 'wrong' | 'missed' (gt has value, pred null) | 'spurious' | 'na' (both null)."""
    res = {}
    for f in SCALAR_FIELDS:
        g, p = norm(f, gt.get(f)), norm(f, pred.get(f))
        if g is None:
            res[f] = "na" if p is None else "spurious"
        elif p is None:
            res[f] = "missed"
        else:
            res[f] = "correct" if g == p else "wrong"
    g_items, p_items = gt.get("line_items") or [], pred.get("line_items") or []
    if not g_items:
        res["line_items"] = "na" if not p_items else "spurious"
        res["line_item_count"] = res["line_items"]
    else:
        res["line_item_count"] = ("correct" if len(g_items) == len(p_items) else "wrong") if p_items else "missed"
        res["line_items"] = ("correct" if items_key(g_items) == items_key(p_items) else "wrong") if p_items else "missed"
    return res


def pipeline(doc, provider, threshold=THRESHOLD, is_duplicate=None, escalate=None, **extra):
    kw = {"threshold": threshold, "is_duplicate": is_duplicate, **extra}
    if escalate is not None:
        kw["escalate"] = escalate
    return run_pipeline(doc, provider, **{k: v for k, v in kw.items() if k in _PIPELINE_PARAMS})


try:  # the production key (tasks._duplicate_checker matches ExtractedData.dedupe_key on it)
    from apps.processing.tasks import dedupe_key as _prod_key

    def dup_key(vendor, number):
        return _prod_key(1, vendor, number)
    DUP_KEY_SOURCE = "apps.processing.tasks.dedupe_key"
except ImportError:  # older code: vendor_name__iexact + invoice_number__iexact
    def dup_key(vendor, number):
        return f"{vendor.casefold()}:{number.casefold()}" if vendor and number else ""
    DUP_KEY_SOURCE = "iexact fallback"


class Registry:
    """Mirrors tasks._duplicate_checker: an earlier document of the org has the same dedupe key."""

    def __init__(self):
        self.seen = []

    def add(self, cid, ex):
        key = dup_key(ex.vendor_name, ex.invoice_number)
        if key:
            self.seen.append((cid, key))

    def checker(self, cid, exclude=()):
        def is_duplicate(ex):
            key = dup_key(ex.vendor_name, ex.invoice_number)
            return bool(key) and any(k == key for c, k in self.seen if c != cid and c not in exclude)
        return is_duplicate


def run_duplicate_variants(cases):
    """A resubmitted invoice is often extracted slightly differently. Which variants does dedupe catch?"""
    base = load_gt("clean_us_classic_iso")
    v, n = base["vendor_name"], base["invoice_number"]
    digits = "".join(ch for ch in n if ch.isdigit())
    variants = {
        "exact": (v, n),
        "upper_case": (v.upper(), n.lower()),
        "number_space_not_dash": (v, n.replace("-", " ")),
        "vendor_without_legal_suffix": (v.replace(" Inc.", ""), n),
        "number_without_prefix": (v, digits),
        "number_with_hash": (v, "#" + n),
        "vendor_abbreviated": (v.replace("Industrial", "Ind."), n),
        "ocr_O_for_0": (v, n.replace("0", "O")),
    }
    registry = Registry()
    registry.add("first", InvoiceExtraction.model_validate(base))
    out = {}
    for name, (vv, nn) in variants.items():
        ex = InvoiceExtraction.model_validate(dict(base, vendor_name=vv, invoice_number=nn))
        res = pipeline(DUMMY_DOC, FakeProvider(fast=ex), is_duplicate=registry.checker("second"), escalate=False)
        out[name] = {"vendor": vv, "number": nn, "caught": "duplicate" in failed_checks(res.report),
                     "decision": res.decision}
    return {"key_source": DUP_KEY_SOURCE, "variants": out,
            "caught": f"{sum(x['caught'] for x in out.values())}/{len(out)}"}


def failed_checks(report):
    return [c.name for c in report.failures]


DUMMY_DOC = DocumentInput(source="text", page_count=1, text="(oracle run: content unused)")


# ---------------------------------------------------------------- 1. ingest

def run_ingest(cases):
    rows = []
    for m in cases:
        path = CASES / m["file"]
        row = {"id": m["id"], "file": m["file"], "category": m["category"], "expected_route": m.get("expected")}
        t0 = time.perf_counter()
        try:
            doc = ingest.load_document(LocalFile(m["file"], path=path))
            row.update(route=doc.source, pages=doc.page_count, text_chars=len(doc.text or ""),
                       images=len(doc.images), image_bytes=sum(len(i.data) for i in doc.images), error=None)
            row["content_loss"] = content_loss(path, doc)
        except ProcessingError as exc:
            row.update(route="error", error=f"{type(exc).__name__}: {exc}", content_loss=None)
        except Exception as exc:  # an unhandled crash is a bug: a task would retry forever or 500
            row.update(route="crash", error=f"{type(exc).__name__}: {exc}", content_loss=None)
        row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        rows.append(row)
    rows += run_real_samples()
    by_route = Counter(r["route"] for r in rows)
    losses = [r for r in rows if r.get("content_loss")]
    ingest_only = [r for r in rows if r["category"] == "ingest_only"]
    io_ok = sum(1 for r in ingest_only if (r["route"] == "error") == (r["expected_route"] == "permanent_error")
                and (r["expected_route"] == "permanent_error" or r["route"] == r["expected_route"]))
    return {
        "rows": rows,
        "by_route": dict(by_route),
        "crashes": [r for r in rows if r["route"] == "crash"],
        "content_loss": [{"id": r["id"], "detail": r["content_loss"]} for r in losses],
        "ingest_only_as_expected": f"{io_ok}/{len(ingest_only)}",
    }


def content_loss(path, doc):
    """Pages present in the file that the model will never see."""
    import pypdfium2 as pdfium
    from PIL import Image

    data = path.read_bytes()
    if data.startswith(b"%PDF") and doc.source == "pdf_text":
        pdf = pdfium.PdfDocument(data)
        try:
            empty = []
            for i in range(len(pdf)):
                tp = pdf[i].get_textpage()
                if len((tp.get_text_bounded() or "").strip()) < ingest.MIN_TEXT_CHARS_PER_PAGE:
                    empty.append(i + 1)
                tp.close()
        finally:
            pdf.close()
        if empty:
            return f"page(s) {empty} have no text layer and were not sent as images"
    if doc.source == "image":
        try:
            frames = getattr(Image.open(path), "n_frames", 1)
        except Exception:
            frames = 1
        if frames > doc.page_count:
            return f"{frames}-frame image, only {doc.page_count} page(s) sent"
        try:
            exif = Image.open(path).getexif().get(0x0112, 1)
        except Exception:
            exif = 1
        if exif in (5, 6, 7, 8):  # 90-degree rotations: an upright result swaps width and height
            import io
            src_w, src_h = Image.open(path).size
            out_w, out_h = Image.open(io.BytesIO(doc.images[0].data)).size
            if (src_w > src_h) == (out_w > out_h):
                return f"EXIF orientation {exif} ignored: page sent rotated 90 degrees"
    return None


def run_real_samples():
    rows = []
    for name in ("invoice_1.jpg", "invoice_2.jpg", "text1.txt", "text2.txt"):
        try:
            data = subprocess.run(["git", "-C", str(REPO), "show", f"af53d92:backend/media/documents/{name}"],
                                  capture_output=True, check=True).stdout
        except Exception:
            continue
        row = {"id": f"real_{name}", "file": f"git:af53d92/{name}", "category": "real_sample", "expected_route": None}
        try:
            doc = ingest.load_document(LocalFile(name, data=data))
        except ProcessingError as exc:
            row.update(route="error", error=f"{type(exc).__name__}: {exc}", content_loss=None)
            rows.append(row)
            continue
        row.update(route=doc.source, pages=doc.page_count, text_chars=len(doc.text or ""), images=len(doc.images),
                   image_bytes=sum(len(i.data) for i in doc.images), error=None, content_loss=None)
        try:
            from apps.processing.providers.heuristic import HeuristicProvider
            res = pipeline(doc, HeuristicProvider())
            ex = res.extraction
            row.update(heuristic={"decision": res.decision, "score": res.report.score,
                                  "vendor": ex.vendor_name, "number": ex.invoice_number,
                                  "total": str(ex.total_amount) if ex.total_amount is not None else None,
                                  "failed": failed_checks(res.report)})
        except ProcessingError as exc:
            row["heuristic"] = {"decision": "failed", "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            row.update(route="crash", error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
    return rows


# ---------------------------------------------------------------- 2. real extractor end to end

def run_extractor(cases, provider_name):
    from apps.processing.providers import get_provider

    provider = get_provider(provider_name)
    registry = Registry()
    rows = []
    for m in cases:
        gt = load_gt(m["id"])
        row = {"id": m["id"], "category": m["category"], "expected": m["expected"], "tags": m["tags"]}
        try:
            doc = ingest.load_document(LocalFile(m["file"], path=CASES / m["file"]))
            res = pipeline(doc, provider, is_duplicate=registry.checker(m["id"]))
            pred = res.extraction.to_json()
            registry.add(m["id"], res.extraction)
            row.update(decision=res.decision, score=res.report.score, amounts_proved=res.report.amounts_proved,
                       failed=failed_checks(res.report), fields=compare(gt, pred), error=None,
                       cost_usd=str(res.total_cost) if res.total_cost is not None else None,
                       escalated=len(res.attempts) > 1)
        except Exception as exc:  # ProcessingError = the document fails; anything else is a crash (a bug)
            kind = "failed" if isinstance(exc, ProcessingError) else "crash"
            row.update(decision=kind, error=f"{type(exc).__name__}: {exc}",
                       fields={f: ("missed" if v != "na" else "na") for f, v in compare(gt, {}).items()})
        row["core_correct"] = all(row["fields"].get(f) in ("correct", "na") for f in CORE_FIELDS)
        rows.append(row)

    def accuracy(subset):
        per = {}
        for f in list(SCALAR_FIELDS) + ["line_item_count", "line_items"]:
            outcomes = Counter(r["fields"][f] for r in subset)
            denom = outcomes["correct"] + outcomes["wrong"] + outcomes["missed"]
            per[f] = {"accuracy": round(outcomes["correct"] / denom, 3) if denom else None, "n": denom,
                      "wrong": outcomes["wrong"], "missed": outcomes["missed"], "spurious": outcomes["spurious"]}
        scored = [v for v in per.values() if v["accuracy"] is not None]
        tot_c = sum(round(v["accuracy"] * v["n"]) for v in scored)
        tot_n = sum(v["n"] for v in scored)
        return per, round(tot_c / tot_n, 3) if tot_n else None

    invoices = [r for r in rows if load_gt(r["id"]).get("is_invoice")]
    per_all, micro_all = accuracy(invoices)
    digital = [r for r in invoices if not set(r["tags"]) & {"image", "scanned_pdf", "mixed_pdf"}]
    per_dig, micro_dig = accuracy(digital)
    scans = [r for r in invoices if set(r["tags"]) & {"image", "scanned_pdf", "mixed_pdf"}]
    _, micro_scan = accuracy(scans)
    core_digital = [round(sum(1 for r in digital if r["fields"][f] == "correct") / len(digital), 3) for f in CORE_FIELDS]

    clean = [r for r in rows if r["expected"] == "auto_approve"]
    negative = [r for r in rows if r["expected"] == "review"]
    approved = [r for r in rows if r.get("decision") == AUTO_APPROVE]
    return {
        "provider": provider_name,
        "rows": rows,
        "field_accuracy_all_invoices": per_all,
        "micro_accuracy_all_invoices": micro_all,
        "field_accuracy_digital": per_dig,
        "micro_accuracy_digital": micro_dig,
        "micro_accuracy_scans": micro_scan,
        "core_field_accuracy_digital": dict(zip(CORE_FIELDS, core_digital)),
        "docs_all_core_fields_correct": f"{sum(r['core_correct'] for r in invoices)}/{len(invoices)}",
        "stp_rate_clean": rate(sum(r.get("decision") == AUTO_APPROVE for r in clean), len(clean)),
        "false_approve_negatives": rate(sum(r.get("decision") == AUTO_APPROVE for r in negative), len(negative)),
        "wrong_but_approved": {r["id"]: [f for f in CORE_FIELDS if r["fields"][f] not in ("correct", "na")]
                               for r in approved if not r["core_correct"]},
        "wrong_but_approved_ignoring_vendor_name": [
            r["id"] for r in approved
            if any(r["fields"][f] not in ("correct", "na") for f in CORE_FIELDS if f != "vendor_name")],
        "failed_docs": [r["id"] for r in rows if r.get("decision") in ("failed", "crash")],
        "escalated_docs": sum(1 for r in rows if r.get("escalated")),
        "total_cost_usd": str(sum((Decimal(r["cost_usd"]) for r in rows if r.get("cost_usd")), Decimal("0"))),
    }


def rate(k, n):
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None}


# ---------------------------------------------------------------- 3. oracle + 4. corruptions

def run_oracle(cases, threshold=THRESHOLD):
    registry = Registry()
    rows = []
    for m in cases:
        gt = load_gt(m["id"])
        res = pipeline(DUMMY_DOC, FakeProvider(fast=gt, strong=gt), threshold=threshold,
                       is_duplicate=registry.checker(m["id"]))
        registry.add(m["id"], res.extraction)
        rows.append({"id": m["id"], "category": m["category"], "expected": m["expected"], "decision": res.decision,
                     "score": res.report.score, "amounts_proved": res.report.amounts_proved,
                     "failed": failed_checks(res.report), "correct": res.decision == m["expected"]})
    clean = [r for r in rows if r["expected"] == "auto_approve"]
    neg = [r for r in rows if r["expected"] == "review"]
    return {
        "rows": rows,
        "stp_rate_clean": rate(sum(r["decision"] == AUTO_APPROVE for r in clean), len(clean)),
        "false_approve_negatives": rate(sum(r["decision"] == AUTO_APPROVE for r in neg), len(neg)),
        "missed_negatives": [r["id"] for r in neg if r["decision"] == AUTO_APPROVE],
        "blocked_clean": [{"id": r["id"], "failed": r["failed"], "score": r["score"]}
                          for r in clean if r["decision"] != AUTO_APPROVE],
    }


def _money_fields(d):
    return [f for f in MONEY if d.get(f) is not None]


def _swap_dm(s):
    y, mo, da = s.split("-")
    if int(da) > 12 or da == mo:
        return None
    return f"{y}-{da}-{mo}"


def corruptions(gt):
    """Yield (name, corrupted_dict, arithmetic_detectable). Each mimics a common LLM/OCR extraction error."""
    out = []

    def mk(name, fn, detectable):
        d = copy.deepcopy(gt)
        if fn(d) is not False:
            out.append((name, d, detectable))

    def wrong_total(d):
        d["total_amount"] = str(Decimal(d["total_amount"]) + (Decimal(d["total_amount"]) * Decimal("0.1")).quantize(Decimal("1")) + 1)
    mk("wrong_total", wrong_total, True)

    def total_is_subtotal(d):
        if d.get("subtotal") is None or Decimal(d["subtotal"]) == Decimal(d["total_amount"]):
            return False
        d["total_amount"] = d["subtotal"]
    mk("total_is_subtotal", total_is_subtotal, True)

    def dropped_line(d):
        if len(d["line_items"]) < 2:
            return False
        d["line_items"].pop()
    mk("dropped_line_item", dropped_line, True)

    def dropped_all_lines(d):
        if not d["line_items"]:
            return False
        d["line_items"] = []
    mk("no_line_items_extracted", dropped_all_lines, False)

    def line_misread(d):
        if not d["line_items"]:
            return False
        li = d["line_items"][0]
        li["amount"] = str(Decimal(li["amount"]) + Decimal("9.00"))
    mk("line_amount_misread", line_misread, True)

    def swapped(d):
        a = _swap_dm(d["invoice_date"]) if d.get("invoice_date") else None
        if not a:
            return False
        d["invoice_date"] = a
        if d.get("due_date") and _swap_dm(d["due_date"]):
            d["due_date"] = _swap_dm(d["due_date"])
    mk("swapped_day_month", swapped, False)

    def wrong_currency(d):
        d["currency"] = {"USD": "EUR", "EUR": "USD", "GBP": "EUR"}.get(d["currency"], "USD")
    mk("wrong_currency", wrong_currency, False)

    def missed_tax(d):
        if not d.get("tax_amount") or Decimal(d["tax_amount"]) == 0:
            return False
        d["tax_amount"] = None
    mk("missed_tax", missed_tax, True)

    def missed_discount(d):
        if not d.get("discount_amount"):
            return False
        d["discount_amount"] = None
    mk("missed_discount", missed_discount, True)

    def wrong_number(d):
        n = d.get("invoice_number")
        if not n:
            return False
        last = n[-1]
        d["invoice_number"] = n[:-1] + (str((int(last) + 1) % 10) if last.isdigit() else "0")
    mk("wrong_invoice_number", wrong_number, False)

    def vendor_is_customer(d):
        if not d.get("customer_name"):
            return False
        d["vendor_name"] = d["customer_name"]
    mk("vendor_is_customer", vendor_is_customer, False)

    def scale(d):  # decimal separator lost everywhere (1.234,56 read as 123456)
        for f in _money_fields(d):
            d[f] = str(Decimal(d[f]) * 100)
        for li in d["line_items"]:
            for k in ("unit_price", "amount"):
                if li.get(k) is not None:
                    li[k] = str(Decimal(li[k]) * 100)
    mk("consistent_x100_scale", scale, False)

    def scale10(d):  # one decimal place lost everywhere (123.40 read as 1234.0)
        for f in _money_fields(d):
            d[f] = str(Decimal(d[f]) * 10)
        for li in d["line_items"]:
            for k in ("unit_price", "amount"):
                if li.get(k) is not None:
                    li[k] = str(Decimal(li[k]) * 10)
    mk("consistent_x10_scale", scale10, False)

    def wrong_tax_id(d):  # one digit misread, or a fraudster's own tax ID on a known vendor's invoice
        if not d.get("vendor_tax_id"):
            return False
        t = d["vendor_tax_id"]
        d["vendor_tax_id"] = t[:-1] + ("0" if t[-1] != "0" else "1")
    mk("wrong_vendor_tax_id", wrong_tax_id, False)

    def net_as_total(d):  # subtotal dropped and the net amount reported as the total
        if not d.get("subtotal") or not d.get("tax_amount") or Decimal(d["tax_amount"]) == 0:
            return False
        d["total_amount"] = d["subtotal"]
        d["subtotal"] = None
    mk("net_reported_as_total", net_as_total, True)

    def uncertain(d):
        d["uncertain_fields"] = ["total_amount"]
    mk("model_flags_uncertain", uncertain, True)
    return out


def steady_state_context(gt: dict) -> dict:
    """An organization that already approved 3 invoices from this vendor, in the same currency,
    with a median total between 0.5x and 2x of this invoice's true total (deterministic per
    vendor, so history is not centered on the right answer), and whose own name is stored
    without the legal suffix printed on the invoice ("Globex", printed "Globex Corporation")."""
    import random
    import re as _re
    from decimal import Decimal

    from apps.processing.validation import VendorHistory

    total = Decimal(str(gt.get("total_amount") or 0))
    factor = Decimal(str(round(random.Random(gt.get("vendor_name") or "").uniform(0.5, 2.0), 3)))
    history = VendorHistory(count=3, currencies=frozenset({gt["currency"]}) if gt.get("currency") else frozenset(),
                            median_total=(total * factor).quantize(Decimal("0.01")))
    ctx = {}
    if "vendor_history" in _PIPELINE_PARAMS and total > 0:
        ctx["vendor_history"] = lambda ex: history
    if "vendor_lookup" in _PIPELINE_PARAMS and gt.get("vendor_name"):
        from apps.processing.normalize import normalize_tax_id
        from apps.processing.validation import VendorRecord

        record = VendorRecord(id=1, name=gt["vendor_name"], tax_id=normalize_tax_id(gt.get("vendor_tax_id") or ""),
                              default_currency=gt.get("currency") or "")
        ctx["vendor_lookup"] = lambda ex: record
    if "own_names" in _PIPELINE_PARAMS and gt.get("customer_name"):
        short = _re.sub(r"[,.]?\s+(Inc|LLC|Ltd|Corporation|Corp|GmbH|SA|BV|AG|Co)\.?$", "", gt["customer_name"])
        ctx["own_names"] = (short,)
    return ctx


def run_corruptions(cases, threshold=THRESHOLD, steady_state=False):
    clean = [m for m in cases if m["expected"] == "auto_approve"]
    registry = Registry()
    for m in clean:
        registry.add(m["id"], InvoiceExtraction.model_validate(load_gt(m["id"])))
    by_type = defaultdict(lambda: {"n": 0, "approved": 0, "caught_by": Counter(), "detectable": None, "approved_cases": []})
    escalation = Counter()
    for m in clean:
        ctx = steady_state_context(load_gt(m["id"])) if steady_state else {}
        for name, bad, detectable in corruptions(load_gt(m["id"])):
            res = pipeline(DUMMY_DOC, FakeProvider(fast=bad), threshold=threshold,
                           is_duplicate=registry.checker(m["id"]), escalate=False, **ctx)
            t = by_type[name]
            t["n"] += 1
            t["detectable"] = detectable
            if res.decision == AUTO_APPROVE:
                t["approved"] += 1
                t["approved_cases"].append(m["id"])
            for c in failed_checks(res.report):
                t["caught_by"][c] += 1
            # escalation: fast wrong, strong correct
            gt = load_gt(m["id"])
            prov = FakeProvider(fast=bad, strong=gt)
            res2 = pipeline(DUMMY_DOC, prov, threshold=threshold, is_duplicate=registry.checker(m["id"]), escalate=True, **ctx)
            escalated = "strong" in prov.calls
            final_ok = res2.extraction.to_json() == InvoiceExtraction.model_validate(gt).to_json()
            escalation["cases"] += 1
            escalation["escalated"] += escalated
            escalation["recovered_auto_approved_correct"] += (res2.decision == AUTO_APPROVE and final_ok)
            escalation["approved_wrong_value"] += (res2.decision == AUTO_APPROVE and not final_ok)
    types = {}
    for name, t in by_type.items():
        types[name] = {"n": t["n"], "false_approved": t["approved"], "false_approve_rate": round(t["approved"] / t["n"], 3),
                       "arithmetic_detectable": t["detectable"], "caught_by": dict(t["caught_by"].most_common()),
                       "approved_cases": t["approved_cases"]}
    total_n = sum(t["n"] for t in types.values())
    total_a = sum(t["false_approved"] for t in types.values())
    det = [t for t in types.values() if t["arithmetic_detectable"]]
    undet = [t for t in types.values() if not t["arithmetic_detectable"]]
    return {
        "types": types,
        "overall_false_approve": rate(total_a, total_n),
        "arithmetic_detectable_false_approve": rate(sum(t["false_approved"] for t in det), sum(t["n"] for t in det)),
        "not_arithmetic_detectable_false_approve": rate(sum(t["false_approved"] for t in undet), sum(t["n"] for t in undet)),
        "escalation": dict(escalation),
    }


def run_threshold_sweep(cases):
    out = []
    for th in (0.80, 0.85, 0.90, 0.91, 0.92, 0.95, 0.99, 1.0):
        o = run_oracle(cases, th)
        c = run_corruptions(cases, th)
        out.append({"threshold": th, "oracle_stp": o["stp_rate_clean"]["rate"],
                    "oracle_false_approve_negatives": o["false_approve_negatives"]["rate"],
                    "corruption_false_approve": c["overall_false_approve"]["rate"],
                    "detectable_corruption_false_approve": c["arithmetic_detectable_false_approve"]["rate"]})
    return out


# ---------------------------------------------------------------- report

def pct(r):
    return "n/a" if r is None or r.get("rate") is None else f"{r['rate'] * 100:.1f}% ({r['k']}/{r['n']})"


def summary(res):
    lines = []
    ing = res["ingest"]
    lines.append(f"INGEST  routes={ing['by_route']}  crashes={len(ing['crashes'])}  ingest-only as expected={ing['ingest_only_as_expected']}")
    for loss in ing["content_loss"]:
        lines.append(f"  content loss: {loss['id']}: {loss['detail']}")
    for r in ing["rows"]:
        if r["category"] in ("ingest_only", "real_sample"):
            lines.append(f"  {r['id']:<34} -> {r['route']:<9} {r.get('error') or ''} {r.get('heuristic') or ''}")
    for key, ex in res["extractor"].items():
        lines.append(f"EXTRACTOR [{key}]  micro field accuracy: all invoices {ex['micro_accuracy_all_invoices']}, "
                     f"digital/text {ex['micro_accuracy_digital']}, scans {ex['micro_accuracy_scans']}")
        lines.append("  core fields (digital): " + ", ".join(f"{k}={v}" for k, v in ex["core_field_accuracy_digital"].items()))
        lines.append(f"  docs with all core fields right: {ex['docs_all_core_fields_correct']}")
        lines.append(f"  STP clean: {pct(ex['stp_rate_clean'])}   false-approve negatives: {pct(ex['false_approve_negatives'])}"
                     f"   failed: {ex['failed_docs']}   escalated: {ex['escalated_docs']}   cost: ${ex['total_cost_usd']}")
        lines.append(f"  wrong-but-approved (core fields wrong): {ex['wrong_but_approved']}")
        lines.append(f"  wrong-but-approved ignoring vendor_name: {ex['wrong_but_approved_ignoring_vendor_name']}")
        lines.append("  per field (digital): " + ", ".join(
            f"{f}={v['accuracy']}" for f, v in ex["field_accuracy_digital"].items()))
    o = res["oracle"]
    lines.append(f"ORACLE (perfect extraction)  STP clean: {pct(o['stp_rate_clean'])}   false-approve negatives: "
                 f"{pct(o['false_approve_negatives'])}   missed negatives: {o['missed_negatives']}")
    for b in o["blocked_clean"]:
        lines.append(f"  clean case blocked: {b}")
    c = res["corruption"]
    lines.append(f"CORRUPTIONS  overall false-approve {pct(c['overall_false_approve'])}; arithmetic-detectable "
                 f"{pct(c['arithmetic_detectable_false_approve'])}; not detectable by arithmetic {pct(c['not_arithmetic_detectable_false_approve'])}")
    for name, t in sorted(c["types"].items(), key=lambda kv: -kv[1]["false_approve_rate"]):
        lines.append(f"  {name:<26} n={t['n']:<3} false-approve={t['false_approve_rate']:<5} caught_by={t['caught_by']}")
    lines.append(f"ESCALATION (fast wrong, strong right): {c['escalation']}")
    if "corruption_steady_state" in res:
        c = res["corruption_steady_state"]
        lines.append(f"CORRUPTIONS, STEADY STATE (3 approved invoices per vendor, median 0.5-2x off; org name without suffix)  overall false-approve "
                     f"{pct(c['overall_false_approve'])}; not detectable by arithmetic {pct(c['not_arithmetic_detectable_false_approve'])}")
        for name, t in sorted(c["types"].items(), key=lambda kv: -kv[1]["false_approve_rate"]):
            if t["false_approve_rate"] or name in ("wrong_currency", "vendor_is_customer", "consistent_x100_scale", "wrong_vendor_tax_id"):
                lines.append(f"  {name:<26} n={t['n']:<3} false-approve={t['false_approve_rate']:<5} caught_by={t['caught_by']}")
    d = res["duplicates"]
    lines.append(f"DUPLICATE VARIANTS ({d['key_source']}): caught {d['caught']}: " + ", ".join(
        f"{k}={'caught' if x['caught'] else x['decision']}" for k, x in d["variants"].items()))
    lines.append("THRESHOLD SWEEP: " + "; ".join(
        f"{t['threshold']}: STP {t['oracle_stp']}, FA-neg {t['oracle_false_approve_negatives']}, FA-corrupt {t['corruption_false_approve']}"
        for t in res["threshold_sweep"]))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", action="append", default=None,
                    help="extractor(s) to run end to end (heuristic, anthropic, openai). Default: heuristic")
    ap.add_argument("--out", default=str(HERE / "results.json"))
    args = ap.parse_args()
    if not (CASES / "manifest.json").exists():
        sys.exit("No cases. Run: backend/.venv/bin/python backend/benchmark/generate.py")
    allcases = manifest()
    labeled = [m for m in allcases if m["category"] != "ingest_only"]
    providers = args.provider or ["heuristic"]
    keys = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
    missing = [keys[p] for p in providers if p in keys and not os.environ.get(keys[p])]
    if missing:
        sys.exit(f"Set {', '.join(missing)} to run {providers} (this calls the paid API: ~40 documents).")

    res = {"meta": {"n_labeled": len(labeled), "n_files": len(allcases), "threshold": THRESHOLD,
                    "run_pipeline_params": sorted(_PIPELINE_PARAMS)}}
    res["ingest"] = run_ingest(allcases)
    res["extractor"] = {p: run_extractor(labeled, p) for p in providers}
    res["oracle"] = run_oracle(labeled)
    res["corruption"] = run_corruptions(labeled)
    res["corruption_steady_state"] = run_corruptions(labeled, steady_state=True)
    res["duplicates"] = run_duplicate_variants(labeled)
    res["threshold_sweep"] = run_threshold_sweep(labeled)
    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(summary(res))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
