/**
 * Screen 4 — Validation Live View (PRD 13.4).
 *
 * Rules stream in by gate, in evaluation order, each row moving from pending to
 * evaluating to resolved. This is the screen that answers the brief's
 * "everything that happened in between visible".
 *
 * The ⊘ glyph must be unmistakably different from ✗. "I could not check this"
 * and "this is wrong" are different statements, and conflating them is the
 * defining failure of naive document AI — that distinction is the entire point
 * of Edge Case 2.
 */

import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';

import { Disclosure, SeverityChip } from './ui';
import { GATE_LABEL, OUTCOME } from '../lib/format';

const GATE_ORDER = [
  'INGEST', 'EXTRACTION', 'VENDOR', 'PURCHASE_ORDER',
  'FINANCIAL', 'LINE_ITEMS', 'DUPLICATES', 'POLICY',
];

export default function ValidationLive({
  rules = [], catalogue = [], streaming, onEvidenceClick, poNumber, invoiceId,
}) {
  const byId = useMemo(() => Object.fromEntries(rules.map((r) => [r.rule_id, r])), [rules]);

  const gates = useMemo(() => {
    const active = catalogue.filter((spec) => spec.mvp && spec.id !== 'POL-06');
    return GATE_ORDER.map((gate) => {
      const specs = active.filter((s) => s.gate === gate);
      const results = specs.map((spec) => byId[spec.id]).filter(Boolean);
      return { gate, specs, results };
    }).filter((g) => g.specs.length);
  }, [catalogue, byId]);

  const tally = useMemo(() => {
    const counts = { PASS: 0, FAIL: 0, WARN: 0, CANNOT_EVALUATE: 0, NOT_APPLICABLE: 0 };
    rules.forEach((r) => { counts[r.outcome] = (counts[r.outcome] || 0) + 1; });
    return counts;
  }, [rules]);

  const totalActive = catalogue.filter((s) => s.mvp && s.id !== 'POL-06').length;

  return (
    <div className="space-y-3">
      {/* Progress header */}
      <div className="card px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs sticky top-0 z-10">
        <span className="text-slate-300 font-medium">
          {rules.length} of {totalActive || '—'} checks
          {streaming ? ' running…' : ' complete'}
        </span>
        <span className="text-slate-700">·</span>
        {Object.entries(tally).filter(([, n]) => n > 0).map(([outcome, n]) => {
          const spec = OUTCOME[outcome];
          return (
            <span key={outcome} className={`flex items-center gap-1 ${spec.className.split(' ')[0]}`}>
              <span aria-hidden="true">{spec.glyph}</span>
              {n} {spec.label.toLowerCase()}
            </span>
          );
        })}
      </div>

      {gates.map(({ gate, specs, results }) => {
        const done = results.length;
        return (
          <div key={gate} className="card overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-ink-850 border-b border-ink-800">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                {GATE_LABEL[gate]}
              </h3>
              <span className="text-[11px] text-slate-500 mono">{done}/{specs.length}</span>
            </div>

            <div className="divide-y divide-ink-850">
              {specs.map((spec) => {
                const result = byId[spec.id];
                if (!result) {
                  return (
                    <div key={spec.id}
                      className={`px-3 py-2 flex items-center gap-3 ${
                        streaming && done === specs.findIndex((s) => s.id === spec.id)
                          ? 'animate-evaluating' : ''}`}>
                      <span className="text-slate-700 w-4 text-center" aria-hidden="true">·</span>
                      <span className="mono text-xs text-slate-600 w-16">{spec.id}</span>
                      <span className="text-xs text-slate-600 flex-1">{spec.name}</span>
                      <span className="text-[11px] text-slate-700">
                        {streaming ? 'queued' : 'not run'}
                      </span>
                    </div>
                  );
                }
                return (
                  <RuleRow key={spec.id} spec={spec} result={result}
                    onEvidenceClick={onEvidenceClick} poNumber={poNumber}
                    invoiceId={invoiceId} />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RuleRow({ spec, result, onEvidenceClick, poNumber, invoiceId }) {
  const outcome = OUTCOME[result.outcome] || OUTCOME.NOT_APPLICABLE;
  const notable = ['FAIL', 'WARN', 'CANNOT_EVALUATE'].includes(result.outcome);

  return (
    <div className={`px-3 py-2 ${result.outcome === 'FAIL' ? 'bg-rose-500/[0.04]'
      : result.outcome === 'CANNOT_EVALUATE' ? 'bg-sky-500/[0.04]' : ''}`}>
      <div className="flex items-start gap-3">
        <span className={`w-4 text-center shrink-0 ${outcome.className.split(' ')[0]}`}
          title={outcome.label} aria-label={outcome.label}>
          {outcome.glyph}
        </span>
        <span className="mono text-xs text-slate-500 w-16 shrink-0 pt-0.5">{result.rule_id}</span>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`text-xs ${notable ? 'text-slate-200' : 'text-slate-400'}`}>
              {result.name}
            </span>
            {notable && <SeverityChip severity={result.severity} />}
            {spec.type === 'AI_ASSISTED' && (
              <span className="chip border-violet-500/30 bg-violet-500/10 text-violet-300"
                title="The model proposes candidates with scores; the engine applies the confidence floor and decides. The model never determines this outcome.">
                AI-assisted
              </span>
            )}
          </div>

          {notable && result.message && (
            <p className={`text-xs mt-1 ${
              result.outcome === 'FAIL' ? 'text-rose-200'
                : result.outcome === 'CANNOT_EVALUATE' ? 'text-sky-200' : 'text-amber-200'}`}>
              {result.message}
            </p>
          )}

          {/* Blocked-by is what makes CANNOT_EVALUATE actionable rather than
              merely honest: it names the one input to fix. */}
          {result.blocked_by?.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {result.blocked_by.map((blocker) => (
                <li key={blocker} className="text-[11px] text-sky-300/80 flex items-start gap-1.5">
                  <span aria-hidden="true">└─</span>
                  <span className="mono">{blocker}</span>
                </li>
              ))}
            </ul>
          )}

          {notable && (result.expected_value || result.actual_value) && (
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px]">
              {result.expected_value && (
                <span className="text-slate-500">
                  expected <span className="mono text-slate-300">{result.expected_value}</span>
                </span>
              )}
              {result.actual_value && (
                <span className="text-slate-500">
                  actual <span className="mono text-slate-300">{result.actual_value}</span>
                </span>
              )}
              {result.delta && (
                <span className="text-slate-500">
                  delta <span className="mono text-rose-300">{result.delta}</span>
                  {result.delta_pct && <span className="mono text-rose-300"> ({result.delta_pct}%)</span>}
                </span>
              )}
              {result.threshold_applied && (
                <span className="text-slate-500"
                  title="The threshold in force when this ran. Stored with the result so the decision stays explicable after thresholds change.">
                  threshold <span className="mono text-slate-400">{result.threshold_applied}</span>
                </span>
              )}
            </div>
          )}

          {notable && result.evidence && Object.keys(result.evidence).length > 0 && (
            <div className="mt-1.5 flex items-center gap-3">
              <Disclosure summary="Evidence">
                <pre className="text-[10px] text-slate-400 bg-ink-950 p-2 rounded border border-ink-800
                                overflow-x-auto max-h-56">
                  {JSON.stringify(result.evidence, null, 2)}
                </pre>
              </Disclosure>

              {result.rule_id === 'PO-07' && poNumber && (
                <Link to={`/pos/${poNumber}`}
                  className="text-[11px] text-accent hover:underline flex items-center gap-1">
                  View consumption ledger <ExternalLink className="w-2.5 h-2.5" />
                </Link>
              )}
              {result.rule_id.startsWith('DUP') && invoiceId && (
                <Link to={`/invoices/${invoiceId}/duplicates`}
                  className="text-[11px] text-accent hover:underline flex items-center gap-1">
                  Compare side by side <ExternalLink className="w-2.5 h-2.5" />
                </Link>
              )}
              {Object.keys(result.evidence).some((k) => k.startsWith('header.')) && (
                <button
                  onClick={() => onEvidenceClick?.(
                    Object.keys(result.evidence).find((k) => k.startsWith('header.')),
                  )}
                  className="text-[11px] text-accent hover:underline">
                  Show on document
                </button>
              )}
            </div>
          )}
        </div>

        {result.duration_ms !== undefined && (
          <span className="text-[10px] text-slate-700 mono shrink-0 pt-0.5">
            {result.duration_ms}ms
          </span>
        )}
      </div>
    </div>
  );
}
