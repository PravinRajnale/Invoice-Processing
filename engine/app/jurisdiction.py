"""Jurisdiction inference and indirect-tax rate sets.

FIN-03 originally validated the implied tax rate against India's GST set. That
is right for an Indian invoice and wrong for every other one — a UK invoice at
20% VAT would fail a rule that has nothing to say about it.

The fix is not to widen the set until nothing fails; that would gut the control.
It is to work out **whose tax rules apply**, and then to be honest about the
three cases:

* jurisdiction known, rate recognised   -> PASS
* jurisdiction known, rate unrecognised -> FAIL, naming the permitted set
* jurisdiction unknown                  -> arithmetic checked, rate reported,
                                           WARN only if implausible

The third case matters most in practice. Refusing to decide a Swedish invoice
because we cannot prove 25% is a real Swedish rate is a false exception; passing
it silently is a missed control. Saying "this looks like 25%, which we cannot
verify for this jurisdiction" is the honest answer, and it is what a human
reviewer would say.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

D = Decimal


class Jurisdiction:
    def __init__(self, code: str, label: str, tax_label: str,
                 rates: List[Decimal], currency: str):
        self.code = code
        self.label = label
        self.tax_label = tax_label
        self.rates = rates
        self.currency = currency

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Jurisdiction {self.code}>"


# Standard and reduced rates in force for the jurisdictions the platform can
# positively identify. Deliberately not exhaustive — an unlisted country is
# handled by the unknown-jurisdiction path rather than being guessed at.
JURISDICTIONS: Dict[str, Jurisdiction] = {
    "IN": Jurisdiction("IN", "India", "GST", [D("0"), D("0.25"), D("3"), D("5"),
                                              D("12"), D("18"), D("28")], "INR"),
    "GB": Jurisdiction("GB", "United Kingdom", "VAT", [D("0"), D("5"), D("20")], "GBP"),
    "IE": Jurisdiction("IE", "Ireland", "VAT", [D("0"), D("4.8"), D("9"),
                                                D("13.5"), D("23")], "EUR"),
    "DE": Jurisdiction("DE", "Germany", "VAT", [D("0"), D("7"), D("19")], "EUR"),
    "FR": Jurisdiction("FR", "France", "VAT", [D("0"), D("2.1"), D("5.5"),
                                               D("10"), D("20")], "EUR"),
    "NL": Jurisdiction("NL", "Netherlands", "VAT", [D("0"), D("9"), D("21")], "EUR"),
    "ES": Jurisdiction("ES", "Spain", "VAT", [D("0"), D("4"), D("10"), D("21")], "EUR"),
    "IT": Jurisdiction("IT", "Italy", "VAT", [D("0"), D("4"), D("5"),
                                              D("10"), D("22")], "EUR"),
    "SE": Jurisdiction("SE", "Sweden", "VAT", [D("0"), D("6"), D("12"), D("25")], "SEK"),
    "AE": Jurisdiction("AE", "United Arab Emirates", "VAT", [D("0"), D("5")], "AED"),
    "SG": Jurisdiction("SG", "Singapore", "GST", [D("0"), D("9")], "SGD"),
    "AU": Jurisdiction("AU", "Australia", "GST", [D("0"), D("10")], "AUD"),
    "NZ": Jurisdiction("NZ", "New Zealand", "GST", [D("0"), D("15")], "NZD"),
    "CA": Jurisdiction("CA", "Canada", "GST/HST", [D("0"), D("5"), D("13"),
                                                   D("14.975"), D("15")], "CAD"),
    "ZA": Jurisdiction("ZA", "South Africa", "VAT", [D("0"), D("15")], "ZAR"),
    "JP": Jurisdiction("JP", "Japan", "consumption tax", [D("0"), D("8"), D("10")], "JPY"),
    "CH": Jurisdiction("CH", "Switzerland", "VAT", [D("0"), D("2.6"),
                                                    D("3.8"), D("8.1")], "CHF"),
}

# US sales tax is set by state and county and has no national rate set. Treating
# it as "unknown" is more honest than pretending a national list exists.
NO_NATIONAL_RATE_SET = {"US"}

# Tax registration number shapes, matched only when they are distinctive enough
# to identify a country on their own.
_TAX_ID_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("IN", re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")),  # GSTIN
    ("GB", re.compile(r"^GB[0-9]{9}([0-9]{3})?$|^GB(HA|GD)[0-9]{3}$")),
    ("DE", re.compile(r"^DE[0-9]{9}$")),
    ("FR", re.compile(r"^FR[0-9A-Z]{2}[0-9]{9}$")),
    ("NL", re.compile(r"^NL[0-9]{9}B[0-9]{2}$")),
    ("IE", re.compile(r"^IE[0-9][0-9A-Z+*][0-9]{5}[A-Z]{1,2}$")),
    ("ES", re.compile(r"^ES[0-9A-Z][0-9]{7}[0-9A-Z]$")),
    ("IT", re.compile(r"^IT[0-9]{11}$")),
    ("SE", re.compile(r"^SE[0-9]{12}$")),
    ("CH", re.compile(r"^CHE[0-9]{9}(MWST|TVA|IVA)?$")),
    ("AU", re.compile(r"^(ABN)?[0-9]{11}$")),
    ("US", re.compile(r"^[0-9]{2}-?[0-9]{7}$")),          # EIN
    ("ZA", re.compile(r"^4[0-9]{9}$")),
    ("SG", re.compile(r"^[0-9]{9}[A-Z]$|^[0-9]{8}[A-Z]$")),
]

_CURRENCY_TO_COUNTRY = {
    "INR": "IN", "GBP": "GB", "SEK": "SE", "AED": "AE", "SGD": "SG",
    "AUD": "AU", "NZD": "NZ", "CAD": "CA", "ZAR": "ZA", "JPY": "JP",
    "CHF": "CH", "USD": "US",
    # EUR spans twenty countries and identifies none of them.
}

# Indirect-tax rates in use somewhere in the world. Used only to judge whether
# an unverifiable rate is *plausible*, never to pass or fail on its own.
PLAUSIBLE_RATES = [
    D("0"), D("2.1"), D("2.6"), D("3"), D("3.8"), D("4"), D("4.8"), D("5"),
    D("5.5"), D("6"), D("7"), D("7.7"), D("8"), D("8.1"), D("9"), D("10"),
    D("12"), D("13"), D("13.5"), D("14"), D("15"), D("17"), D("18"), D("19"),
    D("20"), D("21"), D("22"), D("23"), D("24"), D("25"), D("27"), D("28"),
]


def normalise_tax_id(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(raw).upper())


def infer(tax_id: Optional[str] = None, currency: Optional[str] = None,
          address: Optional[str] = None) -> Tuple[Optional[Jurisdiction], str]:
    """Best-effort jurisdiction, with the evidence that produced it.

    Tax ID first — it is a registered identifier, not a formatting habit.
    Currency is a weaker signal and is only used when the ID says nothing.
    """
    candidate = normalise_tax_id(tax_id)
    if candidate:
        for code, pattern in _TAX_ID_PATTERNS:
            if pattern.match(candidate):
                if code in NO_NATIONAL_RATE_SET:
                    return None, (
                        f"tax registration looks like a {code} identifier, which has "
                        f"no single national rate set"
                    )
                jurisdiction = JURISDICTIONS.get(code)
                if jurisdiction:
                    return jurisdiction, f"tax registration number matches {code} format"

    code = _CURRENCY_TO_COUNTRY.get((currency or "").upper())
    if code:
        if code in NO_NATIONAL_RATE_SET:
            return None, (f"currency {currency} implies {code}, which has no single "
                          f"national rate set")
        jurisdiction = JURISDICTIONS.get(code)
        if jurisdiction:
            return jurisdiction, f"currency {currency} is specific to {code}"

    if currency:
        return None, (f"currency {currency} does not identify a single jurisdiction")
    return None, "no tax registration or currency to infer from"


def nearest_rate(implied: Decimal, rates: List[Decimal]) -> Decimal:
    return min(rates, key=lambda r: abs(r - implied))


def is_plausible(implied: Decimal, tolerance: Decimal = Decimal("0.6")) -> bool:
    """True when the implied rate is close to a rate used somewhere in the world.

    The tolerance absorbs rounding on the invoice — an 8.5% US sales tax computed
    on a rounded subtotal can imply 8.49%, and that is not evidence of anything.
    """
    if implied < 0 or implied > 40:
        return False
    return any(abs(implied - rate) <= tolerance for rate in PLAUSIBLE_RATES)
