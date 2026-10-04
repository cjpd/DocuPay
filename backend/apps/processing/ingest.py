"""
Turn an uploaded file into model input.

Files are read through the storage API (`FieldFile.open`), never through a
local path, so the pipeline works the same on local disk and on S3.

Order of preference, cheapest first:
1. Digital PDF -> embedded text layer (free, exact).
2. Scanned PDF -> one PNG per page for a vision model.
3. Image -> resized PNG/JPEG for a vision model.
4. Plain text -> as is.
"""
import io
from dataclasses import dataclass, field
from typing import List, Optional

from django.conf import settings
from PIL import Image

from .errors import PermanentProcessingError

# Long edge in pixels. Larger images cost more tokens without better accuracy.
MAX_IMAGE_EDGE = 1568
# Average text characters per page below which a PDF is treated as scanned.
MIN_TEXT_CHARS_PER_PAGE = 40
# Refuse images larger than this before decoding them (memory safety).
MAX_SOURCE_PIXELS = 40_000_000
# Highest render scale for PDF pages (2 = 144 dpi).
MAX_RENDER_SCALE = 2.0
JPEG_QUALITY = 85


@dataclass
class ImagePart:
    media_type: str
    data: bytes


@dataclass
class DocumentInput:
    source: str  # "pdf_text" | "pdf_scan" | "image" | "text"
    page_count: int
    text: Optional[str] = None
    images: List[ImagePart] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return bool(self.images)


def max_pages() -> int:
    return int(getattr(settings, "PROCESSING_MAX_PAGES", 20))


def max_bytes() -> int:
    return int(getattr(settings, "PROCESSING_MAX_FILE_BYTES", 20 * 1024 * 1024))


def read_file_bytes(file_field) -> bytes:
    if not file_field:
        raise PermanentProcessingError("Document has no file attached")
    try:
        file_field.open("rb")
        try:
            data = file_field.read(max_bytes() + 1)
        finally:
            file_field.close()
    except FileNotFoundError as exc:
        raise PermanentProcessingError(f"File not found in storage: {file_field.name}") from exc
    if len(data) > max_bytes():
        raise PermanentProcessingError(f"File is larger than {max_bytes()} bytes")
    if not data:
        raise PermanentProcessingError("File is empty")
    return data


def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8") or data[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    return "text" if decode_text(data) is not None else "unknown"


def decode_text(data: bytes):
    """UTF-8, else Windows-1252 (common for exports from older accounting tools). None for binary data."""
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        sample = text[:4096]
        printable = sum(ch.isprintable() or ch in "\r\n\t\f" for ch in sample)
        if sample and printable / len(sample) >= 0.95:
            return text
    return None


def load_document(file_field) -> DocumentInput:
    data = read_file_bytes(file_field)
    kind = sniff(data)
    if kind == "pdf":
        return _from_pdf(data)
    if kind == "image":
        return _from_image(data)
    if kind == "text":
        return DocumentInput(source="text", page_count=1, text=decode_text(data))
    raise PermanentProcessingError("Unsupported file type. Upload a PDF, an image or a text file.")


def _from_pdf(data: bytes) -> DocumentInput:
    """Route each page on its own: a PDF can mix text pages with scanned pages
    (for example a typed cover page and a scanned invoice), and no page may be dropped."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise PermanentProcessingError("The PDF file is damaged or encrypted") from exc
    try:
        count = len(pdf)
        if count == 0:
            raise PermanentProcessingError("The PDF has no pages")
        if count > max_pages():
            raise PermanentProcessingError(f"The PDF has {count} pages. The limit is {max_pages()}.")
        texts, images = [], []
        for i in range(count):
            page = pdf[i]
            textpage = page.get_textpage()
            text = (textpage.get_text_bounded() or "").strip()
            textpage.close()
            if len(text) >= MIN_TEXT_CHARS_PER_PAGE:
                texts.append(f"--- page {i + 1} ---\n{text}")
                continue
            # Render at the target size directly: a huge page (PDF allows 200 x 200 inches)
            # must never be rasterized at full resolution.
            scale = min(MAX_RENDER_SCALE, MAX_IMAGE_EDGE / max(page.get_size()))
            images.append(_image_part(page.render(scale=scale).to_pil()))
        source = "pdf_text" if not images else "pdf_scan" if not texts else "pdf_mixed"
        return DocumentInput(source=source, page_count=count, text="\n\n".join(texts) or None, images=images)
    finally:
        pdf.close()


def _from_image(data: bytes) -> DocumentInput:
    """Every frame of a multi-page TIFF or GIF, upright (phone photos carry EXIF rotation)."""
    from PIL import ImageOps, ImageSequence

    img = _open_image(data)
    frames = []
    for frame in ImageSequence.Iterator(img):
        if len(frames) >= max_pages():
            raise PermanentProcessingError(f"The image has more than {max_pages()} pages.")
        if frame.width * frame.height > MAX_SOURCE_PIXELS:
            raise PermanentProcessingError("The image is too large. Upload a smaller scan.")
        frames.append(_image_part(ImageOps.exif_transpose(frame.copy())))
    return DocumentInput(source="image", page_count=len(frames), images=frames)


def _open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:  # Pillow raises many types for bad input
        raise PermanentProcessingError("The image file cannot be read") from exc
    # Image.open reads only the header, so the size is known before decoding.
    if img.width * img.height > MAX_SOURCE_PIXELS:
        raise PermanentProcessingError("The image is too large. Upload a smaller scan.")
    try:
        if getattr(img, "draft", None) and img.format == "JPEG":
            img.draft("RGB", (MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))  # decode JPEGs at reduced size
        img.load()
        return img
    except Exception as exc:
        raise PermanentProcessingError("The image file cannot be read") from exc


def _image_part(img: Image.Image) -> ImagePart:
    """Resize to MAX_IMAGE_EDGE and encode as JPEG: scans and photos are several times
    smaller as JPEG than PNG, which keeps multi-page requests under the size limit."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    longest = max(img.size)
    if longest > MAX_IMAGE_EDGE:
        ratio = MAX_IMAGE_EDGE / longest
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return ImagePart(media_type="image/jpeg", data=buf.getvalue())


def preview_page(data: bytes, page: int = 1):
    """One page of a document as a JPEG for the review screen (works in every browser,
    including phones that cannot show PDFs inline). Returns (bytes, media_type, page_count).
    Text files are returned as text."""
    kind = sniff(data)
    if kind == "text":
        return decode_text(data).encode("utf-8"), "text/plain; charset=utf-8", 1
    if kind == "pdf":
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PermanentProcessingError("The PDF file is damaged or encrypted") from exc
        try:
            count = len(pdf)
            if not 1 <= page <= count:
                raise PermanentProcessingError(f"Page {page} does not exist")
            p = pdf[page - 1]
            scale = min(MAX_RENDER_SCALE, MAX_IMAGE_EDGE / max(p.get_size()))
            return _image_part(p.render(scale=scale).to_pil()).data, "image/jpeg", count
        finally:
            pdf.close()
    if kind == "image":
        from PIL import ImageOps, ImageSequence

        frames = list(ImageSequence.Iterator(_open_image(data)))[: max_pages()]
        if not 1 <= page <= len(frames):
            raise PermanentProcessingError(f"Page {page} does not exist")
        return _image_part(ImageOps.exif_transpose(frames[page - 1].copy())).data, "image/jpeg", len(frames)
    raise PermanentProcessingError("Unsupported file type")
