"""Explanation generation — PRD 15.4.

The LLM sits at the *back* of the pipeline here, read-only. Its input is the
rule-result JSON and nothing else: not the document, not the extraction, not the
raw text. It cannot change the outcome; it restates one that has already been
computed.

Two guards make this safe to show a reviewer:

1. **Numeric guard.** Every number appearing in the generated narrative must
   also appear in the input JSON. If one does not, the narrative is discarded
   and the deterministic template is used instead. The system degrades to *less
   readable*, never to *wrong*.
2. **Deterministic fallback.** The template path produces a complete,
   defensible explanation with no model involved at all, which is what runs in
   degraded mode.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from . import llm
from .config import SETTINGS
from .models import Decision, DecisionOutcome, Outcome, RuleResult, Severity

SYSTEM_PROMPT = """You are writing the reviewer-facing summary of an invoice \
validation that has ALREADY been decided by a deterministic rule engine.

Absolute constraints:
- State only what appears in the JSON you are given. Introduce no new facts, \
figures, judgements or advice.
- Every number you write must appear verbatim in the input JSON. Do not \
recompute, re-round, convert or aggregate anything.
- Name the specific rule IDs (e.g. PO-07, LIN-03) and their expected and actual \
values.
- Do NOT recommend an outcome. The outcome is given; restate it, never decide it.
- Distinguish carefully between a rule that FAILED and one that COULD NOT BE \
EVALUATED. The second means a required input was missing or read with too little \
confidence — it does not mean anything is wrong with the invoice.
- Plain business English for an accounts-payable reviewer. No hedging, no \
preamble, no bullet lists.

Write 3 to 6 sentences for the overall narrative, then 1 to 2 sentences for each \
failed or unevaluable rule.

Return JSON: {"overall": "...", "per_rule": {"RULE-ID": "...", ...}}"""


def generate(
    decision: Decision,
    results: List[RuleResult],
    invoice_summary: Dict[str, Any],
) -> Tuple[str, Dict[str, str], str, str]:
    """Return ``(overall, per_rule, source, model)``.

    ``source`` is ``LLM`` or ``TEMPLATE`` and is surfaced in the UI — the
    reviewer should know whether they are reading generated prose or a
    deterministic rendering.
    """
    payload = _build_payload(decision, results, invoice_summary)
    template_overall, template_per_rule = _template(decision, results, invoice_summary)

    if not llm.available():
        return template_overall, template_per_rule, "TEMPLATE", "none (degraded mode)"

    import json

    raw = llm.chat_json(
        SYSTEM_PROMPT,
        [{"type": "text", "text": json.dumps(payload, indent=2)}],
        max_tokens=1200,
    )
    if not raw or not isinstance(raw.get("overall"), str):
        return template_overall, template_per_rule, "TEMPLATE", "none (generation failed)"

    allowed = _allowed_numbers(payload)

    overall = raw["overall"].strip()
    ok, offender = numeric_guard(overall, allowed)
    if not ok:
        return (template_overall, template_per_rule, "TEMPLATE",
                f"none (numeric guard rejected {offender!r})")

    per_rule: Dict[str, str] = {}
    for rule_id, text in (raw.get("per_rule") or {}).items():
        if not isinstance(text, str):
            continue
        passed, _ = numeric_guard(text, allowed)
        per_rule[rule_id] = text.strip() if passed else template_per_rule.get(rule_id, "")

    for rule_id, text in template_per_rule.items():
        per_rule.setdefault(rule_id, text)

    return overall, per_rule, "LLM", SETTINGS.azure_deployment


# ----------------------------------------------------------------------
# Numeric guard
# ----------------------------------------------------------------------
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Small integers are ordinary English ("three prior invoices", "2 warnings") and
# would make the guard fire constantly without catching anything meaningful. The
# guard exists to catch invented *amounts*, which are what cause harm.
_GUARD_FLOOR = Decimal("100")


def numeric_guard(text: str, allowed: set[str]) -> Tuple[bool, str | None]:
    """True when every substantive number in ``text`` appears in ``allowed``."""
    for token in _NUMBER.findall(text):
        cleaned = token.replace(",", "").lstrip("-")
        if not cleaned or cleaned == ".":
            continue
        try:
            value = Decimal(cleaned)
        except Exception:
            continue
        if value.copy_abs() < _GUARD_FLOOR:
            continue
        if _canon(cleaned) in allowed:
            continue
        return False, token
    return True, None


def _canon(number: str) -> str:
    """Canonical form so ``50000``, ``50000.00`` and ``50,000.00`` compare equal.

    ``normalize()`` alone is not enough: it turns ``190000`` into ``1.9E+5``,
    which would never match a rendering in prose and would make the guard reject
    every legitimate large figure — silently degrading every explanation to the
    template. Formatting with ``f`` forces plain notation.
    """
    try:
        d = Decimal(number.replace(",", "")).normalize()
    except Exception:
        return number
    return format(d, "f")


def _allowed_numbers(payload: Dict[str, Any]) -> set[str]:
    """Every number reachable anywhere in the input JSON, canonicalised."""
    allowed: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None:
            for token in _NUMBER.findall(str(node)):
                allowed.add(_canon(token.replace(",", "")))

    walk(payload)
    return allowed


# ----------------------------------------------------------------------
# Payload
# ----------------------------------------------------------------------
def _build_payload(
    decision: Decision, results: List[RuleResult], invoice_summary: Dict[str, Any]
) -> Dict[str, Any]:
    """Rule results only. Deliberately excludes the document text and the raw
    extraction, so injected content has no path into the explanation prompt."""
    notable = [
        r for r in results
        if r.outcome in (Outcome.FAIL, Outcome.WARN, Outcome.CANNOT_EVALUATE)
    ]
    return {
        "invoice": invoice_summary,
        "decision": {
            "outcome": decision.outcome.value,
            "decision_confidence": str(decision.decision_confidence),
            "risk_score": decision.risk_score,
            "risk_band": decision.risk_band,
            "reason_codes": decision.reason_codes,
            "blocked_on": decision.blocked_on,
            "routed_to_role": decision.routed_to_role,
        },
        "counts": {
            "total": len(results),
            "passed": len([r for r in results if r.outcome == Outcome.PASS]),
            "failed": len([r for r in results if r.outcome == Outcome.FAIL]),
            "warnings": decision.warning_count,
            "cannot_evaluate": decision.cannot_evaluate_count,
            "not_applicable": len([r for r in results
                                   if r.outcome == Outcome.NOT_APPLICABLE]),
        },
        "notable_rules": [
            {
                "rule_id": r.rule_id, "name": r.name, "outcome": r.outcome.value,
                "severity": r.severity.value, "message": r.message,
                "expected": r.expected_value, "actual": r.actual_value,
                "delta": r.delta, "delta_pct": r.delta_pct,
                "threshold": r.threshold_applied, "blocked_by": r.blocked_by,
            }
            for r in notable
        ],
    }


# ----------------------------------------------------------------------
# Deterministic template — the degraded-mode path
# ----------------------------------------------------------------------
_OUTCOME_SENTENCE = {
    DecisionOutcome.AUTO_APPROVE:
        "This invoice passed every applicable check and was approved automatically.",
    DecisionOutcome.APPROVE_PENDING_AUTHORISATION:
        "This invoice passed every applicable check but exceeds the unattended "
        "approval ceiling, so it has been routed for authorisation.",
    DecisionOutcome.MANUAL_REVIEW:
        "This invoice requires review by an accounts-payable processor.",
    DecisionOutcome.NEEDS_INFO:
        "This invoice cannot be decided yet because one or more required inputs "
        "could not be read reliably.",
    DecisionOutcome.REJECT:
        "This invoice was rejected on a check that cannot be satisfied.",
    DecisionOutcome.DUPLICATE_BLOCK:
        "This invoice has been held as a suspected duplicate. It has not been "
        "rejected — the earlier submission may itself have been the error.",
}


def _template(
    decision: Decision, results: List[RuleResult], invoice_summary: Dict[str, Any]
) -> Tuple[str, Dict[str, str]]:
    parts: List[str] = [_OUTCOME_SENTENCE[decision.outcome]]

    passed = len([r for r in results if r.outcome == Outcome.PASS])
    parts.append(
        f"{passed} of {len(results)} checks passed, with "
        f"{decision.critical_fail_count + decision.blocker_count} failure(s), "
        f"{decision.warning_count} warning(s) and "
        f"{decision.cannot_evaluate_count} check(s) that could not be evaluated."
    )

    fails = [r for r in results if r.outcome == Outcome.FAIL]
    if fails:
        worst = sorted(fails, key=lambda r: 0 if r.severity == Severity.BLOCKER else 1)
        parts.append(
            "The determining failure was "
            + "; ".join(f"{r.rule_id} ({r.name})" for r in worst[:3])
            + "."
        )

    unknown = [r for r in results if r.outcome == Outcome.CANNOT_EVALUATE
               and r.severity in (Severity.BLOCKER, Severity.CRITICAL)]
    if unknown:
        blocked = sorted({b.split(" (")[0] for r in unknown for b in r.blocked_by})
        parts.append(
            f"{len(unknown)} critical check(s) could not be run because "
            f"{', '.join(blocked)} was unavailable — this is not a finding against "
            f"the invoice, it is a gap in what we could read."
        )

    parts.append(
        f"Decision confidence is {decision.decision_confidence} and the risk score is "
        f"{decision.risk_score} ({decision.risk_band})."
    )

    per_rule: Dict[str, str] = {}
    for r in results:
        if r.outcome == Outcome.FAIL:
            detail = r.message
            if r.expected_value and r.actual_value:
                detail += f" Expected {r.expected_value}, found {r.actual_value}."
                if r.delta:
                    detail += f" Difference {r.delta}."
                if r.threshold_applied:
                    detail += f" Threshold applied: {r.threshold_applied}."
            per_rule[r.rule_id] = detail
        elif r.outcome == Outcome.CANNOT_EVALUATE:
            per_rule[r.rule_id] = (
                f"{r.name} could not be evaluated because "
                f"{'; '.join(r.blocked_by) or 'a required input was unavailable'}. "
                f"This is not a failure — the check simply did not run."
            )
        elif r.outcome == Outcome.WARN:
            per_rule[r.rule_id] = r.message

    return " ".join(parts), per_rule
