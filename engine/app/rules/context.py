"""Evaluation context — everything a rule is allowed to see.

A rule receives this object and nothing else. It never touches the raw document
text, never calls an LLM, and never performs I/O beyond reading master data
through the store. That constraint is what makes Edge Case 5 (prompt injection)
structurally impossible rather than merely filtered: no string in the document
can reach a comparison operator, because the comparison operators only ever see
typed, normalised values that came out of a schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..config import CONFIG, Config
from ..models import ExtractedField, MatchResult


@dataclass
class LineMatch:
    """One invoice line, resolved (or not) to a PO line."""

    invoice_line_no: int
    po_line_id: Optional[str]
    score: Decimal
    method: str
    note: str = ""


@dataclass
class RuleContext:
    invoice_id: str
    document: Dict[str, Any]
    invoice: Dict[str, Any]                       # normalised header; money as Decimal
    lines: List[Dict[str, Any]]                   # normalised invoice lines
    fields: Dict[str, ExtractedField]             # field_path -> provenance
    store: Any
    cfg: Config = CONFIG

    vendor: Optional[Dict[str, Any]] = None
    vendor_match: Optional[MatchResult] = None
    po: Optional[Dict[str, Any]] = None
    po_match: Optional[MatchResult] = None
    po_lines: List[Dict[str, Any]] = field(default_factory=list)
    line_matches: List[LineMatch] = field(default_factory=list)

    security_flags: List[Dict[str, Any]] = field(default_factory=list)
    # Set when this invoice appears to duplicate an earlier one whose claim is
    # already in the ledger. Ledger-based rules stand down rather than reporting
    # an over-consumption that the duplication itself created.
    duplicate_of: Optional[Dict[str, Any]] = None
    today: date = field(default_factory=date.today)

    # Populated by _build_availability()
    _available: Dict[str, str] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self._build_availability()

    def _mark(self, token: str, present: bool, reason: str = "") -> None:
        """Record availability of a `requires` token. Empty string == available."""
        self._available[token] = "" if present else (reason or "not extracted")

    def _build_availability(self) -> None:
        """Compute, once, which `requires` tokens are satisfiable.

        A token is unavailable either because the value is absent, or because it
        was read below its confidence floor. Both are legitimate reasons to
        report CANNOT_EVALUATE — a value read at 0.58 confidence is not a value
        you may put into a payment decision.
        """
        inv, doc, cfg = self.invoice, self.document, self.cfg
        floor = cfg.confidence.critical_field_floor

        def conf_ok(path: str) -> bool:
            f = self.fields.get(path)
            return f is not None and Decimal(f.confidence) >= floor

        def present(key: str) -> bool:
            v = inv.get(key)
            return v is not None and v != "" and v != []

        # --- document
        self._mark("document.mime_type", bool(doc.get("mime_type")))
        self._mark("document.page_count", doc.get("page_count") is not None)
        self._mark("document.sha256", bool(doc.get("sha256")))
        self._mark("document.classification", doc.get("classification") is not None,
                   "document classification unavailable")

        # --- invoice header
        for key in ("invoice_number", "invoice_date", "currency", "grand_total",
                    "subtotal", "tax_amount", "vendor_name", "vendor_tax_id",
                    "po_number", "due_date", "cost_center", "bank_account",
                    "payment_terms", "invoice_number_normalised",
                    "invoice_number_canonical"):
            self._mark(f"invoice.{key}", present(key))

        self._mark("invoice.lines", bool(self.lines))
        self._mark("invoice.vendor_id", self.vendor is not None,
                   "vendor not resolved to master")
        self._mark("invoice.po_inference",
                   self.po_match is not None and self.po_match.matched_id is not None,
                   "no PO reference and none inferable")
        self._mark("invoice.correction_history", True)

        # --- per-field confidence gates: these are what cascade in Edge Case 2
        for key in ("invoice_number", "invoice_date", "grand_total", "vendor_name"):
            ok = conf_ok(f"header.{key}")
            f = self.fields.get(f"header.{key}")
            reason = (
                f"confidence {Decimal(f.confidence):.2f} < {floor}"
                if f is not None else "not extracted"
            )
            self._mark(f"confidence.{key}", ok, reason)

        # A grand total below the confidence floor must not enter arithmetic at
        # all — this is what turns EXT-11 into a cascade rather than a lone flag.
        if not conf_ok("header.grand_total"):
            f = self.fields.get("header.grand_total")
            reason = (f"confidence {Decimal(f.confidence):.2f} < {floor}"
                      if f else "not extracted")
            self._mark("invoice.grand_total", False, reason)
        for key in ("subtotal", "tax_amount"):
            fld = self.fields.get(f"header.{key}")
            if fld is not None and Decimal(fld.confidence) < floor:
                self._mark(f"invoice.{key}", False,
                           f"confidence {Decimal(fld.confidence):.2f} < {floor}")

        # --- vendor master
        self._mark("master.vendors", True)
        for key in ("status", "approval_status", "tax_id", "contract_start",
                    "contract_end", "permitted_currencies", "payment_terms_days"):
            self._mark(f"vendor.{key}",
                       self.vendor is not None and self.vendor.get(key) is not None,
                       "vendor not resolved to master")

        # --- purchase order
        self._mark("master.purchase_orders", True)
        po_ok = self.po is not None
        for key in ("status", "vendor_id", "currency", "po_date", "valid_until",
                    "total_amount", "allows_partial_invoicing"):
            self._mark(f"po.{key}", po_ok and self.po.get(key) is not None,
                       "purchase order not resolved")
        self._mark("po.lines", po_ok and bool(self.po_lines), "purchase order not resolved")
        self._mark("po.remaining_balance", po_ok, "purchase order not resolved")
        self._mark("po.consumption", po_ok, "purchase order not resolved")
        self._mark("po.line_consumption", po_ok and bool(self.po_lines),
                   "purchase order not resolved")

        # --- deferred-rule inputs: absent by design, and labelled as such
        for token, why in (
            ("vendor.bank_account_hash", "bank account master not in scope"),
            ("vendor.contract_payment_terms", "contract repository not in scope"),
            ("grn.received_quantities", "goods receipt capture not in scope"),
            ("fx.rate_table", "FX rate table not in scope"),
            ("master.gl_accounts", "GL / cost-centre master not in scope"),
            ("master.budgets", "budget master not in scope"),
        ):
            self._mark(token, False, why)

        self._mark("action.actor_id", True)
        self._mark("approval.doa", True)

    # ------------------------------------------------------------------
    def missing(self, requires: List[str]) -> List[str]:
        """Which required inputs are unavailable.

        ``a|b`` is satisfied when either alternative is available — used where
        a rule can work from one of two sources (vendor name *or* tax ID).
        Returns human-readable "token (reason)" strings for the UI.
        """
        blocked: List[str] = []
        for token in requires:
            if "|" in token:
                alts = token.split("|")
                if any(self._available.get(a, "unknown input") == "" for a in alts):
                    continue
                reasons = "; ".join(
                    f"{a}: {self._available.get(a, 'unknown input')}" for a in alts
                )
                blocked.append(f"{token} ({reasons})")
            else:
                reason = self._available.get(token, "unknown input")
                if reason:
                    blocked.append(f"{token} ({reason})")
        return blocked

    # -- convenience accessors ----------------------------------------
    def amount(self, key: str) -> Optional[Decimal]:
        v = self.invoice.get(key)
        return v if isinstance(v, Decimal) else None

    @property
    def currency(self) -> str:
        """Currency for formatting and rounding.

        Falls back to INR only for *display*; every rule that depends on the
        currency declares ``invoice.currency`` in its ``requires`` and reports
        CANNOT_EVALUATE when it is unknown, so no comparison is ever made in an
        assumed currency.
        """
        return self.invoice.get("currency") or "INR"

    def po_line(self, po_line_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not po_line_id:
            return None
        return next((l for l in self.po_lines if l["id"] == po_line_id), None)

    def match_for(self, line_no: int) -> Optional[LineMatch]:
        return next((m for m in self.line_matches if m.invoice_line_no == line_no), None)

    def field_evidence(self, *paths: str) -> Dict[str, Any]:
        """Evidence block linking a rule result back to document regions, so the
        UI can deep-link from a failed rule to the pixels it came from."""
        out = {}
        for p in paths:
            f = self.fields.get(p)
            if f:
                out[p] = {
                    "value": f.normalised_value,
                    "raw": f.raw_value,
                    "confidence": str(f.confidence),
                    "page": f.page_number,
                    "bbox": f.bbox,
                    "method": f.extraction_method,
                }
        return out

    def effective_amount_tolerance(self, base: Decimal) -> tuple[Decimal, str]:
        """Amount tolerance for a comparison against ``base``.

        ``max(percentage, absolute)`` — the absolute figure is a *floor* for
        small invoices, not a cap on large ones. A ₹1,000 invoice gets ₹500 of
        room rather than ₹20; a ₹10,00,000 invoice gets 2% rather than being
        pinned to ₹500.

        Note: PRD 9.4 annotates ``amount_abs`` with "whichever is LOWER
        applies", which contradicts FIN-05's own stated formula
        ``abs(inv − po_scope) <= max(2%, ₹500)`` and would make Edge Case 4
        impossible — its ₹800 variance on a ₹4,85,000 PO (0.16%) must pass
        FIN-05 for the offsetting-line trap to exist at all. The ``max``
        reading is implemented as the one both FIN-05 and Edge Case 4 require.
        """
        pct = self.cfg.tolerance.amount_pct
        abs_floor = self.cfg.tolerance.amount_abs
        source = "global"
        if self.po:
            if self.po.get("amount_tolerance_pct") is not None:
                pct = Decimal(str(self.po["amount_tolerance_pct"]))
                source = "per-PO"
            if self.po.get("amount_tolerance_abs") is not None:
                abs_floor = Decimal(str(self.po["amount_tolerance_abs"]))
                source = "per-PO"
        pct_value = (base * pct / Decimal(100))
        allowed = max(pct_value, abs_floor)
        label = f"max({pct}% = {pct_value:.2f}, {abs_floor}) [{source}]"
        return allowed, label
