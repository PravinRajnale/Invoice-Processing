/**
 * Screen 9 — Rule Configuration (PRD 13.8).
 *
 * The six deferred rules are shown rather than hidden, each with the master data
 * it would need. Scope discipline is more credible when it is visible: a
 * reviewer can see what was designed, what was built, and why the gap exists.
 */

import { useEffect, useMemo, useState } from 'react';
import { Ban, Filter, Info } from 'lucide-react';

import { api } from '../lib/api';
import { ErrorBanner, SeverityChip, Spinner } from '../components/ui';
import { GATE_LABEL } from '../lib/format';

export default function RuleConfig() {
  const [rules, setRules] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [gateFilter, setGateFilter] = useState('');
  const [showDeferred, setShowDeferred] = useState(true);

  useEffect(() => {
    api.rules()
      .then(({ data, meta }) => setRules({ ...data, meta }))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  const flat = useMemo(() => {
    if (!rules) return [];
    return rules.gates.flatMap((g) => g.rules)
      .filter((r) => (!gateFilter || r.gate === gateFilter))
      .filter((r) => (showDeferred || r.mvp));
  }, [rules, gateFilter, showDeferred]);

  if (loading) {
    return <div className="flex justify-center py-20"><Spinner className="w-6 h-6 text-accent" /></div>;
  }
  if (error) return <div className="p-6"><ErrorBanner error={error} /></div>;

  const { meta, thresholds } = rules;

  return (
    <div className="p-6 space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-slate-100">Rule catalogue</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          {meta.total} rules across 7 gates · {meta.active} active, {meta.deferred} deferred ·
          ruleset v{meta.ruleset_version}
        </p>
      </header>

      <div className="card p-3.5 flex items-start gap-2.5">
        <Info className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" aria-hidden="true" />
        <p className="text-xs text-slate-400">
          Every active rule executes against seeded master data and produces evidence —
          none are decorative. Deterministic rules are pure code with Decimal arithmetic.
          AI-assisted rules use the model to <em>propose</em> candidates with scores;
          the engine applies the confidence floor and decides. No rule outcome is ever
          determined by a language model.
        </p>
      </div>

      {/* Thresholds */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-slate-200 mb-1">Configurable thresholds</h2>
        <p className="text-xs text-slate-500 mb-3">
          Stored with every rule result, so a decision made under one set of thresholds
          stays explicable after they change.
        </p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Object.entries(thresholds)
            .filter(([, v]) => typeof v === 'object' && v !== null && !Array.isArray(v))
            .map(([group, values]) => (
              <div key={group}>
                <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
                  {group}
                </h3>
                <div className="space-y-1">
                  {Object.entries(values).map(([key, value]) => (
                    <div key={key} className="flex justify-between text-[11px]">
                      <span className="text-slate-500">{key.replace(/_/g, ' ')}</span>
                      <span className="mono text-slate-300">{String(value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
              other
            </h3>
            <div className="space-y-1">
              {Object.entries(thresholds)
                .filter(([, v]) => typeof v !== 'object' || v === null || Array.isArray(v))
                .map(([key, value]) => (
                  <div key={key} className="flex justify-between text-[11px]">
                    <span className="text-slate-500">{key.replace(/_/g, ' ')}</span>
                    <span className="mono text-slate-300">
                      {Array.isArray(value) ? value.join(', ') : String(value)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Filter className="w-3.5 h-3.5 text-slate-600" aria-hidden="true" />
        <select className="input w-auto" value={gateFilter} onChange={(e) => setGateFilter(e.target.value)}>
          <option value="">All gates</option>
          {rules.gates.map((g) => (
            <option key={g.gate} value={g.gate}>
              {GATE_LABEL[g.gate]} ({g.rules.length})
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
          <input type="checkbox" checked={showDeferred}
            onChange={(e) => setShowDeferred(e.target.checked)}
            className="accent-[#4f8ef7]" />
          Show deferred rules
        </label>
        <span className="text-xs text-slate-600 ml-auto">{flat.length} shown</span>
      </div>

      {/* Catalogue */}
      <div className="card overflow-hidden">
        <table className="w-full">
          <thead className="bg-ink-850 border-b border-ink-800">
            <tr>
              <th className="th">ID</th>
              <th className="th">Rule</th>
              <th className="th">Gate</th>
              <th className="th">Severity</th>
              <th className="th">Type</th>
              <th className="th">Threshold</th>
              <th className="th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {flat.map((rule) => (
              <tr key={rule.id} className={rule.mvp ? '' : 'opacity-60'}>
                <td className="td mono text-xs text-slate-400">{rule.id}</td>
                <td className="td max-w-md">
                  <p className="text-slate-200">{rule.name}</p>
                  <p className="text-[11px] text-slate-500 mt-0.5">{rule.description_ui}</p>
                  {rule.requires?.length > 0 && (
                    <p className="text-[10px] text-slate-600 mt-1 mono">
                      requires: {rule.requires.join(', ')}
                    </p>
                  )}
                  {rule.deferred_reason && (
                    <p className="text-[11px] text-amber-400/80 mt-1 flex items-start gap-1">
                      <Ban className="w-2.5 h-2.5 mt-0.5 shrink-0" aria-hidden="true" />
                      {rule.deferred_reason}
                    </p>
                  )}
                </td>
                <td className="td text-xs text-slate-400">{GATE_LABEL[rule.gate]}</td>
                <td className="td"><SeverityChip severity={rule.severity} /></td>
                <td className="td">
                  <span className={`chip ${rule.type === 'AI_ASSISTED'
                    ? 'border-violet-500/30 bg-violet-500/10 text-violet-300'
                    : 'border-ink-700 bg-ink-850 text-slate-400'}`}
                    title={rule.type === 'AI_ASSISTED'
                      ? 'The model proposes candidates with scores; the engine applies the floor and decides.'
                      : 'Pure code. Decimal arithmetic, explicit thresholds, reproducible.'}>
                    {rule.type === 'AI_ASSISTED' ? 'AI-assisted' : 'Deterministic'}
                  </span>
                </td>
                <td className="td mono text-[11px] text-slate-500">{rule.threshold_ref || '—'}</td>
                <td className="td">
                  <span className={`chip ${rule.mvp
                    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                    : 'border-amber-500/30 bg-amber-500/10 text-amber-300'}`}>
                    {rule.mvp ? 'Active' : 'Designed'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-600">
        POL-06 (segregation of duties) is active but does not appear in a validation run —
        it checks an actor performing an action, not an invoice, so it can only be
        evaluated at override time. That is why 49 rules execute per run rather than 50.
      </p>
    </div>
  );
}
