/**
 * Screen 7 — PO Ledger View (PRD 13.7).
 *
 * The visual payoff for Edge Case 1. The consumption bar is segmented by
 * invoice with the 100% line marked, and anything beyond it rendered in red.
 * Seeing the third invoice cross that line is the entire argument for holding
 * cross-document state.
 */

import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft } from 'lucide-react';

import { api } from '../lib/api';
import { Empty, ErrorBanner, Spinner, StatusChip, KeyValue } from '../components/ui';
import { date, money, pct } from '../lib/format';

const SEGMENT_COLOURS = ['#4f8ef7', '#22d3ee', '#a78bfa', '#f59e0b', '#34d399'];

export default function PoLedger() {
  const { poNumber } = useParams();
  const navigate = useNavigate();
  const [ledger, setLedger] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.ledger(poNumber)
      .then(({ data }) => setLedger(data))
      .catch(setError)
      .finally(() => setLoading(false));
  }, [poNumber]);

  if (loading) {
    return <div className="flex justify-center py-20"><Spinner className="w-6 h-6 text-accent" /></div>;
  }
  if (error || !ledger) {
    return <div className="p-6"><ErrorBanner error={error || new Error('Not found')} /></div>;
  }

  const po = ledger.purchaseOrder;
  const consumedPct = Number(ledger.consumedPct);
  const counted = ledger.entries.filter((e) => e.countsTowardConsumption);

  // The bar is scaled so 100% of the PO occupies a fixed share of the width,
  // leaving room for the overrun to be visibly *past* the line.
  const scale = consumedPct > 100 ? 100 / consumedPct : 1;

  return (
    <div className="p-6 max-w-5xl space-y-5">
      <header className="flex items-start gap-4">
        <button onClick={() => navigate(-1)} className="text-slate-500 hover:text-slate-200 mt-1"
          aria-label="Back">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <h1 className="text-lg font-semibold text-slate-100 mono">{po.po_number}</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {ledger.vendor?.trade_name} · consumption ledger
          </p>
        </div>
      </header>

      {ledger.overConsumed && (
        <div className="flex items-start gap-3 p-3.5 rounded-md border border-rose-500/40 bg-rose-500/10">
          <AlertTriangle className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="text-sm text-rose-200 font-medium">
              This purchase order is over-consumed at {pct(consumedPct, 2)}
            </p>
            <p className="text-xs text-rose-300/80 mt-1">
              {money(ledger.consumed)} has been claimed against a {money(ledger.totalAmount)} order —
              {' '}{money(String(Number(ledger.consumed) - Number(ledger.totalAmount)))} beyond its value.
              Each invoice below is individually well-formed. Only the running total reveals the problem,
              which is why this state has to be persistent rather than recomputed per document.
            </p>
          </div>
        </div>
      )}

      {/* Consumption bar */}
      <div className="card p-5">
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-200">Cumulative consumption</h2>
          <span className={`mono text-sm ${ledger.overConsumed ? 'text-rose-400' : 'text-slate-300'}`}>
            {money(ledger.consumed)} of {money(ledger.totalAmount)} · {pct(consumedPct, 2)}
          </span>
        </div>

        <div className="relative h-10 bg-ink-950 rounded border border-ink-800 overflow-hidden">
          {counted.map((entry, index) => {
            const width = (Number(entry.amount) / Number(ledger.totalAmount)) * 100 * scale;
            const left = ((Number(entry.runningTotal) - Number(entry.amount))
              / Number(ledger.totalAmount)) * 100 * scale;
            const overruns = Number(entry.runningPct) > 100;
            return (
              <div
                key={entry.invoiceId}
                className="absolute top-0 h-full flex items-center justify-center
                           border-r border-ink-950/60 transition-all"
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  background: overruns ? '#e11d48' : SEGMENT_COLOURS[index % SEGMENT_COLOURS.length],
                  opacity: entry.ledgerStatus === 'PROVISIONAL' ? 0.72 : 1,
                }}
                title={`${entry.invoiceNumber} — ${money(entry.amount)} (running ${entry.runningPct}%)`}
              >
                {width > 8 && (
                  <span className="text-[10px] font-medium text-white/90 mono truncate px-1">
                    {entry.invoiceNumber}
                  </span>
                )}
              </div>
            );
          })}

          {/* The 100% line */}
          <div className="absolute top-0 h-full w-px bg-white/70 z-10"
            style={{ left: `${100 * scale}%` }} title="100% of PO value">
            <span className="absolute -top-0.5 left-1 text-[9px] text-white/80 whitespace-nowrap">
              100%
            </span>
          </div>
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3 text-[11px] text-slate-500">
          <span>Remaining: <span className={`mono ${
            Number(ledger.remaining) < 0 ? 'text-rose-400' : 'text-slate-300'}`}>
            {money(ledger.remaining)}</span></span>
          <span>Provisional claims are shown slightly faded — they are held while an
            invoice is under review, and released if it is rejected.</span>
        </div>
      </div>

      {/* PO detail */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-2">Purchase order</h2>
          <KeyValue label="Status" value={po.status} />
          <KeyValue label="Value" value={money(po.total_amount, po.currency)} mono />
          <KeyValue label="Subtotal" value={money(po.subtotal, po.currency)} mono />
          <KeyValue label="Tax" value={money(po.tax_amount, po.currency)} mono />
          <KeyValue label="PO date" value={date(po.po_date)} />
          <KeyValue label="Valid until" value={date(po.valid_until)} />
          <KeyValue
            label="Partial invoicing"
            value={po.allows_partial_invoicing ? 'Permitted' : 'Not permitted'}
            title="When not permitted, POL-02 fails any second invoice against this PO."
          />
          <KeyValue label="Cost centre" value={po.cost_center} mono />
        </div>

        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-2">Vendor</h2>
          <KeyValue label="Name" value={ledger.vendor?.trade_name} />
          <KeyValue label="Code" value={ledger.vendor?.vendor_code} mono />
          <KeyValue label="GSTIN" value={ledger.vendor?.tax_id} mono />
          <KeyValue label="Status" value={ledger.vendor?.status} />
          <KeyValue label="Payment terms" value={`Net ${ledger.vendor?.payment_terms_days}`} />
          <KeyValue label="Contract" value={`${date(ledger.vendor?.contract_start)} → ${date(ledger.vendor?.contract_end)}`} />
        </div>
      </div>

      {/* Invoices against this PO */}
      <div className="card overflow-hidden">
        <div className="px-4 py-2.5 border-b border-ink-800">
          <h2 className="text-sm font-semibold text-slate-200">Invoices against this PO</h2>
        </div>
        {ledger.entries.length === 0 ? (
          <Empty title="Nothing billed yet" hint="No invoice has claimed against this purchase order." />
        ) : (
          <table className="w-full">
            <thead className="bg-ink-850 border-b border-ink-800">
              <tr>
                <th className="th">Invoice</th>
                <th className="th">Date</th>
                <th className="th text-right">Amount</th>
                <th className="th text-right">Running total</th>
                <th className="th text-right">% of PO</th>
                <th className="th">Ledger</th>
                <th className="th">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {ledger.entries.map((entry) => {
                const over = Number(entry.runningPct) > 100 && entry.countsTowardConsumption;
                return (
                  <tr key={entry.invoiceId} className={over ? 'bg-rose-500/[0.06]' : ''}>
                    <td className="td">
                      <Link to={`/invoices/${entry.invoiceId}`}
                        className="mono text-accent hover:underline">
                        {entry.invoiceNumber || entry.invoiceId.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="td text-slate-400">{date(entry.invoiceDate)}</td>
                    <td className="td text-right mono">{money(entry.amount)}</td>
                    <td className={`td text-right mono ${over ? 'text-rose-300' : ''}`}>
                      {entry.countsTowardConsumption ? money(entry.runningTotal) : '—'}
                    </td>
                    <td className={`td text-right mono ${over ? 'text-rose-300 font-medium' : ''}`}>
                      {entry.countsTowardConsumption ? `${entry.runningPct}%` : '—'}
                    </td>
                    <td className="td">
                      <span className={`chip ${
                        entry.ledgerStatus === 'COMMITTED' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                          : entry.ledgerStatus === 'PROVISIONAL' ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                            : 'border-ink-700 bg-ink-850 text-slate-500'}`}
                        title={entry.ledgerStatus === 'PROVISIONAL'
                          ? 'Held while under review. Committed on approval, released on rejection.'
                          : entry.ledgerStatus === 'RELEASED'
                            ? 'Released — this claim no longer consumes PO headroom.'
                            : 'Committed on approval.'}>
                        {entry.ledgerStatus.toLowerCase()}
                      </span>
                    </td>
                    <td className="td"><StatusChip status={entry.invoiceStatus} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Per-line consumption */}
      <div className="card overflow-hidden">
        <div className="px-4 py-2.5 border-b border-ink-800">
          <h2 className="text-sm font-semibold text-slate-200">Line-level consumption</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Quantity billed per line across every invoice — what LIN-02 evaluates against.
          </p>
        </div>
        <table className="w-full">
          <thead className="bg-ink-850 border-b border-ink-800">
            <tr>
              <th className="th">#</th>
              <th className="th">SKU</th>
              <th className="th">Description</th>
              <th className="th text-right">Ordered</th>
              <th className="th text-right">Billed</th>
              <th className="th text-right">Remaining</th>
              <th className="th w-32">Consumed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {ledger.lines.map((line) => {
              const consumed = Number(line.consumedPct);
              const over = consumed > 100;
              return (
                <tr key={line.id} className={over ? 'bg-rose-500/[0.06]' : ''}>
                  <td className="td text-slate-500">{line.line_no}</td>
                  <td className="td mono text-xs">{line.sku}</td>
                  <td className="td max-w-xs truncate">{line.description}</td>
                  <td className="td text-right mono">{line.quantity_ordered} {line.uom}</td>
                  <td className={`td text-right mono ${over ? 'text-rose-300' : ''}`}>
                    {line.quantityConsumed}
                  </td>
                  <td className={`td text-right mono ${
                    Number(line.quantityRemaining) < 0 ? 'text-rose-300' : 'text-slate-400'}`}>
                    {line.quantityRemaining}
                  </td>
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 bg-ink-800 rounded overflow-hidden">
                        <div className={`h-full ${over ? 'bg-rose-500' : 'bg-accent'}`}
                          style={{ width: `${Math.min(consumed, 100)}%` }} />
                      </div>
                      <span className={`mono text-[10px] ${over ? 'text-rose-300' : 'text-slate-500'}`}>
                        {line.consumedPct}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
