/**
 * Screen 5 — Decision Workspace (PRD 13.5).
 *
 * The vertical order is deliberate and is the whole design of this screen:
 * signals, then what matched, then what did not, then what could not be
 * checked, and only THEN the recommendation.
 *
 * If the recommendation appears first, reviewers rubber-stamp it. The override
 * rate falls, and with it the value of having a human in the loop at all.
 * Making the evidence arrive before the answer is the cheapest available
 * defence against automation bias.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertOctagon, CheckCircle2, ExternalLink, HelpCircle, ShieldAlert, Sparkles,
} from 'lucide-react';

import {
  ConfidenceBar, DecisionChip, Disclosure, RiskChip, SectionTitle, SeverityChip,
} from './ui';
import { money, confidencePct } from '../lib/format';

const SEVERITY_RANK = { BLOCKER: 0, CRITICAL: 1, WARNING: 2, INFO: 3 };

export default function DecisionPanel({
  invoice, decision, rules = [], explanation, securityFlags = [], poNumber,
  onEvidenceClick, onResolveField, actions,
}) {
  const failures = useMemo(() => rules
    .filter((r) => r.outcome === 'FAIL')
    .sort((a, b) => (SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])
      || Number(b.delta || 0) - Number(a.delta || 0)), [rules]);

  const warnings = useMemo(() => rules.filter((r) => r.outcome === 'WARN'), [rules]);
  const unevaluable = useMemo(() => rules.filter((r) => r.outcome === 'CANNOT_EVALUATE'), [rules]);
  const matched = useMemo(() => rules.filter((r) => r.outcome === 'PASS'), [rules]);

  if (!decision) {
    return (
      <div className="card p-8 text-center">
        <p className="text-sm text-slate-400">This invoice has not been validated yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Security banner sits above everything — a vendor embedding instructions
          in a document is a fact about the vendor, not about this invoice. */}
      {securityFlags.length > 0 && (
        <div className="p-3.5 rounded-md border border-fuchsia-500/40 bg-fuchsia-500/10">
          <div className="flex items-center gap-2 mb-2">
            <ShieldAlert className="w-4 h-4 text-fuchsia-400" aria-hidden="true" />
            <p className="text-sm font-medium text-fuchsia-200">
              Suspicious content detected in this document
            </p>
          </div>
          <p className="text-xs text-fuchsia-200/80 mb-2.5">
            Text addressed to an automated system was found in the document. It changed
            nothing: the rule engine is deterministic code that never reads free text,
            so no string in a PDF can reach a decision. The attempt is recorded and
            adds 30 to the risk score.
          </p>
          {securityFlags.map((flag, i) => (
            <div key={i} className="mb-1.5 last:mb-0">
              <p className="text-[11px] text-fuchsia-300">{flag.reason} · page {flag.page}</p>
              <p className="mono text-[11px] text-fuchsia-100/70 bg-ink-950/50 p-1.5 rounded mt-1
                            border border-fuchsia-500/20">
                “{flag.quote}”
              </p>
            </div>
          ))}
        </div>
      )}

      {/* 1. Signal strip */}
      <div className="card p-4 grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="col-span-2 md:col-span-1">
          <ConfidenceBar
            value={decision.decision_confidence}
            label="Decision confidence"
            breakdown={decision.confidence_breakdown}
          />
          <p className="text-[10px] text-slate-600 mt-1">derived, never generated</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 mb-1.5">Risk</p>
          <RiskChip band={decision.risk_band} score={decision.risk_score} />
        </div>
        <div>
          <p className="text-xs text-slate-500 mb-1.5">Amount</p>
          <p className="mono text-sm text-slate-100">{money(invoice.grand_total, invoice.currency)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 mb-1.5">Vendor</p>
          <p className="text-sm text-slate-200 truncate">{invoice.vendor_name || '—'}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500 mb-1.5">Purchase order</p>
          {poNumber ? (
            <Link to={`/pos/${poNumber}`}
              className="mono text-sm text-accent hover:underline flex items-center gap-1">
              {poNumber} <ExternalLink className="w-3 h-3" />
            </Link>
          ) : <p className="text-sm text-slate-500">—</p>}
        </div>
      </div>

      {/* 2. What matched */}
      <div className="card p-4">
        <SectionTitle hint={`${matched.length} check${matched.length === 1 ? '' : 's'} passed`}>
          What matched
        </SectionTitle>
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5">
          {matched.slice(0, 10).map((rule) => (
            <div key={rule.rule_id} className="flex items-start gap-2 text-xs">
              <CheckCircle2 className="w-3 h-3 text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />
              <span className="mono text-slate-500 w-14 shrink-0">{rule.rule_id}</span>
              <span className="text-slate-400 flex-1">{rule.name}</span>
            </div>
          ))}
        </div>
        {matched.length > 10 && (
          <Disclosure summary={`Show all ${matched.length} passing checks`} className="mt-3">
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5 pt-1">
              {matched.slice(10).map((rule) => (
                <div key={rule.rule_id} className="flex items-start gap-2 text-xs">
                  <CheckCircle2 className="w-3 h-3 text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />
                  <span className="mono text-slate-500 w-14 shrink-0">{rule.rule_id}</span>
                  <span className="text-slate-400 flex-1">{rule.name}</span>
                </div>
              ))}
            </div>
          </Disclosure>
        )}
      </div>

      {/* 3. What did not match */}
      {(failures.length > 0 || warnings.length > 0) && (
        <div className="card p-4">
          <SectionTitle hint="Sorted by severity, then financial impact.">
            What did not match
          </SectionTitle>
          <div className="space-y-2.5">
            {[...failures, ...warnings].map((rule) => (
              <Discrepancy key={rule.rule_id} rule={rule} explanation={explanation?.per_rule?.[rule.rule_id]}
                onEvidenceClick={onEvidenceClick} poNumber={poNumber} />
            ))}
          </div>
        </div>
      )}

      {/* 4. What could not be checked — never mixed with failures */}
      {unevaluable.length > 0 && (
        <div className="card p-4 border-sky-500/30">
          <SectionTitle
            hint="These are not findings against the invoice. A required input could not be read, so the check did not run."
          >
            <span className="flex items-center gap-2">
              <HelpCircle className="w-4 h-4 text-sky-400" aria-hidden="true" />
              What could not be checked
            </span>
          </SectionTitle>
          <div className="space-y-2">
            {unevaluable.map((rule) => {
              const blocker = rule.blocked_by?.[0];
              const fieldPath = blocker?.match(/(header\.[a-z_]+)/)?.[1]
                || (blocker?.includes('grand_total') ? 'header.grand_total' : null);
              return (
                <div key={rule.rule_id}
                  className="flex items-start justify-between gap-3 p-2.5 rounded-md
                             bg-sky-500/[0.06] border border-sky-500/20">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span aria-hidden="true" className="text-sky-300">⊘</span>
                      <span className="mono text-xs text-slate-500">{rule.rule_id}</span>
                      <span className="text-xs text-slate-200">{rule.name}</span>
                      <SeverityChip severity={rule.severity} />
                    </div>
                    {rule.blocked_by?.map((b) => (
                      <p key={b} className="text-[11px] text-sky-300/80 mono mt-1 ml-6">└─ {b}</p>
                    ))}
                  </div>
                  {fieldPath && onResolveField && (
                    <button className="btn-ghost py-1 px-2 text-xs shrink-0"
                      onClick={() => onResolveField(fieldPath)}>
                      Resolve
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 5. Recommendation — deliberately after the evidence */}
      <div className="card p-4 border-accent/30">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-200 mb-1.5">Recommendation</h3>
            <DecisionChip outcome={decision.outcome} size="lg" />
          </div>
          {decision.routed_to_role && (
            <div className="text-right">
              <p className="text-xs text-slate-500">Routed to</p>
              <p className="text-xs text-slate-300">{decision.routed_to_role.replace(/_/g, ' ')}</p>
            </div>
          )}
        </div>

        {explanation?.text && (
          <div className="p-3 rounded-md bg-ink-850 border border-ink-800 mb-3">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Sparkles className="w-3 h-3 text-violet-400" aria-hidden="true" />
              <span className="text-[11px] text-slate-500">
                {explanation.source === 'LLM'
                  ? `AI-generated summary of the checks above (${explanation.model})`
                  : 'Deterministic summary of the checks above'}
              </span>
            </div>
            <p className="text-sm text-slate-300 leading-relaxed">{explanation.text}</p>
            <p className="text-[10px] text-slate-600 mt-2">
              {explanation.source === 'LLM'
                ? 'Generated from the rule results only — never from the document. Every number in it was checked against the rule JSON before it was shown.'
                : 'Rendered from the rule results by template. No model was involved.'}
            </p>
          </div>
        )}

        <Disclosure summary="Reasoning — the rules that produced this outcome">
          <div className="flex flex-wrap gap-1.5 pt-1">
            {(decision.reason_codes || []).map((code) => (
              <span key={code} className="chip border-ink-700 bg-ink-850 text-slate-300 mono">
                {code}
              </span>
            ))}
            {(!decision.reason_codes || decision.reason_codes.length === 0) && (
              <span className="text-xs text-slate-500">
                No failures, no warnings, nothing unevaluable — every applicable check passed.
              </span>
            )}
          </div>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
            <Tally label="Blockers failed" value={decision.blocker_count} />
            <Tally label="Critical failed" value={decision.critical_fail_count} />
            <Tally label="Warnings" value={decision.warning_count} />
            <Tally label="Could not check" value={decision.cannot_evaluate_count} />
          </div>
        </Disclosure>

        {decision.risk_breakdown?.length > 0 && (
          <Disclosure summary={`Risk score derivation (${decision.risk_score} of 100)`} className="mt-2">
            <ul className="space-y-1 pt-1">
              {decision.risk_breakdown.map((entry, i) => (
                <li key={i} className="text-[11px] text-slate-400 flex gap-2">
                  <span className="mono text-rose-300 w-8 shrink-0">+{entry.points}</span>
                  <span>{entry.reason}</span>
                </li>
              ))}
            </ul>
            <p className="text-[10px] text-slate-600 mt-2">
              Risk measures exposure, not uncertainty. You can be entirely confident
              that an invoice is fraudulent.
            </p>
          </Disclosure>
        )}
      </div>

      {/* 6. Actions */}
      {actions}
    </div>
  );
}

function Discrepancy({ rule, explanation, onEvidenceClick, poNumber }) {
  const [open, setOpen] = useState(false);
  const isFail = rule.outcome === 'FAIL';

  return (
    <div className={`p-3 rounded-md border ${isFail
      ? 'border-rose-500/30 bg-rose-500/[0.06]'
      : 'border-amber-500/30 bg-amber-500/[0.06]'}`}>
      <div className="flex items-start gap-2.5">
        {isFail
          ? <AlertOctagon className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />
          : <span className="text-amber-400 shrink-0" aria-hidden="true">⚠</span>}

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="mono text-xs text-slate-400">{rule.rule_id}</span>
            <span className="text-sm text-slate-200">{rule.name}</span>
            <SeverityChip severity={rule.severity} />
          </div>

          <p className={`text-xs mt-1.5 ${isFail ? 'text-rose-200' : 'text-amber-200'}`}>
            {rule.message}
          </p>

          {(rule.expected_value || rule.actual_value) && (
            <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2">
              <Cell label="Expected" value={rule.expected_value} />
              <Cell label="Actual" value={rule.actual_value} />
              <Cell label="Delta" value={rule.delta
                ? `${rule.delta}${rule.delta_pct ? ` (${rule.delta_pct}%)` : ''}` : null}
                tone={isFail ? 'text-rose-300' : 'text-amber-300'} />
              <Cell label="Threshold" value={rule.threshold_applied} />
            </div>
          )}

          {explanation && (
            <p className="text-xs text-slate-400 mt-2 pl-2 border-l-2 border-ink-700">
              {explanation}
            </p>
          )}

          <div className="flex items-center gap-3 mt-2">
            <button onClick={() => setOpen((v) => !v)}
              className="text-[11px] text-slate-500 hover:text-slate-300">
              {open ? 'Hide evidence' : 'Show evidence'}
            </button>
            {rule.rule_id === 'PO-07' && poNumber && (
              <Link to={`/pos/${poNumber}`} className="text-[11px] text-accent hover:underline">
                Open consumption ledger →
              </Link>
            )}
          </div>

          {open && (
            <pre className="mt-2 text-[10px] text-slate-400 bg-ink-950 p-2 rounded
                            border border-ink-800 overflow-x-auto max-h-64">
              {JSON.stringify(rule.evidence, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function Cell({ label, value, tone = 'text-slate-200' }) {
  if (!value) return <div />;
  return (
    <div>
      <p className="text-[10px] text-slate-600 uppercase tracking-wide">{label}</p>
      <p className={`mono text-xs ${tone} break-words`}>{value}</p>
    </div>
  );
}

function Tally({ label, value }) {
  return (
    <div className="p-2 rounded bg-ink-850 border border-ink-800">
      <p className="text-slate-500">{label}</p>
      <p className="mono text-slate-200 text-sm">{value ?? 0}</p>
    </div>
  );
}
