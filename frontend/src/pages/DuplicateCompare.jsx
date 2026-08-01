/**
 * Screen 8 — Duplicate Comparison (PRD 13.8).
 *
 * Both documents side by side with a field-level diff. The whole point of
 * Edge Case 3 is that the difference is a single character in a place the eye
 * slides over, so the diff has to point at it explicitly rather than leaving
 * the reviewer to spot it.
 *
 * Releasing a suspected duplicate is the highest-risk override in the system —
 * it is the one action that most directly causes a duplicate payment — so it
 * always demands a second approver.
 */

import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, Check, Unlock } from 'lucide-react';

import { api, session } from '../lib/api';
import {
  Empty, ErrorBanner, Modal, Spinner, StatusChip,
} from '../components/ui';
import { date, money } from '../lib/format';

export default function DuplicateCompare() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.duplicates(id)
      .then((res) => setData(res.data))
      .catch(setError)
      .finally(() => setLoading(false));
  }, [id]);

  async function confirmDuplicate() {
    setBusy(true);
    try {
      await api.confirmDuplicate(id, {
        reasonNote: 'Confirmed as a duplicate of the earlier submission.',
      });
      navigate(`/invoices/${id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="flex justify-center py-20"><Spinner className="w-6 h-6 text-accent" /></div>;
  }
  if (error) return <div className="p-6"><ErrorBanner error={error} /></div>;

  const candidate = data?.candidates?.[0];
  const other = candidate?.invoice;

  return (
    <div className="p-6 max-w-6xl space-y-5">
      <header className="flex items-start gap-4">
        <button onClick={() => navigate(`/invoices/${id}`)}
          className="text-slate-500 hover:text-slate-200 mt-1" aria-label="Back">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Duplicate review</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Held, not rejected — the earlier submission may itself have been the error.
          </p>
        </div>
      </header>

      {!candidate ? (
        <div className="card">
          <Empty title="No duplicate candidates"
            hint="No prior invoice matched this one on any duplicate signal." />
        </div>
      ) : (
        <>
          {/* Why it was flagged */}
          <div className="card p-4 border-fuchsia-500/30">
            <div className="flex items-center gap-2 mb-2.5">
              <AlertTriangle className="w-4 h-4 text-fuchsia-400" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-slate-200">Why this was flagged</h2>
            </div>
            <div className="space-y-2">
              {candidate.signals.map((signal, i) => (
                <div key={i} className="p-2.5 rounded bg-ink-850 border border-ink-800">
                  <div className="flex items-center gap-2">
                    <span className="mono text-xs text-fuchsia-300">{signal.rule_id}</span>
                    <span className="text-xs text-slate-300">{signal.rule_name}</span>
                  </div>
                  {signal.detail.distance !== undefined && (
                    <p className="text-[11px] text-slate-400 mt-1">
                      {signal.detail.distance === 0
                        ? `Both numbers reduce to ${signal.detail.normalised} once confusable characters are folded — they are the same number written two ways.`
                        : `${signal.detail.distance} character(s) apart after normalisation, at the identical amount.`}
                    </p>
                  )}
                  {signal.detail.days_apart !== undefined && (
                    <p className="text-[11px] text-slate-400 mt-1">
                      Same vendor, identical amount, {signal.detail.days_apart} day(s) apart.
                      An independent signal from the number comparison — two agreeing is far
                      stronger than one.
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Field diff */}
          <div className="card overflow-hidden">
            <div className="px-4 py-2.5 border-b border-ink-800">
              <h2 className="text-sm font-semibold text-slate-200">Field comparison</h2>
            </div>
            <table className="w-full">
              <thead className="bg-ink-850 border-b border-ink-800">
                <tr>
                  <th className="th">Field</th>
                  <th className="th">This submission</th>
                  <th className="th">Earlier invoice</th>
                  <th className="th w-24">Match</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                <DiffRow label="Invoice number"
                  a={data.invoice.invoice_number} b={other.invoice_number} highlightChars />
                <DiffRow label="Invoice date" a={date(data.invoice.invoice_date)} b={date(other.invoice_date)} />
                <DiffRow label="Grand total" a={money(data.invoice.grand_total)} b={money(other.grand_total)} mono />
                <DiffRow label="Vendor" a={data.invoice.vendor_name} b={other.vendor_name} />
                <DiffRow label="Purchase order" a={data.invoice.po_number} b={other.po_number} mono />
                <tr>
                  <td className="td text-slate-500">Status</td>
                  <td className="td"><StatusChip status={data.invoice.status} /></td>
                  <td className="td">
                    <StatusChip status={other.status} />
                    <Link to={`/invoices/${other.id}`} className="text-[11px] text-accent hover:underline ml-2">
                      open →
                    </Link>
                  </td>
                  <td className="td" />
                </tr>
              </tbody>
            </table>
          </div>

          {/* Documents side by side */}
          <div className="grid md:grid-cols-2 gap-4">
            {[
              { title: 'This submission', invoiceId: id, invoice: data.invoice },
              { title: 'Earlier invoice', invoiceId: other.id, invoice: other },
            ].map((panel) => (
              <div key={panel.invoiceId} className="card overflow-hidden">
                <div className="px-3 py-2 border-b border-ink-800 flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-300">{panel.title}</span>
                  <span className="mono text-[11px] text-slate-500">
                    {panel.invoice.invoice_number}
                  </span>
                </div>
                <div className="bg-ink-950 p-2 max-h-[520px] overflow-auto">
                  <img
                    src={`/api/v1/invoices/${panel.invoiceId}/page/1.png?dpi=140&token=${
                      encodeURIComponent(session.token || '')}`}
                    alt={`${panel.title} page 1`}
                    className="w-full rounded"
                  />
                </div>
              </div>
            ))}
          </div>

          {/* Actions */}
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-200 mb-3">Decide</h2>
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary" onClick={confirmDuplicate} disabled={busy}>
                {busy ? <Spinner /> : <Check className="w-4 h-4" aria-hidden="true" />}
                Confirm duplicate — block
              </button>
              <button className="btn-ghost" onClick={() => setReleaseOpen(true)}
                disabled={!session.can('duplicate:release')}
                title={session.can('duplicate:release')
                  ? 'Release this invoice back into processing. Requires a second approver.'
                  : 'Your role cannot release a held duplicate.'}>
                <Unlock className="w-4 h-4" aria-hidden="true" />
                Release as legitimate
              </button>
            </div>
            <p className="text-[11px] text-slate-500 mt-2.5">
              Blocking is the default. Releasing requires dual authorisation because it is
              the single action in this system that most directly causes a duplicate payment.
            </p>
          </div>
        </>
      )}

      <ReleaseModal open={releaseOpen} onClose={() => setReleaseOpen(false)}
        invoiceId={id} onDone={() => navigate(`/invoices/${id}`)} />
    </div>
  );
}

function DiffRow({ label, a, b, mono, highlightChars }) {
  const matches = a === b;
  return (
    <tr className={matches ? '' : 'bg-rose-500/[0.06]'}>
      <td className="td text-slate-500">{label}</td>
      <td className={`td ${mono ? 'mono' : ''}`}>
        {highlightChars && !matches ? <CharDiff value={a} against={b} /> : (a || '—')}
      </td>
      <td className={`td ${mono ? 'mono' : ''}`}>
        {highlightChars && !matches ? <CharDiff value={b} against={a} /> : (b || '—')}
      </td>
      <td className="td">
        <span className={`chip ${matches
          ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
          : 'border-rose-500/30 bg-rose-500/10 text-rose-300'}`}>
          {matches ? 'identical' : 'differs'}
        </span>
      </td>
    </tr>
  );
}

/** Highlight the characters that actually differ, so a one-character swap is
 *  impossible to miss. */
function CharDiff({ value = '', against = '' }) {
  return (
    <span className="mono">
      {String(value).split('').map((char, i) => {
        const differs = against[i] !== char;
        return (
          <span key={i}
            className={differs ? 'bg-rose-500/40 text-rose-100 px-0.5 rounded-sm font-bold' : ''}>
            {char}
          </span>
        );
      })}
    </span>
  );
}

function ReleaseModal({ open, onClose, invoiceId, onDone }) {
  const [users, setUsers] = useState([]);
  const [approver, setApprover] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open) return;
    fetch('/api/v1/auth/personas').then((r) => r.json())
      .then(({ data }) => setUsers((data || []).filter(
        (u) => ['AP_MANAGER', 'CONTROLLER'].includes(u.role),
      )))
      .catch(() => {});
  }, [open]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.releaseDuplicate(invoiceId, {
        reasonCode: 'VENDOR_CLARIFICATION_RECEIVED',
        reasonNote: note.trim(),
        secondApproverId: approver,
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
    <Modal open={open} onClose={onClose} title="Release a held duplicate"
      subtitle="This is the highest-risk override in the system.">
      <div className="space-y-4">
        <div className="p-3 rounded-md border border-amber-500/40 bg-amber-500/10">
          <p className="text-xs text-amber-200">
            You are asserting that this invoice is genuinely separate from the one it
            matched. If that is wrong, the vendor is paid twice. A second approver is
            always required.
          </p>
        </div>

        <div>
          <label className="label" htmlFor="release-note">
            Justification (minimum 50 characters)
          </label>
          <textarea id="release-note" rows={3} className="input resize-none" value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Explain what establishes that these are two separate invoices." />
          <p className={`text-[11px] mono mt-1 text-right ${note.trim().length >= 50
            ? 'text-emerald-400' : 'text-slate-600'}`}>
            {note.trim().length} / 50
          </p>
        </div>

        <div>
          <label className="label" htmlFor="release-approver">Second approver (required)</label>
          <select id="release-approver" className="input" value={approver}
            onChange={(e) => setApprover(e.target.value)}>
            <option value="">Select…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name} — {u.roleLabel}</option>
            ))}
          </select>
        </div>

        <ErrorBanner error={error} onDismiss={() => setError(null)} />

        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-danger" onClick={submit}
            disabled={busy || note.trim().length < 50 || !approver}>
            {busy ? <Spinner /> : <Unlock className="w-4 h-4" aria-hidden="true" />}
            Release for processing
          </button>
        </div>
      </div>
    </Modal>
  );
}
