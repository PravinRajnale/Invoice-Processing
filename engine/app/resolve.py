"""S5: entity resolution — vendor, purchase order, and line-level mapping.

This is the one place where fuzzy and semantic techniques are legitimate, and
the discipline that keeps it safe is: **matching proposes, the engine decides.**
Every function returns candidates with scores and a named method. Applying the
confidence floor and turning a score into a rule outcome happens in the rule
implementations, never here.

The method is always recorded and always displayed. "Matched by fuzzy 0.91" is
honest; "Matched" is not (PRD 11.2).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from .config import CONFIG, Config
from .models import MatchResult
from .money import parse_date
from .normalise import (
    normalise_description,
    normalise_po_number,
    normalise_sku,
    normalise_tax_id,
    normalise_vendor_name,
)
from .rules.context import LineMatch

ZERO = Decimal("0")
ONE = Decimal("1")


def _score(value: float) -> Decimal:
    return (Decimal(str(value)) / Decimal(100)).quantize(Decimal("0.0001"))


# ----------------------------------------------------------------------
# Vendor
# ----------------------------------------------------------------------
def resolve_vendor(
    invoice_header: Dict[str, Any], store, cfg: Config = CONFIG
) -> MatchResult:
    """Tiered resolution: tax ID exact -> name exact -> alias -> fuzzy.

    Tax ID is tried first and wins outright because it is a registered
    identifier rather than a string a vendor chose how to spell today.
    """
    vendors = store.all("vendors")
    inv_tax = normalise_tax_id(invoice_header.get("vendor_tax_id"))
    inv_name = normalise_vendor_name(invoice_header.get("vendor_name"))

    if inv_tax:
        for v in vendors:
            if normalise_tax_id(v.get("tax_id")) == inv_tax:
                return MatchResult(v["id"], ONE, "TAX_ID_EXACT",
                                   [_candidate(v, ONE, "TAX_ID_EXACT")],
                                   f"GSTIN {v['tax_id']} matched exactly")

    if not inv_name:
        return MatchResult(None, ZERO, "NO_CANDIDATE", [],
                           "No vendor name or tax ID to match on")

    candidates: List[Dict[str, Any]] = []
    for v in vendors:
        best_score, best_method = ZERO, "FUZZY"

        for target, method in _vendor_name_variants(v):
            if not target:
                continue
            if target == inv_name:
                best_score, best_method = ONE, method
                break
            ratio = _score(fuzz.token_sort_ratio(inv_name, target))
            if ratio > best_score:
                best_score, best_method = ratio, f"FUZZY_{ratio:.2f}"

        if best_score > Decimal("0.5"):
            candidates.append(_candidate(v, best_score, best_method))

    if not candidates:
        return MatchResult(None, ZERO, "NO_CANDIDATE", [],
                           f"No vendor scored above 0.50 against {inv_name!r}")

    candidates.sort(key=lambda c: Decimal(c["score"]), reverse=True)
    top = candidates[0]
    return MatchResult(
        top["vendor_id"], Decimal(top["score"]), top["method"], candidates[:5],
        f"Best of {len(candidates)} candidate(s)",
    )


def _vendor_name_variants(vendor: Dict[str, Any]):
    yield normalise_vendor_name(vendor.get("legal_name")), "NAME_EXACT"
    yield normalise_vendor_name(vendor.get("trade_name")), "NAME_EXACT"
    for alias in vendor.get("aliases") or []:
        yield normalise_vendor_name(alias), "ALIAS"


def _candidate(vendor: Dict[str, Any], score: Decimal, method: str) -> Dict[str, Any]:
    return {
        "vendor_id": vendor["id"],
        "vendor_code": vendor.get("vendor_code"),
        "name": vendor.get("trade_name") or vendor.get("legal_name"),
        "tax_id": vendor.get("tax_id"),
        "status": vendor.get("status"),
        "score": str(score),
        "method": method,
    }


# ----------------------------------------------------------------------
# Purchase order
# ----------------------------------------------------------------------
def resolve_po(
    invoice_header: Dict[str, Any],
    vendor_id: Optional[str],
    store,
    cfg: Config = CONFIG,
) -> MatchResult:
    """Explicit reference first; inference only when nothing is printed."""
    pos = store.all("purchase_orders")
    printed = invoice_header.get("po_number_normalised") or normalise_po_number(
        invoice_header.get("po_number")
    )

    if printed:
        for po in pos:
            if normalise_po_number(po["po_number"]) == printed:
                return MatchResult(po["id"], ONE, "PO_NUMBER_EXACT",
                                   [_po_candidate(po, ONE, "PO_NUMBER_EXACT")],
                                   f"PO number {po['po_number']} printed on the invoice")

        # Printed but not found: report the near misses rather than silently
        # falling through to inference, which would mask a typo'd reference.
        near = []
        for po in pos:
            ratio = _score(fuzz.ratio(printed, normalise_po_number(po["po_number"])))
            if ratio >= Decimal("0.80"):
                near.append(_po_candidate(po, ratio, f"FUZZY_{ratio:.2f}"))
        near.sort(key=lambda c: Decimal(c["score"]), reverse=True)
        return MatchResult(
            None, Decimal(near[0]["score"]) if near else ZERO, "NOT_FOUND", near[:5],
            f"PO reference {invoice_header.get('po_number')} is printed on the invoice "
            f"but does not exist in the procurement system",
        )

    if not vendor_id:
        return MatchResult(None, ZERO, "NO_CANDIDATE", [],
                           "No PO reference and no vendor to infer from")

    return _infer_po(invoice_header, vendor_id, pos, store, cfg)


def _infer_po(
    invoice_header: Dict[str, Any],
    vendor_id: str,
    pos: List[Dict[str, Any]],
    store,
    cfg: Config,
) -> MatchResult:
    """Infer the PO from vendor, amount and date window.

    Scored, never assumed: the score is returned and EXT-10 applies the floor.
    An inference that lands below the floor produces a failed rule and a human
    look, which is the correct outcome — a wrong PO match silently validates
    against the wrong contract.
    """
    total = invoice_header.get("grand_total")
    inv_date = invoice_header.get("invoice_date")
    candidates: List[Dict[str, Any]] = []

    for po in pos:
        if po["vendor_id"] != vendor_id:
            continue
        if po["status"] not in ("OPEN", "PARTIALLY_INVOICED"):
            continue

        score = Decimal("0.50")     # right vendor, billable status
        reasons = ["vendor matches", f"status {po['status']}"]

        if total is not None:
            po_total = Decimal(str(po["total_amount"]))
            remaining = po_total - store.po_consumed(po["id"])
            if remaining > ZERO:
                ratio = min(total, remaining) / max(total, remaining)
                if ratio > Decimal("0.98"):
                    score += Decimal("0.35")
                    reasons.append("amount matches remaining balance")
                elif ratio > Decimal("0.80"):
                    score += Decimal("0.20")
                    reasons.append("amount close to remaining balance")
                elif total <= remaining:
                    score += Decimal("0.10")
                    reasons.append("amount fits within remaining balance")

        if inv_date is not None:
            try:
                po_date, _ = parse_date(po["po_date"])
                valid_until, _ = parse_date(po["valid_until"])
            except Exception:
                po_date = valid_until = None
            if po_date and valid_until and po_date <= inv_date <= valid_until:
                score += Decimal("0.15")
                reasons.append("invoice date inside PO validity window")

        score = min(score, Decimal("0.95"))   # inference never claims certainty
        candidates.append(_po_candidate(po, score, "INFERRED", "; ".join(reasons)))

    if not candidates:
        return MatchResult(None, ZERO, "NO_CANDIDATE", [],
                           "No open PO for this vendor")

    candidates.sort(key=lambda c: Decimal(c["score"]), reverse=True)
    top = candidates[0]

    # Two near-equal candidates is genuine ambiguity, not a match.
    if len(candidates) > 1:
        runner_up = Decimal(candidates[1]["score"])
        if Decimal(top["score"]) - runner_up < Decimal("0.10"):
            return MatchResult(
                None, Decimal(top["score"]), "AMBIGUOUS", candidates[:5],
                f"{len(candidates)} open POs score within 0.10 of each other; "
                f"inference is not safe",
            )

    return MatchResult(top["po_id"], Decimal(top["score"]), "INFERRED",
                       candidates[:5], top.get("reasons", ""))


def _po_candidate(po: Dict[str, Any], score: Decimal, method: str,
                  reasons: str = "") -> Dict[str, Any]:
    return {
        "po_id": po["id"],
        "po_number": po["po_number"],
        "vendor_id": po["vendor_id"],
        "status": po["status"],
        "total_amount": str(po["total_amount"]),
        "score": str(score),
        "method": method,
        "reasons": reasons,
    }


# ----------------------------------------------------------------------
# Line items
# ----------------------------------------------------------------------
def resolve_lines(
    invoice_lines: List[Dict[str, Any]],
    po_lines: List[Dict[str, Any]],
    cfg: Config = CONFIG,
) -> List[LineMatch]:
    """Map invoice lines to PO lines: SKU exact -> description fuzzy.

    Greedy with exclusivity — once a PO line is claimed it cannot be claimed
    again, which prevents two invoice lines both matching the same PO line and
    quietly doubling the apparent contracted quantity.
    """
    matches: List[LineMatch] = []
    claimed: set[str] = set()

    # Pass 1: SKU equality. Unambiguous, so it gets first refusal.
    for line in invoice_lines:
        sku = normalise_sku(line.get("sku"))
        if not sku:
            continue
        for po_line in po_lines:
            if po_line["id"] in claimed:
                continue
            if normalise_sku(po_line.get("sku")) == sku:
                claimed.add(po_line["id"])
                matches.append(LineMatch(line["line_no"], po_line["id"], ONE,
                                         "SKU_EXACT",
                                         f"SKU {line['sku']} matched exactly"))
                break

    matched_line_nos = {m.invoice_line_no for m in matches}

    # Pass 2: description similarity for whatever is left.
    for line in invoice_lines:
        if line["line_no"] in matched_line_nos:
            continue
        desc = normalise_description(line.get("description"))
        best, best_score = None, ZERO
        for po_line in po_lines:
            if po_line["id"] in claimed:
                continue
            target = normalise_description(po_line.get("description"))
            if not desc or not target:
                continue
            score = max(
                _score(fuzz.token_set_ratio(desc, target)),
                _score(fuzz.partial_ratio(desc, target)) * Decimal("0.95"),
            )
            if score > best_score:
                best, best_score = po_line, score

        floor = cfg.confidence.line_match_floor
        if best is not None and best_score >= floor:
            claimed.add(best["id"])
            matches.append(LineMatch(
                line["line_no"], best["id"], best_score, f"DESCRIPTION_{best_score:.2f}",
                f"“{line.get('description')}” matched “{best.get('description')}”",
            ))
        else:
            matches.append(LineMatch(
                line["line_no"], None, best_score, "UNMATCHED",
                (f"Best candidate scored {best_score:.2f}, below the {floor} floor"
                 if best is not None else "No PO line available to match against"),
            ))

    matches.sort(key=lambda m: m.invoice_line_no)
    return matches
