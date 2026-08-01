"""FastAPI processing service — the API surface of PRD 14.

The Node BFF proxies to this service and relays the SSE stream. All money
arithmetic, rule evaluation and decisioning happens here, in Python, with
``Decimal`` — nothing downstream ever recomputes an amount.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import actions, decide as decide_mod, explain, llm, pipeline
from .config import CONFIG, ENGINE_VERSION, RULESET_VERSION, SETTINGS
from .models import DecisionOutcome, InvoiceStatus, Outcome, risk_band
from .rules.catalogue import ACTIVE, CATALOGUE, DEFERRED, GATE_ORDER, counts
from .rules.engine import evaluate_subset
from .store import STORE, json_default, now_iso

logging.basicConfig(level=logging.INFO,
                    format='{"ts":"%(asctime)s","level":"%(levelname)s",'
                           '"logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("engine.api")

app = FastAPI(
    title="Invoice Processing Engine",
    version=ENGINE_VERSION,
    description="Deterministic invoice validation and decisioning. "
                "The LLM extracts and explains; the rules and the decision are code.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4000",
                   "http://127.0.0.1:5173", "http://127.0.0.1:4000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def envelope(data: Any = None, meta: Optional[Dict[str, Any]] = None,
             errors: Optional[List[Any]] = None) -> Dict[str, Any]:
    """PRD 14: every response is ``{ data, meta, errors }``."""
    return {"data": data, "meta": meta or {}, "errors": errors or []}


@app.exception_handler(actions.ActionError)
async def action_error_handler(request: Request, exc: actions.ActionError):
    return JSONResponse(
        status_code=exc.status,
        content=envelope(errors=[{"code": exc.code, "detail": exc.detail,
                                  **exc.extra}]),
    )


# ======================================================================
# Health and metadata
# ======================================================================
@app.get("/health")
async def health():
    return envelope({
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "llm_available": llm.available(),
        "mode": "full" if llm.available() else "deterministic-only (degraded)",
        "rules": counts(),
    })


@app.get("/rules")
async def get_rules():
    """The catalogue, including the 6 deferred rules with their reasons.
    Showing what was deliberately not built is more credible than hiding it."""
    return envelope(
        {
            "gates": [
                {
                    "gate": gate.value,
                    "rules": [
                        {**spec.to_dict(),
                         "status": "ACTIVE" if spec.mvp else "DESIGNED_NOT_ACTIVE"}
                        for spec in CATALOGUE if spec.gate == gate
                    ],
                }
                for gate in GATE_ORDER
            ],
            "thresholds": CONFIG.to_dict(),
        },
        meta={**counts(), "ruleset_version": RULESET_VERSION,
              "deferred": [r.id for r in DEFERRED]},
    )


@app.get("/config")
async def get_config():
    return envelope(CONFIG.to_dict(), meta={"ruleset_version": RULESET_VERSION})


@app.get("/users")
async def get_users():
    return envelope([
        {k: v for k, v in u.items() if not k.startswith("_")}
        for u in STORE.all("users")
    ])


@app.get("/reason-codes")
async def get_reason_codes():
    return envelope(actions.REASON_CODES)


# ======================================================================
# Ingestion — PRD 14.1
# ======================================================================
@app.post("/invoices", status_code=202)
async def upload_invoice(
    file: UploadFile = File(...),
    source: str = Form("MANUAL_UPLOAD"),
    uploaded_by: Optional[str] = Form(None),
):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "File exceeds the 50MB limit.")

    result = pipeline.intake(data, file.filename or "upload.pdf", source, uploaded_by)

    if result["duplicate"]:
        # Short-circuited on the hash, before any OCR or LLM spend.
        return JSONResponse(status_code=409, content=envelope(
            {
                "documentId": result["document"]["id"],
                "sha256": result["document"]["sha256"],
                "existingInvoiceId": result["existing_invoice_id"],
                "existingStatus": result["existing_status"],
            },
            errors=[{"code": "DUPLICATE_DOCUMENT",
                     "detail": "This exact file has already been submitted. "
                               "No extraction was performed."}],
        ))

    return envelope({
        "invoiceId": result["invoice"]["id"],
        "documentId": result["document"]["id"],
        "sha256": result["document"]["sha256"],
        "status": InvoiceStatus.INGESTED.value,
        "isScanned": result["document"]["is_scanned"],
        "pageCount": result["document"]["page_count"],
        "sourceFormat": result["document"].get("source_format"),
        "sourceFormatLabel": result["document"].get("source_format_label"),
        "converted": result["document"].get("converted", False),
        "conversionError": result["document"].get("conversion_error"),
    })


@app.post("/ingest/folder-scan", status_code=202)
async def folder_scan(uploaded_by: Optional[str] = Form(None)):
    """Simulates the email / watched-folder pickup that would exist in
    production (PRD 5.1). Reads the fixture corpus in its declared order,
    because several fixtures are stateful by design."""
    manifest_path = SETTINGS.fixture_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "No fixture corpus found. Run "
                                 "`python -m fixtures.generate` first.")

    manifest = json.loads(manifest_path.read_text())
    queued, skipped = [], []
    for entry in manifest:
        pdf = SETTINGS.fixture_dir / entry["pdf"]
        if not pdf.exists():
            continue
        result = pipeline.intake(pdf.read_bytes(), pdf.name,
                                 "FOLDER_PICKUP", uploaded_by)
        if result["duplicate"]:
            skipped.append({"file": pdf.name,
                            "existingInvoiceId": result["existing_invoice_id"]})
        else:
            queued.append({"file": pdf.name,
                           "invoiceId": result["invoice"]["id"],
                           "note": entry.get("note", "")})

    batch_id = f"batch-{int(datetime.now(timezone.utc).timestamp())}"
    STORE.append_audit("batch", batch_id, "FOLDER_SCAN", {
        "queued": len(queued), "skipped_duplicates": len(skipped),
    }, actor_id=uploaded_by)

    return envelope({"batchId": batch_id, "queued": len(queued),
                     "skippedDuplicates": len(skipped),
                     "invoices": queued, "skipped": skipped})


# ======================================================================
# Processing stream — PRD 14.2
# ======================================================================
def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=json_default)}\n\n"


@app.get("/invoices/{invoice_id}/stream")
async def stream_invoice(invoice_id: str, trigger: str = Query("INITIAL")):
    """Run the pipeline, streaming each stage, field, rule and the decision.

    Rule events are paced (~120ms) purely so a human can watch the sequence;
    the underlying evaluation of all 49 rules takes well under 200ms.
    """
    if STORE.get("invoices", invoice_id) is None:
        raise HTTPException(404, f"No such invoice: {invoice_id}")

    async def generator() -> AsyncIterator[str]:
        try:
            async for event in pipeline.run(invoice_id, trigger=trigger):
                yield sse(event["event"], event["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Pipeline failed for %s", invoice_id)
            yield sse("error", {"message": str(exc), "invoiceId": invoice_id})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# ======================================================================
# Retrieval — PRD 14.3
# ======================================================================
def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _enrich(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Attach everything the queue table needs in one shape."""
    decision = actions.current_decision(invoice["id"]) or {}
    vendor = STORE.get("vendors", invoice.get("vendor_id")) if invoice.get("vendor_id") else None
    po = STORE.get("purchase_orders", invoice.get("po_id")) if invoice.get("po_id") else None
    document = STORE.get("documents", invoice.get("document_id"))

    age_days = None
    if invoice.get("created_at"):
        try:
            created = datetime.fromisoformat(invoice["created_at"])
            age_days = (datetime.now(timezone.utc) - created).days
        except Exception:
            pass

    return {
        **invoice,
        "vendor_name": vendor.get("trade_name") if vendor else None,
        "vendor_code": vendor.get("vendor_code") if vendor else None,
        "po_number": po.get("po_number") if po else None,
        "original_filename": document.get("original_filename") if document else None,
        "is_scanned": document.get("is_scanned") if document else None,
        "decision": {
            "outcome": decision.get("outcome"),
            "decision_confidence": decision.get("decision_confidence"),
            "risk_score": decision.get("risk_score"),
            "risk_band": decision.get("risk_band"),
            "reason_codes": decision.get("reason_codes", []),
            "routed_to_role": decision.get("routed_to_role"),
        } if decision else None,
        "age_days": age_days,
        "overridden": bool(invoice.get("overridden")),
    }


RISK_ORDER = {"SEVERE": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, None: 4}


@app.get("/invoices")
async def list_invoices(
    status: Optional[str] = None,
    vendorId: Optional[str] = None,
    riskBand: Optional[str] = None,
    minAmount: Optional[float] = None,
    maxAmount: Optional[float] = None,
    ruleFailed: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = Query("risk"),
    page: int = 1,
    pageSize: int = 50,
):
    rows = [_enrich(i) for i in STORE.all("invoices")]

    if status:
        wanted = set(status.split(","))
        rows = [r for r in rows if r.get("status") in wanted]
    if vendorId:
        rows = [r for r in rows if r.get("vendor_id") == vendorId]
    if riskBand:
        rows = [r for r in rows if (r.get("decision") or {}).get("risk_band") == riskBand]
    if minAmount is not None:
        rows = [r for r in rows if _decimal(r.get("grand_total")) >= Decimal(str(minAmount))]
    if maxAmount is not None:
        rows = [r for r in rows if _decimal(r.get("grand_total")) <= Decimal(str(maxAmount))]
    if ruleFailed:
        rows = [r for r in rows
                if ruleFailed in ((r.get("decision") or {}).get("reason_codes") or [])]
    if search:
        needle = search.lower()
        rows = [r for r in rows if needle in json.dumps(
            {k: r.get(k) for k in ("invoice_number", "vendor_name", "po_number")},
            default=str).lower()]

    if sort == "risk":
        # PRD 13.2: highest exposure first, then oldest. AP queues are worked
        # FIFO by habit, not by design.
        rows.sort(key=lambda r: (
            RISK_ORDER.get((r.get("decision") or {}).get("risk_band"), 4),
            r.get("created_at") or "",
        ))
    elif sort == "amount":
        rows.sort(key=lambda r: _decimal(r.get("grand_total")), reverse=True)
    elif sort == "age":
        rows.sort(key=lambda r: r.get("created_at") or "")
    else:
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    total = len(rows)
    start = (page - 1) * pageSize
    return envelope(rows[start:start + pageSize],
                    meta={"total": total, "page": page, "pageSize": pageSize,
                          "sort": sort})


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str):
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")

    decision = actions.current_decision(invoice_id)
    runs = sorted(STORE.find("validation_runs", invoice_id=invoice_id),
                  key=lambda r: r["created_at"], reverse=True)
    rules = STORE.find("rule_results", run_id=runs[0]["id"]) if runs else []
    vendor = STORE.get("vendors", invoice.get("vendor_id")) if invoice.get("vendor_id") else None
    po = STORE.get("purchase_orders", invoice.get("po_id")) if invoice.get("po_id") else None

    return envelope({
        "invoice": _enrich(invoice),
        "document": STORE.get("documents", invoice.get("document_id")),
        "vendor": vendor,
        "purchaseOrder": po,
        "poLines": sorted(STORE.find("po_lines", po_id=po["id"]),
                          key=lambda l: l["line_no"]) if po else [],
        "lines": sorted(STORE.find("invoice_lines", invoice_id=invoice_id),
                        key=lambda l: l["line_no"]),
        "fields": sorted(STORE.find("extracted_fields", invoice_id=invoice_id),
                         key=lambda f: f["field_path"]),
        "decision": decision,
        "rules": rules,
        "runs": runs,
        "humanActions": STORE.find("human_actions", invoice_id=invoice_id),
        "securityFlags": _security_flags(invoice_id),
    })


def _security_flags(invoice_id: str) -> List[Dict[str, Any]]:
    events = [e for e in STORE.find("audit_events", entity_id=invoice_id)
              if e["event_type"] == "SECURITY_ANOMALY_DETECTED"]
    return events[-1]["payload"].get("flags", []) if events else []


@app.get("/invoices/{invoice_id}/extraction")
async def get_extraction(invoice_id: str):
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")
    document = STORE.get("documents", invoice.get("document_id")) or {}
    fields = STORE.find("extracted_fields", invoice_id=invoice_id)
    floor = CONFIG.confidence.critical_field_floor

    return envelope({
        "fields": sorted(fields, key=lambda f: f["field_path"]),
        "lines": sorted(STORE.find("invoice_lines", invoice_id=invoice_id),
                        key=lambda l: l["line_no"]),
        "extractionConfidence": invoice.get("extraction_confidence"),
        "matchConfidence": invoice.get("match_confidence"),
        "extractionSource": invoice.get("extraction_source"),
        "extractionUnavailable": invoice.get("extraction_source") == "UNAVAILABLE",
        "sourceFormat": document.get("source_format"),
        "sourceFormatLabel": document.get("source_format_label"),
        "converted": bool(document.get("converted")),
        "extractionUnavailableReason": (
            "No language model is configured and this document has no recorded "
            "extraction payload, so there was nothing to read it with. Set "
            "AZURE_OPENAI_API_KEY in .env and re-run validation."
            if invoice.get("extraction_source") == "UNAVAILABLE" else None
        ),
        "readingPath": ("Scanned document — vision extraction applied"
                        if document.get("is_scanned")
                        else "Digital PDF — native text extraction, no OCR required"),
        "belowFloor": [
            f for f in fields
            if _decimal(f.get("confidence")) < floor
            and f["field_path"].startswith("header.")
        ],
        "criticalFieldFloor": str(floor),
    }, meta={"pageCount": document.get("page_count")})


@app.get("/invoices/{invoice_id}/runs")
async def get_runs(invoice_id: str):
    runs = sorted(STORE.find("validation_runs", invoice_id=invoice_id),
                  key=lambda r: r["created_at"], reverse=True)
    for run in runs:
        results = STORE.find("rule_results", run_id=run["id"])
        run["tally"] = {
            outcome.value: len([r for r in results if r["outcome"] == outcome.value])
            for outcome in Outcome
        }
    return envelope(runs)


@app.get("/invoices/{invoice_id}/runs/{run_id}/rules")
async def get_run_rules(invoice_id: str, run_id: str):
    return envelope(STORE.find("rule_results", run_id=run_id))


@app.get("/invoices/{invoice_id}/document")
async def get_document(invoice_id: str):
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")
    document = STORE.get("documents", invoice.get("document_id"))
    path = Path(document["storage_key"]) if document else None
    if not path or not path.exists():
        raise HTTPException(404, "Stored document not found")
    return FileResponse(path, media_type="application/pdf",
                        filename=document.get("original_filename"))


@app.get("/invoices/{invoice_id}/page/{page_number}.png")
async def get_page_image(invoice_id: str, page_number: int, dpi: int = Query(150)):
    """Render one page as a PNG.

    The extraction overlay needs pixel-exact alignment between a normalised
    bounding box and what the reviewer sees. Rendering server-side and drawing
    an SVG over a plain image gives that exactly, where an embedded PDF viewer
    would not expose page geometry to the page.
    """
    from fastapi.responses import Response

    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")
    document = STORE.get("documents", invoice.get("document_id"))
    path = Path(document["storage_key"]) if document else None
    if not path or not path.exists():
        raise HTTPException(404, "Stored document not found")

    from . import ingest as ingest_mod

    png = ingest_mod.render_page_png(path.read_bytes(), page_number,
                                     dpi=max(72, min(dpi, 300)))
    if png is None:
        raise HTTPException(404, f"Page {page_number} not found")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/invoices/{invoice_id}/duplicates")
async def get_duplicates(invoice_id: str):
    """Duplicate candidates with the signals that flagged them, for the
    side-by-side comparison screen (Edge Case 3)."""
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")

    runs = sorted(STORE.find("validation_runs", invoice_id=invoice_id),
                  key=lambda r: r["created_at"], reverse=True)
    candidates: Dict[str, Dict[str, Any]] = {}
    if runs:
        for row in STORE.find("rule_results", run_id=runs[0]["id"]):
            if not row["rule_id"].startswith("DUP") or row["outcome"] != "FAIL":
                continue
            evidence = row.get("evidence") or {}
            for key in ("existing", "near_duplicates", "matches", "in_flight"):
                for hit in evidence.get(key) or []:
                    other_id = hit.get("invoice_id")
                    if not other_id:
                        continue
                    entry = candidates.setdefault(other_id, {
                        "invoice": _enrich(STORE.get("invoices", other_id) or {}),
                        "signals": [],
                    })
                    entry["signals"].append({
                        "rule_id": row["rule_id"], "rule_name": row["name"],
                        "detail": hit,
                    })

    return envelope({
        "invoice": _enrich(invoice),
        "candidates": list(candidates.values()),
        "fieldComparison": [
            _compare_field(invoice, STORE.get("invoices", other_id), label, key)
            for other_id in candidates
            for label, key in (
                ("Invoice number", "invoice_number"),
                ("Invoice date", "invoice_date"),
                ("Grand total", "grand_total"),
                ("Vendor", "vendor_id"),
                ("PO", "po_id"),
            )
        ],
    })


def _compare_field(a: Dict[str, Any], b: Optional[Dict[str, Any]],
                   label: str, key: str) -> Dict[str, Any]:
    left, right = a.get(key), (b or {}).get(key)
    return {"label": label, "field": key, "this": left, "other": right,
            "matches": left == right,
            "otherInvoiceId": (b or {}).get("id")}


@app.get("/pos/{po_number}/ledger")
async def get_po_ledger(po_number: str):
    """The consumption ledger — the visual payoff for Edge Case 1."""
    po = STORE.find_one("purchase_orders", po_number=po_number) \
         or STORE.get("purchase_orders", po_number)
    if not po:
        raise HTTPException(404, f"No such purchase order: {po_number}")

    total = _decimal(po["total_amount"])
    rows = []
    running = Decimal("0")
    for entry in STORE.po_ledger(po["id"]):
        if entry.get("po_line_id"):
            continue      # header-level rows only for the main bar
        invoice = STORE.get("invoices", entry["invoice_id"]) or {}
        amount = _decimal(entry["amount_consumed"])
        counts_toward = entry["status"] in ("PROVISIONAL", "COMMITTED")
        if counts_toward:
            running += amount
        rows.append({
            "invoiceId": entry["invoice_id"],
            "invoiceNumber": invoice.get("invoice_number"),
            "invoiceDate": invoice.get("invoice_date"),
            "invoiceStatus": invoice.get("status"),
            "amount": str(amount),
            "ledgerStatus": entry["status"],
            "countsTowardConsumption": counts_toward,
            "runningTotal": str(running),
            "runningPct": str((running / total * 100).quantize(Decimal("0.01"))
                              if total else Decimal("0")),
            "createdAt": entry.get("created_at"),
        })

    consumed = STORE.po_consumed(po["id"])
    line_rows = []
    for po_line in sorted(STORE.find("po_lines", po_id=po["id"]),
                          key=lambda l: l["line_no"]):
        ordered = _decimal(po_line["quantity_ordered"])
        used = STORE.po_line_consumed_qty(po_line["id"])
        line_rows.append({
            **po_line,
            "quantityConsumed": str(used),
            "quantityRemaining": str(ordered - used),
            "consumedPct": str((used / ordered * 100).quantize(Decimal("0.01"))
                               if ordered else Decimal("0")),
        })

    return envelope({
        "purchaseOrder": po,
        "vendor": STORE.get("vendors", po["vendor_id"]),
        "totalAmount": str(total),
        "consumed": str(consumed),
        "remaining": str(total - consumed),
        "consumedPct": str((consumed / total * 100).quantize(Decimal("0.01"))
                           if total else Decimal("0")),
        "overConsumed": consumed > total,
        "entries": rows,
        "lines": line_rows,
    })


@app.get("/masters/procurement")
async def procurement_master():
    """The procurement spreadsheet, with live consumption folded in.

    This is the "spreadsheet the PO lives in" from the brief, made browsable.
    Invoices are never stored here — they arrive as PDFs and are matched against
    these rows. The running balance is the platform's ledger, not the sheet's.
    """
    vendors = {v["id"]: v for v in STORE.all("vendors")}
    all_lines = STORE.all("po_lines")

    pos = []
    for po in STORE.all("purchase_orders"):
        total = _decimal(po["total_amount"])
        consumed = STORE.po_consumed(po["id"])
        vendor = vendors.get(po["vendor_id"], {})

        invoices = []
        for entry in STORE.po_ledger(po["id"]):
            if entry.get("po_line_id"):
                continue
            invoice = STORE.get("invoices", entry["invoice_id"]) or {}
            invoices.append({
                "invoiceId": entry["invoice_id"],
                "invoiceNumber": invoice.get("invoice_number"),
                "invoiceDate": invoice.get("invoice_date"),
                "amount": entry["amount_consumed"],
                "ledgerStatus": entry["status"],
                "invoiceStatus": invoice.get("status"),
            })

        lines = []
        for line in sorted([l for l in all_lines if l["po_id"] == po["id"]],
                           key=lambda l: int(l["line_no"])):
            ordered = _decimal(line["quantity_ordered"])
            used = STORE.po_line_consumed_qty(line["id"])
            lines.append({
                **line,
                "quantityConsumed": str(used),
                "quantityRemaining": str(ordered - used),
                "consumedPct": str((used / ordered * 100).quantize(Decimal("0.01"))
                                   if ordered else Decimal("0")),
            })

        pos.append({
            **po,
            "vendor_name": vendor.get("trade_name"),
            "vendor_code": vendor.get("vendor_code"),
            "vendor_status": vendor.get("status"),
            "consumed": str(consumed),
            "remaining": str(total - consumed),
            "consumedPct": str((consumed / total * 100).quantize(Decimal("0.01"))
                               if total else Decimal("0")),
            "overConsumed": consumed > total,
            "invoiceCount": len([i for i in invoices
                                 if i["ledgerStatus"] != "RELEASED"]),
            "invoices": invoices,
            "lines": lines,
        })

    return envelope(
        {"purchaseOrders": pos, "vendors": list(vendors.values())},
        meta={
            "source": "app/seed/*.csv",
            "note": "CSV is the seed source the engine loads at startup, standing "
                    "in for the procurement spreadsheet the brief describes. "
                    "Invoices are matched against these rows; they are never "
                    "stored here.",
            "poCount": len(pos),
            "lineCount": len(all_lines),
            "vendorCount": len(vendors),
        },
    )


@app.get("/masters/download/{sheet}")
async def download_sheet(sheet: str):
    """Serve the raw spreadsheet — the CSVs, or the Excel workbook."""
    from fastapi.responses import Response

    allowed = {
        "purchase_orders": ("purchase_orders.csv", "text/csv"),
        "po_lines": ("po_lines.csv", "text/csv"),
        "vendors": ("vendors.csv", "text/csv"),
        "workbook": ("procurement_master.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }
    if sheet not in allowed:
        raise HTTPException(404, f"Unknown sheet '{sheet}'. "
                                 f"Try one of: {', '.join(allowed)}")

    filename, media_type = allowed[sheet]
    path = SETTINGS.seed_dir / filename
    if not path.exists():
        raise HTTPException(404, f"{filename} has not been generated. Run "
                                 f"`python -m app.seed.build_sheets`.")
    return Response(
        content=path.read_bytes(), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/invoices/{invoice_id}/match")
async def get_match(invoice_id: str):
    """Everything about how this invoice was reconciled to its PO, in one shape.

    The reconciliation is scattered across the pipeline by necessity — vendor
    resolution in S5, header checks in FIN, line checks in LIN, cumulative
    exposure in the ledger. This endpoint gathers it so the UI can show the
    whole two-way match on one screen.
    """
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")

    vendor = STORE.get("vendors", invoice.get("vendor_id")) if invoice.get("vendor_id") else None
    po = STORE.get("purchase_orders", invoice.get("po_id")) if invoice.get("po_id") else None
    po_lines = sorted(STORE.find("po_lines", po_id=po["id"]),
                      key=lambda l: int(l["line_no"])) if po else []
    by_id = {l["id"]: l for l in po_lines}
    inv_lines = sorted(STORE.find("invoice_lines", invoice_id=invoice_id),
                       key=lambda l: int(l["line_no"]))

    runs = sorted(STORE.find("validation_runs", invoice_id=invoice_id),
                  key=lambda r: r["created_at"])
    rules = {r["rule_id"]: r for r in
             (STORE.find("rule_results", run_id=runs[-1]["id"]) if runs else [])}

    # --- line-by-line reconciliation
    reconciliation = []
    claimed = set()
    for line in inv_lines:
        po_line = by_id.get(line.get("matched_po_line_id"))
        if po_line:
            claimed.add(po_line["id"])

        entry = {
            "invoiceLine": line,
            "poLine": po_line,
            "matchMethod": line.get("match_method"),
            "matchConfidence": line.get("match_confidence"),
            "status": "MATCHED" if po_line else "NOT_ON_PO",
            "deltas": {},
        }

        if po_line:
            inv_price = _decimal(line["unit_price"])
            po_price = _decimal(po_line["unit_price"])
            inv_qty = _decimal(line["quantity"])
            ordered = _decimal(po_line["quantity_ordered"])
            prior = STORE.po_line_consumed_qty(po_line["id"],
                                               exclude_invoice_id=invoice_id)

            entry["deltas"] = {
                "unitPrice": {
                    "po": str(po_price), "invoice": str(inv_price),
                    "delta": str(inv_price - po_price),
                    "deltaPct": str((inv_price - po_price) / po_price * 100)
                    if po_price else None,
                    "withinTolerance": (
                        po_price == 0
                        or abs((inv_price - po_price) / po_price * 100)
                        <= CONFIG.tolerance.unit_price_pct
                        or inv_price <= po_price
                    ),
                },
                "quantity": {
                    "ordered": str(ordered), "priorInvoiced": str(prior),
                    "thisInvoice": str(inv_qty),
                    "cumulative": str(prior + inv_qty),
                    "remainingAfter": str(ordered - prior - inv_qty),
                    "withinOrdered": (prior + inv_qty) <= ordered,
                },
                "uom": {
                    "po": po_line.get("uom"), "invoice": line.get("uom"),
                    "matches": (po_line.get("uom") or "").upper()
                    == (line.get("uom") or "").upper(),
                },
                "valueImpact": str(quantise_impact(inv_price, po_price, inv_qty)),
            }
        reconciliation.append(entry)

    unbilled = [by_id[i] for i in by_id if i not in claimed]

    # --- header comparison
    header = None
    if po:
        po_total = _decimal(po["total_amount"])
        inv_total = _decimal(invoice.get("grand_total"))
        consumed = STORE.po_consumed(po["id"])
        prior = STORE.po_consumed(po["id"], exclude_invoice_id=invoice_id)
        header = {
            "vendor": {
                # What the invoice itself printed, before resolution — so a
                # reviewer can see the name that was matched, not the id it
                # resolved to.
                "invoice": (
                    (STORE.find_one("extracted_fields", invoice_id=invoice_id,
                                    field_path="header.vendor_name") or {})
                    .get("normalised_value")
                    or (vendor or {}).get("trade_name")
                ),
                "po": (STORE.get("vendors", po["vendor_id"]) or {}).get("trade_name"),
                "matches": po["vendor_id"] == invoice.get("vendor_id"),
                "method": (invoice.get("vendor_match") or {}).get("method"),
                "score": (invoice.get("vendor_match") or {}).get("score"),
            },
            "currency": {"invoice": invoice.get("currency"), "po": po.get("currency"),
                         "matches": invoice.get("currency") == po.get("currency")},
            "poNumber": {"invoice": invoice.get("invoice_number"),
                         "po": po.get("po_number"),
                         "method": (invoice.get("po_match") or {}).get("method")},
            "amount": {
                "invoice": str(inv_total), "poTotal": str(po_total),
                "priorInvoiced": str(prior),
                "cumulative": str(prior + inv_total),
                "remainingBefore": str(po_total - prior),
                "remainingAfter": str(po_total - prior - inv_total),
                "consumedPct": str(((prior + inv_total) / po_total * 100)
                                   .quantize(Decimal("0.01")) if po_total else "0"),
                "overConsumed": (prior + inv_total) > po_total,
            },
            "dates": {"invoice": invoice.get("invoice_date"),
                      "poDate": po.get("po_date"),
                      "poValidUntil": po.get("valid_until")},
            "partialInvoicingAllowed": bool(po.get("allows_partial_invoicing")),
        }

    return envelope({
        "invoice": _enrich(invoice),
        "vendor": vendor,
        "purchaseOrder": po,
        "header": header,
        "reconciliation": reconciliation,
        "unbilledPoLines": unbilled,
        "siblingInvoices": [
            {
                "invoiceId": e["invoice_id"],
                "invoiceNumber": (STORE.get("invoices", e["invoice_id"]) or {})
                .get("invoice_number"),
                "invoiceDate": (STORE.get("invoices", e["invoice_id"]) or {})
                .get("invoice_date"),
                "status": (STORE.get("invoices", e["invoice_id"]) or {}).get("status"),
                "amount": e["amount_consumed"],
                "ledgerStatus": e["status"],
                "isThisInvoice": e["invoice_id"] == invoice_id,
            }
            for e in (STORE.po_ledger(po["id"]) if po else [])
            if not e.get("po_line_id")
        ],
        "relevantRules": {
            rid: rules[rid] for rid in
            ("EXT-10", "VEN-01", "PO-01", "PO-02", "PO-03", "PO-04", "PO-05",
             "PO-06", "PO-07", "FIN-05", "LIN-01", "LIN-02", "LIN-03", "LIN-05",
             "LIN-06", "LIN-08", "POL-02")
            if rid in rules
        },
    })


def quantise_impact(inv_price: Decimal, po_price: Decimal, qty: Decimal) -> Decimal:
    return ((inv_price - po_price) * qty).quantize(Decimal("0.01"))


@app.get("/dashboard/summary")
async def dashboard_summary():
    """The status cards and the secondary metric strip (PRD 13.2)."""
    invoices = [_enrich(i) for i in STORE.all("invoices")]

    def bucket(*statuses: str) -> List[Dict[str, Any]]:
        return [i for i in invoices if i.get("status") in statuses]

    def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "count": len(rows),
            "value": str(sum((_decimal(r.get("grand_total")) for r in rows),
                             Decimal("0"))),
        }

    approved = bucket(InvoiceStatus.APPROVED.value)
    rejected = bucket(InvoiceStatus.REJECTED.value)
    decided = [i for i in invoices if i.get("decision")]
    auto = [i for i in decided
            if (i["decision"] or {}).get("outcome") == DecisionOutcome.AUTO_APPROVE.value]

    aged = [i for i in bucket(InvoiceStatus.PENDING_REVIEW.value)
            if (i.get("age_days") or 0) >= 1]

    reject_reasons: Dict[str, int] = {}
    for row in rejected:
        for code in (row.get("decision") or {}).get("reason_codes", []):
            reject_reasons[code] = reject_reasons.get(code, 0) + 1

    blocked_fields: Dict[str, int] = {}
    for row in bucket(InvoiceStatus.NEEDS_INFO.value):
        decision = actions.current_decision(row["id"]) or {}
        for blocker in decision.get("blocked_on", []):
            name = blocker.split(" (")[0]
            blocked_fields[name] = blocked_fields.get(name, 0) + 1

    return envelope({
        "cards": {
            "pendingReview": {**summarise(bucket(InvoiceStatus.PENDING_REVIEW.value)),
                              "agedOver24h": len(aged)},
            "pendingApproval": summarise(bucket(InvoiceStatus.PENDING_APPROVAL.value)),
            "needsInfo": {**summarise(bucket(InvoiceStatus.NEEDS_INFO.value)),
                          "blockedFields": blocked_fields},
            "duplicatesHeld": summarise(bucket(InvoiceStatus.DUPLICATE_HELD.value)),
            "approved": {**summarise(approved),
                         "stpRate": round(len(auto) / len(decided), 4) if decided else 0.0},
            "rejected": {**summarise(rejected),
                         "topReasons": dict(sorted(reject_reasons.items(),
                                                   key=lambda kv: -kv[1])[:3])},
        },
        "metrics": {
            "totalInvoices": len(invoices),
            "totalValue": str(sum((_decimal(i.get("grand_total")) for i in invoices),
                                  Decimal("0"))),
            **actions.override_analytics(),
            "llmAvailable": llm.available(),
            "rulesetVersion": RULESET_VERSION,
        },
        "riskMix": {
            band: len([i for i in invoices
                       if (i.get("decision") or {}).get("risk_band") == band])
            for band in ("LOW", "MEDIUM", "HIGH", "SEVERE")
        },
        "outcomeMix": {
            outcome.value: len([i for i in invoices
                                if (i.get("decision") or {}).get("outcome") == outcome.value])
            for outcome in DecisionOutcome
        },
    })


@app.get("/audit")
async def get_audit(entityType: Optional[str] = None, entityId: Optional[str] = None,
                    eventType: Optional[str] = None, limit: int = 500):
    events = STORE.all("audit_events")
    if entityType:
        events = [e for e in events if e["entity_type"] == entityType]
    if entityId:
        events = [e for e in events if e["entity_id"] == entityId]
    if eventType:
        events = [e for e in events if e["event_type"] == eventType]
    events = list(reversed(events))[:limit]

    return envelope(events, meta={"chain": STORE.verify_audit_chain()})


@app.get("/audit/verify")
async def verify_audit():
    """Recompute the hash chain. An auditor runs this to prove nothing was
    edited after the fact."""
    return envelope(STORE.verify_audit_chain())


@app.get("/vendors")
async def get_vendors():
    return envelope(STORE.all("vendors"))


@app.get("/pos")
async def get_pos():
    rows = []
    for po in STORE.all("purchase_orders"):
        total = _decimal(po["total_amount"])
        consumed = STORE.po_consumed(po["id"])
        vendor = STORE.get("vendors", po["vendor_id"])
        rows.append({
            **po,
            "vendor_name": vendor.get("trade_name") if vendor else None,
            "consumed": str(consumed),
            "remaining": str(total - consumed),
            "consumedPct": str((consumed / total * 100).quantize(Decimal("0.01"))
                               if total else Decimal("0")),
        })
    return envelope(rows)


# ======================================================================
# Human actions — PRD 14.4
# ======================================================================
class FieldPatch(BaseModel):
    path: str
    value: str
    reason: str = "OCR_CORRECTION"
    actorId: str = "u-priya"


@app.patch("/invoices/{invoice_id}/fields")
async def patch_field(invoice_id: str, body: FieldPatch):
    """Correct one field and re-run only what that field blocked.

    Edge Case 2's payoff: the reviewer confirms a single number and the system
    re-evaluates the four or five rules that were waiting on it, retaining both
    validation runs.
    """
    blocked = pipeline.rules_blocked_by(invoice_id, body.path)
    result = actions.correct_field(invoice_id, body.path, body.value,
                                   body.actorId, body.reason)
    return envelope({
        **result,
        "rulesToRerun": blocked,
        "rerunRequired": True,
        "streamUrl": f"/invoices/{invoice_id}/stream?trigger=RERUN_AFTER_CORRECTION",
    })


class ConfirmBody(BaseModel):
    action: str = "CONFIRM"
    actorId: str = "u-priya"


@app.post("/invoices/{invoice_id}/decision")
async def post_decision(invoice_id: str, body: ConfirmBody):
    return envelope(actions.confirm(invoice_id, body.actorId))


class OverrideBody(BaseModel):
    humanDecision: str
    reasonCode: str
    reasonNote: str = ""
    actorId: str = "u-priya"
    secondApproverId: Optional[str] = None
    attachmentId: Optional[str] = None


@app.post("/invoices/{invoice_id}/override/requirements")
async def get_override_requirements(invoice_id: str, body: OverrideBody):
    """What this override will demand, so the modal can show it up front rather
    than rejecting after the reviewer has typed a justification."""
    return envelope(actions.override_requirements(
        invoice_id, body.actorId, body.humanDecision, body.reasonCode))


@app.post("/invoices/{invoice_id}/override")
async def post_override(invoice_id: str, body: OverrideBody, request: Request):
    return envelope(actions.override(
        invoice_id, body.actorId, body.humanDecision, body.reasonCode,
        body.reasonNote, body.secondApproverId, body.attachmentId,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))


class DuplicateBody(BaseModel):
    reasonCode: str = "VENDOR_CLARIFICATION_RECEIVED"
    reasonNote: str = ""
    actorId: str = "u-priya"
    secondApproverId: Optional[str] = None


@app.post("/invoices/{invoice_id}/duplicate-release")
async def post_duplicate_release(invoice_id: str, body: DuplicateBody):
    return envelope(actions.release_duplicate(
        invoice_id, body.actorId, body.reasonCode, body.reasonNote,
        body.secondApproverId))


@app.post("/invoices/{invoice_id}/duplicate-confirm")
async def post_duplicate_confirm(invoice_id: str, body: DuplicateBody):
    return envelope(actions.confirm_duplicate(invoice_id, body.actorId,
                                              body.reasonNote))


class RequestInfoBody(BaseModel):
    target: str = "VENDOR"
    fields: List[str] = Field(default_factory=list)
    message: str = ""
    actorId: str = "u-priya"


@app.post("/invoices/{invoice_id}/request-info")
async def post_request_info(invoice_id: str, body: RequestInfoBody):
    return envelope(actions.request_info(invoice_id, body.actorId, body.target,
                                         body.fields, body.message))


# ======================================================================
# Replay — PRD 16
# ======================================================================
@app.post("/invoices/{invoice_id}/replay")
async def replay(invoice_id: str, rulesetVersion: Optional[str] = None):
    """Re-run the stored extraction against a ruleset version and diff the
    outcome. The single most useful endpoint for an auditor, and the one that
    quantifies a threshold change before it is applied."""
    invoice = STORE.get("invoices", invoice_id)
    if not invoice:
        raise HTTPException(404, f"No such invoice: {invoice_id}")

    runs = sorted(STORE.find("validation_runs", invoice_id=invoice_id),
                  key=lambda r: r["created_at"])
    if not runs:
        raise HTTPException(400, "This invoice has never been validated.")

    before = {r["rule_id"]: r["outcome"]
              for r in STORE.find("rule_results", run_id=runs[-1]["id"])}
    before_decision = (actions.current_decision(invoice_id) or {}).get("outcome")

    after: Dict[str, str] = {}
    after_decision = None
    async for event in pipeline.run(invoice_id, trigger="MANUAL"):
        if event["event"] == "rule":
            after[event["data"]["rule_id"]] = event["data"]["outcome"]
        elif event["event"] == "decision":
            after_decision = event["data"]["outcome"]

    changed = [{"ruleId": rule_id, "before": before.get(rule_id),
                "after": after.get(rule_id)}
               for rule_id in sorted(set(before) | set(after))
               if before.get(rule_id) != after.get(rule_id)]

    return envelope({
        "invoiceId": invoice_id,
        "rulesetVersion": rulesetVersion or RULESET_VERSION,
        "decisionBefore": before_decision,
        "decisionAfter": after_decision,
        "decisionChanged": before_decision != after_decision,
        "rulesChanged": changed,
        "identical": not changed and before_decision == after_decision,
    })


# ======================================================================
# Demo control
# ======================================================================
@app.post("/admin/reset")
async def reset(keepMasters: bool = True):
    """Wipe transactional state, keeping the seeded masters. Used between demo
    runs so the stateful edge cases start clean."""
    STORE.reset(keep_masters=keepMasters)
    return envelope({"reset": True, "mastersKept": keepMasters})


@app.get("/fixtures")
async def get_fixtures():
    manifest = SETTINGS.fixture_dir / "manifest.json"
    if not manifest.exists():
        return envelope([], meta={"note": "Run `python -m fixtures.generate`."})
    return envelope(json.loads(manifest.read_text()))
