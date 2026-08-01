"""Decision engine, confidence and risk — PRD 10 and 11.

Nothing in this module calls a model. Every number it produces is derived from
rule outcomes by an arithmetic that can be recomputed by hand from the stored
``rule_results`` rows. That is the point: "confidence 0.87" is only meaningful
if you can say where the 0.13 went, and an auditor must be able to reconstruct
the outcome without trusting anything.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG, Config, doa_tier
from .models import (
    Decision,
    DecisionOutcome,
    ExtractedField,
    MatchResult,
    Outcome,
    RuleResult,
    Severity,
    risk_band,
)

ZERO = Decimal("0")
ONE = Decimal("1")

# Blocker rules that fail because a record is absent from our master data rather
# than because the invoice is invalid. These route to review, not rejection
# (PRD R7). VEN-02/VEN-03 are deliberately NOT here: a vendor we *have* on file
# and have suspended is a real disqualifier.
MASTER_DATA_BLOCKERS = {"VEN-01", "PO-01"}

# Field criticality weights (PRD 11.1). Misreading the grand total matters more
# than misreading the payment terms, so a flat mean would be misleading.
FIELD_WEIGHTS = {
    "header.grand_total": 5,
    "header.invoice_number": 3,
    "header.invoice_date": 3,
    "header.vendor_name": 3,
    "header.po_number": 3,
    "header.subtotal": 2,
    "header.tax_amount": 2,
    "header.currency": 2,
}
LINE_WEIGHT = 3
DEFAULT_WEIGHT = 1


def _clamp(v: Decimal, lo: Decimal = ZERO, hi: Decimal = ONE) -> Decimal:
    return max(lo, min(hi, v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"))


# ----------------------------------------------------------------------
# 11.1 Extraction confidence
# ----------------------------------------------------------------------
def extraction_confidence(fields: Dict[str, ExtractedField]) -> Tuple[Decimal, Dict[str, Any]]:
    """Weighted mean of per-field confidence.

    Human-corrected fields are pinned to 1.00 upstream — a person reading a
    number off the page is ground truth, and the model's opinion of it stops
    being relevant.
    """
    if not fields:
        return ZERO, {"reason": "no fields extracted"}

    numerator, denominator = ZERO, ZERO
    contributions = []
    for path, f in fields.items():
        if path.startswith("lines["):
            weight = LINE_WEIGHT if path.endswith((".line_total", ".unit_price",
                                                   ".quantity")) else DEFAULT_WEIGHT
        else:
            weight = FIELD_WEIGHTS.get(path, DEFAULT_WEIGHT)
        conf = Decimal(f.confidence)
        numerator += Decimal(weight) * conf
        denominator += Decimal(weight)
        if weight >= 3:
            contributions.append({
                "field": path, "confidence": str(conf), "weight": weight,
                "method": f.extraction_method,
            })

    value = _q(numerator / denominator) if denominator else ZERO
    return value, {
        "weighted_mean": str(value),
        "fields_counted": len(fields),
        "high_weight_fields": sorted(contributions, key=lambda c: c["confidence"]),
    }


# ----------------------------------------------------------------------
# 11.2 Match confidence
# ----------------------------------------------------------------------
def match_confidence(
    vendor_match: Optional[MatchResult],
    po_match: Optional[MatchResult],
    line_scores: List[Decimal],
) -> Tuple[Decimal, Dict[str, Any]]:
    v = vendor_match.score if vendor_match else ZERO
    p = po_match.score if po_match else ZERO
    l = (sum(line_scores, ZERO) / Decimal(len(line_scores))) if line_scores else ZERO

    value = _q(Decimal("0.40") * v + Decimal("0.35") * p + Decimal("0.25") * l)
    return value, {
        "vendor": {"score": str(v), "weight": "0.40",
                   "method": vendor_match.method if vendor_match else None},
        "po": {"score": str(p), "weight": "0.35",
               "method": po_match.method if po_match else None},
        "lines": {"mean_score": str(_q(l)), "weight": "0.25", "count": len(line_scores)},
        "total": str(value),
    }


# ----------------------------------------------------------------------
# 11.3 Decision confidence — derived, never generated
# ----------------------------------------------------------------------
def decision_confidence(
    results: List[RuleResult],
    extraction_conf: Decimal,
    match_conf: Decimal,
    cfg: Config = CONFIG,
) -> Tuple[Decimal, Dict[str, Any]]:
    """How much the recommendation itself should be trusted.

    Four penalties. The third is the one that distinguishes a thoughtful system:
    a pass at 1.98% against a 2.00% tolerance carries far less information than
    a pass at 0.1%, and binary pass/fail throws that away.
    """
    base = ONE
    penalties: List[Dict[str, Any]] = []

    def charge(label: str, amount: Decimal, detail: str = "") -> None:
        nonlocal base
        amount = _q(amount)
        if amount <= ZERO:
            return
        base -= amount
        penalties.append({"reason": label, "penalty": str(amount), "detail": detail})

    # 1. Perception quality
    charge("Extraction uncertainty", (ONE - extraction_conf) * Decimal("0.35"),
           f"extraction confidence {extraction_conf}")
    charge("Match uncertainty", (ONE - match_conf) * Decimal("0.25"),
           f"match confidence {match_conf}")

    # 2. Coverage — rules we could not run at all
    applicable = [r for r in results if r.outcome != Outcome.NOT_APPLICABLE]
    unknown = [r for r in results if r.outcome == Outcome.CANNOT_EVALUATE]
    if applicable:
        charge("Unevaluable rules",
               Decimal(len(unknown)) / Decimal(len(applicable)) * Decimal("0.30"),
               f"{len(unknown)} of {len(applicable)} applicable rules could not be run")

    # 3. Proximity to a threshold boundary — a nervous pass is not a clean pass
    for r in results:
        if r.outcome != Outcome.PASS or not r.delta_pct or not r.threshold_applied:
            continue
        margin = _threshold_margin(r)
        if margin is None or margin >= Decimal("0.15"):
            continue
        charge(f"{r.rule_id} passed close to its limit",
               (Decimal("0.15") - margin) * Decimal("0.40"),
               f"{r.name}: {r.actual_value} against threshold {r.threshold_applied} "
               f"(margin {margin:.3f})")

    # 4. Warning drag
    warnings = [r for r in results if r.outcome == Outcome.WARN]
    charge("Warnings present", Decimal(len(warnings)) * Decimal("0.03"),
           f"{len(warnings)} warning(s)")

    value = _clamp(_q(base))
    return value, {
        "base": "1.0000",
        "penalties": penalties,
        "total_penalty": str(_q(ONE - value)),
        "decision_confidence": str(value),
        "auto_approve_floor": str(cfg.confidence.auto_approve_decision_floor),
    }


def _threshold_margin(r: RuleResult) -> Optional[Decimal]:
    """How far inside its threshold a passing rule sat, as a fraction.

    0.0 means it landed exactly on the limit; 1.0 means it used none of the
    allowance. Only meaningful for rules that report a percentage delta against
    a percentage threshold.
    """
    try:
        actual_pct = abs(Decimal(r.delta_pct))
    except Exception:
        return None

    threshold_pct = _extract_pct(r.threshold_applied or "")
    if threshold_pct is None or threshold_pct == ZERO:
        return None
    margin = (threshold_pct - actual_pct) / threshold_pct
    return _clamp(margin)


def _extract_pct(text: str) -> Optional[Decimal]:
    """Pull the first percentage out of a threshold label such as
    ``min(2.0% = 20000.00, 500.00) [global]``."""
    import re

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except Exception:
        return None


# ----------------------------------------------------------------------
# 11.4 Risk — exposure, not uncertainty
# ----------------------------------------------------------------------
def risk_score(
    results: List[RuleResult],
    grand_total: Optional[Decimal],
    security_flags: Optional[List[Dict[str, Any]]] = None,
    cfg: Config = CONFIG,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Fraud and leakage exposure.

    Orthogonal to confidence: you can be entirely confident that an invoice is
    fraudulent. Confidence asks "do we know?"; risk asks "how much is at stake
    if we are right?".
    """
    score = 0
    breakdown: List[Dict[str, Any]] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        if points <= 0:
            return
        score += points
        breakdown.append({"points": points, "reason": reason})

    by_id = {r.rule_id: r for r in results}
    failed = {r.rule_id for r in results if r.outcome == Outcome.FAIL}

    blockers = [r for r in results
                if r.severity == Severity.BLOCKER and r.outcome == Outcome.FAIL]
    criticals = [r for r in results
                 if r.severity == Severity.CRITICAL and r.outcome == Outcome.FAIL]
    crit_unknown = [r for r in results
                    if r.severity == Severity.CRITICAL
                    and r.outcome == Outcome.CANNOT_EVALUATE]
    warns = [r for r in results if r.outcome == Outcome.WARN]

    for r in blockers:
        add(50, f"{r.rule_id} blocker failure — {r.name}")
    for r in criticals:
        add(20, f"{r.rule_id} critical failure — {r.name}")
    for r in warns:
        add(5, f"{r.rule_id} warning — {r.name}")

    # Unevaluable critical rules are charged once per distinct *blocking input*,
    # not once per rule. PRD 11.4 charges 15 per rule, but CANNOT_EVALUATE
    # cascades by design — one unreadable grand total blocks EXT-11, FIN-01,
    # FIN-05, PO-07 and POL-01 at once. Charging each of them would put a merely
    # badly-scanned invoice into the SEVERE band and raise an escalation banner,
    # when the actual exposure is a single unread field. Risk measures exposure,
    # and the exposure here is one field, not five rules.
    blocking_inputs: Dict[str, List[str]] = {}
    for r in crit_unknown:
        for token in (r.blocked_by or ["unknown input"]):
            blocking_inputs.setdefault(token.split(" (")[0], []).append(r.rule_id)
    for token, rule_ids in sorted(blocking_inputs.items()):
        add(15, f"{token} could not be read, blocking "
                f"{len(rule_ids)} critical check(s): {', '.join(sorted(rule_ids))}")

    # Fraud-signal amplifiers
    if "VEN-04" in failed:
        add(25, "Tax ID on the invoice does not match the vendor master")
    if "VEN-07" in failed:
        add(25, "Bank details differ from the registered account")
    if any(rid.startswith("DUP") for rid in failed):
        add(20, "Duplicate invoice signals present")
    if "LIN-06" in failed:
        add(15, "Items billed that do not appear on the purchase order")
    if "PO-07" in failed:
        add(15, "Cumulative billing exceeds the purchase order value")
    if grand_total is not None and grand_total > cfg.approval.auto_approve_ceiling * 5:
        add(10, f"High value invoice ({grand_total})")

    # One anomaly, charged once. A single injected sentence typically trips
    # several detection patterns at the same time ("system note", "skip
    # validation", "pre-approved", "set status to"), and charging each would
    # let one attack saturate the score while telling a reviewer nothing extra.
    if security_flags:
        reasons = ", ".join(sorted({f.get("reason", "security anomaly")
                                    for f in security_flags}))
        add(30, f"Instruction-like content embedded in the document "
                f"({len(security_flags)} span(s): {reasons})")

    return min(score, 100), breakdown


# ----------------------------------------------------------------------
# 10.1 The decision algorithm
# ----------------------------------------------------------------------
def decide(
    results: List[RuleResult],
    grand_total: Optional[Decimal],
    extraction_conf: Decimal,
    match_conf: Decimal,
    security_flags: Optional[List[Dict[str, Any]]] = None,
    cfg: Config = CONFIG,
) -> Decision:
    """Six outcomes, first match wins, evaluated in a deliberate order.

    Ordering rationale (PRD 10.1):
      * duplicates precede blockers because a duplicate is a *hold*, not a
        rejection — the first invoice may have been the error, and rejecting
        the vendor's second copy destroys the relationship;
      * CANNOT_EVALUATE precedes critical failures because you must not reject
        on rules you never ran;
      * the warning cluster exists because three individually tolerable oddities
        on one invoice is a different signal from any one of them alone.
    """
    blockers = [r for r in results
                if r.severity == Severity.BLOCKER and r.outcome == Outcome.FAIL]
    criticals = [r for r in results
                 if r.severity == Severity.CRITICAL and r.outcome == Outcome.FAIL]
    warnings = [r for r in results if r.outcome == Outcome.WARN]
    unknowable = [r for r in results
                  if r.outcome == Outcome.CANNOT_EVALUATE
                  and r.severity in (Severity.BLOCKER, Severity.CRITICAL)]

    conf, conf_detail = decision_confidence(results, extraction_conf, match_conf, cfg)
    risk, risk_detail = risk_score(results, grand_total, security_flags, cfg)

    def build(outcome: DecisionOutcome, reason_codes: List[str],
              blocked_on: Optional[List[str]] = None,
              routed_to: Optional[str] = None) -> Decision:
        return Decision(
            outcome=outcome,
            decision_confidence=conf,
            risk_score=risk,
            risk_band=risk_band(risk),
            reason_codes=reason_codes,
            blocked_on=blocked_on or [],
            routed_to_role=routed_to,
            blocker_count=len(blockers),
            critical_fail_count=len(criticals),
            warning_count=len(warnings),
            cannot_evaluate_count=len([r for r in results
                                       if r.outcome == Outcome.CANNOT_EVALUATE]),
            confidence_breakdown={
                "extraction_confidence": str(extraction_conf),
                "match_confidence": str(match_conf),
                **conf_detail,
            },
            risk_breakdown=risk_detail,
        )

    # 1. Duplicates are held, never auto-rejected.
    dup_hits = [r for r in blockers + criticals if r.rule_id.startswith("DUP")]
    if dup_hits:
        return build(DecisionOutcome.DUPLICATE_BLOCK,
                     ["DUPLICATE_SUSPECTED"] + [r.rule_id for r in dup_hits])

    # 2. Unambiguous disqualifiers.
    #
    # Not every blocker is a disqualifier. "This vendor is not in our master" and
    # "this PO number does not exist here" are statements about *our* data, not
    # about the invoice — a new supplier is an ordinary business event, and PRD
    # R7 says unmatched vendors must "route to review, never auto-reject".
    # Rejecting them would also make the platform useless for any invoice
    # outside the seeded set.
    #
    # Genuine disqualifiers still reject: a blacklisted vendor, a cancelled PO,
    # a negative total, a document that is not an invoice, an unreadable file.
    if blockers:
        unknown_master = [r for r in blockers if r.rule_id in MASTER_DATA_BLOCKERS]
        disqualifying = [r for r in blockers if r.rule_id not in MASTER_DATA_BLOCKERS]

        if disqualifying:
            return build(DecisionOutcome.REJECT, [r.rule_id for r in disqualifying])

        return build(
            DecisionOutcome.MANUAL_REVIEW,
            ["UNKNOWN_TO_MASTER_DATA"] + [r.rule_id for r in unknown_master],
        )

    # 3. Never guess. Unknown is neither fine nor bad.
    if unknowable:
        blocked_on: List[str] = []
        for r in unknowable:
            blocked_on.extend(r.blocked_by)
        return build(DecisionOutcome.NEEDS_INFO,
                     [r.rule_id for r in unknowable],
                     blocked_on=sorted(set(blocked_on)))

    # 4. Material breach -> human.
    if criticals:
        return build(DecisionOutcome.MANUAL_REVIEW, [r.rule_id for r in criticals])

    # 5. Warning cluster -> human.
    if len(warnings) >= cfg.warning_cluster_threshold:
        return build(DecisionOutcome.MANUAL_REVIEW,
                     ["WARNING_CLUSTER"] + [r.rule_id for r in warnings])

    # 6. Clean. Confident enough, and small enough, to trust unattended?
    if conf < cfg.confidence.auto_approve_decision_floor:
        return build(DecisionOutcome.MANUAL_REVIEW, ["LOW_DECISION_CONFIDENCE"])

    if grand_total is not None and grand_total > cfg.approval.auto_approve_ceiling:
        return build(DecisionOutcome.APPROVE_PENDING_AUTHORISATION,
                     ["ABOVE_AUTO_APPROVE_CEILING"],
                     routed_to=doa_tier(grand_total, cfg))

    return build(DecisionOutcome.AUTO_APPROVE, [])
