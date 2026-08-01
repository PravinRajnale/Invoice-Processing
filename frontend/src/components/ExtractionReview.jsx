/**
 * Screen 3 — Extraction Review (PRD 13.3).
 *
 * This replaces the "GPT summary" the original design called for. A prose
 * summary is lossy, unverifiable and useless downstream; a table of
 * field → value → confidence → source location is better on every axis. It is
 * verifiable, it is correctable, it drives the rules, and it is the audit
 * evidence.
 */

import { useMemo, useState } from 'react';
import { AlertTriangle, Check, Edit3, MapPin, X } from 'lucide-react';

import { ConfidenceDot, Disclosure, SectionTitle } from './ui';
import { fieldLabel, money } from '../lib/format';

const GROUPS = [
  { title: 'Document identity', paths: ['header.invoice_number', 'header.invoice_date', 'header.due_date'] },
  { title: 'Vendor', paths: ['header.vendor_name', 'header.vendor_tax_id'] },
  { title: 'Amounts', paths: ['header.subtotal', 'header.tax_amount', 'header.discount_amount', 'header.other_charges', 'header.grand_total', 'header.currency'] },
  { title: 'PO reference', paths: ['header.po_number'] },
  { title: 'Terms', paths: ['header.payment_terms'] },
];

export default function ExtractionReview({
  extraction, lines = [], poLines = [], activeField, onFieldFocus, onCorrect,
  criticalFloor = '0.80', readOnly,
}) {
  const fields = extraction?.fields || [];
  const byPath = useMemo(
    () => Object.fromEntries(fields.map((f) => [f.field_path, f])),
    [fields],
  );
  const belowFloor = extraction?.belowFloor || [];

  return (
    <div className="space-y-4">
      {/* Reading path — different documents took different routes and the
          reviewer should know which. */}
      <div className="flex items-start gap-2 p-2.5 rounded-md bg-ink-850 border border-ink-800">
        <MapPin className="w-3.5 h-3.5 text-slate-500 mt-0.5 shrink-0" aria-hidden="true" />
        <div className="text-xs">
          <p className="text-slate-300">{extraction?.readingPath}</p>
          {extraction?.converted && (
            <p className="text-sky-300/90 mt-0.5">
              Arrived as a {extraction.sourceFormatLabel?.toLowerCase()} and was
              converted to PDF — the highlights below are positioned on that rendition.
            </p>
          )}
          <p className="text-slate-500 mt-0.5">
            Extraction source: <span className="mono">{extraction?.extractionSource || '—'}</span>
            {extraction?.extractionConfidence && (
              <> · weighted extraction confidence{' '}
                <span className="mono">
                  {(Number(extraction.extractionConfidence) * 100).toFixed(1)}%
                </span>
              </>
            )}
          </p>
        </div>
      </div>

      {extraction?.extractionUnavailable && (
        <div className="p-3.5 rounded-md border border-amber-500/50 bg-amber-500/10">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-amber-200">
                Nothing could be extracted from this document
              </p>
              <p className="text-xs text-amber-200/85 mt-1.5">
                {extraction.extractionUnavailableReason}
              </p>
              <p className="text-xs text-amber-200/70 mt-2">
                The rules still ran and reported honestly — every check that needed a
                field it never received says “could not check”, not “failed”. Nothing
                was guessed. That is the intended behaviour when perception is
                unavailable, but it does mean this invoice cannot be decided until the
                document can actually be read.
              </p>
            </div>
          </div>
        </div>
      )}

      {belowFloor.length > 0 && (
        <div className="p-3 rounded-md border border-rose-500/40 bg-rose-500/10">
          <div className="flex items-center gap-2 mb-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400" aria-hidden="true" />
            <p className="text-xs font-medium text-rose-200">
              {belowFloor.length} critical field{belowFloor.length > 1 ? 's' : ''} read below
              the {(Number(criticalFloor) * 100).toFixed(0)}% floor
            </p>
          </div>
          <p className="text-xs text-rose-300/80 mb-2">
            Rules that depend on {belowFloor.length > 1 ? 'these' : 'this'} will report
            “could not check” rather than passing or failing. Confirm the value to unblock them.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {belowFloor.map((f) => (
              <button key={f.field_path}
                onClick={() => onFieldFocus?.(f.field_path)}
                className="chip border-rose-500/40 bg-rose-500/15 text-rose-200 hover:bg-rose-500/25">
                {fieldLabel(f.field_path)} · {(Number(f.confidence) * 100).toFixed(0)}%
              </button>
            ))}
          </div>
        </div>
      )}

      {GROUPS.map((group) => {
        const present = group.paths.filter((p) => byPath[p]);
        if (!present.length) return null;
        return (
          <div key={group.title}>
            <SectionTitle>{group.title}</SectionTitle>
            <div className="card divide-y divide-ink-850">
              {present.map((path) => (
                <FieldRow
                  key={path}
                  field={byPath[path]}
                  active={activeField === path}
                  onFocus={() => onFieldFocus?.(path)}
                  onCorrect={onCorrect}
                  readOnly={readOnly}
                  criticalFloor={criticalFloor}
                />
              ))}
            </div>
          </div>
        );
      })}

      <div>
        <SectionTitle
          hint="Match method is always shown — “matched by description 0.86” is honest, “matched” is not."
        >
          Line items
        </SectionTitle>
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead className="bg-ink-850 border-b border-ink-800">
              <tr>
                <th className="th">#</th>
                <th className="th">SKU</th>
                <th className="th">Description</th>
                <th className="th text-right">Qty</th>
                <th className="th">UOM</th>
                <th className="th text-right">Unit price</th>
                <th className="th text-right">Line total</th>
                <th className="th">Matched PO line</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-850">
              {lines.map((line) => {
                const poLine = poLines.find((l) => l.id === line.matched_po_line_id);
                return (
                  <tr key={line.id || line.line_no} className="hover:bg-ink-850">
                    <td className="td text-slate-500">{line.line_no}</td>
                    <td className="td mono text-xs">{line.sku || '—'}</td>
                    <td className="td max-w-xs">{line.description}</td>
                    <td className="td text-right mono">{line.quantity}</td>
                    <td className="td text-slate-400">{line.uom || '—'}</td>
                    <td className="td text-right mono">{money(line.unit_price)}</td>
                    <td className="td text-right mono">{money(line.line_total)}</td>
                    <td className="td">
                      {poLine ? (
                        <span className="chip border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                          title={`${poLine.description} — matched by ${line.match_method}`}>
                          {poLine.sku || `line ${poLine.line_no}`} · {line.match_method}
                        </span>
                      ) : (
                        <span className="chip border-rose-500/30 bg-rose-500/10 text-rose-300"
                          title="This line does not appear on the purchase order — see LIN-06.">
                          not on PO
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {lines.length === 0 && (
                <tr><td colSpan={8} className="td text-center text-slate-600 py-6">
                  No line items extracted yet.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FieldRow({ field, active, onFocus, onCorrect, readOnly, criticalFloor }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.normalised_value ?? '');
  const [saving, setSaving] = useState(false);

  const low = Number(field.confidence) < Number(criticalFloor);
  const corrected = field.extraction_method === 'HUMAN_CORRECTED';

  async function save() {
    setSaving(true);
    try {
      await onCorrect?.(field.field_path, draft);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className={`px-3 py-2 transition-colors ${active ? 'bg-accent/10' : 'hover:bg-ink-850'}`}
      onMouseEnter={onFocus}
    >
      <div className="flex items-center justify-between gap-3">
        <button onClick={onFocus} className="text-xs text-slate-400 text-left shrink-0 w-32
                                             hover:text-accent flex items-center gap-1"
          title="Show where this came from on the document">
          {fieldLabel(field.field_path)}
          {field.bbox && <MapPin className="w-2.5 h-2.5 opacity-60" aria-hidden="true" />}
        </button>

        {editing ? (
          <div className="flex-1 flex items-center gap-1.5">
            <input className="input py-1 text-xs" value={draft} autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save();
                if (e.key === 'Escape') setEditing(false);
              }} />
            <button className="btn-primary px-2 py-1" onClick={save} disabled={saving} aria-label="Save">
              <Check className="w-3 h-3" />
            </button>
            <button className="btn-ghost px-2 py-1" onClick={() => setEditing(false)} aria-label="Cancel">
              <X className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-end gap-2 min-w-0">
            <span className={`mono text-xs truncate ${low ? 'text-rose-300' : 'text-slate-100'}`}
              title={field.raw_value !== field.normalised_value
                ? `As printed: ${field.raw_value}` : undefined}>
              {field.normalised_value || '—'}
            </span>
            {corrected ? (
              <span className="chip border-violet-500/30 bg-violet-500/10 text-violet-300"
                title={`Corrected by a person${field.previous_value ? `, previously ${field.previous_value}` : ''}. A human reading a number off a page is ground truth, so confidence is pinned to 100%.`}>
                corrected
              </span>
            ) : (
              <ConfidenceDot value={field.confidence} />
            )}
            {!readOnly && (
              <button onClick={() => { setDraft(field.normalised_value ?? ''); setEditing(true); }}
                className="text-slate-600 hover:text-accent p-0.5" aria-label={`Edit ${fieldLabel(field.field_path)}`}>
                <Edit3 className="w-3 h-3" />
              </button>
            )}
          </div>
        )}
      </div>

      {field.raw_value && field.raw_value !== field.normalised_value && !editing && (
        <Disclosure summary="As printed on the document" className="mt-1 ml-32">
          <p className="mono text-[11px] text-slate-500">{field.raw_value}</p>
        </Disclosure>
      )}
    </div>
  );
}
