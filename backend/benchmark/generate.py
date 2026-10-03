"""
Deterministic generator for the DocuPay offline invoice benchmark.

    backend/.venv/bin/python backend/benchmark/generate.py

Writes backend/benchmark/cases/:
  <id>.<ext>        the document (PDF with text layer, scanned PDF, PNG/JPEG/TIFF, TXT)
  <id>.gt.json      ground truth in InvoiceExtraction shape (what is PRINTED on the document)
  manifest.json     one entry per case: category, expected decision, tags

Ground truth is what the document shows, not what it "should" show: for a
negative case whose total does not add up, the ground-truth total is the wrong
printed total. A perfect extractor fed into the validation gate must then send
that case to review.
"""
import io
import json
import random
import re
import shutil
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from PIL import Image, ImageFilter  # noqa: E402
from reportlab.lib.pagesizes import A4, LETTER  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

CASES = HERE / "cases"
SEED = 20261003
CENT = Decimal("0.01")

VENDORS = [
    ("Acme Industrial Supply Inc.", "US", "12-3456789", "400 Market St, Springfield, IL 62701"),
    ("Northwind Traders LLC", "US", "98-7654321", "77 Harbor Way, Seattle, WA 98101"),
    ("Brightline Software Corp.", "US", "45-1122334", "1 Infinite Loop Rd, Austin, TX 73301"),
    ("Müller Maschinenbau GmbH", "DE", "DE811234567", "Industriestraße 12, 70173 Stuttgart"),
    ("Van der Berg Logistiek B.V.", "NL", "NL123456789B01", "Havenweg 4, 3011 Rotterdam"),
    ("Lumière Conseil SARL", "FR", "FR40123456789", "12 Rue de Rivoli, 75001 Paris"),
    ("Thames Office Supplies Ltd", "GB", "GB123456789", "22 Fleet Street, London EC4Y 1AA"),
    ("Highland Print & Design Ltd", "GB", "GB987654321", "5 Royal Mile, Edinburgh EH1 1RE"),
    ("Pacific Coast Catering Co.", "US", "33-4455667", "900 Ocean Ave, San Diego, CA 92101"),
    ("Granite Peak Consulting", "US", "71-2233445", "15 Summit Dr, Denver, CO 80202"),
]
CUSTOMERS = ["DocuPay Demo Corp.", "Globex Corporation", "Initech Ltd", "Umbrella Retail SA", "Stark Components BV"]
ITEMS = [
    ("Consulting services", (80, 250)), ("Steel brackets M8", (2, 15)), ("Office chairs", (90, 400)),
    ("Software license (annual)", (300, 2400)), ("Printer toner cartridge", (25, 120)),
    ("Freight handling", (40, 200)), ("Catering - lunch buffet", (12, 35)), ("Design hours", (60, 140)),
    ("Hydraulic pump", (450, 1800)), ("Safety gloves (box)", (8, 30)), ("Cloud hosting - monthly", (99, 899)),
    ("Training workshop", (500, 3000)), ("Cable ties 200pc", (3, 12)), ("Maintenance visit", (150, 600)),
    ("Laminated signage", (15, 80)), ("Ergonomic keyboard", (45, 160)),
]

# Label sets: different wordings for the same fields.
LAYOUTS = {
    "classic": dict(title_first=False, number="Invoice Number:", date="Invoice Date:", due="Due Date:",
                    po="PO Number:", bill="Bill To:", subtotal="Subtotal", tax="Tax ({rate})",
                    discount="Discount", shipping="Shipping", total="Total", columns="full"),
    "modern": dict(title_first=True, number="Invoice #", date="Date:", due="Payment Due:", po="P.O. #",
                   bill="Billed to:", subtotal="Sub-total", tax="Sales Tax", discount="Discount ({rate})",
                   shipping="Freight", total="Amount Due", columns="full"),
    "euro": dict(title_first=False, number="Invoice No.", date="Date of issue:", due="Due:",
                 po="Purchase order:", bill="Customer:", subtotal="Net amount", tax="VAT {rate}",
                 discount="Discount", shipping="Delivery", total="Total due", columns="full"),
    "german": dict(title_first=False, number="Rechnung Nr.:", date="Rechnungsdatum:", due="Fällig am:",
                   po="Bestellnummer:", bill="Kunde:", subtotal="Zwischensumme", tax="MwSt. {rate}",
                   discount="Rabatt", shipping="Versand", total="Gesamtbetrag", columns="full"),
    "minimal": dict(title_first=True, number="No.", date="Issued", due="Pay by", po="Your ref",
                    bill="To", subtotal="SUBTOTAL", tax="TAX", discount="LESS DISCOUNT",
                    shipping="SHIPPING", total="TOTAL", columns="full"),
    "amount_only": dict(title_first=False, number="Invoice Number:", date="Invoice Date:", due="Due Date:",
                        po="PO Number:", bill="Bill To:", subtotal="Subtotal", tax="Tax",
                        discount="Discount", shipping="Shipping", total="Grand Total", columns="amount"),
}


# ---------------------------------------------------------------- formatting

def q(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(amount: Decimal, style: str) -> str:
    neg = amount < 0
    a = abs(amount)
    whole, frac = f"{a:.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    if style == "us":
        s = "$" + ",".join(groups) + "." + frac
    elif style == "us_code":
        s = ",".join(groups) + "." + frac
    elif style == "gb":
        s = "£" + ",".join(groups) + "." + frac
    elif style == "eu":
        s = ".".join(groups) + "," + frac + " €"
    elif style == "eu_prefix":
        s = "€" + ".".join(groups) + "," + frac
    else:
        raise ValueError(style)
    return ("-" + s) if neg else s


def fmt_date(d: date, style: str) -> str:
    return {
        "iso": d.strftime("%Y-%m-%d"),
        "us": d.strftime("%m/%d/%Y"),
        "eu_dot": d.strftime("%d.%m.%Y"),
        "eu_slash": d.strftime("%d/%m/%Y"),
        "mon": f"{d.strftime('%b')} {d.day}, {d.year}",
        "long": f"{d.strftime('%B')} {d.day}, {d.year}",
        "dmy_mon": f"{d.day} {d.strftime('%b')} {d.year}",
    }[style]


# ---------------------------------------------------------------- invoice model

def make_invoice(rng, *, vendor_idx, currency, n_items, tax_rate, discount_pct=0, shipping=None,
                 inv_date=None, terms_days=30, number=None, po=True, customer=None):
    vendor, country, tax_id, address = VENDORS[vendor_idx]
    picks = rng.sample(ITEMS, k=min(n_items, len(ITEMS)))
    while len(picks) < n_items:
        picks.append(rng.choice(ITEMS))
    lines = []
    for i, (desc, (lo, hi)) in enumerate(picks):
        qty = rng.choice([1, 1, 2, 3, 4, 5, 10, 12, 25])
        unit = q(Decimal(rng.randint(lo * 100, hi * 100)) / 100)
        label = desc if n_items <= len(ITEMS) else f"{desc} #{i + 1}"
        lines.append({"description": label, "quantity": qty, "unit_price": unit, "amount": q(unit * qty)})
    subtotal = sum((li["amount"] for li in lines), Decimal("0"))
    discount = q(subtotal * Decimal(discount_pct) / 100) if discount_pct else None
    taxable = subtotal - (discount or 0)
    tax = q(taxable * Decimal(str(tax_rate)) / 100) if tax_rate else None
    ship = q(shipping) if shipping else None
    total = subtotal - (discount or 0) + (tax or 0) + (ship or 0)
    inv_date = inv_date or date(2026, 6, 1) + timedelta(days=rng.randint(0, 110))
    number = number or f"INV-{rng.randint(2026000, 2026999)}"
    return {
        "is_invoice": True,
        "vendor_name": vendor, "vendor_tax_id": tax_id, "vendor_address": address,
        "customer_name": customer or rng.choice(CUSTOMERS),
        "invoice_number": number,
        "purchase_order": f"PO-{rng.randint(10000, 99999)}" if po else None,
        "invoice_date": inv_date, "due_date": inv_date + timedelta(days=terms_days) if terms_days is not None else None,
        "payment_terms": f"Net {terms_days}" if terms_days else None,
        "currency": currency,
        "subtotal": subtotal, "discount_amount": discount, "tax_amount": tax, "shipping_amount": ship,
        "total_amount": total, "line_items": lines,
        "_tax_rate": tax_rate, "_discount_pct": discount_pct,
    }


def gt_json(inv: dict) -> dict:
    """Ground truth validated through the app's own schema so field shapes match exactly."""
    from apps.processing.schema import InvoiceExtraction

    data = {k: v for k, v in inv.items() if not k.startswith("_")}
    data = json.loads(json.dumps(data, default=str))
    return InvoiceExtraction.model_validate(data).to_json()


# ---------------------------------------------------------------- PDF rendering

def _rate(r) -> str:
    return f"{Decimal(str(r)).normalize():f}%"


def render_pdf(path_or_buf, inv, layout="classic", money="us", dates="iso", pagesize=LETTER,
               items_per_page=22, show_subtotal=True, title="INVOICE", extra_header=None, labels=None):
    L = dict(LAYOUTS[layout], **(labels or {}))
    W, H = pagesize
    c = canvas.Canvas(path_or_buf, pagesize=pagesize, invariant=1)
    c.setTitle(f"{title} {inv.get('invoice_number') or ''}")
    lines = inv["line_items"]
    pages = [lines[i:i + items_per_page] for i in range(0, max(1, len(lines)), items_per_page)] or [[]]
    n_pages = len(pages)
    m = lambda v: fmt_money(v, money)  # noqa: E731

    for p, chunk in enumerate(pages, 1):
        y = H - 60
        if p == 1:
            if L["title_first"]:
                c.setFont("Helvetica-Bold", 22)
                c.drawString(50, y, title)
                y -= 34
                c.setFont("Helvetica-Bold", 13)
                c.drawString(50, y, inv["vendor_name"] or "")
            else:
                c.setFont("Helvetica-Bold", 15)
                c.drawString(50, y, inv["vendor_name"] or "")
                c.setFont("Helvetica-Bold", 20)
                c.drawRightString(W - 50, y, title)
            c.setFont("Helvetica", 9)
            y -= 14
            if inv.get("vendor_address"):
                c.drawString(50, y, inv["vendor_address"])
                y -= 12
            if inv.get("vendor_tax_id"):
                c.drawString(50, y, f"Tax ID: {inv['vendor_tax_id']}")
                y -= 12
            if extra_header:
                for ln in extra_header:
                    c.drawString(50, y, ln)
                    y -= 12
            y -= 14
            c.setFont("Helvetica", 10)
            meta = []
            if inv.get("invoice_number"):
                meta.append((L["number"], inv["invoice_number"]))
            if inv.get("invoice_date"):
                meta.append((L["date"], fmt_date(inv["invoice_date"], dates)))
            if inv.get("due_date"):
                meta.append((L["due"], fmt_date(inv["due_date"], dates)))
            if inv.get("purchase_order"):
                meta.append((L["po"], inv["purchase_order"]))
            if layout == "minimal" and inv.get("currency"):
                meta.append(("Currency", inv["currency"]))
            for label, value in meta:
                c.drawString(50, y, f"{label} {value}")
                y -= 14
            y -= 6
            if inv.get("customer_name"):
                c.drawString(50, y, f"{L['bill']} {inv['customer_name']}")
                y -= 24
        else:
            c.setFont("Helvetica", 9)
            c.drawString(50, y, f"{inv['vendor_name']} - {inv.get('invoice_number') or ''} (continued)")
            y -= 30

        # table header
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Description")
        if L["columns"] == "full":
            c.drawRightString(360, y, "Qty")
            c.drawRightString(460, y, "Unit price")
        c.drawRightString(W - 50, y, "Amount")
        y -= 16
        c.setFont("Helvetica", 10)
        for li in chunk:
            c.drawString(50, y, li["description"])
            if L["columns"] == "full":
                c.drawRightString(360, y, str(li["quantity"]))
                c.drawRightString(460, y, m(li["unit_price"]))
            c.drawRightString(W - 50, y, m(li["amount"]))
            y -= 15

        if p == n_pages:
            y -= 12
            rows = []
            if show_subtotal and inv.get("subtotal") is not None:
                rows.append((L["subtotal"], m(inv["subtotal"])))
            if inv.get("discount_amount"):
                rows.append((L["discount"].format(rate=_rate(inv.get("_discount_pct") or 0)), m(-inv["discount_amount"])))
            if inv.get("shipping_amount"):
                rows.append((L["shipping"], m(inv["shipping_amount"])))
            if inv.get("tax_amount"):
                rows.append((L["tax"].format(rate=_rate(inv.get("_tax_rate") or 0)), m(inv["tax_amount"])))
            if layout in ("classic", "amount_only", "modern") and money == "us_code":
                rows.append((L["total"] + " (" + inv["currency"] + ")", m(inv["total_amount"])))
            else:
                rows.append((L["total"], m(inv["total_amount"])))
            for i, (label, value) in enumerate(rows):
                c.setFont("Helvetica-Bold" if i == len(rows) - 1 else "Helvetica", 10)
                c.drawString(330, y, label)
                c.drawRightString(W - 50, y, value)
                y -= 15
            y -= 20
            c.setFont("Helvetica", 8)
            if inv.get("payment_terms"):
                c.drawString(50, y, f"Terms: {inv['payment_terms']}. Thank you for your business.")
        c.setFont("Helvetica", 8)
        c.drawRightString(W - 50, 30, f"Page {p} of {n_pages}")
        c.showPage()
    c.save()


def render_text_doc(path, title, paragraphs, pagesize=LETTER):
    W, H = pagesize
    c = canvas.Canvas(str(path), pagesize=pagesize, invariant=1)
    y = H - 70
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    y -= 30
    c.setFont("Helvetica", 10)
    for para in paragraphs:
        for ln in para.split("\n"):
            c.drawString(50, y, ln)
            y -= 14
        y -= 8
    c.showPage()
    c.save()


# ---------------------------------------------------------------- scans

def _zero_ids(data: bytes) -> bytes:
    def repl(mm):
        return re.sub(rb"[0-9A-Fa-f]", b"0", mm.group(0))
    data = re.sub(rb"/ID\s*\[\s*<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\s*\]", repl, data)
    return re.sub(rb"\(D:\d{14}", b"(D:20261003000000", data)


def pdf_to_images(pdf_bytes, scale=1.4):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return [pdf[i].render(scale=scale).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()


def scanify(img: Image.Image, rng, angle=None) -> Image.Image:
    """Grayscale, small rotation, blur and speckle noise: a cheap office-scanner look."""
    g = img.convert("L")
    angle = angle if angle is not None else rng.uniform(-1.5, 1.5)
    g = g.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    px = g.load()
    w, h = g.size
    r = random.Random(rng.random())
    for _ in range(w * h // 400):
        x, y = r.randrange(w), r.randrange(h)
        px[x, y] = r.choice((0, 90, 200))
    return g


def images_to_pdf(path, images, pagesize=LETTER, footer_text=None):
    W, H = pagesize
    c = canvas.Canvas(path if hasattr(path, "write") else str(path), pagesize=pagesize, invariant=1)
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=W, height=H)
        if footer_text:
            c.setFont("Helvetica", 6)
            c.drawString(20, 10, footer_text)
        c.showPage()
    c.save()


# ---------------------------------------------------------------- text invoice

def render_txt(inv, money="us", dates="iso") -> str:
    m = lambda v: fmt_money(v, money)  # noqa: E731
    out = [inv["vendor_name"], inv.get("vendor_address") or "", "", "INVOICE",
           f"Invoice Number: {inv['invoice_number']}",
           f"Invoice Date: {fmt_date(inv['invoice_date'], dates)}",
           f"Due Date: {fmt_date(inv['due_date'], dates)}", f"Bill To: {inv['customer_name']}", "",
           "Description Qty Unit Amount"]
    for li in inv["line_items"]:
        out.append(f"{li['description']} {li['quantity']} {m(li['unit_price'])} {m(li['amount'])}")
    out.append("")
    out.append(f"Subtotal: {m(inv['subtotal'])}")
    if inv.get("tax_amount"):
        out.append(f"Tax: {m(inv['tax_amount'])}")
    out.append(f"Total: {m(inv['total_amount'])}")
    out.append(f"Currency: {inv['currency']}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- case catalogue

def build(rng):
    """Yield (case_id, meta, writer) where writer(path_stem) writes the file and returns its name."""
    cases = []

    def add(cid, category, expected, inv, fmt, tags, writer, note=""):
        cases.append(dict(id=cid, category=category, expected=expected, inv=inv, format=fmt,
                          tags=tags, writer=writer, note=note))

    def pdf_writer(**kw):
        def w(inv, stem):
            path = stem.with_suffix(".pdf")
            render_pdf(str(path), inv, **kw)
            return path.name
        return w

    # ---- clean digital PDFs (expected: auto-approve with a correct extraction)
    d = lambda y, mo, da: date(y, mo, da)  # noqa: E731
    specs = [
        ("clean_us_classic_iso", dict(vendor_idx=0, currency="USD", n_items=4, tax_rate=8.25, inv_date=d(2026, 9, 3)),
         dict(layout="classic", money="us", dates="iso"), ["usd", "iso_date", "tax"]),
        ("clean_us_modern_usdate", dict(vendor_idx=1, currency="USD", n_items=3, tax_rate=7, inv_date=d(2026, 8, 5)),
         dict(layout="modern", money="us", dates="us"), ["usd", "us_date", "title_first"]),
        ("clean_us_mon_date", dict(vendor_idx=2, currency="USD", n_items=2, tax_rate=0, terms_days=15, inv_date=d(2026, 9, 1)),
         dict(layout="classic", money="us", dates="mon"), ["usd", "mon_date", "no_tax"]),
        ("clean_us_long_date_discount", dict(vendor_idx=9, currency="USD", n_items=5, tax_rate=6, discount_pct=10, inv_date=d(2026, 7, 9)),
         dict(layout="classic", money="us", dates="long"), ["usd", "long_date", "discount", "tax"]),
        ("clean_us_shipping", dict(vendor_idx=8, currency="USD", n_items=3, tax_rate=7.75, shipping=45, inv_date=d(2026, 6, 11)),
         dict(layout="modern", money="us", dates="us"), ["usd", "shipping", "tax", "title_first"]),
        ("clean_us_code_currency", dict(vendor_idx=0, currency="USD", n_items=4, tax_rate=5, inv_date=d(2026, 8, 20)),
         dict(layout="classic", money="us_code", dates="iso"), ["usd", "no_symbol"]),
        ("clean_us_amount_only", dict(vendor_idx=9, currency="USD", n_items=4, tax_rate=0, inv_date=d(2026, 7, 2)),
         dict(layout="amount_only", money="us", dates="iso"), ["usd", "no_qty_columns"]),
        ("clean_eu_decimal_comma", dict(vendor_idx=3, currency="EUR", n_items=4, tax_rate=19, inv_date=d(2026, 9, 7)),
         dict(layout="euro", money="eu", dates="eu_dot"), ["eur", "decimal_comma", "eu_dot_date", "vat"]),
        ("clean_eu_german_labels", dict(vendor_idx=3, currency="EUR", n_items=3, tax_rate=19, inv_date=d(2026, 8, 14)),
         dict(layout="german", money="eu", dates="eu_dot"), ["eur", "decimal_comma", "german"]),
        ("clean_eu_slash_date", dict(vendor_idx=4, currency="EUR", n_items=3, tax_rate=21, inv_date=d(2026, 9, 4)),
         dict(layout="euro", money="eu_prefix", dates="eu_slash"), ["eur", "decimal_comma", "eu_slash_date_ambiguous"]),
        ("clean_eu_slash_date_unambiguous", dict(vendor_idx=5, currency="EUR", n_items=2, tax_rate=20, inv_date=d(2026, 8, 25)),
         dict(layout="euro", money="eu", dates="eu_slash"), ["eur", "decimal_comma", "eu_slash_date"]),
        ("clean_eu_discount_shipping_tax", dict(vendor_idx=4, currency="EUR", n_items=5, tax_rate=21, discount_pct=5, shipping=35, inv_date=d(2026, 7, 21)),
         dict(layout="euro", money="eu", dates="iso", pagesize=A4), ["eur", "decimal_comma", "discount", "shipping", "vat"]),
        ("clean_gb_vat_mon", dict(vendor_idx=6, currency="GBP", n_items=3, tax_rate=20, inv_date=d(2026, 9, 1)),
         dict(layout="euro", money="gb", dates="mon", pagesize=A4), ["gbp", "vat", "mon_date"]),
        ("clean_gb_dmy_mon", dict(vendor_idx=7, currency="GBP", n_items=4, tax_rate=20, shipping=12.5, inv_date=d(2026, 8, 3)),
         dict(layout="classic", money="gb", dates="dmy_mon", pagesize=A4), ["gbp", "dmy_mon_date", "shipping"]),
        ("clean_minimal_layout", dict(vendor_idx=1, currency="USD", n_items=3, tax_rate=9, inv_date=d(2026, 9, 10)),
         dict(layout="minimal", money="us", dates="iso"), ["usd", "title_first", "terse_labels"]),
        ("clean_multipage_2", dict(vendor_idx=0, currency="USD", n_items=30, tax_rate=8.25, inv_date=d(2026, 8, 28)),
         dict(layout="classic", money="us", dates="iso", items_per_page=18), ["usd", "multipage"]),
        ("clean_multipage_3_eur", dict(vendor_idx=3, currency="EUR", n_items=50, tax_rate=19, discount_pct=3, inv_date=d(2026, 9, 8)),
         dict(layout="euro", money="eu", dates="eu_dot", items_per_page=20, pagesize=A4), ["eur", "multipage", "decimal_comma", "discount"]),
        ("clean_many_lines_single_page", dict(vendor_idx=8, currency="USD", n_items=16, tax_rate=7.75, inv_date=d(2026, 6, 30)),
         dict(layout="modern", money="us", dates="us", items_per_page=40), ["usd", "many_lines"]),
    ]
    for cid, inv_kw, render_kw, tags in specs:
        inv = make_invoice(rng, **inv_kw)
        add(cid, "clean_digital", "auto_approve", inv, "pdf", ["digital_pdf"] + tags, pdf_writer(**render_kw))

    # ---- VAT-inclusive (gross) prices: common for EU retail/hospitality. No net subtotal printed.
    inv = make_invoice(rng, vendor_idx=5, currency="EUR", n_items=3, tax_rate=0, inv_date=d(2026, 9, 16))
    gross = inv["total_amount"]
    inv["tax_amount"] = q(gross - gross / Decimal("1.2"))
    inv["subtotal"] = None
    inv["prices_include_tax"] = True
    inv["_tax_rate"] = 20
    add("clean_eu_vat_inclusive", "clean_digital", "auto_approve", inv, "pdf",
        ["digital_pdf", "eur", "decimal_comma", "vat_inclusive", "no_subtotal"],
        pdf_writer(layout="euro", money="eu", dates="eu_dot", show_subtotal=False,
                   labels=dict(tax="incl. VAT {rate}")),
        "Line prices include VAT; 'incl. VAT 20%' is informational; total = sum of lines.")

    # ---- plain-text invoices
    for cid, kw, money in (
        ("clean_text_usd", dict(vendor_idx=2, currency="USD", n_items=3, tax_rate=8, inv_date=d(2026, 9, 12)), "us"),
        ("clean_text_gbp", dict(vendor_idx=6, currency="GBP", n_items=2, tax_rate=20, inv_date=d(2026, 9, 2)), "gb"),
    ):
        inv = make_invoice(rng, **kw)

        def w(inv, stem, money=money):
            path = stem.with_suffix(".txt")
            path.write_text(render_txt(inv, money=money), encoding="utf-8")
            return path.name
        add(cid, "clean_text", "auto_approve", inv, "txt", ["text_file"], w)

    # ---- scanned / image-only
    def img_writer(fmt, render_kw, scan=True, multipage_pdf=False, tiff=False, footer=None):
        def w(inv, stem):
            buf = io.BytesIO()
            render_pdf(buf, inv, **render_kw)
            imgs = pdf_to_images(buf.getvalue(), scale=1.4)
            imgs = [scanify(i, rng) if scan else i for i in imgs]
            if fmt == "pdf":
                path = stem.with_suffix(".pdf")
                images_to_pdf(path, imgs, footer_text=footer)
            elif tiff:
                path = stem.with_suffix(".tiff")
                imgs[0].save(path, format="TIFF", save_all=True, append_images=imgs[1:], compression="tiff_deflate")
            elif fmt == "png":
                path = stem.with_suffix(".png")
                imgs[0].convert("L").save(path, format="PNG", optimize=True)
            else:
                path = stem.with_suffix(".jpg")
                imgs[0].save(path, format="JPEG", quality=65)
            return path.name
        return w

    scan_specs = [
        ("scan_png_us", dict(vendor_idx=1, currency="USD", n_items=3, tax_rate=6.5, inv_date=d(2026, 9, 9)),
         "png", dict(layout="classic", money="us", dates="us"), dict(scan=False), ["image", "png"]),
        ("scan_jpg_eu", dict(vendor_idx=5, currency="EUR", n_items=3, tax_rate=20, inv_date=d(2026, 8, 18)),
         "jpg", dict(layout="euro", money="eu", dates="eu_dot", pagesize=A4), {}, ["image", "jpg", "noisy", "decimal_comma"]),
        ("scan_pdf_1page", dict(vendor_idx=7, currency="GBP", n_items=4, tax_rate=20, inv_date=d(2026, 7, 6)),
         "pdf", dict(layout="classic", money="gb", dates="dmy_mon", pagesize=A4), {}, ["scanned_pdf"]),
        ("scan_pdf_2page", dict(vendor_idx=0, currency="USD", n_items=24, tax_rate=8.25, inv_date=d(2026, 8, 12)),
         "pdf", dict(layout="modern", money="us", dates="iso", items_per_page=14), {}, ["scanned_pdf", "multipage"]),
        ("scan_pdf_with_scanner_stamp", dict(vendor_idx=9, currency="USD", n_items=2, tax_rate=0, inv_date=d(2026, 9, 15)),
         "pdf", dict(layout="classic", money="us", dates="iso"), dict(footer="Scanned 2026-09-16"),
         ["scanned_pdf", "tiny_text_layer"]),
        ("scan_tiff_2page", dict(vendor_idx=2, currency="USD", n_items=24, tax_rate=8, inv_date=d(2026, 9, 5)),
         "tiff", dict(layout="classic", money="us", dates="iso", items_per_page=14), dict(tiff=True),
         ["image", "tiff", "multipage"]),
    ]
    for cid, inv_kw, fmt, render_kw, wkw, tags in scan_specs:
        inv = make_invoice(rng, **inv_kw)
        add(cid, "clean_scan", "auto_approve", inv, fmt, tags, img_writer(fmt, render_kw, **wkw))

    # ---- mixed PDF: page 1 digital, page 2 a scanned continuation that holds the totals
    inv = make_invoice(rng, vendor_idx=8, currency="USD", n_items=26, tax_rate=7.75, inv_date=d(2026, 8, 22))

    def mixed_writer(inv, stem):
        import pypdfium2 as pdfium

        buf = io.BytesIO()
        render_pdf(buf, inv, layout="classic", money="us", dates="iso", items_per_page=16)
        imgs = pdf_to_images(buf.getvalue(), scale=1.4)
        scan_buf = io.BytesIO()
        images_to_pdf(scan_buf, [scanify(imgs[1], rng)])
        out = pdfium.PdfDocument.new()
        src_text, src_scan = pdfium.PdfDocument(buf.getvalue()), pdfium.PdfDocument(scan_buf.getvalue())
        out.import_pages(src_text, [0])
        out.import_pages(src_scan, [0])
        path = stem.with_suffix(".pdf")
        raw = io.BytesIO()
        out.save(raw)
        # PDFium writes a random /ID; zero it (same length, so xref offsets stay valid) for byte-stable output.
        path.write_bytes(_zero_ids(raw.getvalue()))
        return path.name
    add("mixed_text_then_scan", "clean_scan", "auto_approve", inv, "pdf",
        ["mixed_pdf", "multipage", "totals_on_scanned_page"], mixed_writer,
        note="Page 1 has a text layer; page 2 (with the totals) is an image.")

    # ---- negatives (expected: review)
    neg = []

    inv = make_invoice(rng, vendor_idx=1, currency="USD", n_items=3, tax_rate=8, inv_date=d(2026, 9, 14))
    inv["total_amount"] += Decimal("100.00")
    neg.append(("neg_total_mismatch", inv, dict(layout="classic", money="us", dates="iso"),
                ["totals_dont_add_up"], "Printed total is 100.00 more than subtotal + tax."))

    inv = make_invoice(rng, vendor_idx=9, currency="USD", n_items=4, tax_rate=6, inv_date=d(2026, 9, 2))
    inv["total_amount"] += Decimal("37.50")
    neg.append(("neg_total_mismatch_no_subtotal", inv, dict(layout="classic", money="us", dates="iso", show_subtotal=False),
                ["totals_dont_add_up", "no_subtotal"], "No subtotal printed; total != lines + tax."))
    neg[-1][1]["subtotal"] = None

    inv = make_invoice(rng, vendor_idx=3, currency="EUR", n_items=4, tax_rate=19, inv_date=d(2026, 8, 31))
    bad = inv["line_items"][1]
    bad["unit_price"] += Decimal("50.00")
    bad["amount"] = q(bad["unit_price"] * bad["quantity"])  # the line itself is consistent
    neg.append(("neg_lines_dont_sum", inv, dict(layout="euro", money="eu", dates="eu_dot"),
                ["line_items_dont_sum"], "One line is 50.00 higher; subtotal/tax/total are self-consistent."))

    inv = make_invoice(rng, vendor_idx=6, currency="GBP", n_items=3, tax_rate=20, inv_date=d(2026, 9, 3))
    li = inv["line_items"][0]
    li["quantity"] = li["quantity"] + 1  # printed qty no longer matches qty x unit = amount
    neg.append(("neg_line_qty_price_mismatch", inv, dict(layout="classic", money="gb", dates="iso"),
                ["line_math_wrong"], "Line 1: qty x unit price != amount (vendor arithmetic error); sums are consistent."))

    inv = make_invoice(rng, vendor_idx=2, currency="USD", n_items=3, tax_rate=8.25, inv_date=d(2026, 9, 18))
    inv["invoice_number"] = None
    neg.append(("neg_missing_invoice_number", inv, dict(layout="classic", money="us", dates="iso"),
                ["missing_invoice_number"], "No invoice number anywhere on the document."))

    inv = make_invoice(rng, vendor_idx=4, currency="EUR", n_items=2, tax_rate=21, inv_date=d(2026, 9, 20))
    inv["due_date"] = inv["invoice_date"] - timedelta(days=10)
    inv["payment_terms"] = None
    neg.append(("neg_due_before_invoice", inv, dict(layout="euro", money="eu", dates="iso"),
                ["due_before_invoice_date"], "Due date is 10 days before the invoice date."))

    inv = make_invoice(rng, vendor_idx=7, currency="GBP", n_items=2, tax_rate=20, inv_date=date(2026, 11, 2))
    neg.append(("neg_future_invoice_date", inv, dict(layout="classic", money="gb", dates="iso"),
                ["future_date"], "Invoice date is after the benchmark date (2026-10-03)."))

    inv = make_invoice(rng, vendor_idx=1, currency="USD", n_items=2, tax_rate=8, inv_date=d(2026, 9, 22))
    for li in inv["line_items"]:
        li["unit_price"], li["amount"] = -li["unit_price"], -li["amount"]
    for f in ("subtotal", "tax_amount", "total_amount"):
        inv[f] = -inv[f]
    inv["invoice_number"] = "CN-" + inv["invoice_number"][4:]
    inv["due_date"], inv["payment_terms"] = None, None
    neg.append(("neg_credit_note", inv, dict(layout="classic", money="us", dates="iso", title="CREDIT NOTE",
                                             labels=dict(number="Credit Note No.:")),
                ["credit_note", "negative_amounts"],
                "A credit note (negative total). Must not be auto-approved for payment as if it were an invoice."))

    for cid, inv, render_kw, tags, note in neg:
        add(cid, "negative", "review", inv, "pdf", ["digital_pdf"] + tags, pdf_writer(**render_kw), note)

    # non-invoices
    def letter_writer(inv, stem):
        path = stem.with_suffix(".pdf")
        render_text_doc(path, "Northwind Traders LLC", [
            "77 Harbor Way, Seattle, WA 98101",
            "September 21, 2026",
            "Dear Accounts Payable team,",
            "Re: change of bank details\nPlease note that from October 1 our remittance account changes.\n"
            "Future invoices will show the new account. No payment is requested by this letter.",
            "Kind regards,\nJane Doe, Finance Manager",
        ])
        return path.name
    add("neg_not_invoice_letter", "negative", "review",
        {"is_invoice": False, "vendor_name": "Northwind Traders LLC", "line_items": []}, "pdf",
        ["non_invoice", "mentions_invoice"], letter_writer, "Business letter that mentions invoices.")

    inv = make_invoice(rng, vendor_idx=5, currency="EUR", n_items=3, tax_rate=20, inv_date=d(2026, 9, 11), po=False)
    inv["is_invoice"] = False
    inv["invoice_number"] = "Q-2026-118"
    add("neg_not_invoice_quote", "negative", "review", inv, "pdf", ["non_invoice", "quotation"],
        pdf_writer(layout="euro", money="eu", dates="eu_dot", title="QUOTATION",
                   labels=dict(number="Quote No.", date="Quote date:", due="Valid until:")),
        "A quotation: same structure as an invoice, consistent totals, but not a bill.")

    inv = make_invoice(rng, vendor_idx=0, currency="USD", n_items=3, tax_rate=0, inv_date=d(2026, 9, 9), po=False)
    inv["is_invoice"] = False
    inv["invoice_number"] = "PO-55120"
    add("neg_not_invoice_purchase_order", "negative", "review", inv, "pdf", ["non_invoice", "purchase_order"],
        pdf_writer(layout="classic", money="us", dates="iso", title="PURCHASE ORDER",
                   labels=dict(number="PO Number:", date="Order Date:", due="Deliver by:", bill="Ship To:")),
        "A purchase order issued BY the customer; looks like an invoice.")

    # duplicate: same vendor + number as clean_us_classic_iso, re-sent with a different layout.
    first = next(cs for cs in cases if cs["id"] == "clean_us_classic_iso")["inv"]
    dup = dict(first)
    add("neg_duplicate_resubmission", "negative", "review", dup, "pdf", ["duplicate"],
        pdf_writer(layout="modern", money="us", dates="us"),
        "Same vendor and invoice number as clean_us_classic_iso (processed earlier).")
    return cases


def ingest_only(rng):
    """Files with no extraction ground truth: they test only how ingest routes or rejects them."""
    out = []

    def too_many_pages(stem):
        inv = make_invoice(rng, vendor_idx=0, currency="USD", n_items=210, tax_rate=0)
        path = stem.with_suffix(".pdf")
        render_pdf(str(path), inv, items_per_page=10)
        return path.name
    out.append(("ingest_21_pages", too_many_pages, "permanent_error", "21-page PDF (limit is 20)"))

    def empty(stem):
        path = stem.with_suffix(".pdf")
        path.write_bytes(b"")
        return path.name
    out.append(("ingest_empty_file", empty, "permanent_error", "Zero-byte upload"))

    def truncated(stem):
        buf = io.BytesIO()
        render_pdf(buf, make_invoice(rng, vendor_idx=1, currency="USD", n_items=2, tax_rate=0))
        path = stem.with_suffix(".pdf")
        path.write_bytes(buf.getvalue()[:400])
        return path.name
    out.append(("ingest_truncated_pdf", truncated, "permanent_error", "PDF cut off after 400 bytes"))

    def docx_like(stem):
        path = stem.with_suffix(".docx")
        path.write_bytes(b"PK\x03\x04" + bytes(range(256)) * 4)
        return path.name
    out.append(("ingest_docx_zip", docx_like, "permanent_error", "Office/zip file (unsupported)"))

    def latin1_text(stem):
        path = stem.with_suffix(".txt")
        path.write_bytes("Rechnung Nr. 4711\nGesamtbetrag: 1.190,00 EUR\nMüller GmbH\n".encode("latin-1"))
        return path.name
    out.append(("ingest_latin1_text", latin1_text, "text", "Latin-1 encoded text invoice (common from legacy ERPs)"))

    def webp(stem):
        buf = io.BytesIO()
        render_pdf(buf, make_invoice(rng, vendor_idx=2, currency="USD", n_items=2, tax_rate=0))
        img = pdf_to_images(buf.getvalue(), scale=1.0)[0]
        path = stem.with_suffix(".webp")
        img.save(path, format="WEBP", quality=60)
        return path.name
    out.append(("ingest_webp_image", webp, "image", "WEBP phone capture"))

    def exif_rotated(stem):
        buf = io.BytesIO()
        render_pdf(buf, make_invoice(rng, vendor_idx=8, currency="USD", n_items=2, tax_rate=0))
        page = pdf_to_images(buf.getvalue(), scale=1.2)[0]
        # Phone cameras store the sensor image as-is and set EXIF Orientation=6 ("rotate 90 CW to view").
        sensor = page.transpose(Image.ROTATE_90)
        exif = Image.Exif()
        exif[0x0112] = 6
        path = stem.with_suffix(".jpg")
        sensor.save(path, format="JPEG", quality=70, exif=exif)
        return path.name
    out.append(("ingest_exif_rotated_photo", exif_rotated, "image", "Phone photo with EXIF Orientation=6"))
    return out


def main():
    if CASES.exists():
        shutil.rmtree(CASES)
    CASES.mkdir(parents=True)
    rng = random.Random(SEED)
    manifest = []
    for case in build(rng):
        stem = CASES / case["id"]
        fname = case["writer"](case["inv"], stem)
        gt = gt_json(case["inv"])
        (CASES / f"{case['id']}.gt.json").write_text(json.dumps(gt, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest.append(dict(id=case["id"], file=fname, category=case["category"], expected=case["expected"],
                             format=case["format"], tags=case["tags"], note=case["note"]))
    for cid, writer, expect, note in ingest_only(rng):
        fname = writer(CASES / cid)
        manifest.append(dict(id=cid, file=fname, category="ingest_only", expected=expect, format=fname.rsplit(".", 1)[-1],
                             tags=["ingest_only"], note=note))
    (CASES / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    size = sum(p.stat().st_size for p in CASES.iterdir())
    labeled = sum(1 for m in manifest if m["category"] != "ingest_only")
    print(f"wrote {labeled} labeled cases + {len(manifest) - labeled} ingest-only files to {CASES} ({size / 1e6:.2f} MB)")


if __name__ == "__main__":
    import django
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test")
    django.setup()
    main()
