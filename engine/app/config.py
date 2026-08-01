"""Versioned configuration: thresholds, DoA matrix, runtime settings.

Every rule result records the ``threshold_applied`` it was evaluated against,
so a decision made under thresholds v1.0 stays explicable after they change
(PRD 9.4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

ENGINE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ENGINE_ROOT.parent

load_dotenv(PROJECT_ROOT / ".env")

RULESET_VERSION = "1.0.0"
ENGINE_VERSION = "1.0.0"


def _D(v: str) -> Decimal:
    return Decimal(v)


@dataclass(frozen=True)
class Tolerance:
    amount_pct: Decimal = _D("2.0")
    amount_abs: Decimal = _D("500.00")   # whichever is LOWER applies
    quantity_pct: Decimal = _D("0.0")
    unit_price_pct: Decimal = _D("2.0")
    tax_abs: Decimal = _D("1.00")
    rounding_epsilon: Decimal = _D("0.02")


@dataclass(frozen=True)
class ConfidenceFloors:
    ocr_floor: Decimal = _D("0.70")
    critical_field_floor: Decimal = _D("0.80")
    vendor_match_floor: Decimal = _D("0.88")
    po_match_floor: Decimal = _D("0.85")
    line_match_floor: Decimal = _D("0.80")
    po_inference_floor: Decimal = _D("0.75")
    auto_approve_decision_floor: Decimal = _D("0.90")


@dataclass(frozen=True)
class ApprovalLimits:
    auto_approve_ceiling: Decimal = _D("50000.00")
    manager_ceiling: Decimal = _D("500000.00")
    # above manager_ceiling -> controller
    sod_threshold: Decimal = _D("500000.00")


@dataclass(frozen=True)
class DuplicateConfig:
    amount_date_window_days: int = 7
    fuzzy_number_max_distance: int = 2


@dataclass(frozen=True)
class Config:
    tolerance: Tolerance = field(default_factory=Tolerance)
    confidence: ConfidenceFloors = field(default_factory=ConfidenceFloors)
    approval: ApprovalLimits = field(default_factory=ApprovalLimits)
    duplicate: DuplicateConfig = field(default_factory=DuplicateConfig)
    max_invoice_age_days: int = 180
    warning_cluster_threshold: int = 3
    permitted_tax_rates: tuple = (_D("0"), _D("5"), _D("12"), _D("18"), _D("28"))
    # PO-08 three-way match stays off until GRN data exists (PRD 2.2.6).
    enable_three_way_match: bool = False
    ruleset_version: str = RULESET_VERSION

    def to_dict(self) -> Dict[str, Any]:
        def conv(o: Any) -> Any:
            if isinstance(o, Decimal):
                return str(o)
            if isinstance(o, (list, tuple)):
                return [conv(x) for x in o]
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            return o

        return conv(asdict(self))


CONFIG = Config()


def doa_tier(amount: Decimal, cfg: Config = CONFIG) -> str:
    """Delegation-of-Authority routing target for an amount (POL-01)."""
    if amount <= cfg.approval.auto_approve_ceiling:
        return "AP_PROCESSOR"
    if amount <= cfg.approval.manager_ceiling:
        return "AP_MANAGER"
    return "CONTROLLER"


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Azure credentials are read from the environment only —
    never committed, never logged."""

    azure_api_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")
    azure_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    data_dir: Path = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", str(PROJECT_ROOT / "storage")))
    seed_dir: Path = ENGINE_ROOT / "app" / "seed"
    fixture_dir: Path = ENGINE_ROOT / "fixtures"

    # Rule events are paced so a human can read the stream. Real evaluation of
    # all 50 rules takes well under 200ms (PRD 7).
    rule_stream_delay_ms: int = int(os.getenv("RULE_STREAM_DELAY_MS", "120"))

    @property
    def llm_available(self) -> bool:
        return bool(self.azure_api_key and self.azure_endpoint)


SETTINGS = Settings()
