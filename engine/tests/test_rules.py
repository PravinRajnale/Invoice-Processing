"""Per-rule unit tests: pass, fail, boundary, and missing-input.

The missing-input cases matter most. Each asserts that a rule deprived of a
required input reports CANNOT_EVALUATE with the blocking input named — not FAIL,
and not a silent pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.config import CONFIG
from app.models import ExtractedField, MatchResult, Outcome
from app.rules.catalogue import BY_ID
from app.rules.context import LineMatch, RuleContext
from app.rules.engine import evaluate_one
from app.store import Store

TODAY = date(2026, 7, 31)


@pytest.fixture
def store(tmp_path):
    return Store(data_dir=tmp_path / "data")


def field(path: str, value: str, confidence: str = "0.96") -> ExtractedField:
    return ExtractedField(
        field_path=path, raw_value=value, normalised_value=value,
        confidence=Decimal(confidence), page_number=1,
        bbox={"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.02},
    )


def make_ctx(store, **overrides) -> RuleContext:
    """A clean, fully-passing context. Tests perturb one thing at a time."""
    vendor = store.get("vendors", "V-1004")
    po = store.get("purchase_orders", "PO-7723")
    po_lines = sorted(store.find("po_lines", po_id="PO-7723"),
                      key=lambda l: l["line_no"])

    invoice = {
        "invoice_number": "NOS/26-27/0412",
        "invoice_number_normalised": "NOS26270412",
        "invoice_number_canonical": "N0526270412",
        "invoice_date": date(2026, 7, 12),
        "invoice_date_ambiguous": False,
        "due_date": date(2026, 7, 27),
        "currency": "INR",
        "currency_source": "document",
        "vendor_name": "Nimbus Office Solutions",
        "vendor_tax_id": "07AAEFN9012M1ZQ",
        "vendor_id": "V-1004",
        "po_number": "PO-7723",
        "po_number_normalised": "PO7723",
        "subtotal": Decimal("38135.59"),
        "tax_amount": Decimal("6864.41"),
        "grand_total": Decimal("45000.00"),
    }
    lines = [
        {"line_no": 1, "sku": "SKU-1101", "description": "A4 Copier Paper 75gsm",
         "quantity": Decimal("300"), "uom": "REAM",
         "unit_price": Decimal("112.00"), "line_total": Decimal("33600.00"),
         "tax_rate_pct": Decimal("18"), "line_discount": Decimal("0")},
        {"line_no": 2, "sku": "SKU-1102", "description": "Whiteboard marker",
         "quantity": Decimal("110"), "uom": "PACK",
         "unit_price": Decimal("38.00"), "line_total": Decimal("4180.00"),
         "tax_rate_pct": Decimal("18"), "line_discount": Decimal("0")},
        {"line_no": 3, "sku": "SVC-DEL", "description": "Delivery charges",
         "quantity": Decimal("1"), "uom": "LOT",
         "unit_price": Decimal("355.59"), "line_total": Decimal("355.59"),
         "tax_rate_pct": Decimal("18"), "line_discount": Decimal("0")},
    ]
    fields = {
        "header.invoice_number": field("header.invoice_number", "NOS/26-27/0412"),
        "header.invoice_date": field("header.invoice_date", "12/07/2026"),
        "header.grand_total": field("header.grand_total", "45,000.00"),
        "header.vendor_name": field("header.vendor_name", "Nimbus Office Solutions"),
        "header.subtotal": field("header.subtotal", "38,135.59"),
        "header.tax_amount": field("header.tax_amount", "6,864.41"),
    }

    invoice.update(overrides.pop("invoice", {}))
    ctx = RuleContext(
        invoice_id=overrides.pop("invoice_id", "INV-TEST"),
        document=overrides.pop("document", {
            "id": "DOC-TEST", "sha256": "a" * 64, "mime_type": "application/pdf",
            "page_count": 1, "encrypted": False, "corrupt": False,
            "classification": {"label": "INVOICE", "confidence": 0.98},
        }),
        invoice=invoice,
        lines=overrides.pop("lines", lines),
        fields={**fields, **overrides.pop("fields", {})},
        store=store,
        cfg=CONFIG,
        vendor=overrides.pop("vendor", vendor),
        vendor_match=overrides.pop("vendor_match",
                                   MatchResult("V-1004", Decimal("1"), "TAX_ID_EXACT")),
        po=overrides.pop("po", po),
        po_match=overrides.pop("po_match",
                               MatchResult("PO-7723", Decimal("1"), "PO_NUMBER_EXACT")),
        po_lines=overrides.pop("po_lines", po_lines),
        line_matches=overrides.pop("line_matches", [
            LineMatch(1, "POL-7723-1", Decimal("1"), "SKU_EXACT"),
            LineMatch(2, "POL-7723-2", Decimal("1"), "SKU_EXACT"),
            LineMatch(3, "POL-7723-3", Decimal("1"), "SKU_EXACT"),
        ]),
        today=TODAY,
        **overrides,
    )
    return ctx


def run(rule_id: str, ctx: RuleContext):
    return evaluate_one(BY_ID[rule_id], ctx)


# ----------------------------------------------------------------------
# Baseline: the clean context passes everything it should
# ----------------------------------------------------------------------
@pytest.mark.parametrize("rule_id", [
    "ING-01", "ING-02", "ING-03", "EXT-01", "EXT-02", "EXT-03", "EXT-04",
    "EXT-05", "EXT-06", "EXT-07", "EXT-08", "EXT-09", "EXT-10", "EXT-11",
    "EXT-12", "VEN-01", "VEN-02", "VEN-03", "VEN-04", "VEN-05", "VEN-06",
    "PO-01", "PO-02", "PO-03", "PO-04", "PO-05", "PO-06", "PO-07",
    "FIN-01", "FIN-02", "FIN-03", "FIN-04", "FIN-05", "FIN-06",
    "LIN-01", "LIN-02", "LIN-03", "LIN-04", "LIN-05", "LIN-06", "LIN-07", "LIN-08",
    "DUP-01", "DUP-02", "DUP-03", "DUP-04", "POL-01", "POL-02", "POL-03",
])
def test_clean_invoice_passes_every_rule(store, rule_id):
    result = run(rule_id, make_ctx(store))
    assert result.outcome == Outcome.PASS, f"{rule_id}: {result.message}"


# ----------------------------------------------------------------------
# CANNOT_EVALUATE — the state that must never collapse into FAIL
# ----------------------------------------------------------------------
def test_low_confidence_grand_total_blocks_dependent_rules(store):
    """Edge Case 2 in miniature: one field below the floor must make the rules
    that depend on it unevaluable, and leave the rest untouched."""
    ctx = make_ctx(store, fields={
        "header.grand_total": field("header.grand_total", "1,84,500.00", "0.58"),
    })

    for rule_id in ("EXT-07", "EXT-11", "FIN-01", "FIN-05", "PO-07", "POL-01"):
        result = run(rule_id, ctx)
        assert result.outcome == Outcome.CANNOT_EVALUATE, \
            f"{rule_id} should be unevaluable, got {result.outcome}"
        assert result.blocked_by, f"{rule_id} must name what blocked it"
        assert any("grand_total" in b for b in result.blocked_by)
        assert "0.58" in " ".join(result.blocked_by)

    # Independent checks still run and still report normally.
    for rule_id in ("EXT-01", "EXT-02", "VEN-01", "VEN-02", "PO-01", "PO-02"):
        assert run(rule_id, ctx).outcome == Outcome.PASS


def test_unresolved_vendor_blocks_vendor_rules_without_failing_them(store):
    ctx = make_ctx(store, vendor=None,
                   vendor_match=MatchResult(None, Decimal("0"), "NO_CANDIDATE"))
    for rule_id in ("VEN-02", "VEN-03", "VEN-04", "VEN-05", "VEN-06"):
        result = run(rule_id, ctx)
        assert result.outcome == Outcome.CANNOT_EVALUATE
        assert any("vendor" in b for b in result.blocked_by)


def test_missing_po_blocks_po_rules(store):
    ctx = make_ctx(store, po=None, po_lines=[],
                   po_match=MatchResult(None, Decimal("0"), "NOT_FOUND"))
    for rule_id in ("PO-02", "PO-03", "PO-04", "PO-05", "PO-06", "PO-07"):
        result = run(rule_id, ctx)
        assert result.outcome == Outcome.CANNOT_EVALUATE


def test_ambiguous_date_is_unevaluable_not_wrong(store):
    ctx = make_ctx(store, invoice={"invoice_date_ambiguous": True,
                                   "invoice_date_raw": "04/06/2026"})
    result = run("EXT-02", ctx)
    assert result.outcome == Outcome.CANNOT_EVALUATE
    assert "04/06/2026" in result.blocked_by[0]


def test_deferred_rules_report_their_missing_master_data(store):
    """The 6 deferred rules must say *why* they did not run, so the Rule
    Configuration screen can show scope discipline rather than a silent gap."""
    ctx = make_ctx(store)
    for rule_id in ("VEN-07", "VEN-08", "PO-08", "FIN-07", "POL-04", "POL-05"):
        result = run(rule_id, ctx)
        assert result.outcome == Outcome.CANNOT_EVALUATE
        assert any("not in scope" in b for b in result.blocked_by), result.blocked_by


# ----------------------------------------------------------------------
# Financial arithmetic
# ----------------------------------------------------------------------
def test_fin01_detects_inconsistent_total(store):
    ctx = make_ctx(store, invoice={"grand_total": Decimal("46000.00")})
    result = run("FIN-01", ctx)
    assert result.outcome == Outcome.FAIL
    assert result.delta == "₹1,000.00"


def test_fin01_tolerates_rounding_noise(store):
    """Two paise of drift is rounding, not an error."""
    ctx = make_ctx(store, invoice={"grand_total": Decimal("45000.02")})
    assert run("FIN-01", ctx).outcome == Outcome.PASS

    ctx = make_ctx(store, invoice={"grand_total": Decimal("45000.03")})
    assert run("FIN-01", ctx).outcome == Outcome.FAIL


def test_fin03_rejects_a_rate_that_is_not_a_real_gst_rate(store):
    ctx = make_ctx(store, invoice={
        "tax_amount": Decimal("5720.34"), "grand_total": Decimal("43855.93"),
    })
    result = run("FIN-03", ctx)
    assert result.outcome == Outcome.FAIL
    assert "15.0" in result.evidence["implied_rate_pct"]


def test_fin04_rejects_zero_and_negative_totals(store):
    for amount in (Decimal("0.00"), Decimal("-100.00")):
        ctx = make_ctx(store, invoice={"grand_total": amount})
        assert run("FIN-04", ctx).outcome == Outcome.FAIL


def test_fin05_boundary_at_exactly_the_tolerance(store):
    """PO-7723 totals 45,000. Tolerance is max(2% = 900, 500) = 900."""
    for amount, expected in (
        (Decimal("45900.00"), Outcome.PASS),    # exactly 2.00%
        (Decimal("45899.99"), Outcome.PASS),    # 1.99%
        (Decimal("45900.01"), Outcome.FAIL),    # 2.01%
    ):
        ctx = make_ctx(store, invoice={"grand_total": amount})
        assert run("FIN-05", ctx).outcome == expected, amount


def test_fin06_flags_impossible_precision(store):
    ctx = make_ctx(store, invoice={"grand_total": Decimal("45000.12345")})
    assert run("FIN-06", ctx).outcome == Outcome.WARN


# ----------------------------------------------------------------------
# Line items — Edge Case 4
# ----------------------------------------------------------------------
def test_lin03_catches_a_line_overcharge_that_the_header_hides(store):
    """The core of Edge Case 4: header inside tolerance, line outside it."""
    po = store.get("purchase_orders", "PO-3417")
    po_lines = sorted(store.find("po_lines", po_id="PO-3417"),
                      key=lambda l: l["line_no"])
    lines = [
        {"line_no": 1, "sku": "SKU-4471", "description": "Bearing assembly",
         "quantity": Decimal("200"), "uom": "EA", "unit_price": Decimal("1566.00"),
         "line_total": Decimal("313200.00"), "tax_rate_pct": Decimal("0"),
         "line_discount": Decimal("0")},
        {"line_no": 2, "sku": "SKU-2210", "description": "Mounting bracket",
         "quantity": Decimal("500"), "uom": "EA", "unit_price": Decimal("342.00"),
         "line_total": Decimal("171000.00"), "tax_rate_pct": Decimal("0"),
         "line_discount": Decimal("0")},
    ]
    ctx = make_ctx(
        store, po=po, po_lines=po_lines, lines=lines,
        po_match=MatchResult("PO-3417", Decimal("1"), "PO_NUMBER_EXACT"),
        line_matches=[LineMatch(1, "POL-3417-1", Decimal("1"), "SKU_EXACT"),
                      LineMatch(2, "POL-3417-2", Decimal("1"), "SKU_EXACT")],
        invoice={"subtotal": Decimal("484200.00"), "tax_amount": Decimal("0.00"),
                 "grand_total": Decimal("484200.00"), "po_number": "PO-3417"},
    )

    # Header passes — correctly. The variance really is inside tolerance.
    assert run("FIN-05", ctx).outcome == Outcome.PASS

    # The line does not, and a header pass must never suppress it.
    result = run("LIN-03", ctx)
    assert result.outcome == Outcome.FAIL
    assert result.delta_pct == "8.00"
    assert result.evidence["total_overcharge"] == "23200.00"


def test_lin03_boundary_at_exactly_two_percent(store):
    lines = [dict(l) for l in make_ctx(store).lines]
    lines[0]["unit_price"] = Decimal("114.24")     # exactly +2.00%
    lines[0]["line_total"] = Decimal("34272.00")
    ctx = make_ctx(store, lines=lines)
    assert run("LIN-03", ctx).outcome == Outcome.PASS

    lines[0]["unit_price"] = Decimal("114.25")     # +2.01%
    ctx = make_ctx(store, lines=lines)
    assert run("LIN-03", ctx).outcome == Outcome.FAIL


def test_lin05_normalises_units_before_comparing(store):
    lines = [dict(l) for l in make_ctx(store).lines]
    lines[0]["uom"] = "REAMS"     # plural of the PO's REAM
    assert run("LIN-05", make_ctx(store, lines=lines)).outcome == Outcome.PASS

    lines[0]["uom"] = "BOX"       # genuinely different unit
    result = run("LIN-05", make_ctx(store, lines=lines))
    assert result.outcome == Outcome.WARN


def test_lin06_flags_items_absent_from_the_po(store):
    lines = [dict(l) for l in make_ctx(store).lines]
    lines.append({"line_no": 4, "sku": "SKU-9999",
                  "description": "Executive desk organiser",
                  "quantity": Decimal("5"), "uom": "EA",
                  "unit_price": Decimal("800.00"), "line_total": Decimal("4000.00"),
                  "tax_rate_pct": Decimal("18"), "line_discount": Decimal("0")})
    matches = list(make_ctx(store).line_matches) + [
        LineMatch(4, None, Decimal("0.2"), "UNMATCHED")
    ]
    result = run("LIN-06", make_ctx(store, lines=lines, line_matches=matches))
    assert result.outcome == Outcome.FAIL
    assert result.evidence["value"] == "4000.00"


# ----------------------------------------------------------------------
# Vendor
# ----------------------------------------------------------------------
def test_ven03_hard_stops_a_blacklisted_vendor(store):
    ctx = make_ctx(store, vendor=store.get("vendors", "V-1008"))
    assert run("VEN-03", ctx).outcome == Outcome.FAIL
    assert run("VEN-02", ctx).outcome == Outcome.FAIL


def test_ven04_flags_a_tax_id_mismatch(store):
    ctx = make_ctx(store, invoice={"vendor_tax_id": "07AAEFN9012M1ZZ"})
    result = run("VEN-04", ctx)
    assert result.outcome == Outcome.FAIL


def test_ven06_rejects_an_unpermitted_currency(store):
    ctx = make_ctx(store, invoice={"currency": "USD"})
    assert run("VEN-06", ctx).outcome == Outcome.FAIL


# ----------------------------------------------------------------------
# Purchase order
# ----------------------------------------------------------------------
def test_po02_blocks_a_cancelled_po(store):
    ctx = make_ctx(store, po=store.get("purchase_orders", "PO-8890"),
                   po_lines=store.find("po_lines", po_id="PO-8890"))
    assert run("PO-02", ctx).outcome == Outcome.FAIL


def test_po03_catches_a_po_raised_on_a_different_vendor(store):
    ctx = make_ctx(store, po=store.get("purchase_orders", "PO-2291"),
                   po_lines=store.find("po_lines", po_id="PO-2291"))
    result = run("PO-03", ctx)
    assert result.outcome == Outcome.FAIL


def test_po06_catches_an_expired_po(store):
    ctx = make_ctx(store, po=store.get("purchase_orders", "PO-8890"),
                   po_lines=store.find("po_lines", po_id="PO-8890"))
    result = run("PO-06", ctx)
    assert result.outcome == Outcome.FAIL
    # PO-8890 expired 2026-05-31; the invoice is dated 2026-07-12.
    assert result.delta == "42 days"


# ----------------------------------------------------------------------
# PO-07 and the consumption ledger — Edge Case 1
# ----------------------------------------------------------------------
def test_po07_counts_prior_invoices_not_just_this_one(store):
    po = store.get("purchase_orders", "PO-2291")
    store.insert("invoices", {"id": "INV-PRIOR-1", "po_id": "PO-2291",
                              "invoice_number": "INV-A/8801",
                              "grand_total": "420954.38", "status": "APPROVED"})
    store.reserve("PO-2291", "INV-PRIOR-1", Decimal("420954.38"))
    store.settle("INV-PRIOR-1", "COMMITTED")

    ctx = make_ctx(store, po=po, po_lines=store.find("po_lines", po_id="PO-2291"),
                   vendor=store.get("vendors", "V-1001"),
                   invoice={"grand_total": Decimal("400000.00")})

    result = run("PO-07", ctx)
    assert result.outcome == Outcome.PASS
    assert result.evidence["prior_consumed"] == "420954.38"
    assert result.evidence["cumulative"] == "820954.38"


def test_po07_fails_only_on_the_cumulative_total(store):
    """The invoice under test is well-formed in isolation. It is wrong only
    relative to what has already been billed."""
    po = store.get("purchase_orders", "PO-2291")
    for idx, amount in enumerate(["420954.38", "391170.00"], start=1):
        store.insert("invoices", {"id": f"INV-PRIOR-{idx}", "po_id": "PO-2291",
                                  "invoice_number": f"INV-A/880{idx}",
                                  "grand_total": amount, "status": "APPROVED"})
        store.reserve("PO-2291", f"INV-PRIOR-{idx}", Decimal(amount))
        store.settle(f"INV-PRIOR-{idx}", "COMMITTED")

    ctx = make_ctx(store, po=po, po_lines=store.find("po_lines", po_id="PO-2291"),
                   vendor=store.get("vendors", "V-1001"),
                   invoice={"grand_total": Decimal("241496.00")})

    result = run("PO-07", ctx)
    assert result.outcome == Outcome.FAIL
    assert result.evidence["consumed_pct"] == "105.36"
    assert len(result.evidence["prior_invoices"]) == 2


def test_po07_boundary_at_exactly_one_hundred_percent(store):
    po = store.get("purchase_orders", "PO-2291")
    store.insert("invoices", {"id": "INV-PRIOR-1", "po_id": "PO-2291",
                              "grand_total": "900000.00", "status": "APPROVED"})
    store.reserve("PO-2291", "INV-PRIOR-1", Decimal("900000.00"))
    store.settle("INV-PRIOR-1", "COMMITTED")

    base = dict(po=po, po_lines=store.find("po_lines", po_id="PO-2291"),
                vendor=store.get("vendors", "V-1001"))
    # Tolerance is max(2% of 1,000,000 = 20,000, 500) = 20,000.
    for amount, expected in (("100000.00", Outcome.PASS),   # exactly 100%
                             ("120000.00", Outcome.PASS),   # exactly at tolerance
                             ("120000.01", Outcome.FAIL)):
        ctx = make_ctx(store, invoice={"grand_total": Decimal(amount)}, **base)
        assert run("PO-07", ctx).outcome == expected, amount


def test_released_reservations_give_headroom_back(store):
    """A rejected or duplicate-blocked invoice must not keep consuming the PO."""
    store.insert("invoices", {"id": "INV-REJECTED", "po_id": "PO-2291",
                              "grand_total": "900000.00", "status": "REJECTED"})
    store.reserve("PO-2291", "INV-REJECTED", Decimal("900000.00"))
    assert store.po_consumed("PO-2291") == Decimal("900000.00")

    store.settle("INV-REJECTED", "RELEASED")
    assert store.po_consumed("PO-2291") == Decimal("0")


# ----------------------------------------------------------------------
# Duplicates — Edge Case 3
# ----------------------------------------------------------------------
def _seed_original(store):
    store.insert("invoices", {
        "id": "INV-ORIGINAL", "vendor_id": "V-1004",
        "invoice_number": "INV-2024-0871",
        "invoice_number_normalised": "INV20240871",
        "invoice_number_canonical": "1NV20240871",
        "invoice_date": "2026-07-12", "grand_total": "45000.00",
        "status": "APPROVED",
    })


def test_dup01_catches_the_same_number_written_differently(store):
    _seed_original(store)
    ctx = make_ctx(store, invoice={
        "invoice_number": "inv/2024/0871",
        "invoice_number_normalised": "INV20240871",
        "invoice_number_canonical": "1NV20240871",
    })
    assert run("DUP-01", ctx).outcome == Outcome.FAIL


def test_dup01_is_defeated_by_a_confusable_character(store):
    """DUP-01 is deliberately literal. The O/0 swap is DUP-02's job, and
    keeping them distinct is what lets the UI explain the difference."""
    _seed_original(store)
    ctx = make_ctx(store, invoice={
        "invoice_number": "INV-2024-O871",
        "invoice_number_normalised": "INV2024O871",
        "invoice_number_canonical": "1NV20240871",
    })
    assert run("DUP-01", ctx).outcome == Outcome.PASS


def test_dup02_catches_what_dup01_missed(store):
    _seed_original(store)
    ctx = make_ctx(store, invoice={
        "invoice_number": "INV-2024-O871",
        "invoice_number_normalised": "INV2024O871",
        "invoice_number_canonical": "1NV20240871",
    })
    result = run("DUP-02", ctx)
    assert result.outcome == Outcome.FAIL
    assert result.evidence["near_duplicates"][0]["distance"] == 0


def test_dup02_does_not_flag_sequential_numbering(store):
    """INV-A/8801 then INV-A/8847 is the next invoice, not a duplicate. Flagging
    it would blow the false-exception budget."""
    store.insert("invoices", {
        "id": "INV-SEQ", "vendor_id": "V-1004", "invoice_number": "INV-A/8801",
        "invoice_number_normalised": "INVA8801",
        "invoice_number_canonical": "1NVA8801",
        "invoice_date": "2026-06-04", "grand_total": "420954.38",
        "status": "APPROVED",
    })
    ctx = make_ctx(store, invoice={
        "invoice_number": "INV-A/8847",
        "invoice_number_normalised": "INVA8847",
        "invoice_number_canonical": "1NVA8847",
        "grand_total": Decimal("391170.00"),
    })
    assert run("DUP-02", ctx).outcome == Outcome.PASS


def test_dup02_flags_an_adjacent_number_at_the_same_amount(store):
    """Two independent signals agreeing is what turns a near miss into a hit."""
    store.insert("invoices", {
        "id": "INV-SEQ", "vendor_id": "V-1004", "invoice_number": "INV-A/8801",
        "invoice_number_normalised": "INVA8801",
        "invoice_number_canonical": "1NVA8801",
        "invoice_date": "2026-07-12", "grand_total": "45000.00",
        "status": "APPROVED",
    })
    ctx = make_ctx(store, invoice={
        "invoice_number": "INV-A/8802",
        "invoice_number_normalised": "INVA8802",
        "invoice_number_canonical": "1NVA8802",
    })
    assert run("DUP-02", ctx).outcome == Outcome.FAIL


def test_dup03_corroborates_on_vendor_amount_and_date(store):
    _seed_original(store)
    ctx = make_ctx(store, invoice={
        "invoice_number": "COMPLETELY-DIFFERENT",
        "invoice_number_normalised": "COMPLETELYDIFFERENT",
        "invoice_number_canonical": "C0MP1ETE1YD1FFERENT",
    })
    result = run("DUP-03", ctx)
    assert result.outcome == Outcome.FAIL
    assert result.evidence["matches"][0]["days_apart"] == 0


def test_dup04_searches_invoices_still_in_the_queue(store):
    """A history-only check misses two copies arriving before either is
    approved."""
    store.insert("invoices", {
        "id": "INV-INFLIGHT", "vendor_id": "V-1004",
        "invoice_number": "NOS/26-27/0412",
        "invoice_number_normalised": "NOS26270412",
        "invoice_number_canonical": "N0526270412",
        "invoice_date": "2026-07-12", "grand_total": "45000.00",
        "status": "PENDING_REVIEW",
    })
    assert run("DUP-04", make_ctx(store)).outcome == Outcome.FAIL
    # DUP-01 only looks at settled history, so it sees nothing.
    assert run("DUP-01", make_ctx(store)).outcome == Outcome.PASS


# ----------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------
def test_pol01_routes_by_amount(store):
    for amount, tier in ((Decimal("45000"), "AP_PROCESSOR"),
                         (Decimal("450000"), "AP_MANAGER"),
                         (Decimal("4500000"), "CONTROLLER")):
        ctx = make_ctx(store, invoice={"grand_total": amount})
        assert run("POL-01", ctx).evidence["routed_to_role"] == tier


def test_pol02_blocks_a_second_invoice_on_a_single_invoice_po(store):
    po = store.get("purchase_orders", "PO-9001")
    store.insert("invoices", {"id": "INV-FIRST", "po_id": "PO-9001",
                              "grand_total": "120000.00", "status": "APPROVED"})
    store.reserve("PO-9001", "INV-FIRST", Decimal("120000.00"))
    store.settle("INV-FIRST", "COMMITTED")

    ctx = make_ctx(store, po=po, po_lines=store.find("po_lines", po_id="PO-9001"),
                   invoice={"grand_total": Decimal("120000.00")})
    assert run("POL-02", ctx).outcome == Outcome.FAIL


def test_pol03_reports_when_unattended_approval_is_unavailable(store):
    ctx = make_ctx(store, invoice={"grand_total": Decimal("60000.00")})
    result = run("POL-03", ctx)
    assert result.outcome == Outcome.PASS      # informational, never blocking
    assert result.evidence["auto_approve_available"] is False


# ----------------------------------------------------------------------
# Unknown master data routes to review, not rejection (PRD R7)
# ----------------------------------------------------------------------
def test_unknown_vendor_and_po_route_to_review_not_rejection(store):
    """A supplier we have never traded with is an ordinary business event.
    Rejecting it is a statement about the invoice; the truth is a statement
    about our own master data."""
    from app.decide import decide
    from app.models import DecisionOutcome

    ctx = make_ctx(store, vendor=None, po=None, po_lines=[],
                   vendor_match=MatchResult(None, Decimal("0"), "NO_CANDIDATE"),
                   po_match=MatchResult(None, Decimal("0"), "NOT_FOUND"))
    results = [run(rid, ctx) for rid in ("VEN-01", "PO-01", "EXT-01", "FIN-04")]

    decision = decide(results, Decimal("7200.00"), Decimal("0.95"), Decimal("0.0"))
    assert decision.outcome == DecisionOutcome.MANUAL_REVIEW
    assert "UNKNOWN_TO_MASTER_DATA" in decision.reason_codes


def test_a_vendor_we_do_have_and_have_blacklisted_still_rejects(store):
    """VEN-03 is a real disqualifier and must not be softened along with the
    not-on-file case."""
    from app.decide import decide
    from app.models import DecisionOutcome

    ctx = make_ctx(store, vendor=store.get("vendors", "V-1008"))
    results = [run(rid, ctx) for rid in ("VEN-02", "VEN-03", "EXT-01")]

    decision = decide(results, Decimal("150000.00"), Decimal("0.95"), Decimal("0.9"))
    assert decision.outcome == DecisionOutcome.REJECT
    assert "VEN-03" in decision.reason_codes


def test_a_non_invoice_still_rejects(store):
    from app.decide import decide
    from app.models import DecisionOutcome

    ctx = make_ctx(store, document={
        "id": "DOC-X", "sha256": "b" * 64, "mime_type": "application/pdf",
        "page_count": 1, "encrypted": False, "corrupt": False,
        "classification": {"label": "DELIVERY_NOTE", "confidence": 0.93},
    })
    decision = decide([run("ING-02", ctx)], Decimal("0"),
                      Decimal("0.9"), Decimal("0.9"))
    assert decision.outcome == DecisionOutcome.REJECT


# ----------------------------------------------------------------------
# FIN-03 across jurisdictions — an invoice from anywhere must be judged by
# whichever tax regime actually applies to it.
# ----------------------------------------------------------------------
@pytest.mark.parametrize("label,tax_id,currency,subtotal,tax,expected", [
    ("India GST 18%",        "27AABCS1429B1ZX", "INR", "38135.59", "6864.41", Outcome.PASS),
    ("India GST 5%",         "27AABCS1429B1ZX", "INR", "10000.00", "500.00",  Outcome.PASS),
    ("India at a bogus 15%", "27AABCS1429B1ZX", "INR", "38135.59", "5720.34", Outcome.FAIL),
    ("UK VAT 20%",           "GB123456789",     "GBP", "6000.00",  "1200.00", Outcome.PASS),
    ("UK VAT 5% reduced",    "GB123456789",     "GBP", "1000.00",  "50.00",   Outcome.PASS),
    ("UK at India's 18%",    "GB123456789",     "GBP", "6000.00",  "1080.00", Outcome.FAIL),
    ("Sweden VAT 25%",       "SE556677889901",  "SEK", "10500.00", "2625.00", Outcome.PASS),
    ("Germany VAT 19%",      "DE123456789",     "EUR", "1000.00",  "190.00",  Outcome.PASS),
    ("Netherlands VAT 21%",  "NL123456789B01",  "EUR", "1000.00",  "210.00",  Outcome.PASS),
    ("UAE VAT 5%",           None,              "AED", "1000.00",  "50.00",   Outcome.PASS),
    ("Australia GST 10%",    None,              "AUD", "1000.00",  "100.00",  Outcome.PASS),
    ("Japan 10%, zero-dp",   None,              "JPY", "10000",    "1000",    Outcome.PASS),
])
def test_fin03_judges_by_the_applicable_jurisdiction(
    store, label, tax_id, currency, subtotal, tax, expected,
):
    ctx = make_ctx(store, invoice={
        "vendor_tax_id": tax_id, "currency": currency,
        "subtotal": Decimal(subtotal), "tax_amount": Decimal(tax),
        "grand_total": Decimal(subtotal) + Decimal(tax),
    })
    result = run("FIN-03", ctx)
    assert result.outcome == expected, f"{label}: {result.message}"


def test_fin03_reports_but_does_not_fail_an_unverifiable_rate(store):
    """US sales tax has no national rate set, and EUR spans twenty countries.
    Failing those would be a false exception; passing silently would be a missed
    control. Reporting them as unverified is the honest third answer."""
    for tax_id, currency, subtotal, tax in (
        ("84-1234567", "USD", "8012.50", "681.06"),   # 8.5% California-ish
        (None, "EUR", "1000.00", "210.00"),            # 21%, could be NL or ES
    ):
        ctx = make_ctx(store, invoice={
            "vendor_tax_id": tax_id, "currency": currency,
            "subtotal": Decimal(subtotal), "tax_amount": Decimal(tax),
            "grand_total": Decimal(subtotal) + Decimal(tax),
        })
        result = run("FIN-03", ctx)
        assert result.outcome == Outcome.PASS
        assert result.evidence["rate_verified"] is False
        assert "unverified" in result.message


def test_fin03_warns_on_a_rate_that_exists_nowhere(store):
    ctx = make_ctx(store, invoice={
        "vendor_tax_id": None, "currency": "EUR",
        "subtotal": Decimal("1000.00"), "tax_amount": Decimal("347.00"),
        "grand_total": Decimal("1347.00"),
    })
    result = run("FIN-03", ctx)
    assert result.outcome == Outcome.WARN
    assert result.evidence["rate_plausible"] is False
