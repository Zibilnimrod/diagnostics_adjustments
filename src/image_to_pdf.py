"""Turn a diagnostic photo/screenshot into a pipeline-ready PDF.

Teachers receive many diagnostics as phone photos or WhatsApp screenshots. The
pipeline only reads PDFs, so an image has to become one — and a raw phone
screenshot carries a status bar, a browser address bar, and a navigation bar
that are pure noise for OCR. This module converts an image to a single-page PDF
and, when it detects that framing chrome, crops it away first.

Used by the GUI when a teacher drops an image onto a class, and reusable from
the CLI. Cropping is conservative: it only trims solid dark bands at the very
top and bottom (phone chrome is dark; the document is bright paper), so a plain
photo of a white page is left untouched.
"""

from __future__ import annotations

import io
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# A row counts as "document" if its mean brightness clears this (0-255).
_BRIGHT = 170
# Only trim chrome if the dark band is a meaningful slice of the height, so we
# never nibble a genuine dark header off a real document.
_MIN_BAND_FRACTION = 0.015


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def _document_bounds(gray) -> tuple[int, int]:
    """Return (top, bottom) rows of the bright document band within an image.

    Rows above the first bright row and below the last are treated as phone
    chrome and cropped, but only when those dark bands are non-trivial.
    """
    import numpy as np

    row_mean = np.asarray(gray).mean(axis=1)
    height = len(row_mean)
    bright = np.where(row_mean > _BRIGHT)[0]
    if bright.size == 0:
        return 0, height  # No bright band at all — don't crop, just convert.

    top, bottom = int(bright.min()), int(bright.max()) + 1
    min_band = int(height * _MIN_BAND_FRACTION)
    if top < min_band:
        top = 0
    if height - bottom < min_band:
        bottom = height
    # Small safety margin so we never clip the first/last line of text.
    top = max(0, top - 4)
    bottom = min(height, bottom + 4)
    return top, bottom


def convert(
    image_path: Path,
    out_path: Path | None = None,
    crop_phone_chrome: bool = True,
) -> Path:
    """Write a single-page PDF from an image and return its path.

    out_path defaults to the image path with a .pdf suffix.
    """
    from PIL import Image
    import fitz  # PyMuPDF

    image_path = Path(image_path)
    out_path = Path(out_path) if out_path else image_path.with_suffix(".pdf")

    img = Image.open(image_path)
    # Respect the phone's EXIF rotation, then drop EXIF so it can't re-rotate.
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGB")

    if crop_phone_chrome:
        try:
            top, bottom = _document_bounds(img.convert("L"))
            if bottom - top >= img.height * 0.4:  # sanity: kept most of the page
                img = img.crop((0, top, img.width, bottom))
        except Exception:
            pass  # numpy missing or odd image — convert without cropping.

    pdf = fitz.open()
    page = pdf.new_page(width=img.width, height=img.height)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    page.insert_image(fitz.Rect(0, 0, img.width, img.height), stream=buf.getvalue())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(str(out_path))
    pdf.close()
    return out_path
