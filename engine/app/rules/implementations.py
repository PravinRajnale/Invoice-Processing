"""The 49 rule implementations evaluated on every validation run.

Every function here is deterministic: same context in, same verdict out, every
time. No LLM call, no clock read beyond ``ctx.today``, no randomness. That is
what makes the decision reproducible and auditable (PRD 6.3, 18).

Money comparisons use ``Decimal`` with an explicit epsilon for rounding noise;
percentage deltas are computed and reported so a reviewer sees *how far* off a
value was, not merely that it failed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .. import jurisdiction as jurisdiction_mod
from ..config import doa_tier
from ..money import decimal_places, fmt_money, pct_delta, quantise
from ..normalise import (
    normalise_description,
    normalise_invoice_number,
    normalise_sku,
    normalise_uom,
    normalise_tax_id,
)
from .context import RuleContext
from .engine import FAIL, NA, PASS, UNKNOWN, WARN, Verdict, rule

ZERO = Decimal("0")
ONE = Decimal("1")


def _money(ctx: RuleContext, v: Optional[Decimal]) -> str:
    return fmt_money(v, ctx.currency)


# ======================================================================
# Gate 0 — Ingest
# ======================================================================

@rule("ING-01")
def ing_01(ctx: RuleContext) -> Verdict:
    doc = ctx.document
    if doc.get("encrypted"):
        return FAIL("File is password protected and cannot be read.",
                    actual="encrypted", expected="not encrypted")
    if doc.get("conversion_error"):
        return FAIL(doc["conversion_error"],
                    actual=doc.get("source_format_label") or doc.get("mime_type"),
                    expected="PDF, image or Word document")
    pages = doc.get("page_count") or 0
    if pages < 1:
        return FAIL("Document contains no pages.", actual=pages, expected=">= 1")
    if doc.get("corrupt"):
        return FAIL("File structure is corrupt.", actual="corrupt", expected="well-formed")
    label = doc.get("source_format_label") or "PDF document"
    if doc.get("converted"):
        return PASS(
            f"{label} converted to a {pages}-page PDF for processing.",
            actual=f"{label}, {pages} page(s)",
            evidence={"source_format": doc.get("source_format"), "converted": True},
        )
    return PASS(f"Readable PDF, {pages} page(s).", actual=f"{pages} page(s)")


@rule("ING-02")
def ing_02(ctx: RuleContext) -> Verdict:
    cls = ctx.document.get("classification") or {}
    label = cls.get("label", "UNKNOWN")
    score = Decimal(str(cls.get("confidence", "0")))
    if label != "INVOICE":
        return FAIL(
            f"Document classified as {label}, not an invoice.",
            expected="INVOICE", actual=label, confidence=score,
            evidence={"classification": cls},
        )
    return PASS(f"Classified as an invoice (confidence {score:.2f}).",
                actual=label, confidence=score, evidence={"classification": cls})


@rule("ING-03")
def ing_03(ctx: RuleContext) -> Verdict:
    """Exact-bytes duplicate. Cheapest possible check and it runs before any OCR
    or LLM spend (PRD 2.2.8)."""
    sha = ctx.document["sha256"]
    prior = [
        d for d in ctx.store.find("documents", sha256=sha)
        if d["id"] != ctx.document.get("id")
    ]
    if prior:
        first = prior[0]
        inv = ctx.store.find_one("invoices", document_id=first["id"])
        return FAIL(
            "This exact file has already been submitted.",
            expected="unseen file hash", actual=sha[:16] + "…",
            evidence={
                "existing_document_id": first["id"],
                "existing_invoice_id": inv["id"] if inv else None,
                "existing_status": inv.get("status") if inv else None,
                "originally_uploaded_at": first.get("uploaded_at"),
                "sha256": sha,
            },
        )
    return PASS("File hash not seen before.", actual=sha[:16] + "…")


# ======================================================================
# Gate 1 — Extraction
# ======================================================================

@rule("EXT-01")
def ext_01(ctx: RuleContext) -> Verdict:
    num = ctx.invoice.get("invoice_number")
    return PASS(f"Invoice number {num}.", actual=num,
                evidence=ctx.field_evidence("header.invoice_number"))


@rule("EXT-02")
def ext_02(ctx: RuleContext) -> Verdict:
    if ctx.invoice.get("invoice_date_ambiguous"):
        raw = ctx.invoice.get("invoice_date_raw")
        return UNKNOWN(
            f"Date {raw!r} could be read as either DD/MM or MM/DD; both are valid dates.",
            blocked_by=[f"invoice.invoice_date (ambiguous format: {raw})"],
            evidence=ctx.field_evidence("header.invoice_date"),
        )
    d = ctx.invoice["invoice_date"]
    return PASS(f"Invoice date {d.isoformat()}.", actual=d.isoformat(),
                evidence=ctx.field_evidence("header.invoice_date"))


@rule("EXT-03")
def ext_03(ctx: RuleContext) -> Verdict:
    d = ctx.invoice["invoice_date"]
    # One day of slack absorbs timezone differences between vendor and us.
    limit = ctx.today
    if (d - limit).days > 1:
        return FAIL(f"Invoice is dated {(d - limit).days} days in the future.",
                    expected=f"<= {limit.isoformat()}", actual=d.isoformat(),
                    delta=f"{(d - limit).days} days",
                    evidence=ctx.field_evidence("header.invoice_date"))
    return PASS("Invoice date is not in the future.", actual=d.isoformat())


@rule("EXT-04")
def ext_04(ctx: RuleContext) -> Verdict:
    d = ctx.invoice["invoice_date"]
    age = (ctx.today - d).days
    cap = ctx.cfg.max_invoice_age_days
    if age > cap:
        return WARN(f"Invoice is {age} days old (limit {cap}).",
                    expected=f"<= {cap} days", actual=f"{age} days",
                    delta=f"{age - cap} days", threshold=f"{cap} days",
                    evidence=ctx.field_evidence("header.invoice_date"))
    return PASS(f"Invoice is {age} days old.", actual=f"{age} days",
                threshold=f"{cap} days")


@rule("EXT-05")
def ext_05(ctx: RuleContext) -> Verdict:
    name = ctx.invoice.get("vendor_name")
    tax = ctx.invoice.get("vendor_tax_id")
    found = name or tax
    return PASS(f"Vendor identity present ({'name' if name else 'tax ID'}).",
                actual=found,
                evidence=ctx.field_evidence("header.vendor_name", "header.vendor_tax_id"))


@rule("EXT-06")
def ext_06(ctx: RuleContext) -> Verdict:
    cur = ctx.invoice.get("currency")
    if not cur:
        return UNKNOWN(
            "No currency is printed on the document and none could be inferred "
            "from the vendor. Amounts cannot be interpreted until it is known.",
            blocked_by=["invoice.currency (no symbol, code or vendor default)"],
            evidence=ctx.field_evidence("header.currency"),
        )
    src = ctx.invoice.get("currency_source", "document")
    return PASS(f"Currency {cur} (from {src}).", actual=cur,
                evidence=ctx.field_evidence("header.currency"))


@rule("EXT-07")
def ext_07(ctx: RuleContext) -> Verdict:
    total = ctx.amount("grand_total")
    return PASS(f"Grand total {_money(ctx, total)}.", actual=_money(ctx, total),
                evidence=ctx.field_evidence("header.grand_total"))


@rule("EXT-08")
def ext_08(ctx: RuleContext) -> Verdict:
    sub, tax = ctx.amount("subtotal"), ctx.amount("tax_amount")
    return PASS(f"Subtotal {_money(ctx, sub)}, tax {_money(ctx, tax)}.",
                actual=f"{_money(ctx, sub)} + {_money(ctx, tax)}",
                evidence=ctx.field_evidence("header.subtotal", "header.tax_amount"))


@rule("EXT-09")
def ext_09(ctx: RuleContext) -> Verdict:
    n = len(ctx.lines)
    synthetic = any(l.get("synthetic") for l in ctx.lines)
    msg = f"{n} line item(s) extracted."
    if synthetic:
        msg += " Invoice is bundled; a single synthetic line was created."
    return PASS(msg, actual=f"{n} line(s)", evidence={"synthetic_line": synthetic})


@rule("EXT-10")
def ext_10(ctx: RuleContext) -> Verdict:
    po_no = ctx.invoice.get("po_number")
    if po_no:
        return PASS(f"PO reference {po_no} found on the document.", actual=po_no,
                    evidence=ctx.field_evidence("header.po_number"))
    m = ctx.po_match
    floor = ctx.cfg.confidence.po_inference_floor
    if m and m.matched_id and m.score >= floor:
        return PASS(
            f"No PO printed on the invoice; inferred {m.matched_id} "
            f"from vendor, amount and date ({m.method}, score {m.score:.2f}).",
            actual=m.matched_id, confidence=m.score,
            threshold=f"inference floor {floor}",
            evidence={"match": m.to_dict()},
        )
    score = m.score if m else ZERO
    return FAIL(
        f"No PO reference on the document and none could be inferred with "
        f"sufficient confidence (best {score:.2f} < {floor}).",
        expected=f"inference score >= {floor}", actual=f"{score:.2f}",
        threshold=f"inference floor {floor}",
        evidence={"match": m.to_dict() if m else None},
    )


@rule("EXT-11")
def ext_11(ctx: RuleContext) -> Verdict:
    """Confidence gate on the four fields the whole decision rests on.

    Reaching this rule at all means every listed field cleared the floor —
    otherwise the runner short-circuits to CANNOT_EVALUATE with the offending
    field named. That is the mechanism behind Edge Case 2's cascade.
    """
    floor = ctx.cfg.confidence.critical_field_floor
    detail = {
        p: str(ctx.fields[p].confidence)
        for p in ("header.invoice_number", "header.invoice_date",
                  "header.grand_total", "header.vendor_name")
        if p in ctx.fields
    }
    worst = min((Decimal(v) for v in detail.values()), default=Decimal("1"))
    return PASS(f"All critical fields read at or above {floor} (lowest {worst:.2f}).",
                expected=f">= {floor}", actual=f"{worst:.2f}",
                threshold=str(floor), confidence=worst, evidence=detail)


@rule("EXT-12")
def ext_12(ctx: RuleContext) -> Verdict:
    due = ctx.invoice.get("due_date")
    if due:
        return PASS(f"Due date {due.isoformat()} read from the document.",
                    actual=due.isoformat(),
                    evidence=ctx.field_evidence("header.due_date"))
    terms = (ctx.vendor or {}).get("payment_terms_days")
    return PASS(f"No due date printed; defaulting to vendor terms of {terms} days.",
                actual=f"net {terms}", evidence={"source": "vendor_master"})


# ======================================================================
# Gate 2 — Vendor
# ======================================================================

@rule("VEN-01")
def ven_01(ctx: RuleContext) -> Verdict:
    m = ctx.vendor_match
    floor = ctx.cfg.confidence.vendor_match_floor
    if not m or not m.matched_id:
        return FAIL(
            "Vendor could not be matched to the vendor master.",
            expected=f"match score >= {floor}",
            actual=f"best {m.score:.2f}" if m else "no candidate",
            threshold=str(floor),
            evidence={"candidates": m.candidates if m else []},
        )
    if m.score < floor and not m.method.endswith("EXACT"):
        return FAIL(
            f"Best vendor match {ctx.vendor['trade_name']} scored {m.score:.2f}, "
            f"below the {floor} floor.",
            expected=f">= {floor}", actual=f"{m.score:.2f}", threshold=str(floor),
            confidence=m.score, evidence={"candidates": m.candidates},
        )
    return PASS(
        f"Matched to {ctx.vendor['trade_name']} ({ctx.vendor['vendor_code']}) "
        f"by {m.method}.",
        expected=f">= {floor}", actual=f"{m.score:.2f}", threshold=str(floor),
        confidence=m.score,
        evidence={"vendor_id": ctx.vendor["id"], "method": m.method,
                  "candidates": m.candidates},
    )


@rule("VEN-02")
def ven_02(ctx: RuleContext) -> Verdict:
    v = ctx.vendor
    ok_status = v["status"] == "ACTIVE"
    ok_approval = v["approval_status"] == "APPROVED"
    if ok_status and ok_approval:
        return PASS("Vendor is active and approved.", actual="ACTIVE / APPROVED",
                    expected="ACTIVE / APPROVED")
    return FAIL(
        f"Vendor is {v['status']} / {v['approval_status']}.",
        expected="ACTIVE / APPROVED", actual=f"{v['status']} / {v['approval_status']}",
        evidence={"vendor_id": v["id"], "vendor_code": v["vendor_code"]},
    )


@rule("VEN-03")
def ven_03(ctx: RuleContext) -> Verdict:
    v = ctx.vendor
    if v["status"] in ("BLACKLISTED", "SUSPENDED"):
        return FAIL(
            f"Vendor {v['trade_name']} is {v['status']}. No invoice may be processed.",
            expected="not blacklisted or suspended", actual=v["status"],
            evidence={"vendor_id": v["id"], "vendor_code": v["vendor_code"]},
        )
    return PASS("Vendor is neither blacklisted nor suspended.", actual=v["status"])


@rule("VEN-04")
def ven_04(ctx: RuleContext) -> Verdict:
    on_invoice = normalise_tax_id(ctx.invoice.get("vendor_tax_id"))
    on_master = normalise_tax_id(ctx.vendor.get("tax_id"))
    if on_invoice == on_master:
        return PASS(f"Tax ID matches the vendor master ({ctx.vendor['tax_id']}).",
                    expected=ctx.vendor["tax_id"], actual=ctx.invoice["vendor_tax_id"],
                    evidence=ctx.field_evidence("header.vendor_tax_id"))
    return FAIL(
        f"Tax ID on the invoice ({ctx.invoice['vendor_tax_id']}) does not match the "
        f"registered ID ({ctx.vendor['tax_id']}). This is a fraud signal.",
        expected=ctx.vendor["tax_id"], actual=ctx.invoice["vendor_tax_id"],
        evidence=ctx.field_evidence("header.vendor_tax_id"),
    )


@rule("VEN-05")
def ven_05(ctx: RuleContext) -> Verdict:
    from ..money import parse_date

    d = ctx.invoice["invoice_date"]
    start, _ = parse_date(ctx.vendor["contract_start"])
    end, _ = parse_date(ctx.vendor["contract_end"])
    window = f"{start.isoformat()} to {end.isoformat()}"
    if start <= d <= end:
        return PASS(f"Invoice date falls inside the contract period ({window}).",
                    expected=window, actual=d.isoformat())
    side = "before contract start" if d < start else "after contract end"
    gap = (start - d).days if d < start else (d - end).days
    return WARN(f"Invoice dated {d.isoformat()} is {gap} days {side} ({window}).",
                expected=window, actual=d.isoformat(), delta=f"{gap} days")


@rule("VEN-06")
def ven_06(ctx: RuleContext) -> Verdict:
    cur = ctx.currency
    permitted = ctx.vendor.get("permitted_currencies") or [ctx.vendor["default_currency"]]
    if cur in permitted:
        return PASS(f"{cur} is permitted for this vendor.", expected="/".join(permitted),
                    actual=cur)
    return FAIL(f"Vendor is not permitted to invoice in {cur}.",
                expected="/".join(permitted), actual=cur)


# ======================================================================
# Gate 3 — Purchase Order
# ======================================================================

@rule("PO-01")
def po_01(ctx: RuleContext) -> Verdict:
    if ctx.po:
        m = ctx.po_match
        return PASS(f"Purchase order {ctx.po['po_number']} found "
                    f"({m.method if m else 'LOOKUP'}).",
                    actual=ctx.po["po_number"],
                    confidence=m.score if m else None,
                    evidence={"po_id": ctx.po["id"], "method": m.method if m else None})
    return FAIL(f"No purchase order matching {ctx.invoice.get('po_number')} exists.",
                expected="a PO in the procurement system",
                actual=ctx.invoice.get("po_number"),
                evidence={"searched": ctx.invoice.get("po_number")})


@rule("PO-02")
def po_02(ctx: RuleContext) -> Verdict:
    status = ctx.po["status"]
    allowed = ("OPEN", "PARTIALLY_INVOICED")
    if status in allowed:
        return PASS(f"PO status is {status}.", expected=" or ".join(allowed), actual=status)
    return FAIL(f"PO {ctx.po['po_number']} is {status} and cannot be invoiced against.",
                expected=" or ".join(allowed), actual=status,
                evidence={"po_id": ctx.po["id"]})


@rule("PO-03")
def po_03(ctx: RuleContext) -> Verdict:
    po_vendor = ctx.po["vendor_id"]
    inv_vendor = ctx.vendor["id"]
    if po_vendor == inv_vendor:
        return PASS(f"PO and invoice are both for {ctx.vendor['trade_name']}.",
                    expected=inv_vendor, actual=po_vendor)
    other = ctx.store.get("vendors", po_vendor)
    return FAIL(
        f"PO {ctx.po['po_number']} was raised on "
        f"{other['trade_name'] if other else po_vendor}, but this invoice is from "
        f"{ctx.vendor['trade_name']}.",
        expected=f"{other['trade_name']} ({po_vendor})" if other else po_vendor,
        actual=f"{ctx.vendor['trade_name']} ({inv_vendor})",
        evidence={"po_vendor_id": po_vendor, "invoice_vendor_id": inv_vendor},
    )


@rule("PO-04")
def po_04(ctx: RuleContext) -> Verdict:
    if ctx.po["currency"] == ctx.currency:
        return PASS(f"Both PO and invoice are in {ctx.currency}.",
                    expected=ctx.po["currency"], actual=ctx.currency)
    return FAIL(
        f"Invoice is in {ctx.currency} but PO {ctx.po['po_number']} is in "
        f"{ctx.po['currency']}. Implicit conversion is not permitted "
        f"(FIN-07 covers FX and is deferred).",
        expected=ctx.po["currency"], actual=ctx.currency,
    )


@rule("PO-05")
def po_05(ctx: RuleContext) -> Verdict:
    from ..money import parse_date

    po_date, _ = parse_date(ctx.po["po_date"])
    inv_date = ctx.invoice["invoice_date"]
    if po_date <= inv_date:
        return PASS(f"PO dated {po_date.isoformat()}, invoice {inv_date.isoformat()}.",
                    expected=f"PO <= {inv_date.isoformat()}", actual=po_date.isoformat())
    return WARN(
        f"Invoice is dated {(po_date - inv_date).days} days before its own purchase order.",
        expected=f"PO <= {inv_date.isoformat()}", actual=po_date.isoformat(),
        delta=f"{(po_date - inv_date).days} days",
    )


@rule("PO-06")
def po_06(ctx: RuleContext) -> Verdict:
    from ..money import parse_date

    valid_until, _ = parse_date(ctx.po["valid_until"])
    inv_date = ctx.invoice["invoice_date"]
    if inv_date <= valid_until:
        return PASS(f"PO valid until {valid_until.isoformat()}.",
                    expected=f"<= {valid_until.isoformat()}", actual=inv_date.isoformat())
    return FAIL(
        f"PO {ctx.po['po_number']} expired on {valid_until.isoformat()}; the invoice is "
        f"dated {inv_date.isoformat()} ({(inv_date - valid_until).days} days late).",
        expected=f"<= {valid_until.isoformat()}", actual=inv_date.isoformat(),
        delta=f"{(inv_date - valid_until).days} days",
    )


@rule("PO-07")
def po_07(ctx: RuleContext) -> Verdict:
    """Cumulative consumption against the ledger — Edge Case 1.

    The invoice under review is excluded from the prior total (its own
    provisional reservation must not count against itself), then added back as
    the claim being tested.
    """
    po = ctx.po
    if ctx.duplicate_of:
        return NA(
            f"Not evaluated — this invoice appears to duplicate "
            f"{ctx.duplicate_of.get('invoice_number')}, whose claim on "
            f"{po['po_number']} is already counted in the ledger. Adding this one "
            f"would report an over-consumption created by the duplication itself. "
            f"See the duplicate checks.",
            evidence={"duplicate_of": ctx.duplicate_of.get("id"),
                      "duplicate_of_number": ctx.duplicate_of.get("invoice_number")},
        )

    po_total = Decimal(str(po["total_amount"]))
    prior = ctx.store.po_consumed(po["id"], exclude_invoice_id=ctx.invoice_id)
    claim = ctx.amount("grand_total")
    cumulative = prior + claim
    remaining = po_total - prior

    allowed, label = ctx.effective_amount_tolerance(po_total)
    ceiling = po_total + allowed

    ledger = ctx.store.po_ledger(po["id"])
    prior_invoices = []
    for row in ledger:
        if row["invoice_id"] == ctx.invoice_id or row["status"] == "RELEASED":
            continue
        # Line-level rows carry quantity, not value — counting them here would
        # report one invoice several times over.
        if row.get("po_line_id"):
            continue
        inv = ctx.store.get("invoices", row["invoice_id"])
        prior_invoices.append({
            "invoice_id": row["invoice_id"],
            "invoice_number": inv.get("invoice_number") if inv else None,
            "invoice_date": inv.get("invoice_date") if inv else None,
            "amount": row["amount_consumed"],
            "status": row["status"],
        })

    evidence = {
        "po_id": po["id"],
        "po_number": po["po_number"],
        "po_total": str(po_total),
        "prior_consumed": str(prior),
        "this_invoice": str(claim),
        "cumulative": str(cumulative),
        "remaining_before": str(remaining),
        "prior_invoices": prior_invoices,
        "consumed_pct": str(
            (cumulative / po_total * 100).quantize(Decimal("0.01"))
            if po_total else Decimal("0")
        ),
    }

    if cumulative <= ceiling:
        return PASS(
            f"Cumulative billing {_money(ctx, cumulative)} of {_money(ctx, po_total)} "
            f"({evidence['consumed_pct']}% of PO) after {len(prior_invoices)} prior "
            f"invoice(s).",
            expected=f"<= {_money(ctx, ceiling)}", actual=_money(ctx, cumulative),
            threshold=label, evidence=evidence,
        )

    over = cumulative - po_total
    return FAIL(
        f"This invoice would take cumulative billing on {po['po_number']} to "
        f"{_money(ctx, cumulative)} — {evidence['consumed_pct']}% of the "
        f"{_money(ctx, po_total)} PO value. Only {_money(ctx, remaining)} remained "
        f"after {len(prior_invoices)} prior invoice(s).",
        expected=f"<= {_money(ctx, remaining)} remaining",
        actual=f"{_money(ctx, claim)} claimed",
        delta=_money(ctx, over), delta_pct=str(pct_delta(cumulative, po_total)),
        threshold=label, evidence=evidence,
    )


# ======================================================================
# Gate 4 — Financial
# ======================================================================

@rule("FIN-01")
def fin_01(ctx: RuleContext) -> Verdict:
    eps = ctx.cfg.tolerance.rounding_epsilon
    sub = ctx.amount("subtotal")
    tax = ctx.amount("tax_amount")
    disc = ctx.amount("discount_amount") or ZERO
    other = ctx.amount("other_charges") or ZERO
    total = ctx.amount("grand_total")

    computed = quantise(sub + tax - disc + other, ctx.currency)
    diff = (total - computed).copy_abs()
    formula = (f"{_money(ctx, sub)} + {_money(ctx, tax)}"
               f"{' − ' + _money(ctx, disc) if disc else ''}"
               f"{' + ' + _money(ctx, other) if other else ''}")

    evidence = ctx.field_evidence("header.subtotal", "header.tax_amount",
                                 "header.grand_total")
    evidence["formula"] = f"{formula} = {_money(ctx, computed)}"

    if diff <= eps:
        return PASS(f"{formula} = {_money(ctx, total)}.",
                    expected=_money(ctx, computed), actual=_money(ctx, total),
                    delta=_money(ctx, diff), threshold=f"±{eps}", evidence=evidence)
    return FAIL(
        f"Stated total {_money(ctx, total)} does not equal {formula} = "
        f"{_money(ctx, computed)}.",
        expected=_money(ctx, computed), actual=_money(ctx, total),
        delta=_money(ctx, diff), delta_pct=str(pct_delta(total, computed)),
        threshold=f"±{eps}", evidence=evidence,
    )


@rule("FIN-02")
def fin_02(ctx: RuleContext) -> Verdict:
    """Line totals against the stated subtotal.

    The epsilon scales with line count: each line may carry its own half-paisa
    of rounding, so a 40-line invoice legitimately drifts further than a 2-line
    one.
    """
    eps = ctx.cfg.tolerance.rounding_epsilon * max(len(ctx.lines), 1)
    line_sum = quantise(sum((l["line_total"] for l in ctx.lines), ZERO), ctx.currency)
    sub = ctx.amount("subtotal")
    diff = (sub - line_sum).copy_abs()

    evidence = {
        "line_count": len(ctx.lines),
        "lines": [{"line_no": l["line_no"], "description": l.get("description"),
                   "line_total": str(l["line_total"])} for l in ctx.lines],
        "sum": str(line_sum),
    }
    if diff <= eps:
        return PASS(f"{len(ctx.lines)} line(s) sum to {_money(ctx, line_sum)}.",
                    expected=_money(ctx, sub), actual=_money(ctx, line_sum),
                    delta=_money(ctx, diff), threshold=f"±{eps}", evidence=evidence)
    return FAIL(
        f"Line items sum to {_money(ctx, line_sum)} but the stated subtotal is "
        f"{_money(ctx, sub)}.",
        expected=_money(ctx, sub), actual=_money(ctx, line_sum),
        delta=_money(ctx, diff), delta_pct=str(pct_delta(line_sum, sub)),
        threshold=f"±{eps}", evidence=evidence,
    )


@rule("FIN-03")
def fin_03(ctx: RuleContext) -> Verdict:
    """Tax against the implied rate, judged under the applicable jurisdiction.

    Three honest outcomes rather than one blunt one. Where we can establish
    whose tax rules apply, an unrecognised rate is a real finding. Where we
    cannot — a currency shared across twenty countries, an unfamiliar
    registration format — refusing to decide the invoice would be a false
    exception, and passing it silently would be a missed control. So the rate is
    reported, checked for plausibility, and flagged as unverified.
    """
    sub = ctx.amount("subtotal")
    tax = ctx.amount("tax_amount")
    tol = ctx.cfg.tolerance.tax_abs

    if sub == ZERO:
        if tax == ZERO:
            return PASS("Zero subtotal and zero tax.", expected="0.00", actual="0.00")
        return FAIL("Tax charged on a zero subtotal.", expected="0.00",
                    actual=_money(ctx, tax))

    implied = (tax / sub * Decimal(100)).quantize(Decimal("0.01"))
    jurisdiction, basis = jurisdiction_mod.infer(
        ctx.invoice.get("vendor_tax_id"),
        ctx.currency,
        (ctx.vendor or {}).get("registered_address"),
    )

    evidence = ctx.field_evidence("header.subtotal", "header.tax_amount")
    evidence.update({
        "implied_rate_pct": str(implied),
        "jurisdiction": jurisdiction.code if jurisdiction else None,
        "jurisdiction_basis": basis,
    })

    # --- jurisdiction unknown: verify the arithmetic, report the rate
    if jurisdiction is None:
        plausible = jurisdiction_mod.is_plausible(implied)
        evidence["rate_verified"] = False
        evidence["rate_plausible"] = plausible

        if not plausible:
            return WARN(
                f"Tax of {_money(ctx, tax)} on {_money(ctx, sub)} implies {implied}%, "
                f"which does not resemble any indirect-tax rate in use. The "
                f"jurisdiction could not be established ({basis}), so this is "
                f"reported rather than failed.",
                expected="a recognisable indirect-tax rate", actual=f"{implied}%",
                threshold="plausibility only — jurisdiction unknown", evidence=evidence,
            )
        return PASS(
            f"Tax of {_money(ctx, tax)} implies {implied}% on {_money(ctx, sub)}. "
            f"The rate is plausible but unverified — {basis}.",
            expected="a recognisable indirect-tax rate", actual=f"{implied}%",
            threshold="plausibility only — jurisdiction unknown", evidence=evidence,
        )

    # --- jurisdiction known: hold it to that jurisdiction's rate set
    permitted = jurisdiction.rates
    nearest = jurisdiction_mod.nearest_rate(implied, permitted)
    expected_tax = quantise(sub * nearest / Decimal(100), ctx.currency)
    diff = (tax - expected_tax).copy_abs()

    evidence.update({
        "nearest_permitted_rate_pct": str(nearest),
        "permitted_rates": [str(r) for r in permitted],
        "rate_verified": True,
    })
    label = f"{jurisdiction.label} {jurisdiction.tax_label}"

    if diff > tol:
        return FAIL(
            f"Tax of {_money(ctx, tax)} implies a rate of {implied}%, which is not a "
            f"recognised {label} rate ({basis}). At the nearest permitted rate "
            f"({nearest}%) the "
            f"tax would be {_money(ctx, expected_tax)}.",
            expected=_money(ctx, expected_tax), actual=_money(ctx, tax),
            delta=_money(ctx, diff), delta_pct=str(pct_delta(tax, expected_tax)),
            threshold=f"±{tol} at a {label} rate "
                      f"({', '.join(str(r) for r in permitted)}%)",
            evidence=evidence,
        )
    return PASS(
        f"Tax of {_money(ctx, tax)} is consistent with the {nearest}% {label} rate "
        f"on {_money(ctx, sub)}.",
        expected=_money(ctx, expected_tax), actual=_money(ctx, tax),
        delta=_money(ctx, diff),
        threshold=f"±{tol} at a {label} rate", evidence=evidence,
    )


@rule("FIN-04")
def fin_04(ctx: RuleContext) -> Verdict:
    total = ctx.amount("grand_total")
    if total > ZERO:
        return PASS(f"Grand total {_money(ctx, total)} is positive.",
                    expected="> 0", actual=_money(ctx, total))
    return FAIL(
        f"Grand total is {_money(ctx, total)}. Credit notes must be submitted as a "
        f"separate document type, not as a negative invoice.",
        expected="> 0", actual=_money(ctx, total),
        evidence=ctx.field_evidence("header.grand_total"),
    )


def _po_scope(ctx: RuleContext) -> Tuple[Decimal, str]:
    """The PO value this invoice should be compared against.

    PRD FIN-05 is written as ``abs(inv − po_scope)``, not ``po.total_amount``,
    and the distinction matters. On a PO that permits partial invoicing, the
    full PO value is the wrong yardstick — a legitimate first invoice covering
    40% of a PO would fail by 60%. The applicable scope is what *this* invoice
    bills, priced at the contracted rates: the sum of (invoiced quantity × PO
    unit price) over the matched lines, grossed up at the invoice's own tax
    rate.

    Cumulative exposure across invoices is PO-07's job, and unmatched or
    over-priced lines are LIN-06's and LIN-03's. Each check owns one question.
    """
    po_total = Decimal(str(ctx.po["total_amount"]))
    if not ctx.po.get("allows_partial_invoicing"):
        return po_total, "full PO value (partial invoicing not permitted)"

    matched = [m for m in ctx.line_matches if m.po_line_id]
    if not matched:
        return po_total, "full PO value (no lines matched to the PO)"

    net = ZERO
    for line in ctx.lines:
        m = ctx.match_for(line["line_no"])
        po_line = ctx.po_line(m.po_line_id if m else None)
        if po_line is None:
            # Off-PO line: use what was billed. LIN-06 reports it separately;
            # excluding it here would make the scope silently forgiving.
            net += line["line_total"]
        else:
            net += line["quantity"] * Decimal(str(po_line["unit_price"]))

    subtotal = ctx.amount("subtotal")
    tax = ctx.amount("tax_amount")
    if subtotal and subtotal != ZERO and tax is not None:
        net = net * (ONE + tax / subtotal)

    return quantise(net, ctx.currency), "contracted value of the lines billed"


@rule("FIN-05")
def fin_05(ctx: RuleContext) -> Verdict:
    """Header total against the PO. Deliberately independent of LIN-03 — a pass
    here must never suppress a line-level failure (Edge Case 4)."""
    scope, scope_label = _po_scope(ctx)
    total = ctx.amount("grand_total")
    allowed, label = ctx.effective_amount_tolerance(scope)
    diff = (total - scope).copy_abs()
    pct = pct_delta(total, scope)

    evidence = {
        "po_number": ctx.po["po_number"],
        "po_total": str(ctx.po["total_amount"]),
        "comparison_scope": str(scope),
        "scope_basis": scope_label,
        "invoice_total": str(total),
        "allowed_variance": str(allowed),
    }
    if diff <= allowed:
        return PASS(
            f"Invoice total {_money(ctx, total)} is within tolerance of the "
            f"{_money(ctx, scope)} {scope_label} ({pct}%).",
            expected=_money(ctx, scope), actual=_money(ctx, total),
            delta=_money(ctx, diff), delta_pct=str(pct), threshold=label,
            evidence=evidence,
        )
    return FAIL(
        f"Invoice total {_money(ctx, total)} differs from the {_money(ctx, scope)} "
        f"{scope_label} by {_money(ctx, diff)} ({pct}%), outside the allowed "
        f"{_money(ctx, allowed)}.",
        expected=_money(ctx, scope), actual=_money(ctx, total),
        delta=_money(ctx, diff), delta_pct=str(pct), threshold=label, evidence=evidence,
    )


@rule("FIN-06")
def fin_06(ctx: RuleContext) -> Verdict:
    from ..money import MINOR_UNITS

    allowed = MINOR_UNITS.get(ctx.currency, 2)
    offenders = []
    for path, key in (("header.grand_total", "grand_total"),
                      ("header.subtotal", "subtotal"),
                      ("header.tax_amount", "tax_amount")):
        v = ctx.amount(key)
        if v is not None and decimal_places(v) > allowed:
            offenders.append({"field": key, "value": str(v),
                              "places": decimal_places(v)})
    if offenders:
        return WARN(
            f"{len(offenders)} amount(s) carry more than {allowed} decimal places, "
            f"which usually indicates a misread.",
            expected=f"<= {allowed} dp", actual=f"{offenders[0]['places']} dp",
            threshold=f"{allowed} dp for {ctx.currency}", evidence={"fields": offenders},
        )
    return PASS(f"All amounts carry at most {allowed} decimal places.",
                expected=f"<= {allowed} dp", threshold=f"{allowed} dp for {ctx.currency}")


# ======================================================================
# Gate 5 — Line Items
# ======================================================================

@rule("LIN-01")
def lin_01(ctx: RuleContext) -> Verdict:
    floor = ctx.cfg.confidence.line_match_floor
    unmatched = [m for m in ctx.line_matches if not m.po_line_id]
    weak = [m for m in ctx.line_matches if m.po_line_id and m.score < floor]

    detail = [
        {"invoice_line_no": m.invoice_line_no, "po_line_id": m.po_line_id,
         "score": str(m.score), "method": m.method, "note": m.note}
        for m in ctx.line_matches
    ]
    if not unmatched and not weak:
        mean = (sum((m.score for m in ctx.line_matches), ZERO) /
                max(len(ctx.line_matches), 1))
        return PASS(f"All {len(ctx.line_matches)} line(s) matched to PO lines "
                    f"(mean score {mean:.2f}).",
                    expected=f"all lines >= {floor}", actual=f"{mean:.2f}",
                    threshold=str(floor), confidence=mean, evidence={"matches": detail})
    return FAIL(
        f"{len(unmatched)} line(s) could not be matched to the PO and "
        f"{len(weak)} matched below the {floor} confidence floor.",
        expected=f"all lines >= {floor}",
        actual=f"{len(unmatched)} unmatched, {len(weak)} weak",
        threshold=str(floor), evidence={"matches": detail},
    )


@rule("LIN-02")
def lin_02(ctx: RuleContext) -> Verdict:
    if ctx.duplicate_of:
        return NA(
            f"Not evaluated — quantities from "
            f"{ctx.duplicate_of.get('invoice_number')} are already counted in the "
            f"ledger and this invoice appears to duplicate it. See the duplicate "
            f"checks.",
            evidence={"duplicate_of": ctx.duplicate_of.get("id")},
        )

    tol_pct = ctx.cfg.tolerance.quantity_pct
    breaches = []
    for line in ctx.lines:
        m = ctx.match_for(line["line_no"])
        po_line = ctx.po_line(m.po_line_id if m else None)
        if not po_line:
            continue
        ordered = Decimal(str(po_line["quantity_ordered"]))
        prior = ctx.store.po_line_consumed_qty(po_line["id"],
                                               exclude_invoice_id=ctx.invoice_id)
        cumulative = prior + line["quantity"]
        allowed = ordered + (ordered * tol_pct / Decimal(100))
        if cumulative > allowed:
            breaches.append({
                "line_no": line["line_no"], "sku": line.get("sku"),
                "description": line.get("description"),
                "ordered": str(ordered), "prior_invoiced": str(prior),
                "this_invoice": str(line["quantity"]), "cumulative": str(cumulative),
                "over_by": str(cumulative - ordered),
            })
    if breaches:
        first = breaches[0]
        return FAIL(
            f"{len(breaches)} line(s) exceed the ordered quantity. Line "
            f"{first['line_no']}: {first['cumulative']} billed cumulatively against "
            f"{first['ordered']} ordered.",
            expected=f"<= {first['ordered']}", actual=first["cumulative"],
            delta=first["over_by"], threshold=f"{tol_pct}% quantity tolerance",
            evidence={"breaches": breaches},
        )
    return PASS("Cumulative quantities are within ordered quantities on every line.",
                threshold=f"{tol_pct}% quantity tolerance")


@rule("LIN-03")
def lin_03(ctx: RuleContext) -> Verdict:
    """Per-line unit price against the contracted price.

    Runs regardless of whether FIN-05 passed. Edge Case 4 exists precisely
    because a header total can sit comfortably inside tolerance while individual
    lines are over-charged and offset by others.
    """
    tol_pct = ctx.cfg.tolerance.unit_price_pct
    breaches, checked = [], 0
    for line in ctx.lines:
        m = ctx.match_for(line["line_no"])
        po_line = ctx.po_line(m.po_line_id if m else None)
        if not po_line:
            continue
        checked += 1
        contracted = Decimal(str(po_line["unit_price"]))
        billed = line["unit_price"]
        if contracted == ZERO:
            continue
        pct = pct_delta(billed, contracted)
        if pct is not None and pct > tol_pct:
            breaches.append({
                "line_no": line["line_no"], "sku": line.get("sku"),
                "description": line.get("description"),
                "po_unit_price": str(contracted), "invoice_unit_price": str(billed),
                "delta": str(billed - contracted), "delta_pct": str(pct),
                "quantity": str(line["quantity"]),
                "value_impact": str(quantise((billed - contracted) * line["quantity"],
                                             ctx.currency)),
            })

    if breaches:
        worst = max(breaches, key=lambda b: Decimal(b["delta_pct"]))
        impact = sum((Decimal(b["value_impact"]) for b in breaches), ZERO)
        return FAIL(
            f"{len(breaches)} of {checked} line(s) are billed above the contracted unit "
            f"price. Line {worst['line_no']} ({worst['sku']}) is "
            f"{worst['delta_pct']}% over at {_money(ctx, Decimal(worst['invoice_unit_price']))} "
            f"against {_money(ctx, Decimal(worst['po_unit_price']))}. "
            f"Total over-charge {_money(ctx, impact)}.",
            expected=_money(ctx, Decimal(worst["po_unit_price"])),
            actual=_money(ctx, Decimal(worst["invoice_unit_price"])),
            delta=_money(ctx, Decimal(worst["delta"])),
            delta_pct=worst["delta_pct"], threshold=f"{tol_pct}%",
            evidence={"breaches": breaches, "total_overcharge": str(impact),
                      "lines_checked": checked},
        )
    return PASS(f"All {checked} matched line(s) are at or below the contracted unit price.",
                threshold=f"{tol_pct}%", evidence={"lines_checked": checked})


@rule("LIN-04")
def lin_04(ctx: RuleContext) -> Verdict:
    eps = ctx.cfg.tolerance.rounding_epsilon
    breaches = []
    for line in ctx.lines:
        disc = line.get("line_discount") or ZERO
        computed = quantise(line["quantity"] * line["unit_price"] - disc, ctx.currency)
        diff = (line["line_total"] - computed).copy_abs()
        if diff > eps:
            breaches.append({
                "line_no": line["line_no"], "description": line.get("description"),
                "quantity": str(line["quantity"]), "unit_price": str(line["unit_price"]),
                "discount": str(disc), "computed": str(computed),
                "stated": str(line["line_total"]), "delta": str(diff),
            })
    if breaches:
        first = breaches[0]
        return FAIL(
            f"{len(breaches)} line(s) have inconsistent arithmetic. Line "
            f"{first['line_no']}: {first['quantity']} × {first['unit_price']} = "
            f"{first['computed']}, but {first['stated']} is stated.",
            expected=first["computed"], actual=first["stated"], delta=first["delta"],
            threshold=f"±{eps}", evidence={"breaches": breaches},
        )
    return PASS(f"Quantity × unit price reconciles to the line total on all "
                f"{len(ctx.lines)} line(s).", threshold=f"±{eps}")


@rule("LIN-05")
def lin_05(ctx: RuleContext) -> Verdict:
    mismatches = []
    for line in ctx.lines:
        m = ctx.match_for(line["line_no"])
        po_line = ctx.po_line(m.po_line_id if m else None)
        if not po_line:
            continue
        inv_uom = normalise_uom(line.get("uom"))
        po_uom = normalise_uom(po_line.get("uom"))
        if inv_uom and po_uom and inv_uom != po_uom:
            mismatches.append({
                "line_no": line["line_no"], "description": line.get("description"),
                "invoice_uom": line.get("uom"), "po_uom": po_line.get("uom"),
                "invoice_uom_normalised": inv_uom, "po_uom_normalised": po_uom,
            })
    if mismatches:
        first = mismatches[0]
        return WARN(
            f"{len(mismatches)} line(s) have a unit-of-measure mismatch. Line "
            f"{first['line_no']} is billed in {first['invoice_uom']} but ordered in "
            f"{first['po_uom']} — quantities are not comparable.",
            expected=first["po_uom"], actual=first["invoice_uom"],
            evidence={"mismatches": mismatches},
        )
    return PASS("Units of measure agree with the PO on every matched line.")


@rule("LIN-06")
def lin_06(ctx: RuleContext) -> Verdict:
    off_po = []
    for line in ctx.lines:
        m = ctx.match_for(line["line_no"])
        if m and m.po_line_id:
            continue
        off_po.append({
            "line_no": line["line_no"], "sku": line.get("sku"),
            "description": line.get("description"),
            "line_total": str(line["line_total"]),
        })
    if off_po:
        value = sum((Decimal(o["line_total"]) for o in off_po), ZERO)
        return FAIL(
            f"{len(off_po)} line(s) worth {_money(ctx, value)} do not appear on PO "
            f"{ctx.po['po_number']}: "
            + "; ".join(f"“{o['description']}”" for o in off_po[:3]),
            expected="all lines present on the PO",
            actual=f"{len(off_po)} off-PO line(s)", delta=_money(ctx, value),
            evidence={"off_po_lines": off_po, "value": str(value)},
        )
    return PASS("Every billed line appears on the purchase order.")


@rule("LIN-07")
def lin_07(ctx: RuleContext) -> Verdict:
    seen: Dict[tuple, List[int]] = {}
    for line in ctx.lines:
        key = (normalise_sku(line.get("sku")) or
               normalise_description(line.get("description")),
               str(line["quantity"]), str(line["unit_price"]))
        seen.setdefault(key, []).append(line["line_no"])
    dupes = [{"key": " / ".join(k), "line_nos": v} for k, v in seen.items() if len(v) > 1]
    if dupes:
        return WARN(
            f"{len(dupes)} item(s) appear more than once with identical quantity and "
            f"price (lines {dupes[0]['line_nos']}).",
            actual=f"{len(dupes)} repeated item(s)", evidence={"duplicates": dupes},
        )
    return PASS("No repeated line items on this invoice.")


@rule("LIN-08")
def lin_08(ctx: RuleContext) -> Verdict:
    matched_po_lines = {m.po_line_id for m in ctx.line_matches if m.po_line_id}
    unbilled = [
        {"po_line_id": l["id"], "line_no": l["line_no"], "sku": l.get("sku"),
         "description": l.get("description"), "line_total": str(l["line_total"])}
        for l in ctx.po_lines if l["id"] not in matched_po_lines
    ]
    if not unbilled:
        return PASS(f"All {len(ctx.po_lines)} PO line(s) are billed on this invoice.")
    partial_ok = bool(ctx.po.get("allows_partial_invoicing"))
    msg = (f"{len(unbilled)} of {len(ctx.po_lines)} PO line(s) are not billed on this "
           f"invoice.")
    if partial_ok:
        return PASS(msg + " This PO permits partial invoicing.",
                    actual=f"{len(unbilled)} unbilled", evidence={"unbilled": unbilled})
    return PASS(msg + " This PO does not permit partial invoicing — see POL-02.",
                actual=f"{len(unbilled)} unbilled", evidence={"unbilled": unbilled})


# ======================================================================
# Gate 6 — Duplicates
# ======================================================================

def _prior_invoices(ctx: RuleContext, statuses: Optional[tuple] = None) -> List[dict]:
    """Other invoices from the same vendor, optionally filtered by status."""
    rows = ctx.store.where(
        "invoices",
        lambda r: r.get("vendor_id") == (ctx.vendor or {}).get("id")
        and r["id"] != ctx.invoice_id,
    )
    if statuses:
        rows = [r for r in rows if r.get("status") in statuses]
    return rows


SETTLED = ("APPROVED", "PENDING_APPROVAL", "REJECTED")
IN_FLIGHT = ("INGESTED", "EXTRACTING", "VALIDATING", "PENDING_REVIEW",
             "NEEDS_INFO", "DUPLICATE_HELD")


@rule("DUP-01")
def dup_01(ctx: RuleContext) -> Verdict:
    norm = ctx.invoice["invoice_number_normalised"]
    hits = [
        r for r in _prior_invoices(ctx, SETTLED)
        if r.get("invoice_number_normalised") == norm
    ]
    if hits:
        h = hits[0]
        return FAIL(
            f"Invoice number {ctx.invoice['invoice_number']} was already processed for "
            f"this vendor on {h.get('invoice_date')} (status {h.get('status')}).",
            expected="unseen invoice number", actual=ctx.invoice["invoice_number"],
            evidence={"existing": [
                {"invoice_id": r["id"], "invoice_number": r.get("invoice_number"),
                 "invoice_date": r.get("invoice_date"), "status": r.get("status"),
                 "grand_total": r.get("grand_total")} for r in hits]},
        )
    return PASS(f"No prior invoice numbered {ctx.invoice['invoice_number']} from this "
                f"vendor.")


@rule("DUP-02")
def dup_02(ctx: RuleContext) -> Verdict:
    """Near-duplicate on the *normalised* number — Edge Case 3.

    Both numbers are folded through the confusable map before comparison, so
    ``INV-2024-O871`` and ``INV-2024-0871`` collapse to the same string and the
    Levenshtein distance is 0.
    """
    norm = ctx.invoice["invoice_number_canonical"]
    max_dist = ctx.cfg.duplicate.fuzzy_number_max_distance
    total = ctx.amount("grand_total")

    near = []
    for r in _prior_invoices(ctx):
        other = r.get("invoice_number_canonical") or ""
        if not other:
            continue
        dist = Levenshtein.distance(norm, other)
        if dist > max_dist:
            continue
        same_amount = (
            total is not None and r.get("grand_total") is not None
            and (Decimal(str(r["grand_total"])) - total).copy_abs() <= Decimal("0.01")
        )
        near.append({
            "invoice_id": r["id"], "invoice_number": r.get("invoice_number"),
            "normalised": other, "distance": dist, "status": r.get("status"),
            "invoice_date": r.get("invoice_date"),
            "grand_total": r.get("grand_total"),
            "same_amount": same_amount,
        })

    if not near:
        return PASS(f"No near-duplicate invoice number (normalised form {norm}).",
                    threshold=f"distance <= {max_dist}",
                    evidence={"normalised_form": norm})

    near.sort(key=lambda n: (n["distance"], not n["same_amount"]))

    # Distance 0 after folding confusables means the two numbers ARE the same
    # number, written differently — that is the Edge Case 3 signal and it stands
    # on its own.
    exact = [n for n in near if n["distance"] == 0]
    if exact:
        n = exact[0]
        return FAIL(
            f"Invoice number {ctx.invoice['invoice_number']} is identical to "
            f"{n['invoice_number']} once confusable characters are normalised "
            f"(both become {norm}). Already submitted by this vendor "
            f"(status {n['status']}).",
            expected=f"no prior invoice normalising to {norm}", actual=str(n["distance"]),
            threshold="distance 0 after confusable folding",
            evidence={"near_duplicates": near, "normalised_form": norm},
        )

    # Distance 1-2 with genuinely different characters is far more often
    # sequential numbering (INV-A/8801 then INV-A/8847) than a duplicate.
    # Flagging those alone would generate constant false exceptions against the
    # PRD 3.3 target of <= 15%, so corroboration is required: the same vendor
    # billing the same amount under an almost-identical number is a duplicate;
    # an adjacent number for a different amount is just the next invoice.
    corroborated = [n for n in near if n["same_amount"]]
    if corroborated:
        n = corroborated[0]
        return FAIL(
            f"Invoice number {ctx.invoice['invoice_number']} is {n['distance']} "
            f"character(s) from {n['invoice_number']} and bills the identical "
            f"amount {_money(ctx, total)}. Two independent signals agree.",
            expected="no near-identical number at the same amount",
            actual=f"distance {n['distance']}, same amount",
            threshold=f"distance <= {max_dist} with matching amount",
            evidence={"near_duplicates": near, "normalised_form": norm},
        )

    n = near[0]
    return PASS(
        f"{len(near)} prior invoice number(s) within {max_dist} characters "
        f"(nearest {n['invoice_number']}, distance {n['distance']}), but none bills "
        f"the same amount — consistent with sequential numbering rather than a "
        f"duplicate.",
        actual=f"nearest distance {n['distance']}",
        threshold=f"distance <= {max_dist} with matching amount",
        evidence={"near_duplicates": near, "normalised_form": norm},
    )


@rule("DUP-03")
def dup_03(ctx: RuleContext) -> Verdict:
    """Independent corroboration: same vendor, same amount, near date, different
    number. Two independent signals agreeing is far stronger than one."""
    from ..money import parse_date

    window = ctx.cfg.duplicate.amount_date_window_days
    total = ctx.amount("grand_total")
    inv_date = ctx.invoice["invoice_date"]
    hits = []
    for r in _prior_invoices(ctx):
        if not r.get("grand_total") or not r.get("invoice_date"):
            continue
        try:
            other_total = Decimal(str(r["grand_total"]))
            other_date, _ = parse_date(r["invoice_date"])
        except Exception:
            continue
        if (other_total - total).copy_abs() > Decimal("0.01"):
            continue
        gap = abs((other_date - inv_date).days)
        if gap <= window:
            hits.append({
                "invoice_id": r["id"], "invoice_number": r.get("invoice_number"),
                "invoice_date": r["invoice_date"], "grand_total": str(other_total),
                "days_apart": gap, "status": r.get("status"),
            })
    if hits:
        h = hits[0]
        return FAIL(
            f"Another invoice from this vendor for exactly {_money(ctx, total)} is dated "
            f"{h['invoice_date']}, {h['days_apart']} day(s) apart "
            f"({h['invoice_number']}, status {h['status']}).",
            expected=f"no same-amount invoice within {window} days",
            actual=f"{len(hits)} match(es)", threshold=f"±{window} days, amount ±0.01",
            evidence={"matches": hits},
        )
    return PASS(f"No same-amount invoice from this vendor within {window} days.",
                threshold=f"±{window} days, amount ±0.01")


@rule("DUP-04")
def dup_04(ctx: RuleContext) -> Verdict:
    """Duplicates against invoices still in the queue.

    A history-only check misses the case where both copies arrive before either
    is approved — a race that is entirely realistic when a vendor re-sends.
    """
    norm = ctx.invoice["invoice_number_normalised"]
    total = ctx.amount("grand_total")
    hits = []
    for r in _prior_invoices(ctx, IN_FLIGHT):
        same_number = r.get("invoice_number_normalised") == norm
        same_amount = (
            r.get("grand_total") is not None
            and (Decimal(str(r["grand_total"])) - total).copy_abs() <= Decimal("0.01")
        )
        if same_number or same_amount:
            hits.append({
                "invoice_id": r["id"], "invoice_number": r.get("invoice_number"),
                "status": r.get("status"), "grand_total": r.get("grand_total"),
                "signal": "same number" if same_number else "same amount",
            })
    if hits:
        h = hits[0]
        return FAIL(
            f"An unapproved invoice from this vendor is already in the queue with the "
            f"{h['signal']} ({h['invoice_number']}, status {h['status']}).",
            expected="no in-flight match", actual=f"{len(hits)} match(es)",
            evidence={"in_flight": hits},
        )
    return PASS("No matching invoice is currently in the queue.")


# ======================================================================
# Gate 7 — Policy
# ======================================================================

@rule("POL-01")
def pol_01(ctx: RuleContext) -> Verdict:
    total = ctx.amount("grand_total")
    tier = doa_tier(total, ctx.cfg)
    limits = ctx.cfg.approval
    return PASS(
        f"{_money(ctx, total)} routes to {tier.replace('_', ' ').title()} under the "
        f"delegation-of-authority matrix.",
        actual=tier, expected=tier,
        threshold=(f"<= {limits.auto_approve_ceiling} processor; "
                   f"<= {limits.manager_ceiling} manager; above that controller"),
        evidence={"routed_to_role": tier, "amount": str(total)},
    )


@rule("POL-02")
def pol_02(ctx: RuleContext) -> Verdict:
    if ctx.po.get("allows_partial_invoicing"):
        return PASS(f"PO {ctx.po['po_number']} permits partial invoicing.",
                    expected="partial invoicing permitted", actual="permitted")
    prior = ctx.store.po_consumed(ctx.po["id"], exclude_invoice_id=ctx.invoice_id)
    if prior > ZERO:
        ledger = [
            r for r in ctx.store.po_ledger(ctx.po["id"])
            if r["invoice_id"] != ctx.invoice_id and r["status"] != "RELEASED"
        ]
        return FAIL(
            f"PO {ctx.po['po_number']} does not permit partial invoicing, but "
            f"{_money(ctx, prior)} has already been billed against it across "
            f"{len(ledger)} invoice(s).",
            expected="single invoice against this PO",
            actual=f"{len(ledger) + 1} invoice(s)", delta=_money(ctx, prior),
            evidence={"prior_consumed": str(prior), "ledger": ledger},
        )
    total = ctx.amount("grand_total")
    po_total = Decimal(str(ctx.po["total_amount"]))
    allowed, label = ctx.effective_amount_tolerance(po_total)
    if (po_total - total) > allowed:
        return FAIL(
            f"PO {ctx.po['po_number']} must be billed in full, but this invoice covers "
            f"only {_money(ctx, total)} of {_money(ctx, po_total)}.",
            expected=_money(ctx, po_total), actual=_money(ctx, total),
            delta=_money(ctx, po_total - total), threshold=label,
        )
    return PASS(f"PO requires a single invoice and this one covers the full value.",
                expected=_money(ctx, po_total), actual=_money(ctx, total))


@rule("POL-03")
def pol_03(ctx: RuleContext) -> Verdict:
    total = ctx.amount("grand_total")
    ceiling = ctx.cfg.approval.auto_approve_ceiling
    if total <= ceiling:
        return PASS(
            f"{_money(ctx, total)} is within the {_money(ctx, ceiling)} unattended "
            f"approval ceiling.",
            expected=f"<= {_money(ctx, ceiling)}", actual=_money(ctx, total),
            threshold=str(ceiling),
        )
    return PASS(
        f"{_money(ctx, total)} exceeds the {_money(ctx, ceiling)} unattended approval "
        f"ceiling; this invoice requires authorisation regardless of rule outcomes.",
        expected=f"<= {_money(ctx, ceiling)}", actual=_money(ctx, total),
        delta=_money(ctx, total - ceiling), threshold=str(ceiling),
        evidence={"auto_approve_available": False},
    )
