"""Domain types.

The five rule outcome states and six decision outcomes from PRD 2.2.5 are the
load-bearing part of this module. In particular ``CANNOT_EVALUATE`` is a first
class state, distinct from ``FAIL`` — the system must be able to say "I don't
know" without that collapsing into "this is wrong".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANNOT_EVALUATE = "CANNOT_EVALUATE"


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class RuleType(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class Gate(str, Enum):
    INGEST = "INGEST"
    EXTRACTION = "EXTRACTION"
    VENDOR = "VENDOR"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    FINANCIAL = "FINANCIAL"
    LINE_ITEMS = "LINE_ITEMS"
    DUPLICATES = "DUPLICATES"
    POLICY = "POLICY"


class DecisionOutcome(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    APPROVE_PENDING_AUTHORISATION = "APPROVE_PENDING_AUTHORISATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NEEDS_INFO = "NEEDS_INFO"
    REJECT = "REJECT"
    DUPLICATE_BLOCK = "DUPLICATE_BLOCK"


class InvoiceStatus(str, Enum):
    INGESTED = "INGESTED"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    PENDING_REVIEW = "PENDING_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DUPLICATE_HELD = "DUPLICATE_HELD"
    NEEDS_INFO = "NEEDS_INFO"


# Decision outcome -> invoice status -> dashboard card (PRD 10.2)
OUTCOME_TO_STATUS = {
    DecisionOutcome.AUTO_APPROVE: InvoiceStatus.APPROVED,
    DecisionOutcome.APPROVE_PENDING_AUTHORISATION: InvoiceStatus.PENDING_APPROVAL,
    DecisionOutcome.MANUAL_REVIEW: InvoiceStatus.PENDING_REVIEW,
    DecisionOutcome.NEEDS_INFO: InvoiceStatus.NEEDS_INFO,
    DecisionOutcome.REJECT: InvoiceStatus.REJECTED,
    DecisionOutcome.DUPLICATE_BLOCK: InvoiceStatus.DUPLICATE_HELD,
}


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


@dataclass
class RuleSpec:
    """Static definition of a rule (PRD 9.1).

    ``requires`` is what powers CANNOT_EVALUATE: if any listed input is absent
    or below its confidence floor the rule does not run, and reports precisely
    which input blocked it.
    """

    id: str
    name: str
    gate: Gate
    severity: Severity
    type: RuleType
    mvp: bool
    requires: List[str]
    description_ui: str
    threshold_ref: Optional[str] = None
    deferred_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _plain(asdict(self))


@dataclass
class RuleResult:
    """Outcome of one rule against one invoice, with its evidence."""

    rule_id: str
    name: str
    gate: Gate
    severity: Severity
    type: RuleType
    outcome: Outcome
    message: str = ""                       # deterministic template, never LLM
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    delta: Optional[str] = None
    delta_pct: Optional[str] = None
    threshold_applied: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    blocked_by: List[str] = field(default_factory=list)
    confidence: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return _plain(asdict(self))


@dataclass
class ExtractedField:
    """One extracted value with full provenance (PRD 8.3).

    Every field carries where it came from — page and normalised bounding box —
    which is what makes the extraction verifiable in the UI rather than merely
    asserted.
    """

    field_path: str                          # 'header.grand_total', 'lines[2].unit_price'
    raw_value: Optional[str]
    normalised_value: Optional[str]
    confidence: Decimal
    page_number: Optional[int] = None
    bbox: Optional[Dict[str, float]] = None  # {x, y, w, h} normalised 0..1
    extraction_method: str = "LLM"           # OCR_FIELD | LLM | REGEX | HUMAN_CORRECTED
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    corrected_by: Optional[str] = None
    corrected_at: Optional[str] = None
    previous_value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _plain(asdict(self))


@dataclass
class MatchResult:
    """An entity resolution outcome. The *method* is always recorded and always
    shown — "matched by fuzzy 0.91" is honest, "matched" is not (PRD 11.2)."""

    matched_id: Optional[str]
    score: Decimal
    method: str          # TAX_ID_EXACT | NAME_EXACT | ALIAS | FUZZY_x | SEMANTIC_x | INFERRED
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _plain(asdict(self))


@dataclass
class Decision:
    outcome: DecisionOutcome
    decision_confidence: Decimal
    risk_score: int
    risk_band: str
    reason_codes: List[str] = field(default_factory=list)
    blocked_on: List[str] = field(default_factory=list)
    routed_to_role: Optional[str] = None
    blocker_count: int = 0
    critical_fail_count: int = 0
    warning_count: int = 0
    cannot_evaluate_count: int = 0
    ai_explanation: str = ""
    explanation_model: Optional[str] = None
    explanation_source: str = "TEMPLATE"     # LLM | TEMPLATE
    confidence_breakdown: Dict[str, Any] = field(default_factory=dict)
    risk_breakdown: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _plain(asdict(self))


def risk_band(score: int) -> str:
    """PRD 11.4 bands."""
    if score <= 20:
        return "LOW"
    if score <= 50:
        return "MEDIUM"
    if score <= 75:
        return "HIGH"
    return "SEVERE"
