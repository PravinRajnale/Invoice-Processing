"""S0–S2: intake, pre-flight, text acquisition.

The hash is computed before anything else so an exact re-submission
short-circuits to DUPLICATE_BLOCK without spending a single OCR or LLM call
(PRD 2.2.8).
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from . import formats
from .config import SETTINGS

log = logging.getLogger("engine.ingest")

MAX_FILE_BYTES = 50 * 1024 * 1024

# A page with usable embedded text needs more than a stray watermark. Below this
# many extractable characters we treat the page as a scan and route it to vision.
DIGITAL_TEXT_THRESHOLD = 120


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_document(data: bytes, filename: str) -> Tuple[str, Path, Path, Any]:
    """Persist the original and a PDF rendition of it.

    Returns ``(sha256, original_path, pdf_path, format)``. The hash is always
    over the *original* bytes, so idempotency is unaffected by how a file was
    converted. The rendition is what everything downstream reads, which is what
    keeps the viewer, the bbox overlay and the vision path format-agnostic.
    """
    digest = sha256_bytes(data)
    SETTINGS.storage_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix.lower() or ".bin"
    original_path = SETTINGS.storage_dir / f"{digest}{suffix}"
    if not original_path.exists():
        original_path.write_bytes(data)

    pdf_path = SETTINGS.storage_dir / f"{digest}.render.pdf"
    fmt = formats.sniff(data, filename)

    if fmt is formats.PDF:
        pdf_path = original_path if suffix == ".pdf" else pdf_path
        if pdf_path is not original_path and not pdf_path.exists():
            pdf_path.write_bytes(data)
        return digest, original_path, pdf_path, fmt

    if not pdf_path.exists():
        rendition, fmt, error = formats.to_pdf(data, filename)
        if error or not rendition:
            # Nothing to render. Pre-flight reports it and ING-01 fails with the
            # reason, rather than the pipeline throwing somewhere later.
            return digest, original_path, original_path, fmt
        pdf_path.write_bytes(rendition)

    return digest, original_path, pdf_path, fmt


def preflight(data: bytes, filename: str) -> Dict[str, Any]:
    """S1 — everything knowable about the file without reading its content.

    Runs against the PDF rendition, so a photograph of an invoice and a native
    PDF are described the same way. ``source_format`` records what actually
    arrived, because a reviewer should know they are looking at a converted
    document.
    """
    fmt = formats.sniff(data, filename)
    rendition, fmt, conversion_error = formats.to_pdf(data, filename)

    info: Dict[str, Any] = {
        "original_filename": filename,
        "size_bytes": len(data),
        "mime_type": fmt.mime,
        "source_format": fmt.key,
        "source_format_label": fmt.label,
        "converted": fmt is not formats.PDF,
        "conversion_error": conversion_error,
        "page_count": 0,
        "encrypted": False,
        "corrupt": bool(conversion_error),
        "is_scanned": fmt.needs_ocr,
        "oversized": len(data) > MAX_FILE_BYTES,
    }

    if conversion_error or not rendition:
        return info

    try:
        doc = fitz.open(stream=rendition, filetype="pdf")
    except Exception as exc:
        log.warning("PDF open failed for %s: %s", filename, exc)
        info["corrupt"] = True
        return info

    try:
        if doc.needs_pass:
            info["encrypted"] = True
            return info
        info["page_count"] = doc.page_count
        digital_pages = sum(
            1 for i in range(doc.page_count)
            if len(doc.load_page(i).get_text("text").strip()) >= DIGITAL_TEXT_THRESHOLD
        )
        info["digital_pages"] = digital_pages
        # A document is treated as scanned when most pages carry no real text.
        # An image stays "scanned" regardless: wrapping a photograph in a PDF
        # page gives it geometry, not a text layer.
        info["is_scanned"] = fmt.needs_ocr or (
            digital_pages < max(1, doc.page_count // 2 + 1)
        )
    except Exception as exc:
        log.warning("PDF preflight failed for %s: %s", filename, exc)
        info["corrupt"] = True
    finally:
        doc.close()

    return info


def extract_pages(data: bytes) -> List[Dict[str, Any]]:
    """S2 — page text plus word geometry, for digital PDFs.

    Bounding boxes are normalised to 0..1 of the page so the frontend overlay
    is resolution independent.
    """
    pages: List[Dict[str, Any]] = []
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            rect = page.rect
            width = float(rect.width) or 1.0
            height = float(rect.height) or 1.0

            words = []
            for w in page.get_text("words"):
                x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
                words.append({
                    "text": text,
                    "bbox": {
                        "x": round(x0 / width, 5),
                        "y": round(y0 / height, 5),
                        "w": round((x1 - x0) / width, 5),
                        "h": round((y1 - y0) / height, 5),
                    },
                })

            pages.append({
                "page_number": index + 1,
                "text": page.get_text("text"),
                "words": words,
                "width": width,
                "height": height,
            })
    finally:
        doc.close()
    return pages


def render_page_png(data: bytes, page_number: int, dpi: int = 150) -> Optional[bytes]:
    """Render one page to PNG for a vision call or a UI thumbnail."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return None
    try:
        if page_number < 1 or page_number > doc.page_count:
            return None
        pix = doc.load_page(page_number - 1).get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def page_data_uri(data: bytes, page_number: int, dpi: int = 150) -> Optional[str]:
    png = render_page_png(data, page_number, dpi)
    if png is None:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode()


def locate_text(pages: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
    """Find where a value appears on the page, so an extracted field can be
    linked back to the pixels it came from.

    Matching is done on a digits-and-letters-only form because the model returns
    normalised values ("184500.00") while the page shows formatted ones
    ("₹1,84,500.00").
    """
    if not needle:
        return None

    target = _squash(needle)
    if not target:
        return None

    for page in pages:
        words = page.get("words") or []
        # Single word.
        for word in words:
            if _squash(word["text"]) == target:
                return {"page_number": page["page_number"], "bbox": word["bbox"]}
        # Run of consecutive words (amounts often split around separators).
        for start in range(len(words)):
            joined = ""
            for end in range(start, min(start + 6, len(words))):
                joined += _squash(words[end]["text"])
                if joined == target:
                    return {
                        "page_number": page["page_number"],
                        "bbox": _union([w["bbox"] for w in words[start:end + 1]]),
                    }
                if len(joined) > len(target):
                    break
    return None


def _squash(text: str) -> str:
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def _union(boxes: List[Dict[str, float]]) -> Dict[str, float]:
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {"x": round(x0, 5), "y": round(y0, 5),
            "w": round(x1 - x0, 5), "h": round(y1 - y0, 5)}
