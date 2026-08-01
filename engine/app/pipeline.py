"""S0–S9 orchestration.

The pipeline is an async generator: it yields events as it goes and persists as
it goes, so the UI can render checks ticking through live rather than waiting
for a final payload (PRD 7, 13.4).

Stages S6 (rules) and S7 (decision) are the control boundary. Everything before
them is perception; everything after is presentation. No stage after S5 consults
a model for anything that affects the outcome.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from typing import Any, AsyncIterator, Dict, List, Optional

from . import decide as decide_mod
from . import explain, extract, ingest, resolve, security
from .config import CONFIG, ENGINE_VERSION, RULESET_VERSION, SETTINGS
from .models import (
    Decision,
    DecisionOutcome,
    ExtractedField,
    InvoiceStatus,
    OUTCOME_TO_STATUS,
    Outcome,
    RuleResult,
)
from .rules.catalogue import ACTIVE, BY_ID
from .rules.context import RuleContext
from .rules.engine import evaluate_one, evaluate_subset
from .store import STORE, new_id, now_iso

log = logging.getLogger("engine.pipeline")


def _event(kind: str, **payload: Any) -> Dict[str, Any]:
    return {"event": kind, "data": {**payload, "ts": now_iso()}}


# ----------------------------------------------------------------------
# S0 — intake
# ----------------------------------------------------------------------
def intake(data: bytes, filename: str, source: str = "MANUAL_UPLOAD",
           uploaded_by: Optional[str] = None) -> Dict[str, Any]:
    """Hash, store, and register the document.

    Returns ``{"duplicate": True, ...}`` when these exact bytes were seen
    before — the caller short-circuits and no OCR or LLM call is ever made.
    """
    sha256, original_path, pdf_path, fmt = ingest.store_document(data, filename)

    existing = STORE.find_one("documents", sha256=sha256)
    if existing:
        invoice = STORE.find_one("invoices", document_id=existing["id"])
        STORE.append_audit("document", existing["id"], "DUPLICATE_UPLOAD_BLOCKED",
                           {"filename": filename, "sha256": sha256,
                            "existing_invoice_id": invoice["id"] if invoice else None},
                           actor_id=uploaded_by, actor_type="SYSTEM")
        return {
            "duplicate": True,
            "document": existing,
            "existing_invoice_id": invoice["id"] if invoice else None,
            "existing_status": invoice.get("status") if invoice else None,
        }

    info = ingest.preflight(data, filename)
    document = STORE.insert("documents", {
        "sha256": sha256,
        "storage_key": str(original_path),
        "render_key": str(pdf_path),
        "original_filename": filename,
        "mime_type": info["mime_type"],
        "source_format": info.get("source_format"),
        "source_format_label": info.get("source_format_label"),
        "converted": info.get("converted", False),
        "conversion_error": info.get("conversion_error"),
        "page_count": info["page_count"],
        "is_scanned": info["is_scanned"],
        "digital_pages": info.get("digital_pages", 0),
        "size_bytes": info["size_bytes"],
        "encrypted": info["encrypted"],
        "corrupt": info["corrupt"],
        "oversized": info["oversized"],
        "source": source,
        "uploaded_by": uploaded_by,
        "uploaded_at": now_iso(),
    })

    invoice = STORE.insert("invoices", {
        "document_id": document["id"],
        "status": InvoiceStatus.INGESTED.value,
        "source": source,
        "uploaded_by": uploaded_by,
    })

    STORE.append_audit("invoice", invoice["id"], "INGESTED",
                       {"filename": filename, "sha256": sha256,
                        "page_count": info["page_count"],
                        "is_scanned": info["is_scanned"], "source": source},
                       actor_id=uploaded_by)

    return {"duplicate": False, "document": document, "invoice": invoice}


# ----------------------------------------------------------------------
# S1–S9 — the run
# ----------------------------------------------------------------------
async def run(invoice_id: str, trigger: str = "INITIAL",
              today: Optional[date] = None) -> AsyncIterator[Dict[str, Any]]:
    """Execute the full pipeline for an already-ingested invoice."""
    invoice_row = STORE.get("invoices", invoice_id)
    if invoice_row is None:
        yield _event("error", message=f"Invoice {invoice_id} not found")
        return

    document = STORE.get("documents", invoice_row["document_id"])
    pdf_bytes = _read_document(document)

    yield _event("stage", stage="INGEST", status="STARTED")

    # --- S2 text acquisition -----------------------------------------
    pages: List[Dict[str, Any]] = []
    if pdf_bytes and not document.get("corrupt") and not document.get("encrypted"):
        try:
            pages = ingest.extract_pages(pdf_bytes)
        except Exception as exc:
            log.warning("Text extraction failed for %s: %s", invoice_id, exc)

    security_flags = security.scan_pages(pages)
    if security_flags:
        STORE.append_audit("invoice", invoice_id, "SECURITY_ANOMALY_DETECTED",
                           {"flags": security_flags})
        yield _event("security", flags=security_flags)

    yield _event("stage", stage="INGEST", status="COMPLETED",
                 pages=len(pages), is_scanned=bool(document.get("is_scanned")),
                 reading_path=("Scanned document — vision extraction applied"
                               if document.get("is_scanned")
                               else "Digital PDF — native text extraction, no OCR required"))

    # --- S3 extraction ------------------------------------------------
    yield _event("stage", stage="EXTRACT", status="STARTED")
    STORE.update("invoices", invoice_id, status=InvoiceStatus.EXTRACTING.value)

    raw, extraction_source = extract.classify_and_extract(
        pages, pdf_bytes or b"", document["sha256"], bool(document.get("is_scanned"))
    )
    classification = extract.classification(raw)

    # --- S4 normalisation --------------------------------------------
    header, lines, fields, parse_warnings = extract.normalise(
        raw, pages, bool(document.get("is_scanned")),
        blend=(extraction_source != "REPLAY"),
    )

    # A human correction from a previous run outranks anything the model says.
    fields = _apply_corrections(invoice_id, fields, header, lines)

    for path, f in sorted(fields.items()):
        yield _event("field", path=path, value=f.normalised_value,
                     raw=f.raw_value, confidence=str(f.confidence),
                     page=f.page_number, bbox=f.bbox, method=f.extraction_method,
                     candidates=f.candidates)

    yield _event("stage", stage="EXTRACT", status="COMPLETED",
                 source=extraction_source, classification=classification,
                 fields=len(fields), lines=len(lines), warnings=parse_warnings)

    # --- S5 resolution -------------------------------------------------
    yield _event("stage", stage="RESOLVE", status="STARTED")
    STORE.update("invoices", invoice_id, status=InvoiceStatus.VALIDATING.value)

    vendor_match = resolve.resolve_vendor(header, STORE)

    # A candidate below the confidence floor is a *suggestion*, not a resolution.
    # Treating it as resolved lets a 0.51 name similarity drag an invoice into
    # the wrong vendor's purchase orders — which is how a $1,161 US invoice ends
    # up being measured against a ₹10,00,000 Indian PO. VEN-01 still reports the
    # near miss with its score; nothing downstream acts on it.
    floor = CONFIG.confidence.vendor_match_floor
    vendor = None
    if vendor_match.matched_id and (
        vendor_match.score >= floor or vendor_match.method.endswith("EXACT")
    ):
        vendor = STORE.get("vendors", vendor_match.matched_id)

    if vendor and not header.get("currency"):
        header["currency"] = vendor.get("default_currency")
        header["currency_source"] = "vendor master default"

    po_match = resolve.resolve_po(header, vendor["id"] if vendor else None, STORE)
    po = STORE.get("purchase_orders", po_match.matched_id) if po_match.matched_id else None
    po_lines = STORE.find("po_lines", po_id=po["id"]) if po else []
    po_lines.sort(key=lambda l: l["line_no"])

    line_matches = resolve.resolve_lines(lines, po_lines)

    yield _event("stage", stage="RESOLVE", status="COMPLETED",
                 vendor=_vendor_summary(vendor, vendor_match),
                 po=_po_summary(po, po_match),
                 lines_matched=len([m for m in line_matches if m.po_line_id]),
                 lines_total=len(line_matches))

    # --- ledger reservation ------------------------------------------
    # Written before rules run so PO-07 evaluates against a ledger that already
    # accounts for every in-flight claim. Two invoices racing for the same
    # headroom cannot both be told there is room (PRD 8.2).
    #
    # A suspected duplicate is exempt: its claim is already counted under the
    # invoice it duplicates, so reserving again would consume the PO twice and
    # make PO-07 and LIN-02 fail as a side effect of the duplication rather than
    # as findings of their own. The duplicate gates report the duplication; the
    # ledger rules stay quiet about a claim that was never additional.
    duplicate_of = _find_duplicate_original(invoice_id, header, vendor)
    if po and header.get("grand_total") is not None and not duplicate_of:
        with STORE.ledger_lock:
            STORE.reserve(po["id"], invoice_id, header["grand_total"])
            for line in lines:
                match = next((m for m in line_matches
                              if m.invoice_line_no == line["line_no"]), None)
                if match and match.po_line_id:
                    STORE.reserve(po["id"], invoice_id, Decimal("0"),
                                  po_line_id=match.po_line_id,
                                  quantity=line["quantity"])
    elif duplicate_of:
        yield _event("stage", stage="RESOLVE", status="NOTE",
                     message=f"Ledger reservation withheld — this invoice appears "
                             f"to duplicate {duplicate_of.get('invoice_number')}",
                     duplicate_of=duplicate_of.get("id"))

    _persist_extraction(invoice_id, header, lines, fields, vendor, po,
                        vendor_match, po_match, line_matches, classification,
                        extraction_source)

    # --- S6 rules ------------------------------------------------------
    ctx = RuleContext(
        invoice_id=invoice_id,
        document={**document, "classification": classification},
        invoice={**header, "vendor_id": vendor["id"] if vendor else None},
        lines=lines,
        fields=fields,
        store=STORE,
        cfg=CONFIG,
        vendor=vendor,
        vendor_match=vendor_match,
        po=po,
        po_match=po_match,
        po_lines=po_lines,
        line_matches=line_matches,
        security_flags=security_flags,
        duplicate_of=duplicate_of,
        today=today or date.today(),
    )

    run_row = STORE.insert("validation_runs", {
        "invoice_id": invoice_id,
        "ruleset_version": RULESET_VERSION,
        "engine_version": ENGINE_VERSION,
        "trigger": trigger,
        "started_at": now_iso(),
    })

    yield _event("stage", stage="VALIDATE", status="STARTED",
                 run_id=run_row["id"], total_rules=len(ACTIVE))

    results: List[RuleResult] = []
    delay = SETTINGS.rule_stream_delay_ms / 1000.0
    for spec in ACTIVE:
        result = evaluate_one(spec, ctx)
        results.append(result)
        STORE.insert("rule_results", {"run_id": run_row["id"], **result.to_dict()})
        yield _event("rule", **result.to_dict())
        if delay:
            # Paced purely so a human can read the stream; real evaluation of
            # all 49 rules completes in well under 200ms.
            await asyncio.sleep(delay)

    STORE.update("validation_runs", run_row["id"], completed_at=now_iso())
    yield _event("stage", stage="VALIDATE", status="COMPLETED",
                 **_tally(results))

    # --- S7 decision ---------------------------------------------------
    yield _event("stage", stage="DECIDE", status="STARTED")

    extraction_conf, extraction_detail = decide_mod.extraction_confidence(fields)
    match_conf, match_detail = decide_mod.match_confidence(
        vendor_match, po_match, [m.score for m in line_matches if m.po_line_id]
    )
    decision = decide_mod.decide(
        results, header.get("grand_total"), extraction_conf, match_conf,
        security_flags, CONFIG,
    )
    decision.confidence_breakdown["extraction_detail"] = extraction_detail
    decision.confidence_breakdown["match_detail"] = match_detail

    yield _event("decision", **decision.to_dict())

    # --- S8 explanation (read-only, cannot change the outcome) --------
    yield _event("stage", stage="EXPLAIN", status="STARTED")
    overall, per_rule, source, model = explain.generate(
        decision, results, _invoice_summary(header, vendor, po)
    )
    decision.ai_explanation = overall
    decision.explanation_source = source
    decision.explanation_model = model

    yield _event("explanation", text=overall, per_rule=per_rule,
                 source=source, model=model)

    # --- S9 persist and route ------------------------------------------
    status = OUTCOME_TO_STATUS[decision.outcome]

    decision_row = STORE.insert("decisions", {
        "invoice_id": invoice_id,
        "run_id": run_row["id"],
        **decision.to_dict(),
        "per_rule_explanation": per_rule,
        "is_current": True,
    })
    for row in STORE.find("decisions", invoice_id=invoice_id):
        if row["id"] != decision_row["id"] and row.get("is_current"):
            STORE.update("decisions", row["id"], is_current=False)

    STORE.update("invoices", invoice_id,
                 status=status.value,
                 extraction_confidence=str(extraction_conf),
                 match_confidence=str(match_conf))

    _settle_ledger(invoice_id, decision.outcome)

    STORE.append_audit("invoice", invoice_id, "DECIDED", {
        "outcome": decision.outcome.value,
        "decision_confidence": str(decision.decision_confidence),
        "risk_score": decision.risk_score,
        "reason_codes": decision.reason_codes,
        "run_id": run_row["id"],
        "ruleset_version": RULESET_VERSION,
    }, actor_type="SYSTEM")

    yield _event("done", invoice_id=invoice_id, status=status.value,
                 decision_id=decision_row["id"], run_id=run_row["id"])


# ----------------------------------------------------------------------
# Re-run after a targeted correction (Edge Case 2)
# ----------------------------------------------------------------------
def rules_blocked_by(invoice_id: str, field_path: str) -> List[str]:
    """Which rules were unevaluable because of this specific field.

    Used so a reviewer fixing one field re-runs only what that field blocked,
    not the whole catalogue.
    """
    run = _latest_run(invoice_id)
    if not run:
        return []
    blocked = []
    for row in STORE.find("rule_results", run_id=run["id"]):
        if row["outcome"] != Outcome.CANNOT_EVALUATE.value:
            continue
        if any(field_path in b or b.split(" (")[0].endswith(field_path.split(".")[-1])
               for b in row.get("blocked_by") or []):
            blocked.append(row["rule_id"])
    return blocked


def _latest_run(invoice_id: str) -> Optional[Dict[str, Any]]:
    runs = STORE.find("validation_runs", invoice_id=invoice_id)
    return sorted(runs, key=lambda r: r["created_at"])[-1] if runs else None


def _apply_corrections(
    invoice_id: str,
    fields: Dict[str, ExtractedField],
    header: Dict[str, Any],
    lines: List[Dict[str, Any]],
) -> Dict[str, ExtractedField]:
    """Overlay human corrections onto a fresh extraction.

    A corrected field is pinned to confidence 1.00 and marked HUMAN_CORRECTED —
    a person reading a number off the page is ground truth, and the model's
    opinion of it stops being relevant (PRD 11.1).
    """
    from .money import parse_date, parse_money

    corrections = STORE.find("extracted_fields", invoice_id=invoice_id,
                             extraction_method="HUMAN_CORRECTED")
    for row in corrections:
        path = row["field_path"]
        value = row["normalised_value"]
        fields[path] = ExtractedField(
            field_path=path,
            raw_value=row.get("raw_value"),
            normalised_value=value,
            confidence=Decimal("1.0000"),
            page_number=row.get("page_number"),
            bbox=row.get("bbox"),
            extraction_method="HUMAN_CORRECTED",
            corrected_by=row.get("corrected_by"),
            corrected_at=row.get("corrected_at"),
            previous_value=row.get("previous_value"),
        )

        if not path.startswith("header."):
            continue
        key = path.split(".", 1)[1]
        try:
            if key in ("subtotal", "tax_amount", "discount_amount",
                       "other_charges", "grand_total"):
                header[key] = parse_money(value)
            elif key in ("invoice_date", "due_date"):
                header[key], ambiguous = parse_date(value)
                if key == "invoice_date":
                    header["invoice_date_ambiguous"] = ambiguous
            else:
                header[key] = value
        except Exception as exc:
            log.warning("Correction %s=%r unusable: %s", path, value, exc)

    return fields


# ----------------------------------------------------------------------
# Persistence helpers
# ----------------------------------------------------------------------
def _find_duplicate_original(
    invoice_id: str, header: Dict[str, Any], vendor: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The earlier invoice this one appears to duplicate, if any.

    Cheap pre-check using the same two signals DUP-02 and DUP-03 use, run before
    the ledger is touched. It only decides whether to *reserve*; the duplicate
    gates still evaluate independently and are what the reviewer sees.
    """
    if not vendor:
        return None

    canonical = header.get("invoice_number_canonical")
    total = header.get("grand_total")
    inv_date = header.get("invoice_date")
    window = CONFIG.duplicate.amount_date_window_days

    for row in STORE.find("invoices", vendor_id=vendor["id"]):
        if row["id"] == invoice_id:
            continue
        if canonical and row.get("invoice_number_canonical") == canonical:
            return row
        if total is None or inv_date is None or row.get("grand_total") is None:
            continue
        try:
            if (Decimal(str(row["grand_total"])) - total).copy_abs() > Decimal("0.01"):
                continue
            from .money import parse_date

            other_date, _ = parse_date(row["invoice_date"])
        except Exception:
            continue
        if abs((other_date - inv_date).days) <= window:
            return row
    return None


def _read_document(document: Dict[str, Any]) -> Optional[bytes]:
    from pathlib import Path

    try:
        # The rendition, never the original — downstream stages only speak PDF.
        return Path(document.get("render_key") or document["storage_key"]).read_bytes()
    except Exception as exc:
        log.error("Cannot read stored document %s: %s", document.get("id"), exc)
        return None


def _persist_extraction(invoice_id, header, lines, fields, vendor, po,
                        vendor_match, po_match, line_matches, classification,
                        extraction_source) -> None:
    STORE.update(
        "invoices", invoice_id,
        vendor_id=vendor["id"] if vendor else None,
        po_id=po["id"] if po else None,
        invoice_number=header.get("invoice_number"),
        invoice_number_normalised=header.get("invoice_number_normalised"),
        invoice_number_canonical=header.get("invoice_number_canonical"),
        invoice_date=(header["invoice_date"].isoformat()
                      if header.get("invoice_date") else None),
        due_date=(header["due_date"].isoformat() if header.get("due_date") else None),
        currency=header.get("currency"),
        subtotal=_str(header.get("subtotal")),
        tax_amount=_str(header.get("tax_amount")),
        discount_amount=_str(header.get("discount_amount")),
        other_charges=_str(header.get("other_charges")),
        grand_total=_str(header.get("grand_total")),
        vendor_match=vendor_match.to_dict() if vendor_match else None,
        po_match=po_match.to_dict() if po_match else None,
        classification=classification,
        extraction_source=extraction_source,
    )

    # Extraction rows are rewritten each run, except human corrections, which
    # are the ground truth the run was built on and must survive.
    for row in STORE.find("extracted_fields", invoice_id=invoice_id):
        if row.get("extraction_method") != "HUMAN_CORRECTED":
            STORE._tables["extracted_fields"].remove(row)
    STORE.insert_many("extracted_fields", [
        {"invoice_id": invoice_id, **f.to_dict()}
        for f in fields.values() if f.extraction_method != "HUMAN_CORRECTED"
    ])

    for row in STORE.find("invoice_lines", invoice_id=invoice_id):
        STORE._tables["invoice_lines"].remove(row)
    STORE.insert_many("invoice_lines", [
        {
            "invoice_id": invoice_id,
            "line_no": line["line_no"],
            "sku": line.get("sku"),
            "description": line.get("description"),
            "quantity": _str(line["quantity"]),
            "uom": line.get("uom"),
            "unit_price": _str(line["unit_price"]),
            "line_total": _str(line["line_total"]),
            "tax_rate_pct": _str(line.get("tax_rate_pct")),
            "matched_po_line_id": _match_id(line_matches, line["line_no"]),
            "match_confidence": _match_score(line_matches, line["line_no"]),
            "match_method": _match_method(line_matches, line["line_no"]),
        }
        for line in lines
    ])


def _settle_ledger(invoice_id: str, outcome: DecisionOutcome) -> None:
    """Commit on approval, release on rejection or duplicate hold.

    A held duplicate must release its reservation, otherwise it silently
    consumes PO headroom that the legitimate invoice needs (Edge Case 3).
    """
    if outcome in (DecisionOutcome.AUTO_APPROVE,):
        STORE.settle(invoice_id, "COMMITTED")
    elif outcome in (DecisionOutcome.REJECT, DecisionOutcome.DUPLICATE_BLOCK):
        STORE.settle(invoice_id, "RELEASED")
    # MANUAL_REVIEW / NEEDS_INFO / PENDING_AUTHORISATION stay PROVISIONAL: the
    # claim is real and must block other invoices while a human looks at it.


def _str(value) -> Optional[str]:
    return None if value is None else str(value)


def _match_id(matches, line_no):
    m = next((m for m in matches if m.invoice_line_no == line_no), None)
    return m.po_line_id if m else None


def _match_score(matches, line_no):
    m = next((m for m in matches if m.invoice_line_no == line_no), None)
    return str(m.score) if m else None


def _match_method(matches, line_no):
    m = next((m for m in matches if m.invoice_line_no == line_no), None)
    return m.method if m else None


def _vendor_summary(vendor, match):
    return {
        "vendor_id": vendor["id"] if vendor else None,
        "name": vendor.get("trade_name") if vendor else None,
        "code": vendor.get("vendor_code") if vendor else None,
        "status": vendor.get("status") if vendor else None,
        "match": match.to_dict() if match else None,
    }


def _po_summary(po, match):
    return {
        "po_id": po["id"] if po else None,
        "po_number": po.get("po_number") if po else None,
        "status": po.get("status") if po else None,
        "total_amount": po.get("total_amount") if po else None,
        "match": match.to_dict() if match else None,
    }


def _invoice_summary(header, vendor, po) -> Dict[str, Any]:
    return {
        "invoice_number": header.get("invoice_number"),
        "invoice_date": (header["invoice_date"].isoformat()
                         if header.get("invoice_date") else None),
        "currency": header.get("currency"),
        "grand_total": _str(header.get("grand_total")),
        "vendor": vendor.get("trade_name") if vendor else None,
        "po_number": po.get("po_number") if po else None,
        "po_total": po.get("total_amount") if po else None,
    }


def _tally(results: List[RuleResult]) -> Dict[str, int]:
    return {
        "total": len(results),
        "passed": len([r for r in results if r.outcome == Outcome.PASS]),
        "failed": len([r for r in results if r.outcome == Outcome.FAIL]),
        "warnings": len([r for r in results if r.outcome == Outcome.WARN]),
        "cannot_evaluate": len([r for r in results
                                if r.outcome == Outcome.CANNOT_EVALUATE]),
        "not_applicable": len([r for r in results
                               if r.outcome == Outcome.NOT_APPLICABLE]),
    }
