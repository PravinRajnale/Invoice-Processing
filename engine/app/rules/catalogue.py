"""The rule catalogue — PRD 9.3.

56 rules across 7 gates; 50 active in MVP, 6 visibly deferred because they
require master data outside scope (bank account master, GRN capture, FX rate
table, GL/cost-centre master, budget master).

Deferred rules are *shown* in the Rule Configuration screen rather than hidden.
Scope discipline is more credible when it is visible.
"""

from __future__ import annotations

from typing import Dict, List

from ..models import Gate, RuleSpec, RuleType, Severity

B, C, W, I = Severity.BLOCKER, Severity.CRITICAL, Severity.WARNING, Severity.INFO
D, A = RuleType.DETERMINISTIC, RuleType.AI_ASSISTED

_MASTER_DATA_OUT_OF_SCOPE = "Designed — requires master data outside current scope"


def _r(rid, name, gate, sev, typ, mvp, requires, desc, threshold=None, deferred=None):
    return RuleSpec(
        id=rid, name=name, gate=gate, severity=sev, type=typ, mvp=mvp,
        requires=requires, description_ui=desc, threshold_ref=threshold,
        deferred_reason=deferred,
    )


CATALOGUE: List[RuleSpec] = [
    # ---------------------------------------------------------------- Gate 0
    _r("ING-01", "File is readable and not encrypted", Gate.INGEST, B, D, True,
       ["document.mime_type", "document.page_count"],
       "The uploaded file opens, is a supported type, and is not password protected."),
    _r("ING-02", "Document is an invoice", Gate.INGEST, B, A, True,
       ["document.classification"],
       "The document is a vendor invoice, not a purchase order, delivery note or statement."),
    _r("ING-03", "File hash not seen before", Gate.INGEST, B, D, True,
       ["document.sha256"],
       "This exact file has not been submitted before."),

    # ---------------------------------------------------------------- Gate 1
    _r("EXT-01", "Invoice number present", Gate.EXTRACTION, B, D, True,
       ["invoice.invoice_number"],
       "An invoice number was found on the document."),
    _r("EXT-02", "Invoice date present and parseable", Gate.EXTRACTION, B, D, True,
       ["invoice.invoice_date"],
       "The invoice date resolves to a single unambiguous calendar date."),
    _r("EXT-03", "Invoice date not in the future", Gate.EXTRACTION, C, D, True,
       ["invoice.invoice_date"],
       "The invoice is not dated after today."),
    _r("EXT-04", "Invoice date not unreasonably stale", Gate.EXTRACTION, W, D, True,
       ["invoice.invoice_date"],
       "The invoice is not older than the configured maximum age.",
       "staleness.max_invoice_age_days"),
    _r("EXT-05", "Vendor identity present", Gate.EXTRACTION, B, D, True,
       ["invoice.vendor_name|invoice.vendor_tax_id"],
       "Either a vendor name or a tax registration number was extracted."),
    _r("EXT-06", "Currency identified", Gate.EXTRACTION, C, D, True,
       ["invoice.currency"],
       "An ISO-4217 currency was resolved from the symbol, code, or vendor default."),
    _r("EXT-07", "Grand total present", Gate.EXTRACTION, B, D, True,
       ["invoice.grand_total"],
       "A grand total was found and parses to a decimal amount."),
    _r("EXT-08", "Subtotal and tax present", Gate.EXTRACTION, C, D, True,
       ["invoice.subtotal", "invoice.tax_amount"],
       "Both subtotal and tax were extracted; downstream financial checks depend on them."),
    _r("EXT-09", "At least one line item extracted", Gate.EXTRACTION, C, D, True,
       ["invoice.lines"],
       "The invoice has at least one line item. Bundled invoices produce one synthetic line."),
    _r("EXT-10", "PO reference present or inferable", Gate.EXTRACTION, C, A, True,
       ["invoice.po_number|invoice.po_inference"],
       "A purchase order reference was found on the document or inferred from vendor, "
       "amount and date.",
       "confidence.po_inference_floor"),
    _r("EXT-11", "Critical fields above confidence floor", Gate.EXTRACTION, C, D, True,
       ["confidence.invoice_number", "confidence.invoice_date",
        "confidence.grand_total", "confidence.vendor_name"],
       "Invoice number, date, grand total and vendor were all read with sufficient confidence.",
       "confidence.critical_field_floor"),
    _r("EXT-12", "Payment terms / due date extracted", Gate.EXTRACTION, I, D, True,
       ["invoice.due_date|vendor.payment_terms_days"],
       "Payment terms were read from the document or defaulted from the vendor master."),

    # ---------------------------------------------------------------- Gate 2
    _r("VEN-01", "Vendor resolved to Vendor Master", Gate.VENDOR, B, A, True,
       ["invoice.vendor_name|invoice.vendor_tax_id", "master.vendors"],
       "The invoice vendor was matched to a record in the vendor master.",
       "confidence.vendor_match_floor"),
    _r("VEN-02", "Vendor is Active and Approved", Gate.VENDOR, B, D, True,
       ["vendor.status", "vendor.approval_status"],
       "The vendor is active and has completed approval."),
    _r("VEN-03", "Vendor not blacklisted or suspended", Gate.VENDOR, B, D, True,
       ["vendor.status"],
       "The vendor is not blacklisted or suspended."),
    _r("VEN-04", "Tax ID matches Vendor Master", Gate.VENDOR, C, D, True,
       ["invoice.vendor_tax_id", "vendor.tax_id"],
       "The GSTIN on the invoice matches the one registered for this vendor. "
       "A mismatch is a fraud signal."),
    _r("VEN-05", "Vendor contract valid on invoice date", Gate.VENDOR, W, D, True,
       ["invoice.invoice_date", "vendor.contract_start", "vendor.contract_end"],
       "The invoice date falls inside the vendor's contract period."),
    _r("VEN-06", "Invoice currency supported for vendor", Gate.VENDOR, C, D, True,
       ["invoice.currency", "vendor.permitted_currencies"],
       "The invoice currency is one the vendor is permitted to bill in."),
    _r("VEN-07", "Bank details match registered account", Gate.VENDOR, C, D, False,
       ["invoice.bank_account", "vendor.bank_account_hash"],
       "Bank details on the invoice match the registered account. In a real deployment "
       "this is the single highest-value fraud control in the catalogue.",
       None, _MASTER_DATA_OUT_OF_SCOPE + " (bank account master)"),
    _r("VEN-08", "Payment terms match contract", Gate.VENDOR, W, D, False,
       ["invoice.payment_terms", "vendor.contract_payment_terms"],
       "Payment terms stated on the invoice match the contracted terms.",
       None, _MASTER_DATA_OUT_OF_SCOPE + " (contract repository)"),

    # ---------------------------------------------------------------- Gate 3
    _r("PO-01", "PO exists", Gate.PURCHASE_ORDER, B, D, True,
       ["invoice.po_number", "master.purchase_orders"],
       "The referenced purchase order exists in the procurement system."),
    _r("PO-02", "PO status permits invoicing", Gate.PURCHASE_ORDER, B, D, True,
       ["po.status"],
       "The PO is open or partially invoiced. A cancelled PO cannot be billed against."),
    _r("PO-03", "PO vendor equals invoice vendor", Gate.PURCHASE_ORDER, B, D, True,
       ["po.vendor_id", "invoice.vendor_id"],
       "The PO was raised on the same vendor that issued this invoice."),
    _r("PO-04", "PO currency equals invoice currency", Gate.PURCHASE_ORDER, B, D, True,
       ["po.currency", "invoice.currency"],
       "No implicit currency conversion is permitted between PO and invoice."),
    _r("PO-05", "PO date is on or before invoice date", Gate.PURCHASE_ORDER, W, D, True,
       ["po.po_date", "invoice.invoice_date"],
       "An invoice dated before its own purchase order is an anomaly."),
    _r("PO-06", "PO validity period not expired", Gate.PURCHASE_ORDER, C, D, True,
       ["po.valid_until", "invoice.invoice_date"],
       "The invoice date falls within the PO's validity window."),
    _r("PO-07", "Cumulative invoiced within PO value", Gate.PURCHASE_ORDER, C, D, True,
       ["invoice.grand_total", "po.total_amount", "po.remaining_balance"],
       "Total billed against this PO across all invoices — not just this one — stays "
       "within the PO value plus tolerance.",
       "tolerance.amount"),
    _r("PO-08", "Quantity invoiced within quantity received", Gate.PURCHASE_ORDER, C, D, False,
       ["invoice.lines", "grn.received_quantities"],
       "Three-way match: invoiced quantity does not exceed goods actually received.",
       "tolerance.quantity", _MASTER_DATA_OUT_OF_SCOPE + " (GRN capture)"),

    # ---------------------------------------------------------------- Gate 4
    _r("FIN-01", "Grand total arithmetic is internally consistent", Gate.FINANCIAL, C, D, True,
       ["invoice.subtotal", "invoice.tax_amount", "invoice.grand_total"],
       "Subtotal plus tax, less discount, plus other charges equals the grand total.",
       "tolerance.rounding_epsilon"),
    _r("FIN-02", "Line totals sum to subtotal", Gate.FINANCIAL, C, D, True,
       ["invoice.lines", "invoice.subtotal"],
       "The sum of all line totals equals the stated subtotal.",
       "tolerance.rounding_epsilon"),
    _r("FIN-03", "Tax computation correct", Gate.FINANCIAL, C, D, True,
       ["invoice.subtotal", "invoice.tax_amount"],
       "Tax equals subtotal times the applicable rate, and that rate is a permitted one.",
       "tolerance.tax_abs"),
    _r("FIN-04", "No negative or zero grand total", Gate.FINANCIAL, B, D, True,
       ["invoice.grand_total"],
       "The invoice total is positive. Credit notes must use a separate document type."),
    _r("FIN-05", "Invoice total within PO tolerance", Gate.FINANCIAL, C, D, True,
       ["invoice.grand_total", "po.total_amount"],
       "The invoice total is within the allowed variance of the purchase order value.",
       "tolerance.amount"),
    _r("FIN-06", "Decimal precision valid", Gate.FINANCIAL, W, D, True,
       ["invoice.grand_total", "invoice.currency"],
       "Amounts carry no more decimal places than the currency permits; more implies a misread."),
    _r("FIN-07", "FX conversion correct", Gate.FINANCIAL, C, D, False,
       ["invoice.currency", "po.currency", "fx.rate_table"],
       "Cross-currency amounts convert correctly at the booking-date rate.",
       None, _MASTER_DATA_OUT_OF_SCOPE + " (FX rate table)"),

    # ---------------------------------------------------------------- Gate 5
    _r("LIN-01", "Every invoice line maps to a PO line", Gate.LINE_ITEMS, C, A, True,
       ["invoice.lines", "po.lines"],
       "Each billed line was matched to a line on the purchase order.",
       "confidence.line_match_floor"),
    _r("LIN-02", "Cumulative quantity within PO quantity", Gate.LINE_ITEMS, C, D, True,
       ["invoice.lines", "po.lines", "po.line_consumption"],
       "Quantity billed per line across all invoices stays within the ordered quantity.",
       "tolerance.quantity_pct"),
    _r("LIN-03", "Unit price within PO unit price", Gate.LINE_ITEMS, C, D, True,
       ["invoice.lines", "po.lines"],
       "Each line's unit price is within tolerance of the contracted price. Evaluated per "
       "line even when the header total passes.",
       "tolerance.unit_price_pct"),
    _r("LIN-04", "Line total arithmetic correct", Gate.LINE_ITEMS, C, D, True,
       ["invoice.lines"],
       "Quantity times unit price, less line discount, equals the line total.",
       "tolerance.rounding_epsilon"),
    _r("LIN-05", "UOM consistent with PO", Gate.LINE_ITEMS, W, D, True,
       ["invoice.lines", "po.lines"],
       "Units of measure agree after normalisation. Ten boxes is not ten pieces."),
    _r("LIN-06", "No items absent from the PO", Gate.LINE_ITEMS, C, D, True,
       ["invoice.lines", "po.lines"],
       "Nothing was billed that does not appear on the purchase order."),
    _r("LIN-07", "No duplicate lines within the invoice", Gate.LINE_ITEMS, W, D, True,
       ["invoice.lines"],
       "The same item, quantity and price does not appear twice on this invoice."),
    _r("LIN-08", "All PO lines accounted for", Gate.LINE_ITEMS, I, D, True,
       ["invoice.lines", "po.lines", "po.allows_partial_invoicing"],
       "Informational unless the PO forbids partial invoicing."),

    # ---------------------------------------------------------------- Gate 6
    _r("DUP-01", "Vendor and invoice number not already processed", Gate.DUPLICATES, B, D, True,
       ["invoice.vendor_id", "invoice.invoice_number_normalised"],
       "This vendor has not previously submitted an invoice with this number."),
    _r("DUP-02", "No near-duplicate invoice number for vendor", Gate.DUPLICATES, C, A, True,
       ["invoice.vendor_id", "invoice.invoice_number_canonical"],
       "No prior invoice number differs only by confusable characters or separators.",
       "duplicate.fuzzy_number_max_distance"),
    _r("DUP-03", "No same-vendor, same-amount, near-date invoice", Gate.DUPLICATES, C, D, True,
       ["invoice.vendor_id", "invoice.grand_total", "invoice.invoice_date"],
       "No other invoice from this vendor has the same amount within the date window.",
       "duplicate.amount_date_window_days"),
    _r("DUP-04", "No duplicate against in-flight invoices", Gate.DUPLICATES, C, D, True,
       ["invoice.vendor_id", "invoice.invoice_number_normalised", "invoice.grand_total"],
       "Checks unapproved invoices still in the queue, not only approved history."),

    # ---------------------------------------------------------------- Gate 7
    _r("POL-01", "Determine approval tier", Gate.POLICY, I, D, True,
       ["invoice.grand_total"],
       "Routes the invoice to the correct approver under the delegation-of-authority matrix.",
       "approval.doa"),
    _r("POL-02", "Split invoicing permitted on this PO", Gate.POLICY, C, D, True,
       ["po.allows_partial_invoicing", "po.consumption"],
       "This PO permits being billed across multiple invoices."),
    _r("POL-03", "Auto-approval ceiling not exceeded", Gate.POLICY, I, D, True,
       ["invoice.grand_total"],
       "Above the ceiling, unattended approval is unavailable regardless of rule outcomes.",
       "approval.auto_approve_ceiling"),
    _r("POL-04", "Cost centre / GL account valid", Gate.POLICY, W, D, False,
       ["invoice.cost_center", "master.gl_accounts"],
       "The cost centre and GL account exist and are open for posting.",
       None, _MASTER_DATA_OUT_OF_SCOPE + " (GL / cost-centre master)"),
    _r("POL-05", "Budget available on cost centre", Gate.POLICY, C, D, False,
       ["invoice.cost_center", "master.budgets"],
       "Sufficient unspent budget remains on the cost centre.",
       None, _MASTER_DATA_OUT_OF_SCOPE + " (budget master)"),
    _r("POL-06", "Segregation of duties on override", Gate.POLICY, B, D, True,
       ["action.actor_id", "invoice.correction_history"],
       "Evaluated at override time, not at validation time: the person who corrected "
       "extraction cannot be the sole approver above the SoD threshold."),
]

BY_ID: Dict[str, RuleSpec] = {r.id: r for r in CATALOGUE}

# Rules that run during a validation pass. POL-06 is deliberately excluded — it
# is evaluated at override time against an actor, not against an invoice.
ACTIVE: List[RuleSpec] = [r for r in CATALOGUE if r.mvp and r.id != "POL-06"]
DEFERRED: List[RuleSpec] = [r for r in CATALOGUE if not r.mvp]

GATE_ORDER = [
    Gate.INGEST, Gate.EXTRACTION, Gate.VENDOR, Gate.PURCHASE_ORDER,
    Gate.FINANCIAL, Gate.LINE_ITEMS, Gate.DUPLICATES, Gate.POLICY,
]


def counts() -> Dict[str, int]:
    return {
        "total": len(CATALOGUE),
        "active": len([r for r in CATALOGUE if r.mvp]),
        "deferred": len(DEFERRED),
        "evaluated_per_run": len(ACTIVE),
    }


def by_gate() -> Dict[str, List[RuleSpec]]:
    out: Dict[str, List[RuleSpec]] = {g.value: [] for g in GATE_ORDER}
    for spec in CATALOGUE:
        out[spec.gate.value].append(spec)
    return out
