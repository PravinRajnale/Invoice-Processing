/**
 * Single-field verification card — Edge Case 2.
 *
 * The naive responses to a low-confidence field are all wrong: accept it and a
 * guessed number enters a payment decision; reject and you punish a vendor for
 * a bad scan; ask for a full re-key and you discard thirty fields that were
 * read perfectly.
 *
 * The right response is to ask for exactly one field, show the cropped region
 * at high zoom, offer the OCR candidates as one-click buttons, and surface the
 * corroborating arithmetic as a hint. Target reviewer time: about eight seconds.
 */

import { useMemo, useState } from 'react';
import { Check, ScanLine, Sparkles } from 'lucide-react';

import { Spinner } from './ui';
import { api } from '../lib/api';
import { fieldLabel, money } from '../lib/format';

export default function FieldVerification({
  invoiceId, field, extraction, onConfirm, onDismiss,
}) {
  const [value, setValue] = useState(field?.normalised_value ?? '');
  const [busy, setBusy] = useState(false);

  const candidates = field?.candidates || [];

  // Corroborating arithmetic: subtotal + tax should equal the grand total. It
  // supports a reading without proving it, and saying so plainly is more useful
  // to a reviewer than either asserting or omitting it.
  const corroboration = useMemo(() => {
    if (field?.field_path !== 'header.grand_total') return null;
    const byPath = Object.fromEntries((extraction?.fields || []).map((f) => [f.field_path, f]));
    const subtotal = byPath['header.subtotal']?.normalised_value;
    const tax = byPath['header.tax_amount']?.normalised_value;
    if (!subtotal || !tax) return null;

    // Display-only arithmetic; the engine re-does this in Decimal for FIN-01.
    const sum = (Number(subtotal) + Number(tax)).toFixed(2);
    const match = candidates.find((c) => Number(c.value.replace(/,/g, '')).toFixed(2) === sum);
    return { subtotal, tax, sum, matchesCandidate: match?.value };
  }, [field, extraction, candidates]);

  if (!field) return null;

  const cropUrl = field.bbox
    ? api.pageUrl(invoiceId, field.page_number || 1, 260)
    : null;

  async function confirm(v) {
    setBusy(true);
    try {
      await onConfirm?.(field.field_path, v);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4 border-sky-500/40 bg-sky-500/[0.04]">
      <div className="flex items-center gap-2 mb-1">
        <ScanLine className="w-4 h-4 text-sky-400" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-slate-100">
          Confirm one field: {fieldLabel(field.field_path)}
        </h3>
      </div>
      <p className="text-xs text-slate-400 mb-3">
        Read at {(Number(field.confidence) * 100).toFixed(0)}% confidence, below the 80% floor.
        Everything else on this invoice read cleanly — you only need to confirm this one value.
      </p>

      {/* Cropped region at high zoom */}
      {cropUrl && field.bbox && (
        <div className="mb-3">
          <p className="text-[11px] text-slate-500 mb-1.5">
            Page {field.page_number || 1}, as scanned
          </p>
          <div className="overflow-hidden rounded border border-ink-700 bg-white"
            style={{ height: 90 }}>
            <div style={{
              width: `${100 / Math.max(field.bbox.w, 0.02)}%`,
              transform: `translate(${-field.bbox.x * (100 / Math.max(field.bbox.w, 0.02))}%, ${
                -field.bbox.y * (100 / Math.max(field.bbox.h, 0.02)) * (field.bbox.h / field.bbox.w) * 0.4}%)`,
              transformOrigin: 'top left',
            }}>
              <img src={cropUrl} alt={`Region containing ${fieldLabel(field.field_path)}`}
                className="w-full block" />
            </div>
          </div>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="mb-3">
          <p className="text-[11px] text-slate-500 mb-1.5">Candidate readings — one click to accept</p>
          <div className="flex flex-wrap gap-2">
            {candidates.map((candidate) => {
              const corroborated = corroboration?.matchesCandidate === candidate.value;
              return (
                <button
                  key={candidate.value}
                  onClick={() => confirm(candidate.value.replace(/,/g, ''))}
                  disabled={busy}
                  className={`px-3 py-2 rounded-md border text-left transition-colors
                    ${corroborated
                      ? 'border-emerald-500/50 bg-emerald-500/10 hover:bg-emerald-500/20'
                      : 'border-ink-700 bg-ink-850 hover:border-accent/50'}`}
                >
                  <p className="mono text-sm text-slate-100">{candidate.value}</p>
                  <p className="text-[10px] text-slate-500 mt-0.5">
                    confidence {(Number(candidate.confidence) * 100).toFixed(0)}%
                    {corroborated && <span className="text-emerald-400"> · arithmetic agrees</span>}
                  </p>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {corroboration && (
        <div className="mb-3 p-2.5 rounded bg-ink-850 border border-ink-800 flex items-start gap-2">
          <Sparkles className="w-3 h-3 text-slate-500 mt-0.5 shrink-0" aria-hidden="true" />
          <p className="text-[11px] text-slate-400">
            Subtotal <span className="mono text-slate-300">{money(corroboration.subtotal)}</span>
            {' + '}tax <span className="mono text-slate-300">{money(corroboration.tax)}</span>
            {' = '}<span className="mono text-slate-200">{money(corroboration.sum)}</span>.
            {corroboration.matchesCandidate
              ? ` This supports ${corroboration.matchesCandidate}, though it does not prove it — both figures were read from the same scan.`
              : ' This does not match either candidate, which is itself worth a closer look.'}
          </p>
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="label" htmlFor="verify-value">Or enter the value yourself</label>
          <input id="verify-value" className="input mono" value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && confirm(value)} />
        </div>
        <button className="btn-primary" onClick={() => confirm(value)} disabled={busy || !value.trim()}>
          {busy ? <Spinner /> : <Check className="w-4 h-4" aria-hidden="true" />}
          Confirm and re-check
        </button>
        {onDismiss && (
          <button className="btn-ghost" onClick={onDismiss} disabled={busy}>Later</button>
        )}
      </div>

      <p className="text-[10px] text-slate-600 mt-2.5">
        On confirmation the field is pinned to 100% confidence and marked human-corrected,
        and only the rules that were blocked on it are re-evaluated. Both validation runs
        are retained.
      </p>
    </div>
  );
}
