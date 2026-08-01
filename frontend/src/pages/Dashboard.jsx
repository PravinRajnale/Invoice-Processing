/**
 * Screen 1 — Dashboard (PRD 13.2).
 *
 * The queue sorts by risk descending, not by arrival. AP queues are worked FIFO
 * by habit rather than by design; putting the highest exposure first is the
 * single change that makes the queue reflect what actually matters.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, Clock, CopyCheck, FileQuestion, Inbox, RefreshCw, Search,
  ThumbsDown, ThumbsUp,
} from 'lucide-react';

import { api, session } from '../lib/api';
import {
  ConfidenceBar, DecisionChip, Empty, ErrorBanner, RiskChip, Spinner, StatusChip,
} from '../components/ui';
import { money, moneyShort, pct, relativeAge } from '../lib/format';

const CARDS = [
  {
    key: 'pendingReview', label: 'Pending review', icon: AlertTriangle,
    status: 'PENDING_REVIEW', accent: 'text-amber-400',
    hint: 'A material check failed, or warnings clustered.',
  },
  {
    key: 'pendingApproval', label: 'Pending approval', icon: ThumbsUp,
    status: 'PENDING_APPROVAL', accent: 'text-sky-400',
    hint: 'Clean, but above the unattended approval ceiling.',
  },
  {
    key: 'needsInfo', label: 'Needs info', icon: FileQuestion,
    status: 'NEEDS_INFO', accent: 'text-sky-300',
    hint: 'Something could not be read. Not a finding against the invoice.',
  },
  {
    key: 'duplicatesHeld', label: 'Duplicates held', icon: CopyCheck,
    status: 'DUPLICATE_HELD', accent: 'text-fuchsia-400',
    hint: 'Held, never auto-rejected — the first one may have been the error.',
  },
  {
    key: 'approved', label: 'Approved', icon: ThumbsUp,
    status: 'APPROVED', accent: 'text-emerald-400',
    hint: 'Straight-through rate is the share decided with no human touch.',
  },
  {
    key: 'rejected', label: 'Rejected', icon: ThumbsDown,
    status: 'REJECTED', accent: 'text-rose-400',
    hint: 'A blocking check failed on something that cannot be satisfied.',
  },
];

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({ status: '', riskBand: '', search: '', sort: 'risk' });
  const [cursor, setCursor] = useState(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [dash, list] = await Promise.all([
        api.dashboard(),
        api.invoices({ ...filters, pageSize: 100 }),
      ]);
      setSummary(dash.data);
      setInvoices(list.data);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  // Keyboard-first queue navigation (PRD 13.9).
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === 'j') setCursor((c) => Math.min(c + 1, invoices.length - 1));
      else if (e.key === 'k') setCursor((c) => Math.max(c - 1, 0));
      else if (e.key === 'Enter' && invoices[cursor]) navigate(`/invoices/${invoices[cursor].id}`);
      else if (e.key === '/') {
        e.preventDefault();
        document.getElementById('queue-search')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [invoices, cursor, navigate]);

  const metrics = summary?.metrics;

  const blockedFieldSummary = useMemo(() => {
    const fields = summary?.cards?.needsInfo?.blockedFields || {};
    return Object.entries(fields)
      .map(([name, count]) => `${name.replace(/^invoice\.|^confidence\./, '')} ×${count}`)
      .join(', ');
  }, [summary]);

  return (
    <div className="p-6 space-y-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Invoice queue</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Sorted by risk, then age — highest exposure first.
            Press <kbd className="px-1 py-0.5 rounded bg-ink-800 text-[11px] mono">J</kbd>/
            <kbd className="px-1 py-0.5 rounded bg-ink-800 text-[11px] mono">K</kbd> to move,
            <kbd className="px-1 py-0.5 rounded bg-ink-800 text-[11px] mono ml-1">Enter</kbd> to open.
          </p>
        </div>
        <button onClick={load} className="btn-ghost" disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh
        </button>
      </header>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* Status cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
        {CARDS.map((card) => {
          const data = summary?.cards?.[card.key] || {};
          const active = filters.status === card.status;
          return (
            <button
              key={card.key}
              onClick={() => setFilters((f) => ({
                ...f, status: active ? '' : card.status,
              }))}
              title={card.hint}
              className={`card p-3 text-left transition-colors ${active
                ? 'border-accent/60 bg-accent/5'
                : 'hover:border-ink-600'}`}
            >
              <div className="flex items-center gap-1.5 mb-2">
                <card.icon className={`w-3.5 h-3.5 ${card.accent}`} aria-hidden="true" />
                <span className="text-[11px] font-medium text-slate-400">{card.label}</span>
              </div>
              <p className="text-2xl font-semibold text-slate-100 tabular-nums leading-none">
                {data.count ?? '—'}
              </p>
              <p className="text-[11px] text-slate-500 mt-1.5 mono">
                {moneyShort(data.value)}
              </p>
              {card.key === 'pendingReview' && data.agedOver24h > 0 && (
                <p className="text-[11px] text-amber-400 mt-1 flex items-center gap-1">
                  <Clock className="w-3 h-3" aria-hidden="true" />
                  {data.agedOver24h} over 24h
                </p>
              )}
              {card.key === 'approved' && data.stpRate !== undefined && (
                <p className="text-[11px] text-emerald-400 mt-1"
                  title="Straight-through processing: decided with no human touch. Target ≥ 60%.">
                  STP {pct(data.stpRate * 100, 0)}
                </p>
              )}
              {card.key === 'needsInfo' && blockedFieldSummary && (
                <p className="text-[11px] text-slate-500 mt-1 truncate" title={blockedFieldSummary}>
                  {blockedFieldSummary}
                </p>
              )}
              {card.key === 'rejected' && data.topReasons && Object.keys(data.topReasons).length > 0 && (
                <p className="text-[11px] text-slate-500 mt-1 truncate mono"
                  title={Object.entries(data.topReasons).map(([k, v]) => `${k} ×${v}`).join(', ')}>
                  {Object.keys(data.topReasons).slice(0, 3).join(' · ')}
                </p>
              )}
            </button>
          );
        })}
      </div>

      {/* Secondary metric strip */}
      {metrics && (
        <div className="card px-4 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
          <Metric label="Invoices processed" value={metrics.totalInvoices} />
          <Metric label="Value processed" value={moneyShort(metrics.totalValue)} mono />
          <Metric
            label="Override rate"
            value={pct(metrics.override_rate * 100, 1)}
            hint={`${metrics.override_count} override(s) across ${metrics.recommendation_count} recommendation(s). Target ≤ 10%.`}
            mono
          />
          <Metric label="Ruleset" value={`v${metrics.rulesetVersion}`} mono />
          {!metrics.llmAvailable && (
            <span className="chip text-amber-300 border-amber-500/30 bg-amber-500/10"
              title="No language model configured. Rules and the decision are unaffected — they are code.">
              Deterministic-only mode
            </span>
          )}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="w-3.5 h-3.5 text-slate-600 absolute left-3 top-1/2 -translate-y-1/2"
            aria-hidden="true" />
          <input
            id="queue-search"
            className="input pl-9"
            placeholder="Search invoice number, vendor, PO…  (press /)"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
        </div>
        <select className="input w-auto" value={filters.riskBand}
          onChange={(e) => setFilters((f) => ({ ...f, riskBand: e.target.value }))}>
          <option value="">All risk bands</option>
          {['SEVERE', 'HIGH', 'MEDIUM', 'LOW'].map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className="input w-auto" value={filters.sort}
          onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value }))}>
          <option value="risk">Sort: risk, then age</option>
          <option value="amount">Sort: amount</option>
          <option value="age">Sort: oldest first</option>
          <option value="created">Sort: newest first</option>
        </select>
        {(filters.status || filters.riskBand || filters.search) && (
          <button className="btn-ghost"
            onClick={() => setFilters({ status: '', riskBand: '', search: '', sort: filters.sort })}>
            Clear
          </button>
        )}
      </div>

      {/* Queue */}
      <div className="card overflow-hidden">
        {loading && !invoices.length ? (
          <div className="flex justify-center py-16"><Spinner className="w-5 h-5 text-accent" /></div>
        ) : invoices.length === 0 ? (
          <Empty
            icon={Inbox}
            title="No invoices match"
            hint="Load the fixture corpus from the Ingestion screen to see the edge cases."
            action={<button className="btn-primary" onClick={() => navigate('/ingest')}>Go to ingestion</button>}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ink-850 border-b border-ink-800">
                <tr>
                  <th className="th">Invoice</th>
                  <th className="th">Vendor</th>
                  <th className="th text-right">Amount</th>
                  <th className="th">PO</th>
                  <th className="th">Risk</th>
                  <th className="th">Confidence</th>
                  <th className="th">Recommendation</th>
                  <th className="th">Status</th>
                  <th className="th">Age</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {invoices.map((invoice, index) => (
                  <tr
                    key={invoice.id}
                    onClick={() => navigate(`/invoices/${invoice.id}`)}
                    className={`cursor-pointer transition-colors ${index === cursor
                      ? 'bg-accent/10' : 'hover:bg-ink-850'}`}
                  >
                    <td className="td">
                      <span className="mono text-slate-100">{invoice.invoice_number || '—'}</span>
                      {invoice.is_scanned && (
                        <span className="chip border-ink-700 bg-ink-800 text-slate-500 ml-1.5"
                          title="Scanned document — read by vision, not native PDF text">scan</span>
                      )}
                      {invoice.overridden && (
                        <span className="chip border-violet-500/30 bg-violet-500/10 text-violet-300 ml-1.5">
                          overridden
                        </span>
                      )}
                    </td>
                    <td className="td">{invoice.vendor_name || '—'}</td>
                    <td className="td text-right mono">{money(invoice.grand_total, invoice.currency)}</td>
                    <td className="td mono text-slate-400">{invoice.po_number || '—'}</td>
                    <td className="td">
                      {invoice.decision
                        ? <RiskChip band={invoice.decision.risk_band} score={invoice.decision.risk_score} />
                        : <span className="text-slate-600 text-xs">—</span>}
                    </td>
                    <td className="td">
                      {invoice.decision
                        ? <ConfidenceBar value={invoice.decision.decision_confidence} compact />
                        : <span className="text-slate-600 text-xs">—</span>}
                    </td>
                    <td className="td">
                      {invoice.decision?.outcome
                        ? <DecisionChip outcome={invoice.decision.outcome} />
                        : <span className="text-slate-600 text-xs">not yet validated</span>}
                    </td>
                    <td className="td"><StatusChip status={invoice.status} /></td>
                    <td className="td text-slate-500 text-xs">{relativeAge(invoice.age_days)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value, hint, mono }) {
  return (
    <div className="flex items-baseline gap-1.5" title={hint}>
      <span className="text-slate-500">{label}</span>
      <span className={`text-slate-200 font-medium ${mono ? 'mono' : ''}`}>{value}</span>
    </div>
  );
}
