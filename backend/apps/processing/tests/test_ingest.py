from unittest import mock

import pytest
from django.db.models.fields.files import FieldFile
from django.test import override_settings

from apps.processing.errors import PermanentProcessingError
from apps.processing.ingest import MAX_IMAGE_EDGE, load_document

from .factories import INVOICE_TEXT, image_bytes, make_document, make_org, scanned_pdf, text_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return make_org()[0]


def test_storage_path_is_never_used(org):
    """DP-3: the old code used file.path, which S3 storage does not have."""
    doc = make_document(org, b"Invoice", "a.txt")
    no_path = mock.PropertyMock(side_effect=NotImplementedError("This backend doesn't support absolute paths."))
    with mock.patch.object(FieldFile, "path", new_callable=lambda: no_path):
        assert load_document(doc.file).text == "Invoice"
    no_path.assert_not_called()


def test_digital_pdf_uses_text_layer(org):
    doc = make_document(org, text_pdf(), "inv.pdf")
    result = load_document(doc.file)
    assert result.source == "pdf_text"
    assert result.page_count == 1
    assert "INV-1001" in result.text
    assert result.images == []


def test_scanned_pdf_becomes_images(org):
    doc = make_document(org, scanned_pdf(), "scan.pdf")
    result = load_document(doc.file)
    assert result.source == "pdf_scan"
    assert len(result.images) == 1
    assert result.images[0].media_type == "image/jpeg"


def test_image_is_resized(org):
    doc = make_document(org, image_bytes("JPEG", size=(3000, 4000)), "photo.jpg")
    result = load_document(doc.file)
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(result.images[0].data))
    assert max(img.size) == MAX_IMAGE_EDGE


@override_settings(PROCESSING_MAX_PAGES=2)
def test_page_limit(org):
    doc = make_document(org, text_pdf(pages=3), "long.pdf")
    with pytest.raises(PermanentProcessingError, match="3 pages"):
        load_document(doc.file)


@override_settings(PROCESSING_MAX_FILE_BYTES=10)
def test_size_limit(org):
    doc = make_document(org, INVOICE_TEXT.encode(), "big.txt")
    with pytest.raises(PermanentProcessingError, match="larger"):
        load_document(doc.file)


@pytest.mark.parametrize("content", [b"", b"\x00\xff\xfe binary junk \x81"])
def test_bad_files(org, content):
    doc = make_document(org, content, "bad.bin")
    with pytest.raises(PermanentProcessingError):
        load_document(doc.file)


def test_damaged_pdf(org):
    doc = make_document(org, b"%PDF-1.4 broken", "bad.pdf")
    with pytest.raises(PermanentProcessingError, match="damaged"):
        load_document(doc.file)


def test_huge_pdf_page_is_rendered_at_target_size(org):
    """A 200 x 200 inch page must not be rasterized at full resolution (memory DoS)."""
    from io import BytesIO

    from PIL import Image
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(14400, 14400))
    c.rect(10, 10, 100, 100, fill=1)
    c.showPage()
    c.save()
    doc = make_document(org, buf.getvalue(), "huge.pdf")
    result = load_document(doc.file)
    assert result.source == "pdf_scan"
    assert max(Image.open(BytesIO(result.images[0].data)).size) <= MAX_IMAGE_EDGE


def test_image_with_too_many_pixels_is_refused(org):
    doc = make_document(org, image_bytes("PNG", size=(8000, 6000)), "big.png")
    with pytest.raises(PermanentProcessingError, match="too large"):
        load_document(doc.file)
