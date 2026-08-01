"""Normalisation shared by resolution, duplicate detection and the rules.

Duplicate detection in particular lives or dies on this module: Edge Case 3 is
undetectable under string equality because ``INV-2024-O871`` and
``INV-2024-0871`` differ by one confusable character.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Characters that OCR and human typists confuse. Mapping to a canonical form
# collapses the whole family so a fuzzy duplicate becomes an exact one.
CONFUSABLES = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
})

_SEPARATORS = re.compile(r"[\s\-_/\\.,#:]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]")

# Legal-form noise stripped before vendor name comparison.
_COMPANY_NOISE = re.compile(
    r"\b(PRIVATE|PVT|LIMITED|LTD|LLP|INC|CORP|CORPORATION|COMPANY|CO|"
    r"ENTERPRISES|INDUSTRIES|TRADERS|SOLUTIONS|SERVICES|WORKS|AND|M/S)\b"
)

UOM_CANON = {
    "EA": "EA", "EACH": "EA", "PC": "EA", "PCS": "EA", "PIECE": "EA",
    "PIECES": "EA", "NOS": "EA", "NO": "EA", "UNIT": "EA", "UNITS": "EA",
    "KG": "KG", "KGS": "KG", "KILOGRAM": "KG", "KILOGRAMS": "KG",
    "G": "G", "GM": "G", "GRAM": "G", "GRAMS": "G",
    "L": "L", "LTR": "L", "LITRE": "L", "LITER": "L", "LITRES": "L",
    "M": "M", "MTR": "M", "METRE": "M", "METER": "M", "METRES": "M",
    "BOX": "BOX", "BOXES": "BOX", "CTN": "BOX", "CARTON": "BOX",
    "PACK": "PACK", "PKT": "PACK", "PACKET": "PACK", "PKG": "PACK",
    "ROLL": "ROLL", "ROLLS": "ROLL",
    "REAM": "REAM", "REAMS": "REAM",
    "SET": "SET", "SETS": "SET",
    "LOT": "LOT", "JOB": "LOT", "LS": "LOT", "LUMPSUM": "LOT",
    "HR": "HR", "HRS": "HR", "HOUR": "HR", "HOURS": "HR",
    "DAY": "DAY", "DAYS": "DAY",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalise_invoice_number(raw: Optional[str]) -> str:
    """Form used for EXACT duplicate detection (DUP-01).

    Uppercase and strip separators, so ``INV-2024-0871``, ``inv 2024 0871`` and
    ``INV/2024/0871`` are recognised as the same number — but nothing more.
    Characters are preserved exactly, so ``INV-2024-O871`` (letter O) stays
    distinct here. That is deliberate: DUP-01 answers "have we seen this exact
    number", and folding confusables into it would leave DUP-02 with nothing to
    do and no way to explain the difference to a reviewer.
    """
    if not raw:
        return ""
    s = strip_accents(str(raw)).upper()
    return _NON_ALNUM.sub("", s)


def canonical_invoice_number(raw: Optional[str]) -> str:
    """Form used for FUZZY duplicate detection (DUP-02) — Edge Case 3.

    Everything ``normalise_invoice_number`` does, plus folding the confusable
    character set. ``INV-2024-0871`` and ``INV-2024-O871`` both collapse to
    ``1NV20240871``, turning a fuzzy duplicate into an exact one.

    Only ever used for comparison. The original string is what the UI displays,
    so the reviewer sees the single character that actually differs.
    """
    return normalise_invoice_number(raw).translate(CONFUSABLES)


def normalise_vendor_name(raw: Optional[str]) -> str:
    """Vendor name reduced to its distinctive core for matching."""
    if not raw:
        return ""
    s = strip_accents(str(raw)).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _COMPANY_NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalise_tax_id(raw: Optional[str]) -> str:
    """GSTIN / VAT / TIN reduced to alphanumerics, uppercased.

    Confusables are deliberately *not* folded here: a tax ID mismatch is a
    fraud signal (VEN-04) and folding O/0 would mask a real difference.
    """
    if not raw:
        return ""
    return _NON_ALNUM.sub("", strip_accents(str(raw)).upper())


def normalise_po_number(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return _NON_ALNUM.sub("", strip_accents(str(raw)).upper())


def normalise_uom(raw: Optional[str]) -> str:
    """Canonical unit of measure. Unknown units pass through uppercased rather
    than being silently coerced — LIN-05 should flag what it cannot map."""
    if not raw:
        return ""
    s = _NON_ALNUM.sub("", strip_accents(str(raw)).upper())
    return UOM_CANON.get(s, s)


def normalise_sku(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return _NON_ALNUM.sub("", strip_accents(str(raw)).upper())


def normalise_description(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = strip_accents(str(raw)).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()
