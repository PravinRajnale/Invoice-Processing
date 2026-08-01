/**
 * Shared primitives.
 *
 * Two rules from PRD 13.9 are enforced here rather than left to each screen:
 * every status carries glyph + colour + text, and no bare number appears
 * without a tooltip explaining where it came from.
 */

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Info, Loader2, X } from 'lucide-react';
import {
  CONFIDENCE_TIER, DECISION, OUTCOME, RISK, SEVERITY, STATUS,
  confidencePct, confidenceTier,
} from '../lib/format';

export function Chip({ className = '', children, title }) {
  return <span className={`chip ${className}`} title={title}>{children}</span>;
}

export function OutcomeChip({ outcome, showLabel = true }) {
  const spec = OUTCOME[outcome] || OUTCOME.NOT_APPLICABLE;
  return (
    <Chip className={spec.className} title={spec.label}>
      <span aria-hidden="true">{spec.glyph}</span>
      {showLabel && <span>{spec.label}</span>}
      <span className="sr-only">{spec.label}</span>
    </Chip>
  );
}

export function SeverityChip({ severity }) {
  const spec = SEVERITY[severity] || SEVERITY.INFO;
  return <Chip className={spec.className}>{spec.label}</Chip>;
}

export function RiskChip({ band, score }) {
  const spec = RISK[band] || RISK.LOW;
  return (
    <Chip className={spec.className}
      title={`Risk ${score ?? '—'} of 100 — exposure if this invoice is wrong, not uncertainty about it`}>
      {spec.label}{score !== undefined && score !== null ? ` · ${score}` : ''}
    </Chip>
  );
}

export function StatusChip({ status }) {
  const spec = STATUS[status] || { label: status, className: 'text-slate-400 border-ink-700 bg-ink-800' };
  return <Chip className={spec.className}>{spec.label}</Chip>;
}

export function DecisionChip({ outcome, size = 'sm' }) {
  const spec = DECISION[outcome];
  if (!spec) return <Chip className="text-slate-400 border-ink-700 bg-ink-800">{outcome || '—'}</Chip>;
  return (
    <span
      className={`chip ${spec.className} ${size === 'lg' ? 'text-sm px-3 py-1.5' : ''}`}
      title={spec.blurb}
    >
      <span aria-hidden="true">{spec.glyph}</span>
      {spec.label}
    </span>
  );
}

/** A confidence bar that always explains its own derivation on hover. */
export function ConfidenceBar({ value, label = 'Decision confidence', breakdown, compact }) {
  const n = Number(value);
  const width = Number.isFinite(n) ? Math.max(0, Math.min(1, n)) * 100 : 0;
  const colour = width >= 90 ? 'bg-emerald-500' : width >= 75 ? 'bg-amber-500' : 'bg-rose-500';

  const tooltip = breakdown?.penalties?.length
    ? `${label}\nStarted at 1.0000, then:\n${breakdown.penalties
        .map((p) => `  −${p.penalty}  ${p.reason}${p.detail ? ` (${p.detail})` : ''}`)
        .join('\n')}`
    : `${label}: ${confidencePct(value)}`;

  return (
    <div className={compact ? '' : 'space-y-1'} title={tooltip}>
      {!compact && (
        <div className="flex justify-between text-xs">
          <span className="text-slate-400">{label}</span>
          <span className="mono text-slate-200">{confidencePct(value)}</span>
        </div>
      )}
      <div className={`${compact ? 'h-1.5 w-16' : 'h-2 w-full'} bg-ink-800 rounded overflow-hidden`}>
        <div className={`h-full ${colour} transition-all`} style={{ width: `${width}%` }} />
      </div>
      {compact && <span className="mono text-[11px] text-slate-400">{confidencePct(value)}</span>}
    </div>
  );
}

export function ConfidenceDot({ value }) {
  const tier = confidenceTier(value);
  const spec = CONFIDENCE_TIER[tier];
  return (
    <span className={`chip border-ink-700 bg-ink-850 ${spec.className}`}
      title={`Extraction confidence ${confidencePct(value)} — ${
        tier === 'high' ? 'read cleanly'
          : tier === 'medium' ? 'legible but not certain'
            : 'below the critical-field floor of 80%'}`}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: spec.stroke }} />
      {confidencePct(value)}
    </span>
  );
}

export function Spinner({ className = 'w-4 h-4' }) {
  return <Loader2 className={`${className} animate-spin`} aria-hidden="true" />;
}

export function Empty({ icon: Icon = Info, title, hint, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <Icon className="w-8 h-8 text-slate-600 mb-3" aria-hidden="true" />
      <p className="text-slate-300 font-medium">{title}</p>
      {hint && <p className="text-sm text-slate-500 mt-1.5 max-w-md">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-3 p-3 rounded-md border border-rose-500/40 bg-rose-500/10">
      <AlertTriangle className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-rose-200">{error.message || String(error)}</p>
        {error.code && <p className="text-xs text-rose-400/80 mt-0.5 mono">{error.code}</p>}
      </div>
      {onDismiss && (
        <button onClick={onDismiss} className="text-rose-400 hover:text-rose-200" aria-label="Dismiss">
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

export function Modal({ open, onClose, title, subtitle, children, width = 'max-w-2xl' }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    ref.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto py-10 px-4
                    bg-black/70 backdrop-blur-sm"
      role="dialog" aria-modal="true" aria-label={title}>
      <div ref={ref} tabIndex={-1}
        className={`card w-full ${width} shadow-2xl`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between px-5 py-4 border-b border-ink-800">
          <div>
            <h2 className="text-base font-semibold text-slate-100">{title}</h2>
            {subtitle && <p className="text-sm text-slate-400 mt-1">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 p-1" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

/** Collapsible section used for rule evidence expanders. */
export function Disclosure({ summary, children, defaultOpen = false, className = '' }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1.5"
        aria-expanded={open}
      >
        <span className="text-[10px]" aria-hidden="true">{open ? '▾' : '▸'}</span>
        {summary}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function KeyValue({ label, value, mono = false, title, className = '' }) {
  return (
    <div className={`flex justify-between gap-4 py-1.5 ${className}`} title={title}>
      <span className="text-xs text-slate-500 shrink-0">{label}</span>
      <span className={`text-xs text-right ${mono ? 'mono' : ''} text-slate-200 break-words`}>
        {value ?? '—'}
      </span>
    </div>
  );
}

export function SectionTitle({ children, hint, right }) {
  return (
    <div className="flex items-baseline justify-between mb-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-200">{children}</h3>
        {hint && <p className="text-xs text-slate-500 mt-0.5">{hint}</p>}
      </div>
      {right}
    </div>
  );
}
