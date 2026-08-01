/**
 * Review Workspace — Screens 3, 4 and 5 in one two-pane layout.
 *
 * Left: the document, with the bbox overlay.
 * Right: extraction → live validation → decision, as tabs.
 *
 * The document stays visible throughout on purpose. Every claim the right pane
 * makes is checkable against the paper without losing your place.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, Check, CopyCheck, FileText, GitCompareArrows, History, ListChecks,
  MessageSquare, Play, ScrollText, Send, ShieldQuestion,
} from 'lucide-react';

import { api, session, streamInvoice } from '../lib/api';
import {
  DecisionChip, ErrorBanner, Modal, Spinner, StatusChip,
} from '../components/ui';
import DocumentViewer from '../components/DocumentViewer';
import ExtractionReview from '../components/ExtractionReview';
import ValidationLive from '../components/ValidationLive';
import DecisionPanel from '../components/DecisionPanel';
import MatchPanel from '../components/MatchPanel';
import OverrideModal from '../components/OverrideModal';
import FieldVerification from '../components/FieldVerification';
import { dateTime, money } from '../lib/format';

const TABS = [
  { key: 'extraction', label: 'Extraction', icon: FileText },
  { key: 'match', label: 'PO match', icon: GitCompareArrows },
  { key: 'validation', label: 'Validation', icon: ListChecks },
  { key: 'decision', label: 'Decision', icon: ShieldQuestion },
  { key: 'history', label: 'History', icon: History },
];

export default function Workspace() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [aggregate, setAggregate] = useState(null);
  const [extraction, setExtraction] = useState(null);
  const [match, setMatch] = useState(null);
  const [catalogue, setCatalogue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [tab, setTab] = useState('extraction');
  const [activeField, setActiveField] = useState(null);

  // Live stream state
  const [streaming, setStreaming] = useState(false);
  const [liveRules, setLiveRules] = useState([]);
  const [liveDecision, setLiveDecision] = useState(null);
  const [liveExplanation, setLiveExplanation] = useState(null);
  const [liveSecurity, setLiveSecurity] = useState([]);
  const [stage, setStage] = useState(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const closeStream = useRef(null);

  const [overrideOpen, setOverrideOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [verifying, setVerifying] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [agg, ext, mat, rules] = await Promise.all([
        api.invoice(id),
        api.extraction(id).catch(() => ({ data: null })),
        api.match(id).catch(() => ({ data: null })),
        catalogue.length ? Promise.resolve(null) : api.rules(),
      ]);
      setAggregate(agg.data);
      setExtraction(ext.data);
      setMatch(mat.data);
      if (rules) setCatalogue(rules.data.gates.flatMap((g) => g.rules));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [id, catalogue.length]);

  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const runPipeline = useCallback((trigger = 'INITIAL') => {
    setStreaming(true);
    setConnectionLost(false);
    setLiveRules([]);
    setLiveDecision(null);
    setLiveExplanation(null);
    setTab('validation');

    closeStream.current?.();
    closeStream.current = streamInvoice(id, {
      stage: (payload) => setStage(payload),
      security: (payload) => setLiveSecurity(payload.flags || []),
      rule: (payload) => setLiveRules((prev) => [...prev, payload]),
      decision: (payload) => setLiveDecision(payload),
      explanation: (payload) => setLiveExplanation(payload),
      done: () => {
        setStreaming(false);
        setTab('decision');
        load();
      },
      error: (payload) => {
        setStreaming(false);
        setError(new Error(payload.message || 'Processing failed'));
      },
      connectionLost: () => {
        // PRD 13.9: a dropped stream falls back to polling with a visible
        // indicator rather than silently stalling.
        setConnectionLost(true);
        setStreaming(false);
        load();
      },
    }, trigger);
  }, [id, load]);

  // Auto-run for a freshly ingested invoice so upload flows straight into the
  // live check stream.
  useEffect(() => {
    if (aggregate?.invoice?.status === 'INGESTED' && !streaming && !liveRules.length) {
      runPipeline('INITIAL');
    }
  }, [aggregate?.invoice?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => closeStream.current?.(), []);

  const invoice = aggregate?.invoice;
  const rules = liveRules.length ? liveRules : (aggregate?.rules || []);
  const decision = liveDecision || aggregate?.decision;
  const explanation = liveExplanation
    || (aggregate?.decision && {
      text: aggregate.decision.ai_explanation,
      per_rule: aggregate.decision.per_rule_explanation,
      source: aggregate.decision.explanation_source,
      model: aggregate.decision.explanation_model,
    });
  const securityFlags = liveSecurity.length ? liveSecurity : (aggregate?.securityFlags || []);

  const lowConfidenceField = (extraction?.belowFloor || [])[0];

  async function correctField(path, value) {
    setBusy(true);
    try {
      await api.correctField(id, path, value);
      setVerifying(null);
      runPipeline('RERUN_AFTER_CORRECTION');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDecision() {
    setBusy(true);
    try {
      await api.confirm(id);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDuplicate() {
    setBusy(true);
    try {
      await api.confirmDuplicate(id, { reasonNote: 'Confirmed as a duplicate submission.' });
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-screen">
      <Spinner className="w-6 h-6 text-accent" />
    </div>;
  }
  if (!invoice) {
    return <div className="p-6"><ErrorBanner error={error || new Error('Invoice not found')} /></div>;
  }

  const canAct = ['PENDING_REVIEW', 'PENDING_APPROVAL', 'NEEDS_INFO', 'DUPLICATE_HELD']
    .includes(invoice.status);

  return (
    <div className="h-screen flex flex-col">
      {/* Header */}
      <header className="px-5 py-3 border-b border-ink-800 bg-ink-900 flex items-center gap-4">
        <button onClick={() => navigate('/dashboard')}
          className="text-slate-500 hover:text-slate-200" aria-label="Back to queue">
          <ArrowLeft className="w-4 h-4" />
        </button>

        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <h1 className="mono text-sm text-slate-100">{invoice.invoice_number || 'Not yet extracted'}</h1>
            <StatusChip status={invoice.status} />
            {decision?.outcome && <DecisionChip outcome={decision.outcome} />}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {invoice.vendor_name || '—'} · {money(invoice.grand_total, invoice.currency)}
            {invoice.po_number && (
              <> · <Link to={`/pos/${invoice.po_number}`} className="text-accent hover:underline">
                {invoice.po_number}
              </Link></>
            )}
          </p>
        </div>

        <div className="flex-1" />

        {connectionLost && (
          <span className="chip border-amber-500/40 bg-amber-500/10 text-amber-300"
            title="The live stream dropped. Results were reloaded by polling instead.">
            live stream lost — reloaded
          </span>
        )}

        {aggregate.runs?.length > 0 && (
          <span className="text-[11px] text-slate-500">
            {aggregate.runs.length} validation run{aggregate.runs.length > 1 ? 's' : ''}
          </span>
        )}

        <button className="btn-ghost" onClick={() => runPipeline('MANUAL')} disabled={streaming}>
          {streaming ? <Spinner /> : <Play className="w-3.5 h-3.5" aria-hidden="true" />}
          {streaming ? 'Running…' : 'Re-run validation'}
        </button>
      </header>

      {error && <div className="px-5 py-2"><ErrorBanner error={error} onDismiss={() => setError(null)} /></div>}

      {/* Two-pane */}
      <div className="flex-1 flex min-h-0">
        <div className="w-[46%] border-r border-ink-800 min-w-0">
          <DocumentViewer
            invoiceId={id}
            pageCount={aggregate.document?.page_count || 1}
            fields={extraction?.fields || []}
            activeField={activeField}
            onFieldHover={setActiveField}
            onFieldClick={(path) => { setActiveField(path); setTab('extraction'); }}
          />
        </div>

        <div className="flex-1 min-w-0 flex flex-col">
          <nav className="flex border-b border-ink-800 bg-ink-900 px-2" role="tablist">
            {TABS.map((t) => (
              <button key={t.key} role="tab" aria-selected={tab === t.key}
                onClick={() => setTab(t.key)}
                className={`flex items-center gap-1.5 px-3.5 py-2.5 text-xs font-medium border-b-2
                  transition-colors ${tab === t.key
                    ? 'border-accent text-accent'
                    : 'border-transparent text-slate-500 hover:text-slate-300'}`}>
                <t.icon className="w-3.5 h-3.5" aria-hidden="true" />
                {t.label}
                {t.key === 'validation' && rules.length > 0 && (
                  <span className="text-[10px] text-slate-600 mono">({rules.length})</span>
                )}
              </button>
            ))}
          </nav>

          <div className="flex-1 overflow-y-auto p-4">
            {/* Edge Case 2: offer the one-field fix wherever the reviewer is. */}
            {lowConfidenceField && invoice.status === 'NEEDS_INFO' && (
              <div className="mb-4">
                <FieldVerification
                  invoiceId={id}
                  field={verifying || lowConfidenceField}
                  extraction={extraction}
                  onConfirm={correctField}
                />
              </div>
            )}

            {tab === 'extraction' && (
              <ExtractionReview
                extraction={extraction}
                lines={aggregate.lines}
                poLines={aggregate.poLines}
                activeField={activeField}
                onFieldFocus={setActiveField}
                onCorrect={correctField}
                criticalFloor={extraction?.criticalFieldFloor}
                readOnly={!session.can('invoice:correct')}
              />
            )}

            {tab === 'match' && (
              <MatchPanel
                match={match}
                onOpenRule={() => setTab('validation')}
              />
            )}

            {tab === 'validation' && (
              <>
                {streaming && stage && (
                  <div className="mb-3 px-3 py-2 rounded-md bg-accent/10 border border-accent/30
                                  flex items-center gap-2">
                    <Spinner className="w-3 h-3 text-accent" />
                    <span className="text-xs text-accent">
                      {stage.stage} · {stage.status}
                      {stage.reading_path && ` — ${stage.reading_path}`}
                    </span>
                  </div>
                )}
                <ValidationLive
                  rules={rules}
                  catalogue={catalogue}
                  streaming={streaming}
                  poNumber={invoice.po_number}
                  invoiceId={id}
                  onEvidenceClick={(path) => { setActiveField(path); setTab('extraction'); }}
                />
              </>
            )}

            {tab === 'decision' && (
              <DecisionPanel
                invoice={invoice}
                decision={decision}
                rules={rules}
                explanation={explanation}
                securityFlags={securityFlags}
                poNumber={invoice.po_number}
                onEvidenceClick={(path) => { setActiveField(path); setTab('extraction'); }}
                onResolveField={(path) => {
                  const field = (extraction?.fields || []).find((f) => f.field_path === path);
                  if (field) setVerifying(field);
                  setActiveField(path);
                }}
                actions={canAct && (
                  <div className="card p-4">
                    <h3 className="text-sm font-semibold text-slate-200 mb-3">Actions</h3>
                    <div className="flex flex-wrap gap-2">
                      {invoice.status === 'DUPLICATE_HELD' ? (
                        <>
                          <button className="btn-primary" onClick={confirmDuplicate} disabled={busy}>
                            <Check className="w-4 h-4" aria-hidden="true" />
                            Confirm duplicate — block
                          </button>
                          <Link to={`/invoices/${id}/duplicates`} className="btn-ghost">
                            <CopyCheck className="w-4 h-4" aria-hidden="true" />
                            Compare side by side
                          </Link>
                        </>
                      ) : (
                        <button className="btn-primary" onClick={confirmDecision}
                          disabled={busy || !session.can('invoice:confirm')
                            || ['MANUAL_REVIEW', 'NEEDS_INFO'].includes(decision?.outcome)}
                          title={['MANUAL_REVIEW', 'NEEDS_INFO'].includes(decision?.outcome)
                            ? 'This recommendation cannot simply be confirmed — it needs a correction, an override, or a request for information.'
                            : undefined}>
                          {busy ? <Spinner /> : <Check className="w-4 h-4" aria-hidden="true" />}
                          Accept recommendation
                        </button>
                      )}

                      <button className="btn-ghost" onClick={() => setOverrideOpen(true)}
                        disabled={!session.can('invoice:override')}>
                        <ScrollText className="w-4 h-4" aria-hidden="true" />
                        Override
                      </button>

                      <button className="btn-ghost" onClick={() => setInfoOpen(true)}
                        disabled={!session.can('invoice:request-info')}>
                        <MessageSquare className="w-4 h-4" aria-hidden="true" />
                        Request info from vendor
                      </button>
                    </div>
                    {!session.can('invoice:confirm') && (
                      <p className="text-[11px] text-slate-500 mt-2.5">
                        Your role is read-only. Sign in as a processor or manager to act.
                      </p>
                    )}
                  </div>
                )}
              />
            )}

            {tab === 'history' && (
              <HistoryTab aggregate={aggregate} invoiceId={id} />
            )}
          </div>
        </div>
      </div>

      <OverrideModal
        open={overrideOpen}
        onClose={() => setOverrideOpen(false)}
        invoice={invoice}
        decision={decision}
        rules={rules}
        onDone={load}
      />

      <RequestInfoModal
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        invoiceId={id}
        blockedFields={(decision?.blocked_on || [])}
        onDone={load}
      />
    </div>
  );
}

function HistoryTab({ aggregate, invoiceId }) {
  const [audit, setAudit] = useState([]);
  const [replay, setReplay] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.audit({ entityId: invoiceId }).then(({ data }) => setAudit(data)).catch(() => {});
  }, [invoiceId]);

  async function runReplay() {
    setBusy(true);
    try {
      const { data } = await api.replay(invoiceId);
      setReplay(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Validation runs</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Corrections create a new run. Earlier runs are never overwritten.
            </p>
          </div>
          {session.can('audit:read') && (
            <button className="btn-ghost" onClick={runReplay} disabled={busy}>
              {busy ? <Spinner /> : <History className="w-3.5 h-3.5" aria-hidden="true" />}
              Replay
            </button>
          )}
        </div>

        {replay && (
          <div className={`mb-3 p-3 rounded-md border ${replay.identical
            ? 'border-emerald-500/40 bg-emerald-500/10'
            : 'border-amber-500/40 bg-amber-500/10'}`}>
            <p className={`text-xs ${replay.identical ? 'text-emerald-200' : 'text-amber-200'}`}>
              {replay.identical
                ? `Replay reproduced the identical decision (${replay.decisionAfter}) and every rule outcome. This is what determinism means in practice.`
                : `Replay differs: ${replay.decisionBefore} → ${replay.decisionAfter}, ${replay.rulesChanged.length} rule(s) changed.`}
            </p>
            {replay.rulesChanged?.length > 0 && (
              <ul className="mt-2 space-y-0.5">
                {replay.rulesChanged.map((c) => (
                  <li key={c.ruleId} className="text-[11px] mono text-amber-300">
                    {c.ruleId}: {c.before} → {c.after}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="space-y-2">
          {(aggregate.runs || []).map((run) => (
            <div key={run.id} className="p-2.5 rounded bg-ink-850 border border-ink-800">
              <div className="flex items-center justify-between">
                <span className="chip border-ink-700 bg-ink-900 text-slate-300">{run.trigger}</span>
                <span className="text-[11px] text-slate-500">{dateTime(run.created_at)}</span>
              </div>
              {run.tally && (
                <p className="text-[11px] text-slate-500 mt-1.5 mono">
                  {Object.entries(run.tally).filter(([, n]) => n > 0)
                    .map(([k, n]) => `${n} ${k.toLowerCase().replace(/_/g, ' ')}`).join(' · ')}
                </p>
              )}
              <p className="text-[10px] text-slate-600 mt-1 mono">
                ruleset v{run.ruleset_version} · engine v{run.engine_version}
              </p>
            </div>
          ))}
        </div>
      </div>

      {(aggregate.humanActions || []).length > 0 && (
        <div className="card p-4">
          <h3 className="text-sm font-semibold text-slate-200 mb-3">Human actions</h3>
          <div className="space-y-2">
            {aggregate.humanActions.map((action) => (
              <div key={action.id} className="p-2.5 rounded bg-ink-850 border border-ink-800">
                <div className="flex items-center justify-between">
                  <span className="chip border-violet-500/30 bg-violet-500/10 text-violet-300">
                    {action.action.replace(/_/g, ' ')}
                  </span>
                  <span className="text-[11px] text-slate-500">{dateTime(action.created_at)}</span>
                </div>
                {action.ai_recommendation && (
                  <p className="text-[11px] text-slate-400 mt-1.5">
                    {action.ai_recommendation} → {action.human_decision}
                  </p>
                )}
                {action.reason_code && (
                  <p className="text-[11px] text-slate-500 mt-1 mono">{action.reason_code}</p>
                )}
                {action.reason_note && (
                  <p className="text-xs text-slate-400 mt-1 italic">“{action.reason_note}”</p>
                )}
                {action.second_approver_id && (
                  <p className="text-[11px] text-amber-400 mt-1">
                    Second approver: {action.second_approver_id}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card p-4">
        <h3 className="text-sm font-semibold text-slate-200 mb-3">Audit trail</h3>
        <div className="space-y-1.5">
          {audit.map((event) => (
            <div key={event.id} className="flex items-start gap-3 py-1.5 border-b border-ink-850 last:border-0">
              <span className="mono text-[11px] text-slate-500 w-40 shrink-0">
                {dateTime(event.created_at)}
              </span>
              <span className="chip border-ink-700 bg-ink-850 text-slate-300 shrink-0">
                {event.event_type.replace(/_/g, ' ')}
              </span>
              <span className="text-[11px] text-slate-500 flex-1 truncate"
                title={JSON.stringify(event.payload, null, 2)}>
                {event.actor_type} {event.actor_id || ''}
              </span>
              <span className="mono text-[10px] text-slate-700 shrink-0"
                title={`hash ${event.hash}\nprev ${event.prev_hash}`}>
                {event.hash?.slice(0, 8)}
              </span>
            </div>
          ))}
          {audit.length === 0 && <p className="text-xs text-slate-600">No events recorded.</p>}
        </div>
      </div>
    </div>
  );
}

function RequestInfoModal({ open, onClose, invoiceId, blockedFields, onDone }) {
  const [target, setTarget] = useState('VENDOR');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.requestInfo(invoiceId, {
        target,
        fields: blockedFields.map((b) => b.split(' (')[0]),
        message,
      });
      onDone?.();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Request information"
      subtitle="Ask for exactly what is missing, not for the whole invoice again.">
      <div className="space-y-4">
        <div>
          <label className="label" htmlFor="info-target">Ask</label>
          <select id="info-target" className="input" value={target}
            onChange={(e) => setTarget(e.target.value)}>
            <option value="VENDOR">The vendor</option>
            <option value="INTERNAL">Someone internal</option>
          </select>
        </div>

        {blockedFields.length > 0 && (
          <div>
            <p className="label">Blocked inputs</p>
            <div className="flex flex-wrap gap-1.5">
              {blockedFields.map((f) => (
                <span key={f} className="chip border-sky-500/30 bg-sky-500/10 text-sky-300 mono">
                  {f.split(' (')[0]}
                </span>
              ))}
            </div>
          </div>
        )}

        <div>
          <label className="label" htmlFor="info-message">Message</label>
          <textarea id="info-message" rows={3} className="input resize-none" value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="e.g. The total on page 1 is not legible in the copy we received. Could you resend a clearer scan?" />
        </div>

        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy || !message.trim()}>
            {busy ? <Spinner /> : <Send className="w-4 h-4" aria-hidden="true" />}
            Send request
          </button>
        </div>
      </div>
    </Modal>
  );
}
