"""Rule runner.

Deterministic by construction:

* rules are executed in catalogue order, which is fixed;
* every rule is a pure function of the ``RuleContext``;
* a rule whose ``requires`` are not all available is never executed at all — it
  reports CANNOT_EVALUATE with the precise blocking inputs.

The last point is the one that matters. A rule that cannot see its inputs must
not guess and must not fail; conflating "I don't know" with "this is wrong" is
the defining failure of naive document AI (PRD 2.2.5, Edge Case 2).
"""

from __future__ import annotations

import time
import traceback
from typing import Callable, Dict, List, Optional

from ..models import Outcome, RuleResult, RuleSpec
from .catalogue import ACTIVE, BY_ID
from .context import RuleContext

# rule_id -> implementation
REGISTRY: Dict[str, Callable[[RuleContext], "Verdict"]] = {}


class Verdict:
    """What a rule implementation returns.

    Implementations never build a ``RuleResult`` directly; the runner attaches
    the static metadata and timing so those can never drift from the catalogue.
    """

    __slots__ = ("outcome", "message", "expected", "actual", "delta",
                 "delta_pct", "threshold", "evidence", "blocked_by", "confidence")

    def __init__(
        self,
        outcome: Outcome,
        message: str = "",
        expected=None,
        actual=None,
        delta=None,
        delta_pct=None,
        threshold: Optional[str] = None,
        evidence: Optional[dict] = None,
        blocked_by: Optional[List[str]] = None,
        confidence=None,
    ) -> None:
        self.outcome = outcome
        self.message = message
        self.expected = expected
        self.actual = actual
        self.delta = delta
        self.delta_pct = delta_pct
        self.threshold = threshold
        self.evidence = evidence or {}
        self.blocked_by = blocked_by or []
        self.confidence = confidence


def rule(rule_id: str):
    """Register an implementation against a catalogue entry."""

    def decorator(fn):
        if rule_id not in BY_ID:
            raise KeyError(f"{rule_id} is not in the catalogue")
        REGISTRY[rule_id] = fn
        return fn

    return decorator


# Convenience constructors used throughout the implementations.
def PASS(message="", **kw) -> Verdict:
    return Verdict(Outcome.PASS, message, **kw)


def FAIL(message="", **kw) -> Verdict:
    return Verdict(Outcome.FAIL, message, **kw)


def WARN(message="", **kw) -> Verdict:
    return Verdict(Outcome.WARN, message, **kw)


def NA(message="", **kw) -> Verdict:
    return Verdict(Outcome.NOT_APPLICABLE, message, **kw)


def UNKNOWN(message="", blocked_by=None, **kw) -> Verdict:
    return Verdict(Outcome.CANNOT_EVALUATE, message, blocked_by=blocked_by or [], **kw)


def _as_str(value) -> Optional[str]:
    return None if value is None else str(value)


_loaded = False


def _ensure_loaded() -> None:
    """Import the implementations module, which registers every rule.

    Done lazily and centrally rather than at module import, because
    ``implementations`` imports from this module and a top-level import would be
    circular. Every entry point routes through ``evaluate_one``, so this is the
    single place it needs to happen.
    """
    global _loaded
    if not _loaded:
        from . import implementations  # noqa: F401
        _loaded = True


def evaluate_one(spec: RuleSpec, ctx: RuleContext) -> RuleResult:
    """Evaluate a single rule, honouring its `requires` contract."""
    _ensure_loaded()
    started = time.perf_counter()

    blocked = ctx.missing(spec.requires)
    if blocked:
        verdict = UNKNOWN(
            f"Cannot check — {blocked[0].split(' (')[0]} unavailable",
            blocked_by=blocked,
        )
    else:
        impl = REGISTRY.get(spec.id)
        if impl is None:
            verdict = NA("No implementation registered")
        else:
            try:
                verdict = impl(ctx)
            except Exception as exc:  # a rule crash must not take down the run
                verdict = UNKNOWN(
                    f"Rule raised {type(exc).__name__}: {exc}",
                    blocked_by=[f"internal_error ({type(exc).__name__})"],
                    evidence={"traceback": traceback.format_exc(limit=4)},
                )

    duration_ms = int((time.perf_counter() - started) * 1000)

    return RuleResult(
        rule_id=spec.id,
        name=spec.name,
        gate=spec.gate,
        severity=spec.severity,
        type=spec.type,
        outcome=verdict.outcome,
        message=verdict.message,
        expected_value=_as_str(verdict.expected),
        actual_value=_as_str(verdict.actual),
        delta=_as_str(verdict.delta),
        delta_pct=_as_str(verdict.delta_pct),
        threshold_applied=verdict.threshold or spec.threshold_ref,
        evidence=verdict.evidence,
        blocked_by=verdict.blocked_by,
        confidence=_as_str(verdict.confidence),
        duration_ms=duration_ms,
    )


def evaluate_all(ctx: RuleContext, specs: Optional[List[RuleSpec]] = None) -> List[RuleResult]:
    """Run the active catalogue in fixed order."""
    from . import implementations  # noqa: F401  (registers the rules)

    return [evaluate_one(spec, ctx) for spec in (specs or ACTIVE)]


def evaluate_subset(ctx: RuleContext, rule_ids: List[str]) -> List[RuleResult]:
    """Re-run only the named rules — used after a targeted field correction so
    the reviewer's 8-second fix does not re-run the whole catalogue
    (PRD Edge Case 2)."""
    from . import implementations  # noqa: F401

    return [evaluate_one(BY_ID[rid], ctx) for rid in rule_ids if rid in BY_ID]
