/**
 * Display helpers and the shared visual vocabulary for outcomes.
 *
 * PRD 13.9: status is never conveyed by colour alone. Every outcome carries a
 * glyph and a label as well, so the distinction survives a colour-blind reader,
 * a greyscale print, and a screenshot in a review deck.
 */

/** Indian digit grouping, done on the string so no float ever touches money. */
export function money(value, currency = 'INR') {
  if (value === null || value === undefined || value === '') return '—';

  const raw = String(value).trim();
  const negative = raw.startsWith('-');
  const [whole = '0', fraction = '00'] = raw.replace('-', '').split('.');

  let grouped;
  if (currency === 'INR') {
    if (whole.length <= 3) {
      grouped = whole;
    } else {
      const tail = whole.slice(-3);
      let head = whole.slice(0, -3);
      const chunks = [];
      while (head.length > 2) {
        chunks.unshift(head.slice(-2));
        head = head.slice(0, -2);
      }
      if (head) chunks.unshift(head);
      grouped = `${chunks.join(',')},${tail}`;
    }
  } else {
    grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  const symbol = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }[currency] || `${currency} `;
  return `${negative ? '-' : ''}${symbol}${grouped}.${fraction.padEnd(2, '0').slice(0, 2)}`;
}

/** Compact form for dashboard cards: ₹10.54L, ₹1.2Cr. */
export function moneyShort(value, currency = 'INR') {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(String(value));              // display only, never arithmetic
  if (!Number.isFinite(n)) return money(value, currency);
  const symbol = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }[currency] || '';
  const abs = Math.abs(n);
  if (currency === 'INR') {
    if (abs >= 1e7) return `${symbol}${(n / 1e7).toFixed(2)}Cr`;
    if (abs >= 1e5) return `${symbol}${(n / 1e5).toFixed(2)}L`;
    if (abs >= 1e3) return `${symbol}${(n / 1e3).toFixed(1)}K`;
  } else if (abs >= 1e6) return `${symbol}${(n / 1e6).toFixed(2)}M`;
  else if (abs >= 1e3) return `${symbol}${(n / 1e3).toFixed(1)}K`;
  return money(value, currency);
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : '—';
}

export function confidencePct(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—';
}

export function date(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function dateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function relativeAge(days) {
  if (days === null || days === undefined) return '—';
  if (days === 0) return 'today';
  if (days === 1) return '1 day';
  return `${days} days`;
}

// ----------------------------------------------------------------------
// Rule outcomes. The ⊘ / ✗ distinction is the whole point of Edge Case 2 and
// must be unmistakable — different glyph, different colour, different word.
// ----------------------------------------------------------------------
export const OUTCOME = {
  PASS: {
    glyph: '✓', label: 'Passed',
    className: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
    dot: 'bg-emerald-400',
  },
  FAIL: {
    glyph: '✗', label: 'Failed',
    className: 'text-rose-400 border-rose-500/30 bg-rose-500/10',
    dot: 'bg-rose-400',
  },
  WARN: {
    glyph: '⚠', label: 'Warning',
    className: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
    dot: 'bg-amber-400',
  },
  CANNOT_EVALUATE: {
    glyph: '⊘', label: 'Could not check',
    className: 'text-sky-300 border-sky-500/30 bg-sky-500/10',
    dot: 'bg-sky-300',
  },
  NOT_APPLICABLE: {
    glyph: '–', label: 'Not applicable',
    className: 'text-slate-500 border-ink-700 bg-ink-800',
    dot: 'bg-slate-600',
  },
};

export const SEVERITY = {
  BLOCKER: { label: 'Blocker', className: 'text-rose-300 border-rose-500/40 bg-rose-500/15' },
  CRITICAL: { label: 'Critical', className: 'text-orange-300 border-orange-500/40 bg-orange-500/15' },
  WARNING: { label: 'Warning', className: 'text-amber-300 border-amber-500/40 bg-amber-500/15' },
  INFO: { label: 'Info', className: 'text-slate-400 border-ink-700 bg-ink-800' },
};

export const RISK = {
  LOW: { label: 'Low', className: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/15', bar: 'bg-emerald-500' },
  MEDIUM: { label: 'Medium', className: 'text-amber-300 border-amber-500/40 bg-amber-500/15', bar: 'bg-amber-500' },
  HIGH: { label: 'High', className: 'text-orange-300 border-orange-500/40 bg-orange-500/15', bar: 'bg-orange-500' },
  SEVERE: { label: 'Severe', className: 'text-rose-300 border-rose-500/40 bg-rose-500/15', bar: 'bg-rose-500' },
};

export const DECISION = {
  AUTO_APPROVE: {
    label: 'Auto-approved', short: 'Auto-approved', glyph: '✓',
    className: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/15',
    blurb: 'Every applicable check passed and the amount sits below the unattended approval ceiling.',
  },
  APPROVE_PENDING_AUTHORISATION: {
    label: 'Approve — pending authorisation', short: 'Pending approval', glyph: '⇧',
    className: 'text-sky-300 border-sky-500/40 bg-sky-500/15',
    blurb: 'All checks passed, but the amount exceeds the unattended ceiling, so it needs a human signature.',
  },
  MANUAL_REVIEW: {
    label: 'Manual review', short: 'Review', glyph: '⚑',
    className: 'text-amber-300 border-amber-500/40 bg-amber-500/15',
    blurb: 'A material check failed, or several warnings clustered on one invoice.',
  },
  NEEDS_INFO: {
    label: 'Needs information', short: 'Needs info', glyph: '⊘',
    className: 'text-sky-300 border-sky-500/40 bg-sky-500/15',
    blurb: 'One or more required inputs could not be read reliably. This is not a finding against the invoice.',
  },
  REJECT: {
    label: 'Rejected', short: 'Rejected', glyph: '✗',
    className: 'text-rose-300 border-rose-500/40 bg-rose-500/15',
    blurb: 'A blocking check failed on something that cannot be satisfied.',
  },
  DUPLICATE_BLOCK: {
    label: 'Held as duplicate', short: 'Duplicate', glyph: '⧉',
    className: 'text-fuchsia-300 border-fuchsia-500/40 bg-fuchsia-500/15',
    blurb: 'Held, not rejected — the earlier submission may itself have been the error.',
  },
};

export const STATUS = {
  INGESTED: { label: 'Ingested', className: 'text-slate-400 border-ink-700 bg-ink-800' },
  EXTRACTING: { label: 'Extracting', className: 'text-sky-300 border-sky-500/30 bg-sky-500/10' },
  VALIDATING: { label: 'Validating', className: 'text-sky-300 border-sky-500/30 bg-sky-500/10' },
  PENDING_REVIEW: { label: 'Pending review', className: 'text-amber-300 border-amber-500/40 bg-amber-500/15' },
  PENDING_APPROVAL: { label: 'Pending approval', className: 'text-sky-300 border-sky-500/40 bg-sky-500/15' },
  APPROVED: { label: 'Approved', className: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/15' },
  REJECTED: { label: 'Rejected', className: 'text-rose-300 border-rose-500/40 bg-rose-500/15' },
  DUPLICATE_HELD: { label: 'Duplicate held', className: 'text-fuchsia-300 border-fuchsia-500/40 bg-fuchsia-500/15' },
  NEEDS_INFO: { label: 'Needs info', className: 'text-sky-300 border-sky-500/40 bg-sky-500/15' },
};

export const GATE_LABEL = {
  INGEST: 'Ingest',
  EXTRACTION: 'Extraction',
  VENDOR: 'Vendor',
  PURCHASE_ORDER: 'Purchase order',
  FINANCIAL: 'Financial',
  LINE_ITEMS: 'Line items',
  DUPLICATES: 'Duplicates',
  POLICY: 'Policy & routing',
};

/** Confidence banding for the bbox overlay (PRD 13.3). */
export function confidenceTier(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 'unknown';
  if (n >= 0.9) return 'high';
  if (n >= 0.7) return 'medium';
  return 'low';
}

export const CONFIDENCE_TIER = {
  high: { stroke: '#34d399', fill: 'rgba(52,211,153,0.14)', className: 'text-emerald-400' },
  medium: { stroke: '#fbbf24', fill: 'rgba(251,191,36,0.14)', className: 'text-amber-400' },
  low: { stroke: '#fb7185', fill: 'rgba(251,113,133,0.18)', className: 'text-rose-400' },
  unknown: { stroke: '#64748b', fill: 'rgba(100,116,139,0.12)', className: 'text-slate-500' },
};

/** 'header.grand_total' -> 'Grand total'; 'lines[2].unit_price' -> 'Line 2 · Unit price' */
export function fieldLabel(path) {
  if (!path) return '';
  const line = path.match(/^lines\[(\d+)\]\.(.+)$/);
  if (line) return `Line ${line[1]} · ${humanise(line[2])}`;
  return humanise(path.replace(/^header\./, ''));
}

function humanise(key) {
  return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}
