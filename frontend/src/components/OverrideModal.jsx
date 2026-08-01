/**
 * Screen 6 — Override Modal (PRD 13.6).
 *
 * A free-text reason box is necessary but not sufficient. Free text cannot be
 * counted, and counting is how thresholds get tuned — so a structured reason
 * code is mandatory alongside it.
 *
 * The controls are deliberately asymmetric. Overriding *toward* approval is the
 * direction that costs money, so it demands a longer justification and, past
 * the thresholds, a second approver. Overriding toward rejection does not.
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Lock, Users } from 'lucide-react';

import { api } from '../lib/api';
import { DecisionChip, ErrorBanner, Modal, SeverityChip, Spinner } from './ui';
import { money } from '../lib/format';

export default function OverrideModal({
  open, onClose, invoice, decision, rules = [], onDone,
}) {
  const [reasonCodes, setReasonCodes] = useState({});
  const [users, setUsers] = useState([]);
  const [humanDecision, setHumanDecision] = useState('APPROVED');
  const [reasonCode, setReasonCode] = useState('');
  const [note, setNote] = useState('');
  const [secondApprover, setSecondApprover] = useState('');
  const [attachment, setAttachment] = useState('');
  const [requirements, setRequirements] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    api.reasonCodes().then(({ data }) => setReasonCodes(data)).catch(() => {});
    api.invoices({ pageSize: 1 }).catch(() => {});
    fetch('/api/v1/auth/personas').then((r) => r.json())
      .then(({ data }) => setUsers(
        (data || []).filter((u) => ['AP_MANAGER', 'CONTROLLER'].includes(u.role)),
      ))
      .catch(() => {});
  }, [open]);

  // Ask the server what this particular override will demand, rather than
  // guessing in the browser and rejecting after the reviewer has typed.
  useEffect(() => {
    if (!open || !reasonCode || !invoice?.id) return;
    let alive = true;
    api.overrideRequirements(invoice.id, humanDecision, reasonCode)
      .then(({ data }) => alive && setRequirements(data))
      .catch(() => {});
    return () => { alive = false; };
  }, [open, reasonCode, humanDecision, invoice?.id]);

  const failures = rules.filter((r) => ['FAIL', 'CANNOT_EVALUATE'].includes(r.outcome));
  const minLength = requirements?.min_note_length
    ?? (humanDecision === 'APPROVED' ? 50 : 20);
  const spec = reasonCodes[reasonCode] || {};
  const needsSecond = requirements?.requires_second_approver;
  const needsAttachment = spec.requires_attachment;

  const blocked = !reasonCode
    || note.trim().length < minLength
    || (needsSecond && !secondApprover)
    || (needsAttachment && !attachment.trim());

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.override(invoice.id, {
        humanDecision,
        reasonCode,
        reasonNote: note.trim(),
        secondApproverId: secondApprover || undefined,
        attachmentId: attachment.trim() || undefined,
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
    <Modal
      open={open}
      onClose={onClose}
      title="Override the recommendation"
      subtitle={`You are overriding a ${decision?.outcome?.replace(/_/g, ' ')} recommendation.`}
    >
      <div className="space-y-4">
        <div className="flex items-center gap-3 p-3 rounded-md bg-ink-850 border border-ink-800">
          <div>
            <p className="text-[11px] text-slate-500 mb-1">System recommended</p>
            <DecisionChip outcome={decision?.outcome} />
          </div>
          <span className="text-slate-600" aria-hidden="true">→</span>
          <div className="flex-1">
            <label className="text-[11px] text-slate-500 mb-1 block" htmlFor="override-decision">
              You are deciding
            </label>
            <select id="override-decision" className="input py-1.5"
              value={humanDecision} onChange={(e) => setHumanDecision(e.target.value)}>
              <option value="APPROVED">Approve</option>
              <option value="REJECTED">Reject</option>
              <option value="PENDING_APPROVAL">Route for authorisation</option>
              <option value="NEEDS_INFO">Send back for information</option>
            </select>
          </div>
          <div className="text-right">
            <p className="text-[11px] text-slate-500 mb-1">Amount</p>
            <p className="mono text-sm text-slate-100">{money(invoice?.grand_total, invoice?.currency)}</p>
          </div>
        </div>

        {/* Failed rules restated, so the reviewer confirms against evidence
            rather than memory. */}
        {failures.length > 0 && (
          <div>
            <p className="label">What you are overriding</p>
            <div className="space-y-1.5 max-h-40 overflow-y-auto">
              {failures.map((rule) => (
                <div key={rule.rule_id}
                  className="flex items-start gap-2 p-2 rounded bg-ink-850 border border-ink-800">
                  <span aria-hidden="true" className={rule.outcome === 'FAIL' ? 'text-rose-400' : 'text-sky-300'}>
                    {rule.outcome === 'FAIL' ? '✗' : '⊘'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="mono text-[11px] text-slate-500">{rule.rule_id}</span>
                      <span className="text-xs text-slate-300">{rule.name}</span>
                      <SeverityChip severity={rule.severity} />
                    </div>
                    <p className="text-[11px] text-slate-500 mt-0.5">{rule.message}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <label className="label" htmlFor="reason-code">Reason code (required)</label>
          <select id="reason-code" className="input" value={reasonCode}
            onChange={(e) => setReasonCode(e.target.value)}>
            <option value="">Select a reason…</option>
            {Object.entries(reasonCodes).map(([code, info]) => (
              <option key={code} value={code}>{info.label}</option>
            ))}
          </select>
          {spec.help && <p className="text-[11px] text-slate-500 mt-1.5">{spec.help}</p>}
        </div>

        {needsAttachment && (
          <div>
            <label className="label" htmlFor="attachment">
              Supporting evidence reference (required for this reason code)
            </label>
            <input id="attachment" className="input" value={attachment}
              onChange={(e) => setAttachment(e.target.value)}
              placeholder="e.g. PO-2291-A1 amendment, approved by procurement 28 Jul" />
          </div>
        )}

        <div>
          <label className="label" htmlFor="note">
            Justification (minimum {minLength} characters)
          </label>
          <textarea id="note" rows={3} className="input resize-none" value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Explain what you know that the system does not." />
          <div className="flex justify-between mt-1">
            <span className="text-[11px] text-slate-600">
              {humanDecision === 'APPROVED'
                ? 'Overriding toward approval requires a fuller justification.'
                : 'Recorded permanently against your identity.'}
            </span>
            <span className={`text-[11px] mono ${note.trim().length >= minLength
              ? 'text-emerald-400' : 'text-slate-600'}`}>
              {note.trim().length} / {minLength}
            </span>
          </div>
        </div>

        {needsSecond && (
          <div className="p-3 rounded-md border border-amber-500/40 bg-amber-500/10">
            <div className="flex items-center gap-2 mb-2">
              <Lock className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />
              <p className="text-xs font-medium text-amber-200">Second approver required</p>
            </div>
            <ul className="text-[11px] text-amber-200/80 mb-2.5 space-y-0.5">
              {requirements.second_approver_reasons.map((reason) => (
                <li key={reason} className="flex gap-1.5">
                  <span aria-hidden="true">•</span>{reason}
                </li>
              ))}
            </ul>
            {requirements.sod_triggered && (
              <p className="text-[11px] text-amber-200/90 mb-2.5 flex items-start gap-1.5">
                <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" aria-hidden="true" />
                Segregation of duties (POL-06): you corrected extraction on this invoice,
                so you cannot also be its sole approver.
              </p>
            )}
            <label className="label" htmlFor="second-approver">
              <span className="flex items-center gap-1.5">
                <Users className="w-3 h-3" aria-hidden="true" /> Nominate an approver
              </span>
            </label>
            <select id="second-approver" className="input" value={secondApprover}
              onChange={(e) => setSecondApprover(e.target.value)}>
              <option value="">Select…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} — {u.roleLabel} (limit {money(u.approvalLimit)})
                </option>
              ))}
            </select>
          </div>
        )}

        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <div className="flex items-center justify-between pt-1 border-t border-ink-800">
          <p className="text-[11px] text-slate-600 max-w-sm">
            This action is permanently recorded with your identity, timestamp and IP
            address, and feeds the override analytics that drive threshold tuning.
          </p>
          <div className="flex gap-2">
            <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="btn-primary" onClick={submit} disabled={blocked || busy}>
              {busy && <Spinner />}
              Record override
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
