/**
 * Procurement master — the spreadsheet the POs live in.
 *
 * The brief's premise is that procurement keeps purchase orders in a
 * spreadsheet and AP staff look them up by hand. PRD 2.2.3 removed the
 * "upload the PO" step for that reason: a PO is persistent state, not a
 * per-invoice attachment. This screen is that spreadsheet, made browsable, with
 * live consumption folded in.
 *
 * Invoices are never stored here. They arrive as PDFs and are matched against
 * these rows; the running balance lives in the consumption ledger.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, ChevronDown, ChevronRight, Download, FileSpreadsheet, Search,
} from 'lucide-react';

import { api, session } from '../lib/api';
import { Empty, ErrorBanner, Spinner, StatusChip } from '../components/ui';
import { date, money, pct } from '../lib/format';

const PO_STATUS = {
  OPEN: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  PARTIALLY_INVOICED: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  FULLY_INVOICED: 'border-ink-700 bg-ink-850 text-slate-400',
  CLOSED: 'border-ink-700 bg-ink-850 text-slate-400',
  CANCELLED: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  EXPIRED: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
};

export default function Procurement() {
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.procurement()
      .then((res) => { setData(res.data); setMeta(res.meta); })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  const pos = useMemo(() => {
    if (!data) return [];
    const needle = search.trim().toLowerCase();
    if (!needle) return data.purchaseOrders;
    return data.purchaseOrders.filter((p) =>
      [p.po_number, p.vendor_name, p.cost_center, p.status]
        .join(' ').toLowerCase().includes(needle));
  }, [data, search]);

  if (loading) {
    return <div className="flex justify-center py-20"><Spinner className="w-6 h-6 text-accent" /></div>;
  }
  if (error) return <div className="p-6"><ErrorBanner error={error} /></div>;

  return (
    <div className="p-6 space-y-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Procurement master</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {meta?.poCount} purchase orders · {meta?.lineCount} lines ·
            {' '}{meta?.vendorCount} vendors
          </p>
        </div>
        <div className="flex gap-2">
          <a className="btn-ghost" href={api.sheetUrl('workbook')} download>
            <FileSpreadsheet className="w-4 h-4" aria-hidden="true" />
            Excel workbook
          </a>
          <a className="btn-ghost" href={api.sheetUrl('purchase_orders')} download>
            <Download className="w-4 h-4" aria-hidden="true" />
            POs (CSV)
          </a>
          <a className="btn-ghost" href={api.sheetUrl('po_lines')} download>
            <Download className="w-4 h-4" aria-hidden="true" />
            Lines (CSV)
          </a>
        </div>
      </header>

      <div className="card p-3.5 flex items-start gap-2.5">
        <FileSpreadsheet className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" aria-hidden="true" />
        <div className="text-xs text-slate-400">
          <p>
            There is no “upload the PO” step, by design. The brief puts the purchase
            order in a spreadsheet inside a procurement system, so the platform
            <strong className="text-slate-300"> looks it up</strong> rather than asking
            a clerk to attach it. These rows are seeded from
            {' '}<code className="mono text-slate-300">{meta?.source}</code> and loaded
            at startup — edit the CSV in Excel, restart, and validation runs against
            what you typed.
          </p>
          <p className="mt-1.5">
            That choice is what makes <strong className="text-slate-300">one PO carrying
            many invoices</strong> possible at all: the running balance has to be
            persistent state. A PO re-uploaded with each invoice has no memory of the
            last one.
          </p>
        </div>
      </div>

      <div className="relative max-w-md">
        <Search className="w-3.5 h-3.5 text-slate-600 absolute left-3 top-1/2 -translate-y-1/2"
          aria-hidden="true" />
        <input className="input pl-9" placeholder="Search PO number, vendor, cost centre…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <div className="card overflow-hidden">
        {pos.length === 0 ? (
          <Empty title="No purchase orders match" />
        ) : (
          <table className="w-full">
            <thead className="bg-ink-850 border-b border-ink-800">
              <tr>
                <th className="th w-8" />
                <th className="th">PO</th>
                <th className="th">Vendor</th>
                <th className="th text-right">PO value</th>
                <th className="th text-right">Invoiced</th>
                <th className="th text-right">Remaining</th>
                <th className="th w-40">Consumed</th>
                <th className="th text-center">Invoices</th>
                <th className="th">Partial</th>
                <th className="th">Status</th>
                <th className="th">Valid until</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {pos.map((po) => {
                const open = expanded === po.id;
                return (
                  <>
                    <tr key={po.id}
                      onClick={() => setExpanded(open ? null : po.id)}
                      className={`cursor-pointer hover:bg-ink-850 ${
                        po.overConsumed ? 'bg-rose-500/[0.06]' : ''}`}>
                      <td className="td text-slate-600">
                        {open ? <ChevronDown className="w-3.5 h-3.5" />
                          : <ChevronRight className="w-3.5 h-3.5" />}
                      </td>
                      <td className="td">
                        <Link to={`/pos/${po.po_number}`} onClick={(e) => e.stopPropagation()}
                          className="mono text-accent hover:underline">
                          {po.po_number}
                        </Link>
                      </td>
                      <td className="td">
                        {po.vendor_name}
                        {po.vendor_status !== 'ACTIVE' && (
                          <span className="chip border-rose-500/30 bg-rose-500/10 text-rose-300 ml-1.5">
                            {po.vendor_status?.toLowerCase()}
                          </span>
                        )}
                      </td>
                      <td className="td text-right mono">{money(po.total_amount, po.currency)}</td>
                      <td className={`td text-right mono ${po.overConsumed ? 'text-rose-300' : ''}`}>
                        {money(po.consumed, po.currency)}
                      </td>
                      <td className={`td text-right mono ${
                        Number(po.remaining) < 0 ? 'text-rose-300' : 'text-slate-400'}`}>
                        {money(po.remaining, po.currency)}
                      </td>
                      <td className="td">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 bg-ink-800 rounded overflow-hidden relative">
                            <div className={`h-full ${po.overConsumed ? 'bg-rose-500' : 'bg-accent'}`}
                              style={{ width: `${Math.min(Number(po.consumedPct), 100)}%` }} />
                          </div>
                          <span className={`mono text-[10px] w-14 text-right ${
                            po.overConsumed ? 'text-rose-300' : 'text-slate-500'}`}>
                            {pct(po.consumedPct, 1)}
                          </span>
                        </div>
                      </td>
                      <td className="td text-center mono">{po.invoiceCount}</td>
                      <td className="td">
                        <span className={`chip ${po.allows_partial_invoicing
                          ? 'border-ink-700 bg-ink-850 text-slate-400'
                          : 'border-amber-500/30 bg-amber-500/10 text-amber-300'}`}
                          title={po.allows_partial_invoicing
                            ? 'This PO may be billed across several invoices.'
                            : 'Must be billed in one invoice — POL-02 fails a second.'}>
                          {po.allows_partial_invoicing ? 'allowed' : 'single only'}
                        </span>
                      </td>
                      <td className="td">
                        <span className={`chip ${PO_STATUS[po.status] || PO_STATUS.CLOSED}`}>
                          {po.status.replace(/_/g, ' ').toLowerCase()}
                        </span>
                      </td>
                      <td className="td text-slate-500 text-xs">{date(po.valid_until)}</td>
                    </tr>

                    {open && (
                      <tr key={`${po.id}-detail`}>
                        <td colSpan={11} className="bg-ink-950 px-6 py-4">
                          {po.overConsumed && (
                            <div className="flex items-start gap-2 mb-4 p-2.5 rounded
                                            border border-rose-500/40 bg-rose-500/10">
                              <AlertTriangle className="w-3.5 h-3.5 text-rose-400 mt-0.5 shrink-0"
                                aria-hidden="true" />
                              <p className="text-xs text-rose-200">
                                Billed to {pct(po.consumedPct, 2)} of its value across
                                {' '}{po.invoiceCount} invoices. Each is individually
                                well-formed; only the running total shows the problem.
                              </p>
                            </div>
                          )}

                          <div className="grid lg:grid-cols-2 gap-6">
                            {/* Ordered lines */}
                            <div>
                              <h4 className="text-xs font-semibold text-slate-300 mb-2">
                                Ordered lines
                                <span className="text-slate-600 font-normal ml-1.5">
                                  — what LIN-02 and LIN-03 check against
                                </span>
                              </h4>
                              <table className="w-full">
                                <thead>
                                  <tr className="border-b border-ink-800">
                                    <th className="th py-1">#</th>
                                    <th className="th py-1">SKU</th>
                                    <th className="th py-1">Description</th>
                                    <th className="th py-1 text-right">Qty</th>
                                    <th className="th py-1 text-right">Unit price</th>
                                    <th className="th py-1 text-right">Billed</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-ink-850">
                                  {po.lines.map((line) => {
                                    const over = Number(line.consumedPct) > 100;
                                    return (
                                      <tr key={line.id} className={over ? 'bg-rose-500/[0.06]' : ''}>
                                        <td className="td py-1.5 text-slate-600">{line.line_no}</td>
                                        <td className="td py-1.5 mono text-[11px]">{line.sku}</td>
                                        <td className="td py-1.5 text-xs max-w-[200px] truncate"
                                          title={line.description}>{line.description}</td>
                                        <td className="td py-1.5 text-right mono text-xs">
                                          {line.quantity_ordered} {line.uom}
                                        </td>
                                        <td className="td py-1.5 text-right mono text-xs">
                                          {money(line.unit_price, po.currency)}
                                        </td>
                                        <td className={`td py-1.5 text-right mono text-xs ${
                                          over ? 'text-rose-300' : 'text-slate-400'}`}>
                                          {line.quantityConsumed} ({pct(line.consumedPct, 0)})
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>

                            {/* Invoices billed against it */}
                            <div>
                              <h4 className="text-xs font-semibold text-slate-300 mb-2">
                                Invoices billed against this PO
                                <span className="text-slate-600 font-normal ml-1.5">
                                  — the consumption ledger, not the sheet
                                </span>
                              </h4>
                              {po.invoices.length === 0 ? (
                                <p className="text-xs text-slate-600 py-4">
                                  Nothing billed yet.
                                </p>
                              ) : (
                                <table className="w-full">
                                  <thead>
                                    <tr className="border-b border-ink-800">
                                      <th className="th py-1">Invoice</th>
                                      <th className="th py-1">Date</th>
                                      <th className="th py-1 text-right">Amount</th>
                                      <th className="th py-1">Ledger</th>
                                      <th className="th py-1">Status</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-ink-850">
                                    {po.invoices.map((inv) => (
                                      <tr key={inv.invoiceId}>
                                        <td className="td py-1.5">
                                          <Link to={`/invoices/${inv.invoiceId}`}
                                            className="mono text-[11px] text-accent hover:underline">
                                            {inv.invoiceNumber || '—'}
                                          </Link>
                                        </td>
                                        <td className="td py-1.5 text-xs text-slate-500">
                                          {date(inv.invoiceDate)}
                                        </td>
                                        <td className="td py-1.5 text-right mono text-xs">
                                          {money(inv.amount, po.currency)}
                                        </td>
                                        <td className="td py-1.5">
                                          <span className={`chip ${
                                            inv.ledgerStatus === 'COMMITTED'
                                              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                                              : inv.ledgerStatus === 'PROVISIONAL'
                                                ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                                                : 'border-ink-700 bg-ink-850 text-slate-500'}`}>
                                            {inv.ledgerStatus.toLowerCase()}
                                          </span>
                                        </td>
                                        <td className="td py-1.5">
                                          <StatusChip status={inv.invoiceStatus} />
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                              <Link to={`/pos/${po.po_number}`}
                                className="inline-block mt-3 text-xs text-accent hover:underline">
                                Open the full consumption ledger →
                              </Link>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
