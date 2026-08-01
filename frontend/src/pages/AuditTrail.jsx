/**
 * Screen 10 — Audit Trail (PRD 13.1, 16).
 *
 * The chain verification is the feature that matters here. Each event hashes
 * its own payload plus the previous event's hash, so any retroactive edit
 * breaks the chain from that point forward and the verifier says exactly where.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Link2, ShieldAlert, ShieldCheck } from 'lucide-react';

import { api } from '../lib/api';
import { Disclosure, Empty, ErrorBanner, Spinner } from '../components/ui';
import { dateTime } from '../lib/format';

const EVENT_STYLE = {
  INGESTED: 'border-ink-700 bg-ink-850 text-slate-400',
  DECIDED: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  DECISION_CONFIRMED: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  DECISION_OVERRIDDEN: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  FIELD_CORRECTED: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  SECURITY_ANOMALY_DETECTED: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300',
  DUPLICATE_UPLOAD_BLOCKED: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300',
  DUPLICATE_RELEASED: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  DUPLICATE_CONFIRMED: 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300',
  INFORMATION_REQUESTED: 'border-ink-700 bg-ink-850 text-slate-400',
  FOLDER_SCAN: 'border-ink-700 bg-ink-850 text-slate-500',
};

export default function AuditTrail() {
  const [events, setEvents] = useState([]);
  const [chain, setChain] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    Promise.all([api.audit({ limit: 400 }), api.verifyAudit()])
      .then(([list, verify]) => {
        setEvents(list.data);
        setChain(verify.data);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  const shown = filter ? events.filter((e) => e.event_type === filter) : events;
  const types = [...new Set(events.map((e) => e.event_type))].sort();

  return (
    <div className="p-6 space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-slate-100">Audit trail</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Append-only and hash-chained. Nothing here can be edited without the chain saying so.
        </p>
      </header>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {chain && (
        <div className={`card p-4 flex items-start gap-3 ${chain.valid
          ? 'border-emerald-500/30' : 'border-rose-500/40'}`}>
          {chain.valid
            ? <ShieldCheck className="w-5 h-5 text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />
            : <ShieldAlert className="w-5 h-5 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />}
          <div>
            <p className={`text-sm font-medium ${chain.valid ? 'text-emerald-200' : 'text-rose-200'}`}>
              {chain.valid
                ? `Chain intact across ${chain.events} events`
                : `Chain broken at event ${chain.broken_at}`}
            </p>
            <p className="text-xs text-slate-400 mt-1">
              {chain.valid
                ? 'Every event was re-hashed from its own payload plus its predecessor’s hash, and every link matched. No record has been altered or removed since it was written.'
                : `${chain.reason} — event ${chain.event_id}. Everything from this point forward is untrustworthy.`}
            </p>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <select className="input w-auto" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All event types</option>
          {types.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </select>
        <span className="text-xs text-slate-600">{shown.length} events</span>
      </div>

      <div className="card overflow-hidden">
        {loading ? (
          <div className="flex justify-center py-16"><Spinner className="w-5 h-5 text-accent" /></div>
        ) : shown.length === 0 ? (
          <Empty title="No audit events" hint="Process an invoice to generate the trail." />
        ) : (
          <div className="divide-y divide-ink-850">
            {shown.map((event) => (
              <div key={event.id} className="px-4 py-3 hover:bg-ink-850">
                <div className="flex items-start gap-3">
                  <span className="mono text-[11px] text-slate-500 w-40 shrink-0 pt-0.5">
                    {dateTime(event.created_at)}
                  </span>

                  <span className={`chip shrink-0 ${EVENT_STYLE[event.event_type]
                    || 'border-ink-700 bg-ink-850 text-slate-400'}`}>
                    {event.event_type.replace(/_/g, ' ').toLowerCase()}
                  </span>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-slate-500">{event.entity_type}</span>
                      {event.entity_type === 'invoice' ? (
                        <Link to={`/invoices/${event.entity_id}`}
                          className="mono text-accent hover:underline truncate">
                          {event.entity_id.slice(0, 18)}…
                        </Link>
                      ) : (
                        <span className="mono text-slate-500 truncate">{event.entity_id}</span>
                      )}
                      <span className="chip border-ink-700 bg-ink-900 text-slate-500">
                        {event.actor_type}
                      </span>
                      {event.actor_id && (
                        <span className="text-slate-400">{event.actor_id}</span>
                      )}
                    </div>

                    {Object.keys(event.payload || {}).length > 0 && (
                      <Disclosure summary="Payload" className="mt-1.5">
                        <pre className="text-[10px] text-slate-400 bg-ink-950 p-2 rounded
                                        border border-ink-800 overflow-x-auto max-h-56">
                          {JSON.stringify(event.payload, null, 2)}
                        </pre>
                      </Disclosure>
                    )}
                  </div>

                  <div className="shrink-0 text-right" title={`hash ${event.hash}\nprevious ${event.prev_hash}`}>
                    <div className="flex items-center gap-1 text-[10px] text-slate-600">
                      <Link2 className="w-2.5 h-2.5" aria-hidden="true" />
                      <span className="mono">{event.hash?.slice(0, 10)}</span>
                    </div>
                    <p className="mono text-[9px] text-slate-700 mt-0.5">
                      ← {event.prev_hash?.slice(0, 10)}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
