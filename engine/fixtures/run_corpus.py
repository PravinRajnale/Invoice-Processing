"""Run the fixture corpus through the pipeline and diff against expectations.

This is the golden-file regression harness (PRD 19.2). It asserts the whole
``RuleResult`` array, not merely the decision: a rule quietly changing from PASS
to NOT_APPLICABLE is a regression even when the outcome is unchanged.

Fixtures run in listed order because several are stateful by design — Edge Case
1 needs its three invoices in sequence, and Edge Case 3 needs the original
processed before the duplicate.

Run with:  python -m fixtures.run_corpus [--verbose]
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline                      # noqa: E402
from app.store import STORE                   # noqa: E402

ROOT = Path(__file__).resolve().parent

# The corpus is dated around June–July 2026; pin "today" so staleness and
# future-date rules give the same answer whenever the suite is run.
TODAY = date(2026, 7, 31)


async def run_one(name: str, pdf_path: Path) -> Dict[str, Any]:
    data = pdf_path.read_bytes()
    result = pipeline.intake(data, pdf_path.name, source="FIXTURE")

    if result["duplicate"]:
        return {"name": name, "decision": "DUPLICATE_BLOCK",
                "rules": {}, "risk_band": "HIGH", "short_circuit": True,
                "security_flags": 0}

    invoice_id = result["invoice"]["id"]
    rules: Dict[str, str] = {}
    decision: Dict[str, Any] = {}
    flags = 0
    explanation = ""

    async for event in pipeline.run(invoice_id, today=TODAY):
        kind, payload = event["event"], event["data"]
        if kind == "rule":
            rules[payload["rule_id"]] = payload["outcome"]
        elif kind == "decision":
            decision = payload
        elif kind == "security":
            flags = len(payload.get("flags", []))
        elif kind == "explanation":
            explanation = payload.get("text", "")

    return {
        "name": name,
        "invoice_id": invoice_id,
        "decision": decision.get("outcome"),
        "risk_band": decision.get("risk_band"),
        "risk_score": decision.get("risk_score"),
        "decision_confidence": decision.get("decision_confidence"),
        "reason_codes": decision.get("reason_codes", []),
        "rules": rules,
        "security_flags": flags,
        "explanation": explanation,
        "short_circuit": False,
    }


async def main(verbose: bool = False) -> int:
    STORE.reset(keep_masters=True)
    # Rule events are paced for the UI; the corpus does not need the theatre.
    from app.config import SETTINGS
    object.__setattr__(SETTINGS, "rule_stream_delay_ms", 0)

    # Golden-file assertions must not depend on a network call. Extraction runs
    # from the recorded payloads and explanations fall back to the template.
    from app import llm
    llm.get_client = lambda: None
    llm.available = lambda: False

    manifest: List[Dict[str, Any]] = json.loads((ROOT / "manifest.json").read_text())
    failures: List[str] = []

    print(f"\nRunning {len(manifest)} fixtures (today = {TODAY.isoformat()})\n")
    print(f"  {'fixture':34} {'expected':32} {'actual':32} rules")
    print("  " + "-" * 108)

    for entry in manifest:
        name = entry["name"]
        expected = json.loads((ROOT / "expected" / f"{name}.json").read_text())
        actual = await run_one(name, ROOT / entry["pdf"])

        problems: List[str] = []

        if actual["decision"] != expected["decision"]:
            problems.append(
                f"decision {actual['decision']} != expected {expected['decision']}"
            )
        if expected.get("risk_band") and actual.get("risk_band") != expected["risk_band"]:
            problems.append(
                f"risk band {actual.get('risk_band')} != expected {expected['risk_band']}"
            )
        for rule_id, want in (expected.get("rules") or {}).items():
            got = actual["rules"].get(rule_id)
            if got != want:
                problems.append(f"{rule_id} {got} != expected {want}")
        want_flags = expected.get("security_flags_min")
        if want_flags and actual["security_flags"] < want_flags:
            problems.append(
                f"security flags {actual['security_flags']} < expected {want_flags}"
            )

        status = "ok" if not problems else "FAIL"
        print(f"  {name:34} {expected['decision']:32} "
              f"{str(actual['decision']):32} {status}")
        for problem in problems:
            print(f"      ! {problem}")
            failures.append(f"{name}: {problem}")

        if verbose:
            print(f"      risk {actual['risk_score']} ({actual['risk_band']})  "
                  f"confidence {actual['decision_confidence']}  "
                  f"reasons {actual['reason_codes']}")
            if actual["explanation"]:
                print(f"      {actual['explanation'][:200]}")

    print()
    if failures:
        print(f"{len(failures)} assertion(s) failed\n")
        return 1
    print(f"All {len(manifest)} fixtures matched their expected outcomes\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--verbose" in sys.argv)))
