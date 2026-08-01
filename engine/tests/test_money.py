"""Money, number and date parsing — PRD 19.2 "unit"."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.money import (
    ParseError,
    decimal_places,
    detect_currency,
    fmt_money,
    infer_date_order,
    locale_date_order,
    parse_date,
    parse_date_with_order,
    parse_money,
    pct_delta,
    quantise,
)


@pytest.mark.parametrize("raw,expected", [
    ("1,04,832.50", "104832.50"),      # Indian grouping
    ("₹1,04,832.50", "104832.50"),
    ("Rs. 1,04,832.50/-", "104832.50"),
    ("INR 1,04,832.50", "104832.50"),
    ("1.04.832,50", "104832.50"),      # European grouping
    ("1,234.56", "1234.56"),
    ("1234,56", "1234.56"),            # comma as decimal separator
    ("1,234", "1234"),                 # 3 trailing digits = grouping
    ("$1,234.56", "1234.56"),
    ("(1,234.56)", "-1234.56"),        # accounting negative
    ("-500.00", "-500.00"),
    ("0.00", "0.00"),
    ("45000", "45000"),
    ("  1,84,500.00  ", "184500.00"),
    ("12,50,00,000.00", "125000000.00"),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "₹", "--"])
def test_parse_money_rejects_garbage(raw):
    """Unparseable input must raise, never silently become zero. A zero here
    would pass a fabricated figure into a payment decision."""
    with pytest.raises(ParseError):
        parse_money(raw)


def test_parse_money_never_uses_float():
    """0.1 + 0.2 must be exactly 0.3 through this path."""
    assert parse_money("0.1") + parse_money("0.2") == Decimal("0.3")


@pytest.mark.parametrize("raw,expected", [
    ("₹100", "INR"), ("Rs. 100", "INR"), ("INR 100", "INR"),
    ("$100", "USD"), ("US$100", "USD"), ("€100", "EUR"), ("£100", "GBP"),
    ("100", None),
])
def test_detect_currency(raw, expected):
    assert detect_currency(raw) == expected


def test_quantise_rounds_half_up_not_bankers():
    """At exactly .005 the value must round away from zero. Banker's rounding
    is wrong for invoicing (PRD 19.2 boundary tests)."""
    assert quantise(Decimal("2.005")) == Decimal("2.01")
    assert quantise(Decimal("2.015")) == Decimal("2.02")
    assert quantise(Decimal("2.025")) == Decimal("2.03")
    assert quantise(Decimal("100.5"), "JPY") == Decimal("101")


def test_decimal_places():
    assert decimal_places(Decimal("100.00")) == 2
    assert decimal_places(Decimal("100.123")) == 3
    assert decimal_places(Decimal("100")) == 0


def test_pct_delta():
    assert pct_delta(Decimal("102"), Decimal("100")) == Decimal("2.00")
    assert pct_delta(Decimal("98"), Decimal("100")) == Decimal("-2.00")
    # Zero base returns None rather than dividing — the caller must fall back
    # to an absolute tolerance instead of silently passing.
    assert pct_delta(Decimal("5"), Decimal("0")) is None


def test_fmt_money_indian_grouping():
    assert fmt_money(Decimal("1000000")) == "₹10,00,000.00"
    assert fmt_money(Decimal("184500")) == "₹1,84,500.00"
    assert fmt_money(Decimal("999")) == "₹999.00"
    assert fmt_money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert fmt_money(None) == "—"


# ----------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-06-04", date(2026, 6, 4)),
    ("27/07/2026", date(2026, 7, 27)),
    ("27-07-2026", date(2026, 7, 27)),
    ("4 Jun 2026", date(2026, 6, 4)),
    ("June 4, 2026", date(2026, 6, 4)),
])
def test_parse_date_unambiguous(raw, expected):
    value, ambiguous = parse_date(raw)
    assert value == expected
    assert not ambiguous


def test_parse_date_flags_genuine_ambiguity():
    """Both readings are real dates, so the value must be marked ambiguous
    rather than one being picked (PRD 9.3 EXT-02)."""
    _, ambiguous = parse_date("04/06/2026")
    assert ambiguous
    _, ambiguous = parse_date("12/07/2026")
    assert ambiguous


def test_parse_date_not_ambiguous_when_day_exceeds_twelve():
    _, ambiguous = parse_date("27/07/2026")
    assert not ambiguous


def test_infer_date_order_from_a_sibling_date():
    """One unambiguous date on the document settles the convention for all of
    them — otherwise every DD/MM invoice under the 13th is a false exception."""
    assert infer_date_order(["12/07/2026", "27/07/2026"]) == "DMY"
    assert infer_date_order(["07/12/2026", "07/27/2026"]) == "MDY"


def test_infer_date_order_refuses_when_document_contradicts_itself():
    assert infer_date_order(["27/07/2026", "07/27/2026"]) is None


def test_infer_date_order_falls_back_to_locale():
    assert infer_date_order(["12/07/2026"], locale_hint="DMY") == "DMY"
    assert infer_date_order(["12/07/2026"]) is None


def test_locale_date_order_from_the_documents_own_markers():
    """A tax registration format or a country-specific currency identifies the
    jurisdiction, and the jurisdiction settles the convention."""
    assert locale_date_order("27AABCS1429B1ZX", None) == "DMY"      # GSTIN
    assert locale_date_order("27 AABCS 1429 B1ZX", None) == "DMY"   # spaced GSTIN
    assert locale_date_order(None, "INR") == "DMY"
    assert locale_date_order("GB123456789", "GBP") == "DMY"         # UK is day-first
    assert locale_date_order("DE123456789", None) == "DMY"
    assert locale_date_order("SE556677889901", None) == "DMY"


def test_locale_date_order_is_month_first_only_for_the_us():
    """`10/01/2021` is 1 October in the US and 10 January almost everywhere
    else. Getting this backwards silently shifts payment terms."""
    assert locale_date_order("84-1234567", "USD") == "MDY"          # EIN
    assert locale_date_order(None, "USD") == "MDY"


def test_locale_date_order_declines_when_nothing_identifies_a_country():
    """EUR spans twenty countries. Guessing one would be an assumption, not
    evidence — so EXT-02 is left to report the date as unreadable."""
    assert locale_date_order(None, "EUR") is None
    assert locale_date_order(None, None) is None


def test_parse_date_with_order_resolves_ambiguity():
    value, ambiguous = parse_date_with_order("04/06/2026", "DMY")
    assert value == date(2026, 6, 4)
    assert not ambiguous

    value, ambiguous = parse_date_with_order("04/06/2026", "MDY")
    assert value == date(2026, 4, 6)
    assert not ambiguous


def test_parse_date_with_no_order_stays_ambiguous():
    """With nothing to disambiguate, the flag survives and EXT-02 reports
    CANNOT_EVALUATE rather than guessing."""
    _, ambiguous = parse_date_with_order("04/06/2026", None)
    assert ambiguous
