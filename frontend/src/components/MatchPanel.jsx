/**
 * The two-way match, on one screen — invoice against purchase order.
 *
 * The reconciliation is necessarily spread across the pipeline: vendor
 * resolution in S5, header tolerance in FIN-05, per-line price and quantity in
 * LIN-02/03, cumulative exposure in PO-07 against the ledger. A reviewer asked
 * "how did these two documents get matched?" should not have to reassemble that
 * from four tabs, so this panel gathers it.
 *
 * The PO side is master data looked up by number, never an uploaded file
 * (PRD 2.2.3) — which is what allows one PO to carry many invoices with a
 * running balance.
 */

import { Link } from 'react-router-dom';
import {
  AlertTriangle, ArrowRight, CheckCircle2, FileSpreadsheet, FileText, XCircle,
} from 'lucide-react';

import { Disclosure, OutcomeChip, SectionTitle } from './ui';
import { date, money, pct } from '../lib/format';

export default function MatchPanel({ match, onOpenRule }) {
  if (!match) return null;

  const { invoice, purchaseOrder: po, header, reconciliation = [],
    unbilledPoLines = [], siblingInvoices = [], relevantRules = {} } = match;

  if (!po) {
    return (
      <div className="card p-5 border-rose-500/40 bg-rose-500/[0.06]">
        <div className="flex items-start gap-3">
          <XCircle className="w-5 h-5 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-rose-200">
              No purchase order was matched
            </p>
            <p className="text-xs text-rose-200/80 mt-1.5">
              Either no PO reference appeared on the invoice and none could be inferred
              with enough confidence, or the reference did not exist in the procurement
              master. Nothing below can be reconciled without it — see EXT-10 and PO-01
              in the Validation tab.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const counted = siblingInvoices.filter((s) => s.ledgerStatus !== 'RELEASED');
  const isSplit = counted.length > 1;

  return (
    <div className="space-y-4">
      {/* Where each side came from */}
      <div className="grid md:grid-cols-2 gap-3">
        <Source
          icon={FileText}
          title="Invoice"
          subtitle="Arrived as a PDF, extracted"
          rows={[
            ['Number', invoice.invoice_number],
            ['Date', date(invoice.invoice_date)],
            ['Vendor (as printed)', header?.vendor?.invoice],
            ['Total', money(invoice.grand_total, invoice.currency)],
          ]}
        />
        <Source
          icon={FileSpreadsheet}
          title="Purchase order"
          subtitle="Looked up in procurement master — not uploaded"
          accent
          rows={[
            ['Number', po.po_number],
            ['Raised', date(po.po_date)],
            ['Vendor', header?.vendor?.po],
            ['Value', money(po.total_amount, po.currency)],
          ]}
          footer={
            <Link to={`/pos/${po.po_number}`} className="text-[11px] text-accent hover:underline">
              Open the consumption ledger →
            </Link>
          }
        />
      </div>

      {/* How they were linked */}
      <div className="card p-4">
        <SectionTitle hint="Each step records the method it used, not just that it succeeded.">
          How these two were linked
        </SectionTitle>
        <div className="space-y-2">
          <Step id="EXT-10" label="PO reference read off the invoice"
            value={invoice.po_number} rule={relevantRules['EXT-10']} onOpenRule={onOpenRule} />
          <Step id="VEN-01" label="Vendor resolved to the vendor master"
            value={`${header?.vendor?.po} · ${header?.vendor?.method}${
              header?.vendor?.score ? ` (${header.vendor.score})` : ''}`}
            rule={relevantRules['VEN-01']} onOpenRule={onOpenRule} />
          <Step id="PO-01" label="Purchase order found in the master"
            value={`${po.po_number} · ${header?.poNumber?.method}`}
            rule={relevantRules['PO-01']} onOpenRule={onOpenRule} />
          <Step id="PO-03" label="PO vendor is the invoice vendor"
            value={header?.vendor?.matches ? 'Same vendor' : 'DIFFERENT VENDOR'}
            rule={relevantRules['PO-03']} onOpenRule={onOpenRule} />
          <Step id="PO-04" label="Currencies agree"
            value={`${header?.currency?.invoice} / ${header?.currency?.po}`}
            rule={relevantRules['PO-04']} onOpenRule={onOpenRule} />
          <Step id="PO-02" label="PO status permits invoicing"
            value={po.status} rule={relevantRules['PO-02']} onOpenRule={onOpenRule} />
        </div>
      </div>

      {/* One PO, many invoices */}
      <div className={`card p-4 ${header?.amount?.overConsumed ? 'border-rose-500/40' : ''}`}>
        <SectionTitle
          hint={isSplit
            ? `This purchase order carries ${counted.length} invoices. Each is checked against what the others have already claimed.`
            : 'A purchase order may be billed across several invoices. This is the only one so far.'}
        >
          Cumulative position on {po.po_number}
        </SectionTitle>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <Figure label="PO value" value={money(header?.amount?.poTotal, po.currency)} />
          <Figure label="Already invoiced" value={money(header?.amount?.priorInvoiced, po.currency)}
            hint={`Across ${counted.length - 1} earlier invoice(s), from the consumption ledger.`} />
          <Figure label="This invoice" value={money(header?.amount?.invoice, po.currency)} accent />
          <Figure
            label="Remaining after"
            value={money(header?.amount?.remainingAfter, po.currency)}
            tone={Number(header?.amount?.remainingAfter) < 0 ? 'text-rose-300' : 'text-emerald-300'}
          />
        </div>

        {/* Segmented bar */}
        <div className="relative h-8 bg-ink-950 rounded border border-ink-800 overflow-hidden mb-2">
          {(() => {
            const total = Number(header?.amount?.poTotal || 1);
            const pctConsumed = Number(header?.amount?.consumedPct || 0);
            const scale = pctConsumed > 100 ? 100 / pctConsumed : 1;
            let running = 0;
            return counted.map((s, i) => {
              const width = (Number(s.amount) / total) * 100 * scale;
              const left = (running / total) * 100 * scale;
              running += Number(s.amount);
              const overruns = (running / total) * 100 > 100;
              return (
                <div key={s.invoiceId}
                  className="absolute top-0 h-full flex items-center justify-center
                             border-r border-ink-950/60"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: overruns ? '#e11d48'
                      : s.isThisInvoice ? '#4f8ef7' : '#334155',
                  }}
                  title={`${s.invoiceNumber} — ${money(s.amount, po.currency)}`}>
                  {width > 10 && (
                    <span className="text-[10px] text-white/90 mono truncate px-1">
                      {s.invoiceNumber}
                    </span>
                  )}
                </div>
              );
            });
          })()}
          <div className="absolute top-0 h-full w-px bg-white/70 z-10"
            style={{
              left: `${Number(header?.amount?.consumedPct) > 100
                ? (100 / Number(header.amount.consumedPct)) * 100 : 100}%`,
            }}
            title="100% of PO value" />
        </div>

        <p className={`text-xs ${header?.amount?.overConsumed ? 'text-rose-300' : 'text-slate-500'}`}>
          Cumulative {money(header?.amount?.cumulative, po.currency)} of
          {' '}{money(header?.amount?.poTotal, po.currency)} —
          {' '}<strong>{pct(header?.amount?.consumedPct, 2)}</strong> of the purchase order.
          {header?.amount?.overConsumed && ' This invoice takes it past 100%.'}
        </p>

        {counted.length > 0 && (
          <table className="w-full mt-3">
            <thead>
              <tr className="border-b border-ink-800">
                <th className="th py-1">Invoice</th>
                <th className="th py-1">Date</th>
                <th className="th py-1 text-right">Amount</th>
                <th className="th py-1">Ledger</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-850">
              {counted.map((s) => (
                <tr key={s.invoiceId} className={s.isThisInvoice ? 'bg-accent/10' : ''}>
                  <td className="td py-1.5">
                    {s.isThisInvoice ? (
                      <span className="mono text-xs text-accent">{s.invoiceNumber} (this one)</span>
                    ) : (
                      <Link to={`/invoices/${s.invoiceId}`}
                        className="mono text-xs text-accent hover:underline">
                        {s.invoiceNumber}
                      </Link>
                    )}
                  </td>
                  <td className="td py-1.5 text-xs text-slate-500">{date(s.invoiceDate)}</td>
                  <td className="td py-1.5 text-right mono text-xs">
                    {money(s.amount, po.currency)}
                  </td>
                  <td className="td py-1.5">
                    <span className={`chip ${s.ledgerStatus === 'COMMITTED'
                      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                      : 'border-amber-500/30 bg-amber-500/10 text-amber-300'}`}>
                      {s.ledgerStatus.toLowerCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {['PO-07', 'FIN-05', 'POL-02'].filter((r) => relevantRules[r]).map((rid) => (
          <RuleLine key={rid} rule={relevantRules[rid]} onOpenRule={onOpenRule} />
        ))}
      </div>

      {/* Line-by-line */}
      <div className="card p-4">
        <SectionTitle
          hint="Every billed line against its ordered line. A header total inside tolerance never suppresses a line failure."
        >
          Line reconciliation
        </SectionTitle>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-ink-800">
              <tr>
                <th className="th">Invoice line</th>
                <th className="th">Matched to PO line</th>
                <th className="th text-right">Unit price</th>
                <th className="th text-right">Quantity</th>
                <th className="th">UOM</th>
                <th className="th text-right">Impact</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-850">
              {reconciliation.map((entry) => {
                const { invoiceLine: il, poLine: pl, deltas: d } = entry;
                const priceBad = d?.unitPrice && !d.unitPrice.withinTolerance;
                const qtyBad = d?.quantity && !d.quantity.withinOrdered;
                const uomBad = d?.uom && !d.uom.matches;
                const bad = priceBad || qtyBad || uomBad || !pl;

                return (
                  <tr key={il.line_no} className={bad ? 'bg-rose-500/[0.05]' : ''}>
                    <td className="td">
                      <p className="mono text-xs text-slate-300">
                        {il.line_no}. {il.sku || '—'}
                      </p>
                      <p className="text-[11px] text-slate-500 max-w-[190px] truncate"
                        title={il.description}>{il.description}</p>
                    </td>

                    <td className="td">
                      {pl ? (
                        <>
                          <div className="flex items-center gap-1.5">
                            <ArrowRight className="w-3 h-3 text-slate-600" aria-hidden="true" />
                            <span className="mono text-xs text-slate-300">
                              {pl.line_no}. {pl.sku}
                            </span>
                          </div>
                          <span className="chip border-ink-700 bg-ink-850 text-slate-500 mt-1"
                            title="How this pairing was established. SKU equality is unambiguous; a description score is a proposal the engine accepted against its floor.">
                            {entry.matchMethod}
                          </span>
                        </>
                      ) : (
                        <span className="chip border-rose-500/30 bg-rose-500/10 text-rose-300"
                          title="Billed but not ordered. LIN-06 flags this — a common vector for padded invoices.">
                          not on the PO
                        </span>
                      )}
                    </td>

                    <td className="td text-right">
                      {d?.unitPrice ? (
                        <>
                          <p className="mono text-xs">
                            <span className="text-slate-500">{money(d.unitPrice.po)}</span>
                            {' → '}
                            <span className={priceBad ? 'text-rose-300 font-medium' : 'text-slate-200'}>
                              {money(d.unitPrice.invoice)}
                            </span>
                          </p>
                          {d.unitPrice.deltaPct && Number(d.unitPrice.deltaPct) !== 0 && (
                            <p className={`text-[11px] mono ${priceBad ? 'text-rose-300' : 'text-slate-600'}`}>
                              {Number(d.unitPrice.deltaPct) > 0 ? '+' : ''}
                              {pct(d.unitPrice.deltaPct, 2)}
                            </p>
                          )}
                        </>
                      ) : <span className="text-slate-600 text-xs">—</span>}
                    </td>

                    <td className="td text-right">
                      {d?.quantity ? (
                        <>
                          <p className="mono text-xs text-slate-200">{d.quantity.thisInvoice}</p>
                          <p className={`text-[11px] mono ${qtyBad ? 'text-rose-300' : 'text-slate-600'}`}
                            title="Cumulative billed quantity across every invoice against this PO line, versus what was ordered.">
                            {d.quantity.cumulative} / {d.quantity.ordered} cum.
                          </p>
                        </>
                      ) : <span className="mono text-xs">{il.quantity}</span>}
                    </td>

                    <td className="td">
                      {d?.uom ? (
                        <span className={`text-xs ${uomBad ? 'text-amber-300' : 'text-slate-400'}`}
                          title={uomBad
                            ? `Ordered in ${d.uom.po}, billed in ${d.uom.invoice} — quantities are not comparable.`
                            : undefined}>
                          {d.uom.invoice}
                          {uomBad && ` ≠ ${d.uom.po}`}
                        </span>
                      ) : <span className="text-xs text-slate-500">{il.uom || '—'}</span>}
                    </td>

                    <td className="td text-right">
                      {d?.valueImpact && Number(d.valueImpact) !== 0 ? (
                        <span className={`mono text-xs ${
                          Number(d.valueImpact) > 0 ? 'text-rose-300' : 'text-emerald-300'}`}
                          title="Value effect of the price difference on this line, at the billed quantity.">
                          {Number(d.valueImpact) > 0 ? '+' : ''}{money(d.valueImpact)}
                        </span>
                      ) : <span className="text-slate-600 text-xs">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {unbilledPoLines.length > 0 && (
          <Disclosure
            summary={`${unbilledPoLines.length} ordered line(s) not billed on this invoice`}
            className="mt-3">
            <div className="pt-1 space-y-1">
              {unbilledPoLines.map((l) => (
                <p key={l.id} className="text-[11px] text-slate-500">
                  <span className="mono">{l.line_no}. {l.sku}</span> — {l.description}
                  {' · '}{l.quantity_ordered} {l.uom} @ {money(l.unit_price)}
                </p>
              ))}
              <p className="text-[11px] text-slate-600 mt-2">
                {header?.partialInvoicingAllowed
                  ? 'This PO permits partial invoicing, so unbilled lines are expected (LIN-08 is informational).'
                  : 'This PO does not permit partial invoicing — POL-02 evaluates that.'}
              </p>
            </div>
          </Disclosure>
        )}

        <div className="mt-3 space-y-1">
          {['LIN-01', 'LIN-02', 'LIN-03', 'LIN-05', 'LIN-06'].filter((r) => relevantRules[r])
            .map((rid) => <RuleLine key={rid} rule={relevantRules[rid]} onOpenRule={onOpenRule} />)}
        </div>
      </div>
    </div>
  );
}

function Source({ icon: Icon, title, subtitle, rows, accent, footer }) {
  return (
    <div className={`card p-4 ${accent ? 'border-accent/30' : ''}`}>
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`w-4 h-4 ${accent ? 'text-accent' : 'text-slate-500'}`} aria-hidden="true" />
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
      </div>
      <p className="text-[11px] text-slate-500 mb-3">{subtitle}</p>
      <div className="space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3 text-xs">
            <span className="text-slate-500">{label}</span>
            <span className="mono text-slate-200 text-right truncate">{value ?? '—'}</span>
          </div>
        ))}
      </div>
      {footer && <div className="mt-3">{footer}</div>}
    </div>
  );
}

function Step({ id, label, value, rule, onOpenRule }) {
  const outcome = rule?.outcome;
  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-ink-850 last:border-0">
      <button onClick={() => onOpenRule?.(id)}
        className="mono text-[11px] text-slate-500 w-14 shrink-0 hover:text-accent text-left">
        {id}
      </button>
      <span className="text-xs text-slate-400 flex-1">{label}</span>
      <span className="mono text-xs text-slate-200 truncate max-w-[280px]" title={value}>
        {value || '—'}
      </span>
      {outcome ? <OutcomeChip outcome={outcome} showLabel={false} />
        : <span className="text-slate-700 text-xs">—</span>}
    </div>
  );
}

function RuleLine({ rule, onOpenRule }) {
  const bad = ['FAIL', 'WARN', 'CANNOT_EVALUATE'].includes(rule.outcome);
  return (
    <button onClick={() => onOpenRule?.(rule.rule_id)}
      className={`w-full text-left flex items-start gap-2 p-2 rounded text-xs
        ${bad ? 'bg-rose-500/[0.06] hover:bg-rose-500/[0.12]' : 'hover:bg-ink-850'}`}>
      {bad ? <AlertTriangle className="w-3 h-3 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />
        : <CheckCircle2 className="w-3 h-3 text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />}
      <span className="mono text-slate-500 w-14 shrink-0">{rule.rule_id}</span>
      <span className={bad ? 'text-rose-200 flex-1' : 'text-slate-500 flex-1'}>
        {rule.message}
      </span>
    </button>
  );
}

function Figure({ label, value, hint, accent, tone }) {
  return (
    <div className={`p-2.5 rounded border ${accent
      ? 'border-accent/30 bg-accent/[0.06]' : 'border-ink-800 bg-ink-850'}`} title={hint}>
      <p className="text-[10px] text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`mono text-sm mt-1 ${tone || 'text-slate-100'}`}>{value}</p>
    </div>
  );
}
