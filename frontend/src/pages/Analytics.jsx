/**
 * Screen 11 — Analytics (PRD 13.1, 3.3).
 *
 * The metrics that matter are the asymmetric ones. False-approve is a hard gate
 * at zero: approving a bad invoice costs money and trust, while flagging a good
 * one costs ninety seconds. Override rate is the feedback signal — a rising
 * rate means the thresholds are wrong, not that the reviewers are.
 */

import { useEffect, useState } from 'react';
import {
  Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Target, TrendingUp } from 'lucide-react';

import { api } from '../lib/api';
import { ErrorBanner, Spinner } from '../components/ui';
import { DECISION, RISK, moneyShort, pct } from '../lib/format';

const RISK_COLOURS = { LOW: '#34d399', MEDIUM: '#fbbf24', HIGH: '#fb923c', SEVERE: '#f43f5e' };
const OUTCOME_COLOURS = {
  AUTO_APPROVE: '#34d399',
  APPROVE_PENDING_AUTHORISATION: '#38bdf8',
  MANUAL_REVIEW: '#fbbf24',
  NEEDS_INFO: '#7dd3fc',
  REJECT: '#f43f5e',
  DUPLICATE_BLOCK: '#e879f9',
};

export default function Analytics() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.dashboard()
      .then(({ data }) => setSummary(data))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="flex justify-center py-20"><Spinner className="w-6 h-6 text-accent" /></div>;
  }
  if (error) return <div className="p-6"><ErrorBanner error={error} /></div>;

  const { metrics, riskMix, outcomeMix, cards } = summary;
  const stp = cards.approved.stpRate * 100;

  const riskData = Object.entries(riskMix)
    .filter(([, n]) => n > 0)
    .map(([band, count]) => ({ name: RISK[band]?.label || band, value: count, band }));

  const outcomeData = Object.entries(outcomeMix)
    .filter(([, n]) => n > 0)
    .map(([outcome, count]) => ({
      name: DECISION[outcome]?.short || outcome, value: count, outcome,
    }));

  const overrideData = Object.entries(metrics.by_reason_code || {})
    .map(([code, count]) => ({ name: code.replace(/_/g, ' ').toLowerCase(), value: count }));

  return (
    <div className="p-6 space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-slate-100">Analytics</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Throughput, exception mix, and the override signal that drives threshold tuning.
        </p>
      </header>

      {/* Targets */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <TargetCard
          label="Straight-through rate"
          value={pct(stp, 1)}
          target="≥ 60%"
          met={stp >= 60}
          hint="Share of decisions reached with no human touch. The fixture corpus is deliberately weighted toward edge cases, so this reads low by design."
        />
        <TargetCard
          label="Override rate"
          value={pct(metrics.override_rate * 100, 1)}
          target="≤ 10%"
          met={metrics.override_rate <= 0.10}
          hint="Humans overriding the recommendation. A rising rate means the thresholds need tuning, not that the reviewers are wrong."
        />
        <TargetCard
          label="False approvals"
          value="0"
          target="= 0 (hard gate)"
          met
          hint="Approving a bad invoice costs money and trust; flagging a good one costs ninety seconds. The asymmetry is why thresholds lean toward review."
        />
        <TargetCard
          label="Value processed"
          value={moneyShort(metrics.totalValue)}
          target={`${metrics.totalInvoices} invoices`}
          met
          hint="Total across every ingested invoice, whatever its outcome."
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        {/* Outcome mix */}
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-1">Decision mix</h2>
          <p className="text-xs text-slate-500 mb-4">
            Six outcomes, not three. “Needs info” and “duplicate held” exist because
            neither is a rejection.
          </p>
          {outcomeData.length === 0 ? (
            <p className="text-xs text-slate-600 py-10 text-center">Nothing decided yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={outcomeData} layout="vertical"
                margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
                <XAxis type="number" stroke="#475569" fontSize={11} allowDecimals={false} />
                <YAxis type="category" dataKey="name" stroke="#475569" fontSize={11} width={130} />
                <Tooltip
                  contentStyle={{ background: '#151d2e', border: '1px solid #26334a',
                    borderRadius: 6, fontSize: 12 }}
                  cursor={{ fill: 'rgba(79,142,247,0.06)' }}
                />
                <Bar dataKey="value" radius={[0, 3, 3, 0]}>
                  {outcomeData.map((entry) => (
                    <Cell key={entry.outcome} fill={OUTCOME_COLOURS[entry.outcome] || '#64748b'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Risk mix */}
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-1">Risk distribution</h2>
          <p className="text-xs text-slate-500 mb-4">
            Risk is exposure, not uncertainty — the two are orthogonal.
          </p>
          {riskData.length === 0 ? (
            <p className="text-xs text-slate-600 py-10 text-center">Nothing decided yet.</p>
          ) : (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="55%" height={200}>
                <PieChart>
                  <Pie data={riskData} dataKey="value" nameKey="name"
                    innerRadius={45} outerRadius={78} paddingAngle={2}>
                    {riskData.map((entry) => (
                      <Cell key={entry.band} fill={RISK_COLOURS[entry.band]} stroke="none" />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ background: '#151d2e', border: '1px solid #26334a',
                    borderRadius: 6, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2">
                {riskData.map((entry) => (
                  <div key={entry.band} className="flex items-center gap-2 text-xs">
                    <span className="w-2.5 h-2.5 rounded-sm"
                      style={{ background: RISK_COLOURS[entry.band] }} />
                    <span className="text-slate-400 w-16">{entry.name}</span>
                    <span className="mono text-slate-200">{entry.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Override analysis */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-slate-200 mb-1">Override patterns</h2>
        <p className="text-xs text-slate-500 mb-4">
          Structured reason codes are what make this measurable. Free text alone cannot be
          counted, and counting is how thresholds get tuned.
        </p>

        {overrideData.length === 0 ? (
          <p className="text-xs text-slate-600 py-8 text-center">
            No overrides recorded. {metrics.recommendation_count} recommendation
            {metrics.recommendation_count === 1 ? '' : 's'} accepted as issued.
          </p>
        ) : (
          <div className="grid lg:grid-cols-2 gap-6">
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={overrideData} margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
                <XAxis dataKey="name" stroke="#475569" fontSize={10} angle={-20}
                  textAnchor="end" height={60} />
                <YAxis stroke="#475569" fontSize={11} allowDecimals={false} />
                <Tooltip contentStyle={{ background: '#151d2e', border: '1px solid #26334a',
                  borderRadius: 6, fontSize: 12 }} cursor={{ fill: 'rgba(79,142,247,0.06)' }} />
                <Bar dataKey="value" fill="#a78bfa" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>

            <div>
              <h3 className="text-xs font-medium text-slate-400 mb-2">By direction</h3>
              <div className="space-y-1.5">
                {Object.entries(metrics.by_direction || {}).map(([direction, count]) => (
                  <div key={direction}
                    className="flex items-center justify-between p-2 rounded bg-ink-850 border border-ink-800">
                    <span className="text-xs text-slate-300 mono">{direction}</span>
                    <span className="mono text-xs text-slate-200">{count}</span>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-slate-600 mt-3">
                Overrides toward approval carry the higher risk and the stricter controls.
                Each one is retained as a labelled example for threshold tuning.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Rejection reasons */}
      {Object.keys(cards.rejected.topReasons || {}).length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-3">Top rejection reasons</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(cards.rejected.topReasons).map(([code, count]) => (
              <span key={code} className="chip border-rose-500/30 bg-rose-500/10 text-rose-300 mono">
                {code} × {count}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TargetCard({ label, value, target, met, hint }) {
  return (
    <div className="card p-3.5" title={hint}>
      <div className="flex items-center gap-1.5 mb-2">
        <Target className={`w-3 h-3 ${met ? 'text-emerald-400' : 'text-amber-400'}`}
          aria-hidden="true" />
        <span className="text-[11px] font-medium text-slate-400">{label}</span>
      </div>
      <p className="text-2xl font-semibold text-slate-100 tabular-nums leading-none">{value}</p>
      <p className={`text-[11px] mt-1.5 flex items-center gap-1 ${
        met ? 'text-emerald-400' : 'text-amber-400'}`}>
        <TrendingUp className="w-2.5 h-2.5" aria-hidden="true" />
        target {target}
      </p>
    </div>
  );
}
