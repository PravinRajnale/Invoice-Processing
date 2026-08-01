"""No-database persistence layer.

The PRD specifies PostgreSQL 16; this build has no database by requirement, so
the same table shapes from PRD 8 live here as in-memory dicts with JSON
snapshots on disk. The properties that actually matter to the design are
preserved:

* money is stored as strings and only ever handled as ``Decimal`` in Python;
* ``audit_events`` is append-only and hash-chained (PRD 8.4);
* the PO consumption ledger is guarded by a re-entrant lock so the provisional
  reservation cannot race (PRD 8.2, NFR "Concurrency").

A real deployment swaps this module for Prisma/SQLAlchemy without touching the
rule engine, which only ever talks to the accessors below.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .config import SETTINGS

# Tables mirroring PRD 8. Order matters only for readable snapshots.
TABLES = [
    "vendors",
    "purchase_orders",
    "po_lines",
    "goods_receipts",
    "goods_receipt_lines",
    "po_consumption",
    "documents",
    "invoices",
    "invoice_lines",
    "extracted_fields",
    "validation_runs",
    "rule_results",
    "decisions",
    "human_actions",
    "audit_events",
    "users",
]

# Master tables are seeded from files under app/seed and are never written back.
SEEDED = {"vendors", "purchase_orders", "po_lines", "users", "goods_receipts",
          "goods_receipt_lines"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    """UUID v7-ish: time-ordered prefix so ids sort chronologically, which is
    what v7 buys us and what makes the audit trail readable."""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return f"{ts:012x}-{uuid.uuid4().hex[:20]}"


# Columns the sheets carry as pipe-separated lists, and as booleans. Everything
# else stays a string — money in particular, which must never round-trip through
# a float on its way out of a spreadsheet.
_LIST_COLUMNS = {"aliases", "permitted_currencies"}
_BOOL_COLUMNS = {"allows_partial_invoicing"}
# Denormalised for human readability in the sheet; not part of the record.
_DERIVED_COLUMNS = {"vendor_name", "po_number_ref"}


def _read_seed_csv(path: Path) -> List[Dict[str, Any]]:
    import csv

    rows: List[Dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            row: Dict[str, Any] = {}
            for key, value in raw.items():
                if key is None:
                    continue
                key = key.strip()
                value = (value or "").strip()

                if key in _LIST_COLUMNS:
                    row[key] = [v.strip() for v in value.split("|") if v.strip()]
                elif key in _BOOL_COLUMNS:
                    row[key] = value.upper() in ("TRUE", "1", "YES", "Y")
                else:
                    row[key] = value or None
            if row.get("id"):
                rows.append(row)
    return rows


def json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "value"):  # Enum
        return o.value
    raise TypeError(f"not JSON serialisable: {type(o)}")


class Store:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir or SETTINGS.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        SETTINGS.storage_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._tables: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
        self._dirty: set[str] = set()

        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            for table in TABLES:
                path = self.data_dir / f"{table}.json"
                if path.exists():
                    self._tables[table] = json.loads(path.read_text() or "[]")
                elif table in SEEDED:
                    rows = self._load_seed(table)
                    if rows:
                        self._tables[table] = rows
                        self._dirty.add(table)
            self.flush()

    def _load_seed(self, table: str) -> List[Dict[str, Any]]:
        """Seed a master table, preferring the CSV.

        The brief's premise is that procurement keeps its purchase orders in a
        spreadsheet that AP staff look up by hand. Loading the CSV directly makes
        that literal rather than metaphorical: edit
        ``app/seed/purchase_orders.csv`` in Excel, restart, and the engine
        validates against what you typed. JSON remains the fallback for tables
        with no sheet.
        """
        csv_path = SETTINGS.seed_dir / f"{table}.csv"
        if csv_path.exists():
            return _read_seed_csv(csv_path)

        json_path = SETTINGS.seed_dir / f"{table}.json"
        if json_path.exists():
            return json.loads(json_path.read_text() or "[]")
        return []

    def flush(self) -> None:
        with self._lock:
            for table in list(self._dirty) or TABLES:
                path = self.data_dir / f"{table}.json"
                path.write_text(
                    json.dumps(self._tables[table], indent=2, default=json_default)
                )
            self._dirty.clear()

    def reset(self, keep_masters: bool = True) -> None:
        """Wipe transactional state. Used by fixtures and the demo reset."""
        with self._lock:
            for table in TABLES:
                if keep_masters and table in SEEDED:
                    continue
                self._tables[table] = []
                self._dirty.add(table)
            self.flush()

    # ------------------------------------------------------------------
    # Generic accessors
    # ------------------------------------------------------------------
    def insert(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            row.setdefault("id", new_id())
            row.setdefault("created_at", now_iso())
            self._tables[table].append(row)
            self._dirty.add(table)
            self.flush()
            return row

    def insert_many(self, table: str, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        with self._lock:
            for row in rows:
                row.setdefault("id", new_id())
                row.setdefault("created_at", now_iso())
                self._tables[table].append(row)
                out.append(row)
            self._dirty.add(table)
            self.flush()
        return out

    def all(self, table: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._tables[table])

    def find(self, table: str, **criteria: Any) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                r for r in self._tables[table]
                if all(r.get(k) == v for k, v in criteria.items())
            ]

    def find_one(self, table: str, **criteria: Any) -> Optional[Dict[str, Any]]:
        rows = self.find(table, **criteria)
        return rows[0] if rows else None

    def get(self, table: str, row_id: str) -> Optional[Dict[str, Any]]:
        return self.find_one(table, id=row_id)

    def where(self, table: str, predicate: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        with self._lock:
            return [r for r in self._tables[table] if predicate(r)]

    def update(self, table: str, row_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            for row in self._tables[table]:
                if row.get("id") == row_id:
                    row.update(changes)
                    row["updated_at"] = now_iso()
                    self._dirty.add(table)
                    self.flush()
                    return row
            return None

    # ------------------------------------------------------------------
    # Append-only, hash-chained audit (PRD 8.4, 16)
    # ------------------------------------------------------------------
    def append_audit(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: Dict[str, Any] | None = None,
        actor_id: Optional[str] = None,
        actor_type: str = "SYSTEM",
    ) -> Dict[str, Any]:
        """Append a tamper-evident audit event.

        Each row hashes its own payload plus the previous row's hash, so any
        retroactive edit breaks the chain from that point forward.
        """
        with self._lock:
            events = self._tables["audit_events"]
            prev_hash = events[-1]["hash"] if events else "0" * 64
            body = {
                "id": new_id(),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "payload": payload or {},
                "created_at": now_iso(),
                "prev_hash": prev_hash,
            }
            digest_src = json.dumps(body, sort_keys=True, default=json_default)
            body["hash"] = hashlib.sha256(digest_src.encode()).hexdigest()
            events.append(body)
            self._dirty.add("audit_events")
            self.flush()
            return body

    def verify_audit_chain(self) -> Dict[str, Any]:
        """Recompute the chain. An auditor runs this to prove nothing was
        edited after the fact."""
        with self._lock:
            prev = "0" * 64
            for idx, row in enumerate(self._tables["audit_events"]):
                if row.get("prev_hash") != prev:
                    return {"valid": False, "broken_at": idx, "event_id": row.get("id"),
                            "reason": "prev_hash mismatch"}
                body = {k: v for k, v in row.items() if k != "hash"}
                expect = hashlib.sha256(
                    json.dumps(body, sort_keys=True, default=json_default).encode()
                ).hexdigest()
                if expect != row.get("hash"):
                    return {"valid": False, "broken_at": idx, "event_id": row.get("id"),
                            "reason": "payload hash mismatch"}
                prev = row["hash"]
            return {"valid": True, "events": len(self._tables["audit_events"])}

    # ------------------------------------------------------------------
    # PO consumption ledger (PRD 8.2) — the heart of Edge Case 1
    # ------------------------------------------------------------------
    @property
    def ledger_lock(self) -> threading.RLock:
        """Held across read-modify-write of the ledger. This is the no-DB
        equivalent of ``SELECT ... FOR UPDATE`` on the PO row: two invoices
        claiming the same remaining balance cannot both be told there is room.
        """
        return self._lock

    def po_consumed(self, po_id: str, exclude_invoice_id: Optional[str] = None) -> Decimal:
        """Amount already claimed against a PO by PROVISIONAL or COMMITTED rows.

        RELEASED rows (rejected / duplicate-blocked invoices) are excluded —
        that headroom is given back.
        """
        with self._lock:
            total = Decimal("0")
            for row in self._tables["po_consumption"]:
                if row["po_id"] != po_id:
                    continue
                if row["status"] not in ("PROVISIONAL", "COMMITTED"):
                    continue
                if exclude_invoice_id and row.get("invoice_id") == exclude_invoice_id:
                    continue
                total += Decimal(row["amount_consumed"])
            return total

    def po_line_consumed_qty(
        self, po_line_id: str, exclude_invoice_id: Optional[str] = None
    ) -> Decimal:
        with self._lock:
            total = Decimal("0")
            for row in self._tables["po_consumption"]:
                if row.get("po_line_id") != po_line_id:
                    continue
                if row["status"] not in ("PROVISIONAL", "COMMITTED"):
                    continue
                if exclude_invoice_id and row.get("invoice_id") == exclude_invoice_id:
                    continue
                total += Decimal(row.get("quantity_consumed") or "0")
            return total

    def reserve(
        self,
        po_id: str,
        invoice_id: str,
        amount: Decimal,
        po_line_id: Optional[str] = None,
        quantity: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Write a PROVISIONAL claim. Idempotent per (po, invoice, line)."""
        with self._lock:
            existing = next(
                (r for r in self._tables["po_consumption"]
                 if r["po_id"] == po_id
                 and r["invoice_id"] == invoice_id
                 and r.get("po_line_id") == po_line_id
                 and r["status"] != "RELEASED"),
                None,
            )
            if existing:
                existing["amount_consumed"] = str(amount)
                if quantity is not None:
                    existing["quantity_consumed"] = str(quantity)
                self._dirty.add("po_consumption")
                self.flush()
                return existing
            return self.insert("po_consumption", {
                "po_id": po_id,
                "po_line_id": po_line_id,
                "invoice_id": invoice_id,
                "amount_consumed": str(amount),
                "quantity_consumed": str(quantity) if quantity is not None else None,
                "status": "PROVISIONAL",
            })

    def settle(self, invoice_id: str, status: str) -> int:
        """Move an invoice's ledger claims to COMMITTED (approved) or
        RELEASED (rejected / duplicate held). Returns rows affected."""
        assert status in ("COMMITTED", "RELEASED")
        with self._lock:
            count = 0
            for row in self._tables["po_consumption"]:
                if row["invoice_id"] == invoice_id and row["status"] == "PROVISIONAL":
                    row["status"] = status
                    row["settled_at"] = now_iso()
                    count += 1
            if count:
                self._dirty.add("po_consumption")
                self.flush()
            return count

    def po_ledger(self, po_id: str) -> List[Dict[str, Any]]:
        """Full ledger for a PO, chronological, for the PO Ledger screen."""
        with self._lock:
            rows = [r for r in self._tables["po_consumption"] if r["po_id"] == po_id]
            return sorted(rows, key=lambda r: r.get("created_at", ""))


STORE = Store()
