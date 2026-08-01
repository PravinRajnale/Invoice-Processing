"""Dump the full rule trace for one fixture. Debugging aid.

Usage:  python -m fixtures.inspect <fixture-name> [<prerequisite-name> ...]

Prerequisites are run first, in order, without being reported — needed for the
stateful cases (Edge Case 1's sequence, Edge Case 3's original).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline                       # noqa: E402
from app.config import SETTINGS                # noqa: E402
from app.store import STORE                    # noqa: E402

ROOT = Path(__file__).resolve().parent
TODAY = date(2026, 7, 31)

GLYPH = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠",
         "CANNOT_EVALUATE": "⊘", "NOT_APPLICABLE": "–"}


async def run(name: str, report: bool) -> None:
    pdf = ROOT / "pdf" / f"{name}.pdf"
    result = pipeline.intake(pdf.read_bytes(), pdf.name, source="FIXTURE")
    if result["duplicate"]:
        if report:
            print(f"\n{name}: short-circuited at intake — exact hash already seen\n")
        return

    invoice_id = result["invoice"]["id"]
    if report:
        print(f"\n=== {name} ===\n")

    async for event in pipeline.run(invoice_id, today=TODAY):
        if not report:
            continue
        kind, data = event["event"], event["data"]

        if kind == "rule":
            glyph = GLYPH.get(data["outcome"], "?")
            print(f"  {glyph} {data['rule_id']:8} {data['name'][:46]:46} "
                  f"{data['outcome']}")
            if data["outcome"] in ("FAIL", "WARN", "CANNOT_EVALUATE"):
                print(f"        {data['message']}")
                if data.get("expected_value") or data.get("actual_value"):
                    print(f"        expected={data.get('expected_value')} "
                          f"actual={data.get('actual_value')} "
                          f"delta={data.get('delta')} ({data.get('delta_pct')}%) "
                          f"threshold={data.get('threshold_applied')}")
                for blocker in data.get("blocked_by") or []:
                    print(f"        blocked by: {blocker}")

        elif kind == "stage" and data["status"] == "COMPLETED":
            extras = {k: v for k, v in data.items()
                      if k not in ("stage", "status", "ts")}
            print(f"\n  [{data['stage']}] {json.dumps(extras, default=str)[:400]}\n")

        elif kind == "field":
            flag = "  <-- below floor" if float(data["confidence"]) < 0.80 else ""
            print(f"      {data['path']:34} {str(data['value'])[:28]:28} "
                  f"conf={float(data['confidence']):.4f}{flag}")

        elif kind == "security":
            print(f"\n  [SECURITY] {len(data['flags'])} flag(s)")
            for f in data["flags"]:
                print(f"      {f['reason']}: {f['matched_text'][:70]}")

        elif kind == "decision":
            print(f"\n  DECISION  {data['outcome']}")
            print(f"    confidence {data['decision_confidence']}  "
                  f"risk {data['risk_score']} ({data['risk_band']})")
            print(f"    reasons {data['reason_codes']}")
            for p in data["confidence_breakdown"].get("penalties", []):
                print(f"      -{p['penalty']}  {p['reason']} — {p['detail']}")
            for r in data["risk_breakdown"]:
                print(f"      +{r['points']}  {r['reason']}")

        elif kind == "explanation":
            print(f"\n  EXPLANATION ({data['source']} / {data['model']})")
            print(f"    {data['text']}\n")


async def main() -> int:
    STORE.reset(keep_masters=True)
    object.__setattr__(SETTINGS, "rule_stream_delay_ms", 0)

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    target, prerequisites = args[0], args[1:]

    for name in prerequisites:
        await run(name, report=False)
    await run(target, report=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
