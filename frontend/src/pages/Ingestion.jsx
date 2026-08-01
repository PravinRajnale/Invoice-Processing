/**
 * Screen 2 — Ingestion (PRD 13.1).
 *
 * Two paths, as the PRD requires: manual upload is the demo path, folder pickup
 * is the realistic production one standing in for email intake.
 *
 * An exact-hash duplicate returns 409 here and is surfaced as such — no
 * extraction was attempted, so there was no OCR or model spend at all.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CheckCircle2, CopyX, FileUp, FolderInput, Info, RotateCcw, Upload,
} from 'lucide-react';

import { api, session } from '../lib/api';
import { ErrorBanner, Empty, Spinner } from '../components/ui';

export default function Ingestion() {
  const navigate = useNavigate();
  const fileInput = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState([]);
  const [summary, setSummary] = useState(null);
  const [fixtures, setFixtures] = useState([]);

  useEffect(() => {
    api.fixtures().then(({ data }) => setFixtures(data)).catch(() => {});
  }, []);

  const handleFiles = useCallback(async (files) => {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    const collected = [];

    for (const file of Array.from(files)) {
      try {
        const { data } = await api.upload(file);
        collected.push({
          kind: 'queued',
          filename: file.name,
          sourceFormatLabel: data.sourceFormatLabel,
          converted: data.converted,
          ...data,
        });
      } catch (err) {
        if (err.code === 'DUPLICATE_DOCUMENT') {
          collected.push({
            kind: 'duplicate',
            filename: file.name,
            existingInvoiceId: err.extra.existingInvoiceId,
            existingStatus: err.extra.existingStatus,
          });
        } else {
          collected.push({ kind: 'error', filename: file.name, message: err.message });
        }
      }
    }

    setResults((prev) => [...collected, ...prev]);
    setBusy(false);

    const first = collected.find((r) => r.kind === 'queued');
    if (first) navigate(`/invoices/${first.invoiceId}`);
  }, [navigate]);

  async function runFolderScan() {
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const { data } = await api.folderScan();
      setResults((prev) => [
        ...data.invoices.map((i) => ({
          kind: 'queued', filename: i.file, invoiceId: i.invoiceId, note: i.note,
        })),
        ...data.skipped.map((s) => ({
          kind: 'duplicate', filename: s.file, existingInvoiceId: s.existingInvoiceId,
        })),
        ...prev,
      ]);
      // Say plainly what happened. A scan that skips everything is the
      // idempotency check working, but with no summary it reads as a dead button.
      setSummary({
        queued: data.queued,
        skipped: data.skippedDuplicates,
        allSkipped: data.queued === 0 && data.skippedDuplicates > 0,
      });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function resetDemo() {
    setBusy(true);
    try {
      await api.reset();
      setResults([]);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p-6 max-w-5xl space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-slate-100">Ingestion</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Every file is hashed before anything else. A byte-identical resubmission
          short-circuits immediately — no OCR, no model call.
        </p>
      </header>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="grid md:grid-cols-2 gap-4">
        {/* Manual upload */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFiles(e.dataTransfer.files);
          }}
          className={`card p-6 flex flex-col items-center justify-center text-center
            border-dashed transition-colors min-h-[220px]
            ${dragging ? 'border-accent bg-accent/5' : 'border-ink-700'}`}
        >
          <FileUp className={`w-7 h-7 mb-3 ${dragging ? 'text-accent' : 'text-slate-600'}`}
            aria-hidden="true" />
          <p className="text-sm font-medium text-slate-200">Drop invoices here</p>
          <p className="text-xs text-slate-500 mt-1">
            PDF · images (PNG, JPEG, TIFF, BMP, WEBP) · Word .docx
          </p>
          <p className="text-[11px] text-slate-600 mt-1 mb-4">
            Any country, any currency. Images and Word files are converted to PDF on
            arrival, so everything downstream sees one format. Up to 50MB each.
          </p>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.gif,.docx,application/pdf,image/*"
            multiple
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
          <button className="btn-primary" disabled={busy}
            onClick={() => fileInput.current?.click()}>
            {busy ? <Spinner /> : <Upload className="w-4 h-4" aria-hidden="true" />}
            Choose files
          </button>
        </div>

        {/* Folder pickup */}
        <div className="card p-6 flex flex-col min-h-[220px]">
          <div className="flex items-center gap-2 mb-2">
            <FolderInput className="w-4 h-4 text-slate-500" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-slate-200">Folder pickup</h2>
          </div>
          <p className="text-xs text-slate-500 mb-4 flex-1">
            Simulates the watched folder that would receive emailed invoices in
            production. Loads the full fixture corpus in order — several fixtures
            are stateful by design and only mean anything in sequence.
          </p>

          {fixtures.length > 0 && (
            <p className="text-xs text-slate-400 mb-3">
              {fixtures.length} fixtures available, including all five edge cases.
            </p>
          )}

          <div className="flex gap-2">
            <button className="btn-primary" onClick={runFolderScan} disabled={busy}>
              {busy ? <Spinner /> : <FolderInput className="w-4 h-4" aria-hidden="true" />}
              Scan folder
            </button>
            {/* Shown to everyone, enabled only for admins. Hiding it entirely
                leaves a reviewer wondering how to start the demo over. */}
            <button className="btn-ghost" onClick={resetDemo}
              disabled={busy || !session.can('admin:write')}
              title={session.can('admin:write')
                ? 'Wipe transactional state, keeping the seeded vendors and purchase orders. Use between demo runs so the stateful edge cases start clean.'
                : 'Reset is an administrator action. Sign out and sign back in as the System Administrator to use it.'}>
              <RotateCcw className="w-4 h-4" aria-hidden="true" />
              Reset
            </button>
          </div>

          {!session.can('admin:write') && (
            <p className="text-[11px] text-slate-600 mt-2">
              Already loaded everything? Sign in as the System Administrator to reset
              and start the demo over.
            </p>
          )}
        </div>
      </div>

      {/* What the last scan actually did */}
      {summary && (
        <div className={`card p-4 ${summary.allSkipped
          ? 'border-fuchsia-500/40 bg-fuchsia-500/[0.06]'
          : 'border-emerald-500/40 bg-emerald-500/[0.06]'}`}>
          <div className="flex items-start gap-3">
            {summary.allSkipped
              ? <CopyX className="w-5 h-5 text-fuchsia-400 mt-0.5 shrink-0" aria-hidden="true" />
              : <CheckCircle2 className="w-5 h-5 text-emerald-400 mt-0.5 shrink-0" aria-hidden="true" />}

            <div className="flex-1">
              {summary.allSkipped ? (
                <>
                  <p className="text-sm font-medium text-fuchsia-200">
                    Nothing new to load — all {summary.skipped} files were already ingested
                  </p>
                  <p className="text-xs text-fuchsia-200/80 mt-1.5">
                    Every file matched an existing SHA-256, so the duplicate check
                    short-circuited each one before any extraction. That is the idempotency
                    control working, not a failure — but it means the queue is already
                    populated.
                  </p>
                  <div className="flex flex-wrap gap-2 mt-3">
                    <button className="btn-primary" onClick={() => navigate('/dashboard')}>
                      View the queue ({summary.skipped} invoices)
                    </button>
                    {session.can('admin:write') ? (
                      <button className="btn-ghost" onClick={resetDemo} disabled={busy}>
                        <RotateCcw className="w-4 h-4" aria-hidden="true" />
                        Reset and load again
                      </button>
                    ) : (
                      <span className="text-[11px] text-slate-500 self-center">
                        To start over, sign in as the administrator and use Reset.
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <p className="text-sm font-medium text-emerald-200">
                    {summary.queued} invoice{summary.queued === 1 ? '' : 's'} queued
                    {summary.skipped > 0 && `, ${summary.skipped} skipped as duplicates`}
                  </p>
                  <p className="text-xs text-emerald-200/80 mt-1.5">
                    Open any of them to watch the checks stream, or go to the queue.
                  </p>
                  <button className="btn-primary mt-3" onClick={() => navigate('/dashboard')}>
                    View the queue
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Fixture guide */}
      {fixtures.length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-1">Fixture corpus</h2>
          <p className="text-xs text-slate-500 mb-3">
            Each ships with an expected-outcome file that CI asserts against — the
            corpus is the specification in executable form.
          </p>
          <div className="space-y-1.5">
            {fixtures.map((f) => (
              <div key={f.name} className="flex items-start gap-3 py-1.5 border-b border-ink-850 last:border-0">
                <span className="mono text-xs text-slate-300 w-52 shrink-0">{f.name}</span>
                <span className="chip border-ink-700 bg-ink-850 text-slate-400 shrink-0">
                  {f.expected_decision}
                </span>
                {f.scanned && (
                  <span className="chip border-ink-700 bg-ink-850 text-slate-500 shrink-0">scan</span>
                )}
                <span className="text-xs text-slate-500 flex-1">{f.note}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Results */}
      {results.length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-slate-200 mb-3">This session</h2>
          <div className="space-y-2">
            {results.map((r, i) => (
              <div key={`${r.filename}-${i}`}
                className="flex items-center gap-3 p-2.5 rounded-md bg-ink-850 border border-ink-800">
                {r.kind === 'queued' && <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" aria-hidden="true" />}
                {r.kind === 'duplicate' && <CopyX className="w-4 h-4 text-fuchsia-400 shrink-0" aria-hidden="true" />}
                {r.kind === 'error' && <Info className="w-4 h-4 text-rose-400 shrink-0" aria-hidden="true" />}

                <span className="mono text-xs text-slate-300 flex-1 truncate">{r.filename}</span>
                {r.sourceFormatLabel && r.converted && (
                  <span className="chip border-sky-500/30 bg-sky-500/10 text-sky-300 shrink-0"
                    title={`Arrived as a ${r.sourceFormatLabel} and was converted to PDF so the viewer, the highlight overlay and the extractor all see one format.`}>
                    {r.sourceFormatLabel} → PDF
                  </span>
                )}

                {r.kind === 'queued' && (
                  <button className="text-xs text-accent hover:underline"
                    onClick={() => navigate(`/invoices/${r.invoiceId}`)}>
                    Open workspace →
                  </button>
                )}
                {r.kind === 'duplicate' && (
                  <>
                    <span className="text-xs text-fuchsia-300">
                      Identical file already submitted — blocked before extraction
                    </span>
                    {r.existingInvoiceId && (
                      <button className="text-xs text-accent hover:underline"
                        onClick={() => navigate(`/invoices/${r.existingInvoiceId}`)}>
                        View original →
                      </button>
                    )}
                  </>
                )}
                {r.kind === 'error' && <span className="text-xs text-rose-300">{r.message}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {results.length === 0 && fixtures.length === 0 && (
        <div className="card">
          <Empty
            title="Nothing ingested yet"
            hint="Drop a PDF above, or run the folder scan to load the fixture corpus."
          />
        </div>
      )}
    </div>
  );
}
