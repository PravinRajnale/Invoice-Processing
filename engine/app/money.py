"""Money, number and date normalisation.

PRD 2.2.10 / 18: no IEEE floats anywhere in the money path. Everything is
``decimal.Decimal``. The raw source string is always retained alongside the
parsed value so a reviewer can see exactly what was on the page.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Tuple

TWO_PLACES = Decimal("0.01")

# Currency symbols / prefixes seen on Indian and international invoices.
# Symbols and colloquial prefixes. Ambiguous symbols resolve to the most common
# reading; an ISO code printed anywhere on the amount always wins over a symbol,
# because "$" alone cannot distinguish USD from CAD, AUD, SGD or NZD.
_CURRENCY_TOKENS = {
    "₹": "INR", "RS.": "INR", "RS": "INR", "INR": "INR",
    "US$": "USD", "USD": "USD", "$": "USD",
    "€": "EUR", "EUR": "EUR",
    "£": "GBP", "GBP": "GBP",
    "¥": "JPY", "JPY": "JPY", "CNY": "CNY", "RMB": "CNY", "元": "CNY",
    "CHF": "CHF", "FR.": "CHF",
    "KR": "SEK", "SEK": "SEK", "NOK": "NOK", "DKK": "DKK",
    "AED": "AED", "DHS": "AED", "د.إ": "AED",
    "SAR": "SAR", "QAR": "QAR", "KWD": "KWD", "BHD": "BHD", "OMR": "OMR",
    "SGD": "SGD", "S$": "SGD",
    "AUD": "AUD", "A$": "AUD", "NZD": "NZD", "NZ$": "NZD",
    "CAD": "CAD", "C$": "CAD", "CA$": "CAD",
    "ZAR": "ZAR", "R": "ZAR",
    "PLN": "PLN", "CZK": "CZK", "HUF": "HUF", "RON": "RON",
    "TRY": "TRY", "₺": "TRY",
    "THB": "THB", "฿": "THB", "MYR": "MYR", "RM": "MYR",
    "IDR": "IDR", "PHP": "PHP", "₱": "PHP", "VND": "VND", "₫": "VND",
    "KRW": "KRW", "₩": "KRW", "HKD": "HKD", "HK$": "HKD",
    "BRL": "BRL", "R$": "BRL", "MXN": "MXN", "ARS": "ARS", "CLP": "CLP",
    "NGN": "NGN", "₦": "NGN", "KES": "KES", "EGP": "EGP", "GHS": "GHS",
    "ILS": "ILS", "₪": "ILS", "PKR": "PKR", "BDT": "BDT", "৳": "BDT",
    "LKR": "LKR", "NPR": "NPR", "RUB": "RUB", "₽": "RUB", "UAH": "UAH",
}

# Zero-decimal and three-decimal currencies. Getting this wrong means rounding a
# Japanese invoice to two places it does not have, or a Kuwaiti one to two
# instead of three.
MINOR_UNITS = {
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "IDR": 0, "HUF": 0,
    "KWD": 3, "BHD": 3, "OMR": 3,
}
DEFAULT_MINOR_UNITS = 2

_TRAILING_JUNK = re.compile(r"(/-|/=|only|\bonly\b)\s*$", re.IGNORECASE)
_NON_NUMERIC = re.compile(r"[^0-9.,\-]")


class ParseError(ValueError):
    """Raised when a value cannot be parsed. Callers turn this into
    CANNOT_EVALUATE — never into a guess."""


_ISO_CODE = re.compile(r"\b([A-Z]{3})\b")

# Every ISO-4217 code we are willing to accept when it appears as a bare word.
KNOWN_ISO = set(_CURRENCY_TOKENS.values()) | {
    "SEK", "NOK", "DKK", "ISK", "BGN", "HRK", "RSD", "MAD", "TND", "DZD",
    "JOD", "LBP", "IQD", "IRR", "AFN", "MMK", "KHR", "LAK", "MNT", "TWD",
    "COP", "PEN", "UYU", "BOB", "PYG", "CRC", "GTQ", "DOP", "JMD", "TTD",
    "XOF", "XAF", "ETB", "UGX", "TZS", "ZMW", "MUR", "BWP", "NAD",
}


def detect_currency(raw: str) -> Optional[str]:
    """Best-effort ISO-4217 detection from a raw string.

    An explicit three-letter code is checked first and wins outright: "$" cannot
    distinguish USD from CAD, AUD, SGD or NZD, but "CAD 1,200.00" can. Only when
    no code is present do we fall back to symbols, longest token first so "US$"
    beats "$" and "RS." beats "RS".
    """
    if not raw:
        return None
    upper = str(raw).upper()

    for code in _ISO_CODE.findall(upper):
        if code in KNOWN_ISO:
            return code

    for token in sorted(_CURRENCY_TOKENS, key=len, reverse=True):
        # Short alphabetic tokens need a word boundary. "R" for ZAR and "KR" for
        # SEK would otherwise match inside almost any English word, and a
        # currency inferred from the "R" in "FREIGHT" is worse than none.
        if token.isalpha() and len(token) <= 2:
            if re.search(rf"(?<![A-Z]){re.escape(token)}(?![A-Z])", upper):
                return _CURRENCY_TOKENS[token]
        elif token in upper:
            return _CURRENCY_TOKENS[token]
    return None


def parse_money(raw: str | int | float | Decimal | None) -> Decimal:
    """Parse a money string into Decimal.

    Handles Indian digit grouping (``1,04,832.50``), European grouping
    (``1.04.832,50``), currency symbols, and trailing ``/-``.

    Floats are accepted only because upstream JSON may contain them; they are
    routed through ``str()`` so the decimal expansion is the literal one.
    """
    if raw is None:
        raise ParseError("value is None")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int,)):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(str(raw))

    s = str(raw).strip()
    if not s:
        raise ParseError("empty string")

    negative = False
    # Accounting negatives: (1,234.00)
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    s = _TRAILING_JUNK.sub("", s).strip()
    s = _NON_NUMERIC.sub("", s)
    # Currency words leave their own punctuation behind: "Rs. 1,04,832.50"
    # reduces to ".1,04,832.50", whose leading dot would later be mistaken for a
    # decimal point. Strip separators that cannot belong to a number.
    s = s.strip(".,")
    if not s or not any(ch.isdigit() for ch in s):
        raise ParseError(f"no digits in {raw!r}")

    if s.startswith("-"):
        negative = True
        s = s[1:]

    s = _disambiguate_separators(s)

    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ParseError(f"cannot parse {raw!r}") from exc

    return -value if negative else value


def _disambiguate_separators(s: str) -> str:
    """Decide whether ',' or '.' is the decimal separator, then strip grouping.

    Rules, in order:
      1. Both present -> the rightmost one is the decimal separator.
      2. Only one present, appearing once, with 1-2 trailing digits -> decimal.
      3. Anything else -> grouping, strip it.

    ``1,04,832.50`` -> ``104832.50``  (Indian grouping)
    ``1.04.832,50`` -> ``104832.50``  (European grouping)
    ``1234,50``     -> ``1234.50``
    ``1,234``       -> ``1234``       (3 trailing digits = grouping)
    """
    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        grouping_sep = "." if decimal_sep == "," else ","
        s = s.replace(grouping_sep, "")
        return s.replace(decimal_sep, ".")

    sep = "," if has_comma else ("." if has_dot else None)
    if sep is None:
        return s

    parts = s.split(sep)
    if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
        return f"{parts[0]}.{parts[1]}"
    return s.replace(sep, "")


def decimal_places(raw: str | Decimal) -> int:
    """Number of digits after the decimal point in the *parsed* value."""
    value = raw if isinstance(raw, Decimal) else parse_money(raw)
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # NaN / Infinity
        return 0
    return max(0, -exponent)


def quantise(value: Decimal, currency: str = "INR") -> Decimal:
    """Round half-up to the currency's minor units. Banker's rounding is wrong
    for invoicing — a value at exactly .005 must round away from zero."""
    places = MINOR_UNITS.get(currency, DEFAULT_MINOR_UNITS)
    exp = Decimal(1).scaleb(-places)
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def within(actual: Decimal, expected: Decimal, epsilon: Decimal) -> bool:
    """Absolute-tolerance comparison used for rounding noise."""
    return abs(actual - expected) <= epsilon


def pct_delta(actual: Decimal, expected: Decimal) -> Optional[Decimal]:
    """Signed percentage difference of actual from expected.

    Returns None when expected is zero — the caller must then fall back to the
    absolute tolerance rather than dividing by zero or silently passing.
    """
    if expected == 0:
        return None
    return ((actual - expected) / expected * Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "BRL": "R$", "TRY": "₺", "THB": "฿", "PHP": "₱", "VND": "₫", "KRW": "₩",
    "NGN": "₦", "ILS": "₪", "BDT": "৳", "RUB": "₽",
}


def fmt_money(value: Decimal | None, currency: str = "INR") -> str:
    """Format for display and for deterministic rule messages."""
    if value is None:
        return "—"
    symbol = SYMBOLS.get(currency, f"{currency} ")
    places = MINOR_UNITS.get(currency, DEFAULT_MINOR_UNITS)
    q = quantise(value, currency)
    # Indian grouping is 2,2,3 rather than 3,3,3 and applies to INR only.
    if currency == "INR":
        return f"{symbol}{_indian_group(q)}"
    return f"{symbol}{q:,.{places}f}"


def _indian_group(value: Decimal) -> str:
    """2,2,3 grouping: 10,50,000.00"""
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    if len(whole) <= 3:
        grouped = whole
    else:
        head, tail = whole[:-3], whole[-3:]
        chunks = []
        while len(head) > 2:
            chunks.insert(0, head[-2:])
            head = head[:-2]
        if head:
            chunks.insert(0, head)
        grouped = ",".join(chunks) + "," + tail
    return f"{sign}{grouped}.{frac}"


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d",
]


def parse_date(raw: str | None) -> Tuple[date, bool]:
    """Parse a date, returning ``(value, ambiguous)``.

    ``ambiguous`` is True when the string could be read as either DD/MM or
    MM/DD and both readings are valid calendar dates. EXT-02 turns that into
    CANNOT_EVALUATE rather than picking one — guessing a date silently shifts
    payment terms and staleness checks (PRD 9.3 EXT-02).
    """
    if raw is None or not str(raw).strip():
        raise ParseError("empty date")
    s = str(raw).strip()

    ambiguous = _is_ambiguous_dmy(s)

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), ambiguous
        except ValueError:
            continue

    try:
        from dateutil import parser as dateutil_parser

        return dateutil_parser.parse(s, dayfirst=True).date(), ambiguous
    except Exception as exc:
        raise ParseError(f"cannot parse date {raw!r}") from exc


def _is_ambiguous_dmy(s: str) -> bool:
    """True when both the DD/MM and MM/DD readings are real dates and differ."""
    m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$", s)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    if a == b:
        return False
    return 1 <= a <= 12 and 1 <= b <= 12


def infer_date_order(
    samples: list[str | None], locale_hint: Optional[str] = None
) -> Optional[str]:
    """Infer whether a document writes dates day-first or month-first.

    A date like ``12/07/2026`` is ambiguous in isolation, but documents are not
    read in isolation. If any other date on the same page has a first component
    above 12 — ``27/07/2026`` — the whole document's convention is settled, and
    reporting the first date as unreadable would be a false exception.

    ``locale_hint`` is the fallback when no other date settles it — see
    ``locale_date_order``, which derives it from the invoice's own GSTIN or
    currency.

    Returns ``'DMY'``, ``'MDY'`` or ``None`` when nothing on the document
    disambiguates. Only in the ``None`` case does EXT-02 refuse to guess.
    """
    saw_dmy = saw_mdy = False
    for sample in samples:
        if not sample:
            continue
        m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$", str(sample).strip())
        if not m:
            continue
        first, second = int(m.group(1)), int(m.group(2))
        if first > 12 and second <= 12:
            saw_dmy = True
        elif second > 12 and first <= 12:
            saw_mdy = True

    if saw_dmy and not saw_mdy:
        return "DMY"
    if saw_mdy and not saw_dmy:
        return "MDY"
    # Both conventions present: the document contradicts itself. Do not pick one.
    if saw_dmy and saw_mdy:
        return None
    return locale_hint


GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][Z][0-9A-Z]$")


# The handful of places that write dates month-first. Everywhere else that this
# platform can identify writes day-first.
_MONTH_FIRST = {"US"}


def locale_date_order(tax_id: Optional[str], currency: Optional[str]) -> Optional[str]:
    """Infer date convention from the document's own locale markers.

    ``10/01/2021`` on a US invoice is 1 October nowhere and 10 January
    everywhere else — the ambiguity is real, but it is resolvable from evidence
    the document already carries. A tax registration number's format, or a
    currency specific to one country, identifies the jurisdiction; the
    jurisdiction settles the convention.

    This is evidence, not an assumption about the world. Where nothing on the
    document identifies a jurisdiction the function returns None and EXT-02
    declines to guess, which is the correct outcome for a EUR invoice that could
    have come from any of twenty countries.
    """
    from .jurisdiction import infer as infer_jurisdiction

    # Kept as a fast path and because a GSTIN is the most common case here.
    if tax_id:
        candidate = re.sub(r"[^A-Z0-9]", "", str(tax_id).upper())
        if GSTIN.match(candidate):
            return "DMY"

    jurisdiction, _ = infer_jurisdiction(tax_id, currency)
    if jurisdiction:
        return "MDY" if jurisdiction.code in _MONTH_FIRST else "DMY"

    # `infer` returns None for jurisdictions with no national tax-rate set, but
    # a country with no rate list still has a date convention.
    upper = (currency or "").upper()
    if upper == "USD":
        return "MDY"
    if upper == "INR":
        return "DMY"
    return None


def parse_date_with_order(raw: str | None, order: Optional[str]) -> Tuple[date, bool]:
    """Parse using a known document convention.

    When ``order`` settles the question, the returned ``ambiguous`` flag is
    False even for a superficially ambiguous string — the ambiguity was
    resolved by evidence, not by assumption.
    """
    if order is None:
        return parse_date(raw)

    value, ambiguous = parse_date(raw)
    if not ambiguous:
        return value, False

    m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$", str(raw).strip())
    if not m:
        return value, ambiguous

    first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    day, month = (first, second) if order == "DMY" else (second, first)
    try:
        return date(year, month, day), False
    except ValueError:
        return value, ambiguous


def days_between(a: date, b: date) -> int:
    return (b - a).days


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
