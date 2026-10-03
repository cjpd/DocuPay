import io

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw

from apps.documents.models import Document
from apps.organizations.models import Organization, OrgMembership
from apps.users.models import CustomUser

CLEAN_INVOICE = {
    "is_invoice": True,
    "vendor_name": "Acme Supplies LLC",
    "invoice_number": "INV-1001",
    "invoice_date": "2026-09-01",
    "due_date": "2026-10-01",
    "currency": "USD",
    "subtotal": 300.00,
    "tax_amount": 24.00,
    "total_amount": 324.00,
    "line_items": [
        {"description": "Paper A4", "quantity": 10, "unit_price": 20.00, "amount": 200.00},
        {"description": "Toner", "quantity": 1, "unit_price": 100.00, "amount": 100.00},
    ],
    "uncertain_fields": [],
}

INVOICE_TEXT = """Acme Supplies LLC
123 Market St, Springfield
INVOICE
Invoice Number: INV-1001
Invoice Date: 2026-09-01
Due Date: 2026-10-01
Paper A4 10 20.00 200.00
Toner 1 100.00 100.00
Subtotal: 300.00
Tax: 24.00
Total: 324.00 USD
"""


def make_org(threshold=0.92, slug="acme"):
    org = Organization.objects.create(name=slug.title(), slug=slug, auto_approve_threshold=threshold)
    user = CustomUser.objects.create_user(username=f"user-{slug}", password="pw", email=f"{slug}@example.com")
    OrgMembership.objects.create(organization=org, user=user)
    return org, user


def make_document(org, content: bytes, name="invoice.txt", user=None, status=Document.Status.PENDING):
    doc = Document(organization=org, uploaded_by=user, status=status)
    doc.file.save(name, ContentFile(content), save=False)
    doc.save()
    return doc


def text_pdf(text: str = INVOICE_TEXT, pages: int = 1) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(pages):
        y = 750
        for line in text.splitlines():
            c.drawString(72, y, line)
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def image_bytes(fmt="PNG", size=(800, 1000)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((40, 40), "INVOICE INV-1001", fill="black")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def scanned_pdf() -> bytes:
    """A PDF with only an image on the page, no text layer."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawImage(ImageReader(io.BytesIO(image_bytes())), 0, 0, width=612, height=792)
    c.showPage()
    c.save()
    return buf.getvalue()
