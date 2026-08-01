"""Human-in-the-loop actions — PRD 13.6, 14.4, 16.

Field correction, decision confirmation, override with segregation of duties,
and duplicate release. Every one of these writes an immutable audit event and
settles the PO ledger claim appropriately.

POL-06 lives here rather than in the rule catalogue's evaluation pass because it
is a check on an *actor performing an action*, not on an invoice — it can only
be evaluated at override time.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .models import DecisionOutcome, InvoiceStatus
from .store import STORE, now_iso

# PRD 13.6. Codes are what make override reasons analysable; free text alone
# cannot be counted, and counting is how thresholds get tuned.
REASON_CODES: Dict[str, Dict[str, Any]] = {
    "PO_AMENDMENT_PENDING": {
        "label": "PO amendment pending",
        "help": "A purchase order update was approved offline and is not yet in the system.",
        "requires_attachment": True,
    },
    "VENDOR_CLARIFICATION_RECEIVED": {
        "label": "Vendor clarification received",
        "help": "Evidence was obtained from the vendor outside this system.",
        "requires_attachment": True,
    },
    "TOLERANCE_JUDGEMENT": {
        "label": "Tolerance judgement",
        "help": "The variance is accepted as a commercial judgement.",
        "requires_attachment": False,
    },
    "EXTRACTION_ERROR": {
        "label": "Extraction error",
        "help": "The system misread the document; the data has since been corrected.",
        "requires_attachment": False,
    },
    "RULE_NOT_APPLICABLE": {
        "label": "Rule not applicable",
        "help": "The rule fired incorrectly for this case.",
        "requires_attachment": False,
        "requires_second_approver": True,
    },
    "COMMERCIAL_DECISION": {
        "label": "Commercial decision",
        "help": "Accepted for relationship or urgency reasons.",
        "requires_attachment": False,
    },
    "OTHER": {
        "label": "Other",
        "help": "Anything not covered above. A fuller justification is required.",
        "requires_attachment": False,
        "min_note_length": 80,
    },
}

APPROVING_OUTCOMES = {"APPROVED", "AUTO_APPROVE", "APPROVE_PENDING_AUTHORISATION"}


class ActionError(Exception):
    """Raised when an action is refused. ``code`` is the machine-readable
    reason the UI keys off."""

    def __init__(self, code: str, detail: str, status: int = 400,
                 extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.extra = extra or {}


# ----------------------------------------------------------------------
def current_decision(invoice_id: str) -> Optional[Dict[str, Any]]:
    rows = [d for d in STORE.find("decisions", invoice_id=invoice_id)
            if d.get("is_current")]
    return rows[-1] if rows else None


def _user(actor_id: str) -> Dict[str, Any]:
    user = STORE.get("users", actor_id)
    if not user:
        raise ActionError("UNKNOWN_ACTOR", f"No such user: {actor_id}", 401)
    return user


def _invoice(invoice_id: str) -> Dict[str, Any]:
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise ActionError("NOT_FOUND", f"No such invoice: {invoice_id}", 404)
    return invoice


def _amount(invoice: Dict[str, Any]) -> Decimal:
    try:
        return Decimal(str(invoice.get("grand_total") or "0"))
    except Exception:
        return Decimal("0")


# ----------------------------------------------------------------------
# Field correction — Edge Case 2's eight-second fix
# ----------------------------------------------------------------------
def correct_field(
    invoice_id: str, path: str, value: str, actor_id: str,
    reason: str = "OCR_CORRECTION",
) -> Dict[str, Any]:
    """Record a human correction. Confidence is pinned to 1.00.

    The correction is stored, not applied in place: the next validation run
    reads it and overlays it on a fresh extraction, so both the original reading
    and the corrected one remain visible.
    """
    invoice = _invoice(invoice_id)
    user = _user(actor_id)

    existing = STORE.find_one("extracted_fields", invoice_id=invoice_id,
                              field_path=path)
    previous = (existing or {}).get("normalised_value")

    if existing and existing.get("extraction_method") == "HUMAN_CORRECTED":
        STORE.update("extracted_fields", existing["id"],
                     normalised_value=value, raw_value=value,
                     previous_value=previous, corrected_by=actor_id,
                     corrected_at=now_iso())
    else:
        STORE.insert("extracted_fields", {
            "invoice_id": invoice_id,
            "field_path": path,
            "raw_value": value,
            "normalised_value": value,
            "confidence": "1.0000",
            "page_number": (existing or {}).get("page_number"),
            "bbox": (existing or {}).get("bbox"),
            "extraction_method": "HUMAN_CORRECTED",
            "corrected_by": actor_id,
            "corrected_at": now_iso(),
            "previous_value": previous,
        })

    STORE.insert("human_actions", {
        "invoice_id": invoice_id,
        "decision_id": (current_decision(invoice_id) or {}).get("id"),
        "actor_id": actor_id,
        "action": "CORRECT_FIELD",
        "reason_code": reason,
        "reason_note": f"{path}: {previous!r} -> {value!r}",
    })
    STORE.append_audit("invoice", invoice_id, "FIELD_CORRECTED", {
        "field_path": path, "previous_value": previous, "new_value": value,
        "reason": reason, "actor": user["display_name"],
    }, actor_id=actor_id, actor_type="HUMAN")

    return {"field_path": path, "previous_value": previous, "new_value": value,
            "confidence": "1.0000", "extraction_method": "HUMAN_CORRECTED"}


def corrected_fields(invoice_id: str) -> List[str]:
    return [r["field_path"] for r in STORE.find(
        "extracted_fields", invoice_id=invoice_id,
        extraction_method="HUMAN_CORRECTED")]


# ----------------------------------------------------------------------
# Confirming the recommendation
# ----------------------------------------------------------------------
def confirm(invoice_id: str, actor_id: str) -> Dict[str, Any]:
    invoice = _invoice(invoice_id)
    user = _user(actor_id)
    decision = current_decision(invoice_id)
    if not decision:
        raise ActionError("NO_DECISION", "This invoice has not been decided yet.")

    outcome = decision["outcome"]
    amount = _amount(invoice)

    if outcome == DecisionOutcome.DUPLICATE_BLOCK.value:
        raise ActionError(
            "USE_DUPLICATE_RELEASE",
            "A held duplicate is confirmed or released through the duplicate "
            "review, not through the standard confirmation.",
        )

    if outcome in ("APPROVE_PENDING_AUTHORISATION", "AUTO_APPROVE"):
        _assert_within_limit(user, amount)
        new_status = InvoiceStatus.APPROVED
        STORE.settle(invoice_id, "COMMITTED")
    elif outcome == DecisionOutcome.REJECT.value:
        new_status = InvoiceStatus.REJECTED
        STORE.settle(invoice_id, "RELEASED")
    else:
        raise ActionError(
            "NOT_CONFIRMABLE",
            f"A {outcome} recommendation cannot simply be confirmed — it needs "
            f"a correction, an override, or a request for information.",
        )

    STORE.update("invoices", invoice_id, status=new_status.value,
                 decided_by=actor_id, decided_at=now_iso())
    STORE.insert("human_actions", {
        "invoice_id": invoice_id, "decision_id": decision["id"],
        "actor_id": actor_id, "action": "CONFIRM",
        "ai_recommendation": outcome, "human_decision": new_status.value,
    })
    STORE.append_audit("invoice", invoice_id, "DECISION_CONFIRMED", {
        "ai_recommendation": outcome, "final_status": new_status.value,
        "actor": user["display_name"], "amount": str(amount),
    }, actor_id=actor_id, actor_type="HUMAN")

    return {"status": new_status.value, "ai_recommendation": outcome}


def _assert_within_limit(user: Dict[str, Any], amount: Decimal) -> None:
    limit = Decimal(str(user.get("approval_limit") or "0"))
    if amount > limit:
        raise ActionError(
            "ABOVE_APPROVAL_LIMIT",
            f"{user['display_name']} ({user['role']}) may approve up to {limit}; "
            f"this invoice is {amount}.",
            403, {"limit": str(limit), "amount": str(amount)},
        )


# ----------------------------------------------------------------------
# Override — PRD 13.6
# ----------------------------------------------------------------------
def override_requirements(invoice_id: str, actor_id: str,
                          human_decision: str, reason_code: str) -> Dict[str, Any]:
    """What this particular override will demand, computed before submission so
    the modal can show it rather than rejecting after the fact."""
    invoice = _invoice(invoice_id)
    decision = current_decision(invoice_id) or {}
    amount = _amount(invoice)
    spec = REASON_CODES.get(reason_code, {})

    toward_approval = human_decision in APPROVING_OUTCOMES
    ai_rejected = decision.get("outcome") in ("REJECT", "DUPLICATE_BLOCK")
    corrector_is_actor = actor_id in {
        r.get("corrected_by") for r in STORE.find(
            "extracted_fields", invoice_id=invoice_id,
            extraction_method="HUMAN_CORRECTED")
    }

    reasons: List[str] = []
    if amount > CONFIG.approval.manager_ceiling:
        reasons.append(f"amount exceeds the {CONFIG.approval.manager_ceiling} "
                       f"manager ceiling")
    if ai_rejected and toward_approval:
        reasons.append("the override reverses a rejection into an approval")
    if spec.get("requires_second_approver"):
        reasons.append(f"reason code {reason_code} always requires dual authorisation")
    if decision.get("outcome") == "DUPLICATE_BLOCK" and toward_approval:
        reasons.append("releasing a suspected duplicate is the highest-risk "
                       "override in the system")
    # POL-06: whoever corrected the extraction cannot also be the sole approver.
    sod_triggered = (corrector_is_actor and toward_approval
                     and amount > CONFIG.approval.sod_threshold)
    if sod_triggered:
        reasons.append("you corrected extraction on this invoice, and segregation "
                       "of duties applies above the SoD threshold")

    return {
        "requires_second_approver": bool(reasons),
        "second_approver_reasons": reasons,
        "requires_attachment": bool(spec.get("requires_attachment")),
        "min_note_length": spec.get(
            "min_note_length", 50 if toward_approval else 20),
        "toward_approval": toward_approval,
        "sod_triggered": sod_triggered,
        "ai_recommendation": decision.get("outcome"),
        "amount": str(amount),
    }


def override(
    invoice_id: str,
    actor_id: str,
    human_decision: str,
    reason_code: str,
    reason_note: str,
    second_approver_id: Optional[str] = None,
    attachment_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    invoice = _invoice(invoice_id)
    user = _user(actor_id)
    decision = current_decision(invoice_id)
    if not decision:
        raise ActionError("NO_DECISION", "This invoice has not been decided yet.")

    if reason_code not in REASON_CODES:
        raise ActionError("BAD_REASON_CODE",
                          f"{reason_code} is not a recognised reason code.")

    requirements = override_requirements(invoice_id, actor_id, human_decision,
                                         reason_code)

    note = (reason_note or "").strip()
    minimum = requirements["min_note_length"]
    if len(note) < minimum:
        raise ActionError(
            "JUSTIFICATION_TOO_SHORT",
            f"A justification of at least {minimum} characters is required "
            f"{'when overriding toward approval' if requirements['toward_approval'] else ''}"
            f". You wrote {len(note)}.",
            400, {"min_note_length": minimum, "actual": len(note)},
        )

    if requirements["requires_attachment"] and not attachment_id:
        raise ActionError(
            "ATTACHMENT_REQUIRED",
            f"Reason code {reason_code} requires supporting evidence to be attached.",
        )

    if requirements["requires_second_approver"]:
        if not second_approver_id:
            raise ActionError(
                "SOD_VIOLATION" if requirements["sod_triggered"]
                else "SECOND_APPROVER_REQUIRED",
                "A second approver is required because "
                + "; ".join(requirements["second_approver_reasons"]) + ".",
                403, {"reasons": requirements["second_approver_reasons"]},
            )
        if second_approver_id == actor_id:
            raise ActionError(
                "SOD_VIOLATION",
                "The second approver must be a different person.", 403,
            )
        approver = _user(second_approver_id)
        if approver["role"] not in ("AP_MANAGER", "CONTROLLER"):
            raise ActionError(
                "SECOND_APPROVER_INELIGIBLE",
                f"{approver['display_name']} is a {approver['role']} and cannot "
                f"act as second approver.", 403,
            )
        if human_decision in APPROVING_OUTCOMES:
            _assert_within_limit(approver, _amount(invoice))
    elif human_decision in APPROVING_OUTCOMES:
        _assert_within_limit(user, _amount(invoice))

    status_map = {
        "APPROVED": InvoiceStatus.APPROVED,
        "REJECTED": InvoiceStatus.REJECTED,
        "PENDING_APPROVAL": InvoiceStatus.PENDING_APPROVAL,
        "NEEDS_INFO": InvoiceStatus.NEEDS_INFO,
    }
    if human_decision not in status_map:
        raise ActionError("BAD_DECISION",
                          f"{human_decision} is not a valid override outcome.")
    new_status = status_map[human_decision]

    if new_status == InvoiceStatus.APPROVED:
        STORE.settle(invoice_id, "COMMITTED")
    elif new_status == InvoiceStatus.REJECTED:
        STORE.settle(invoice_id, "RELEASED")

    action = STORE.insert("human_actions", {
        "invoice_id": invoice_id,
        "decision_id": decision["id"],
        "actor_id": actor_id,
        "action": "OVERRIDE",
        "ai_recommendation": decision["outcome"],
        "human_decision": human_decision,
        "reason_code": reason_code,
        "reason_note": note,
        "attachment_id": attachment_id,
        "second_approver_id": second_approver_id,
        "second_approved_at": now_iso() if second_approver_id else None,
        "ip_address": ip_address,
        "user_agent": user_agent,
    })

    STORE.update("invoices", invoice_id, status=new_status.value,
                 decided_by=actor_id, decided_at=now_iso(), overridden=True)

    audit = STORE.append_audit("invoice", invoice_id, "DECISION_OVERRIDDEN", {
        "ai_recommendation": decision["outcome"],
        "human_decision": human_decision,
        "reason_code": reason_code,
        "reason_note": note,
        "actor": user["display_name"],
        "actor_role": user["role"],
        "second_approver_id": second_approver_id,
        "amount": str(_amount(invoice)),
        "failed_rules": decision.get("reason_codes", []),
        "ip_address": ip_address,
    }, actor_id=actor_id, actor_type="HUMAN")

    return {
        "status": new_status.value,
        "human_action_id": action["id"],
        "audit_event_id": audit["id"],
        "requires_second_approval": False,
        "second_approver_id": second_approver_id,
    }


# ----------------------------------------------------------------------
# Duplicate release — Edge Case 3
# ----------------------------------------------------------------------
def release_duplicate(
    invoice_id: str, actor_id: str, reason_code: str, reason_note: str,
    second_approver_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Release a held duplicate back into processing.

    Always requires a second approver: this is the single action in the system
    that can most directly cause a duplicate payment.
    """
    invoice = _invoice(invoice_id)
    user = _user(actor_id)
    if invoice.get("status") != InvoiceStatus.DUPLICATE_HELD.value:
        raise ActionError("NOT_HELD",
                          "This invoice is not being held as a duplicate.")

    if not second_approver_id:
        raise ActionError(
            "SECOND_APPROVER_REQUIRED",
            "Releasing a suspected duplicate always requires a second approver.",
            403,
        )
    if second_approver_id == actor_id:
        raise ActionError("SOD_VIOLATION",
                          "The second approver must be a different person.", 403)
    approver = _user(second_approver_id)
    if approver["role"] not in ("AP_MANAGER", "CONTROLLER"):
        raise ActionError("SECOND_APPROVER_INELIGIBLE",
                          f"{approver['display_name']} cannot authorise a "
                          f"duplicate release.", 403)
    if len((reason_note or "").strip()) < 50:
        raise ActionError("JUSTIFICATION_TOO_SHORT",
                          "Releasing a duplicate requires at least 50 characters "
                          "of justification.")

    STORE.update("invoices", invoice_id, status=InvoiceStatus.PENDING_REVIEW.value,
                 duplicate_released=True, duplicate_released_by=actor_id)
    STORE.insert("human_actions", {
        "invoice_id": invoice_id,
        "decision_id": (current_decision(invoice_id) or {}).get("id"),
        "actor_id": actor_id, "action": "RELEASE_DUPLICATE",
        "ai_recommendation": "DUPLICATE_BLOCK",
        "human_decision": "PENDING_REVIEW",
        "reason_code": reason_code, "reason_note": reason_note,
        "second_approver_id": second_approver_id,
        "second_approved_at": now_iso(),
    })
    audit = STORE.append_audit("invoice", invoice_id, "DUPLICATE_RELEASED", {
        "reason_code": reason_code, "reason_note": reason_note,
        "actor": user["display_name"], "second_approver": approver["display_name"],
    }, actor_id=actor_id, actor_type="HUMAN")

    return {"status": InvoiceStatus.PENDING_REVIEW.value,
            "audit_event_id": audit["id"]}


def confirm_duplicate(invoice_id: str, actor_id: str,
                      reason_note: str = "") -> Dict[str, Any]:
    """Confirm the hold. The ledger claim is released so it stops consuming
    PO headroom."""
    _invoice(invoice_id)
    user = _user(actor_id)

    STORE.settle(invoice_id, "RELEASED")
    STORE.update("invoices", invoice_id, status=InvoiceStatus.REJECTED.value,
                 duplicate_confirmed=True, decided_by=actor_id,
                 decided_at=now_iso())
    STORE.insert("human_actions", {
        "invoice_id": invoice_id,
        "decision_id": (current_decision(invoice_id) or {}).get("id"),
        "actor_id": actor_id, "action": "CONFIRM",
        "ai_recommendation": "DUPLICATE_BLOCK", "human_decision": "REJECTED",
        "reason_code": "CONFIRMED_DUPLICATE", "reason_note": reason_note,
    })
    audit = STORE.append_audit("invoice", invoice_id, "DUPLICATE_CONFIRMED", {
        "actor": user["display_name"], "reason_note": reason_note,
    }, actor_id=actor_id, actor_type="HUMAN")

    return {"status": InvoiceStatus.REJECTED.value, "audit_event_id": audit["id"]}


# ----------------------------------------------------------------------
def request_info(invoice_id: str, actor_id: str, target: str,
                 fields: List[str], message: str) -> Dict[str, Any]:
    _invoice(invoice_id)
    user = _user(actor_id)

    STORE.update("invoices", invoice_id, status=InvoiceStatus.NEEDS_INFO.value,
                 info_requested_from=target)
    STORE.insert("human_actions", {
        "invoice_id": invoice_id,
        "decision_id": (current_decision(invoice_id) or {}).get("id"),
        "actor_id": actor_id, "action": "REQUEST_INFO",
        "reason_code": f"INFO_FROM_{target}",
        "reason_note": message,
    })
    audit = STORE.append_audit("invoice", invoice_id, "INFORMATION_REQUESTED", {
        "target": target, "fields": fields, "message": message,
        "actor": user["display_name"],
    }, actor_id=actor_id, actor_type="HUMAN")

    return {"status": InvoiceStatus.NEEDS_INFO.value, "target": target,
            "fields": fields, "audit_event_id": audit["id"]}


def override_analytics() -> Dict[str, Any]:
    """Override patterns — the feedback loop that drives threshold tuning
    (PRD 2.2.9)."""
    actions = STORE.find("human_actions", action="OVERRIDE")
    decisions = STORE.all("decisions")

    by_code: Dict[str, int] = {}
    by_direction: Dict[str, int] = {}
    for a in actions:
        by_code[a.get("reason_code") or "UNKNOWN"] = \
            by_code.get(a.get("reason_code") or "UNKNOWN", 0) + 1
        direction = (f"{a.get('ai_recommendation')} → {a.get('human_decision')}")
        by_direction[direction] = by_direction.get(direction, 0) + 1

    total_recommendations = len([d for d in decisions if d.get("is_current")])
    rate = (len(actions) / total_recommendations) if total_recommendations else 0.0

    return {
        "override_count": len(actions),
        "recommendation_count": total_recommendations,
        "override_rate": round(rate, 4),
        "by_reason_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "by_direction": dict(sorted(by_direction.items(), key=lambda kv: -kv[1])),
        "target_rate": 0.10,
    }
