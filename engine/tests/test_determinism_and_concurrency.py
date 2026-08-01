"""Determinism, concurrency, and the audit hash chain — PRD 19.2.

These three are the load-bearing guarantees behind the architecture claim:

* determinism is what lets an auditor reproduce a decision;
* the ledger lock is what stops two invoices claiming the same PO headroom;
* the hash chain is what makes a retroactive edit detectable.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import pipeline
from app.config import SETTINGS
from app.store import STORE, Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TODAY = date(2026, 7, 31)


@pytest.fixture(autouse=True)
def quiet_stream():
    original = SETTINGS.rule_stream_delay_ms
    object.__setattr__(SETTINGS, "rule_stream_delay_ms", 0)
    yield
    object.__setattr__(SETTINGS, "rule_stream_delay_ms", original)


async def _run_fixture(name: str) -> dict:
    pdf = FIXTURES / "pdf" / f"{name}.pdf"
    result = pipeline.intake(pdf.read_bytes(), pdf.name, source="TEST")
    if result["duplicate"]:
        return {"decision": "DUPLICATE_BLOCK", "rules": [], "short_circuit": True}

    rules, decision = [], {}
    async for event in pipeline.run(result["invoice"]["id"], today=TODAY):
        if event["event"] == "rule":
            payload = dict(event["data"])
            # Timing and timestamps legitimately vary; everything else must not.
            payload.pop("duration_ms", None)
            payload.pop("ts", None)
            rules.append(payload)
        elif event["event"] == "decision":
            decision = {k: v for k, v in event["data"].items() if k != "ts"}
    return {"rules": rules, "decision": decision}


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "happy-path-nimbus", "ec2-orion-scanned", "ec4-vertex-offsetting",
    "ec5-cobalt-injection", "adv-blacklisted-vendor",
])
def test_identical_input_produces_byte_identical_results(name):
    """Ten runs, same answer every time, down to the evidence payloads.

    Each run starts from a clean store so no cross-run state leaks in — these
    fixtures are stateless by choice, unlike the Edge Case 1 sequence.
    """
    signatures = set()
    for _ in range(10):
        STORE.reset(keep_masters=True)
        result = asyncio.run(_run_fixture(name))
        signatures.add(json.dumps(result, sort_keys=True, default=str))

    assert len(signatures) == 1, (
        f"{name} produced {len(signatures)} distinct results across 10 runs"
    )


def test_rules_always_execute_in_catalogue_order():
    STORE.reset(keep_masters=True)
    first = asyncio.run(_run_fixture("happy-path-nimbus"))
    STORE.reset(keep_masters=True)
    second = asyncio.run(_run_fixture("happy-path-nimbus"))

    assert [r["rule_id"] for r in first["rules"]] == \
           [r["rule_id"] for r in second["rules"]]


def test_stateful_sequence_is_reproducible():
    """Edge Case 1 depends on order. Replaying the whole sequence must give the
    same three outcomes every time."""
    outcomes = []
    for _ in range(3):
        STORE.reset(keep_masters=True)
        run_outcomes = []
        for name in ("ec1-a-sharma-8801", "ec1-b-sharma-8847", "ec1-c-sharma-8903"):
            result = asyncio.run(_run_fixture(name))
            run_outcomes.append(result["decision"]["outcome"])
        outcomes.append(tuple(run_outcomes))

    assert len(set(outcomes)) == 1
    assert outcomes[0] == ("APPROVE_PENDING_AUTHORISATION",
                           "APPROVE_PENDING_AUTHORISATION",
                           "MANUAL_REVIEW")


# ----------------------------------------------------------------------
# Concurrency — PRD 8.2, R4
# ----------------------------------------------------------------------
def test_only_one_invoice_wins_the_remaining_headroom(tmp_path):
    """Two invoices claim the same balance simultaneously. Both reservations are
    recorded, but the ledger must total both claims so the second invoice's
    PO-07 sees the first — neither may be silently told there is room.
    """
    store = Store(data_dir=tmp_path / "data")
    po_total = Decimal(str(store.get("purchase_orders", "PO-2291")["total_amount"]))

    store.insert("invoices", {"id": "INV-EARLIER", "po_id": "PO-2291",
                              "grand_total": "800000.00", "status": "APPROVED"})
    store.reserve("PO-2291", "INV-EARLIER", Decimal("800000.00"))
    store.settle("INV-EARLIER", "COMMITTED")

    remaining = po_total - store.po_consumed("PO-2291")
    assert remaining == Decimal("200000.00")

    claim = Decimal("150000.00")     # each alone fits; together they do not
    granted = []

    def submit(invoice_id: str) -> bool:
        with store.ledger_lock:
            already = store.po_consumed("PO-2291")
            headroom = po_total - already
            if claim > headroom:
                return False
            store.insert("invoices", {"id": invoice_id, "po_id": "PO-2291",
                                      "grand_total": str(claim),
                                      "status": "PENDING_REVIEW"})
            store.reserve("PO-2291", invoice_id, claim)
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, f"INV-RACE-{i}") for i in range(2)]
        granted = [f.result() for f in futures]

    assert sum(granted) == 1, "exactly one claim may be granted"
    assert store.po_consumed("PO-2291") == Decimal("950000.00")
    assert store.po_consumed("PO-2291") <= po_total


def test_ledger_survives_many_concurrent_reservations(tmp_path):
    """No lost updates under contention."""
    store = Store(data_dir=tmp_path / "data")

    def reserve(index: int) -> None:
        store.insert("invoices", {"id": f"INV-{index}", "po_id": "PO-2291",
                                  "grand_total": "1000.00", "status": "PENDING_REVIEW"})
        store.reserve("PO-2291", f"INV-{index}", Decimal("1000.00"))

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(reserve, range(50)))

    assert store.po_consumed("PO-2291") == Decimal("50000.00")
    assert len(store.find("po_consumption", po_id="PO-2291")) == 50


def test_provisional_claims_block_but_released_ones_do_not(tmp_path):
    store = Store(data_dir=tmp_path / "data")

    store.insert("invoices", {"id": "INV-A", "po_id": "PO-7723", "status": "PENDING_REVIEW"})
    store.reserve("PO-7723", "INV-A", Decimal("45000.00"))
    assert store.po_consumed("PO-7723") == Decimal("45000.00")

    store.settle("INV-A", "RELEASED")
    assert store.po_consumed("PO-7723") == Decimal("0")

    store.insert("invoices", {"id": "INV-B", "po_id": "PO-7723", "status": "APPROVED"})
    store.reserve("PO-7723", "INV-B", Decimal("45000.00"))
    store.settle("INV-B", "COMMITTED")
    assert store.po_consumed("PO-7723") == Decimal("45000.00")


# ----------------------------------------------------------------------
# Audit hash chain — PRD 8.4, 16
# ----------------------------------------------------------------------
def test_audit_chain_verifies_when_untouched(tmp_path):
    store = Store(data_dir=tmp_path / "data")
    for i in range(5):
        store.append_audit("invoice", f"INV-{i}", "DECIDED", {"outcome": "AUTO_APPROVE"})

    assert store.verify_audit_chain() == {"valid": True, "events": 5}


def test_audit_chain_detects_a_retroactive_edit(tmp_path):
    """Changing history must break the chain from that point forward — that is
    the whole value of the construction to an auditor."""
    store = Store(data_dir=tmp_path / "data")
    for i in range(5):
        store.append_audit("invoice", f"INV-{i}", "DECIDED", {"outcome": "REJECT"})

    store._tables["audit_events"][2]["payload"]["outcome"] = "AUTO_APPROVE"

    report = store.verify_audit_chain()
    assert report["valid"] is False
    assert report["broken_at"] == 2
    assert report["reason"] == "payload hash mismatch"


def test_audit_chain_detects_a_deleted_event(tmp_path):
    store = Store(data_dir=tmp_path / "data")
    for i in range(5):
        store.append_audit("invoice", f"INV-{i}", "DECIDED", {})

    del store._tables["audit_events"][2]

    report = store.verify_audit_chain()
    assert report["valid"] is False
    assert report["broken_at"] == 2
    assert report["reason"] == "prev_hash mismatch"


# ----------------------------------------------------------------------
# Idempotency — PRD 2.2.8
# ----------------------------------------------------------------------
def test_identical_bytes_short_circuit_before_any_extraction_spend():
    STORE.reset(keep_masters=True)
    pdf = FIXTURES / "pdf" / "happy-path-nimbus.pdf"
    data = pdf.read_bytes()

    first = pipeline.intake(data, pdf.name)
    assert first["duplicate"] is False

    second = pipeline.intake(data, "renamed-copy.pdf")
    assert second["duplicate"] is True
    assert second["existing_invoice_id"] == first["invoice"]["id"]

    # Exactly one document row, so no second OCR or LLM call could have happened.
    assert len(STORE.find("documents", sha256=first["document"]["sha256"])) == 1
