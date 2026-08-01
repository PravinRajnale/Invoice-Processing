/**
 * Document viewer with bounding-box overlay — PRD 13.3.
 *
 * Bidirectional linking is the core interaction of this screen. Hovering a box
 * highlights its field; clicking a field scrolls the page and flashes the box.
 * That loop is what makes the extraction *verifiable* rather than merely
 * asserted — a reviewer can see, in one movement, that the number in the table
 * is the number on the paper.
 *
 * Boxes are normalised 0..1 of the page, so the overlay is resolution
 * independent and survives any zoom level.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';
import { CONFIDENCE_TIER, confidenceTier, fieldLabel } from '../lib/format';
import { session } from '../lib/api';

export default function DocumentViewer({
  invoiceId, pageCount = 1, fields = [], activeField, onFieldHover, onFieldClick,
}) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [loaded, setLoaded] = useState(false);
  const containerRef = useRef(null);

  const pageFields = useMemo(
    () => fields.filter((f) => f.bbox && (f.page_number || 1) === page),
    [fields, page],
  );

  // Follow the selection across pages — clicking a field on page 2 should take
  // the viewer there rather than silently doing nothing.
  useEffect(() => {
    if (!activeField) return;
    const field = fields.find((f) => f.field_path === activeField);
    if (field?.page_number && field.page_number !== page) {
      setPage(field.page_number);
    }
  }, [activeField, fields, page]);

  useEffect(() => setLoaded(false), [page, invoiceId]);

  const src = `/api/v1/invoices/${invoiceId}/page/${page}.png?dpi=170&token=${
    encodeURIComponent(session.token || '')}`;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-ink-800 bg-ink-900">
        <div className="flex items-center gap-1">
          <button className="btn-ghost px-2 py-1" disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)} aria-label="Previous page">
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <span className="text-xs text-slate-400 mono px-1.5">
            {page} / {pageCount}
          </span>
          <button className="btn-ghost px-2 py-1" disabled={page >= pageCount}
            onClick={() => setPage((p) => p + 1)} aria-label="Next page">
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="flex items-center gap-3">
          <Legend />
          <div className="flex items-center gap-1">
            <button className="btn-ghost px-2 py-1" onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))}
              aria-label="Zoom out">
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <span className="text-[11px] text-slate-500 mono w-9 text-center">
              {Math.round(zoom * 100)}%
            </span>
            <button className="btn-ghost px-2 py-1" onClick={() => setZoom((z) => Math.min(2.5, z + 0.2))}
              aria-label="Zoom in">
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      <div ref={containerRef} className="flex-1 overflow-auto bg-ink-950 p-4">
        <div className="relative mx-auto shadow-xl" style={{ width: `${zoom * 100}%`, maxWidth: '900px' }}>
          {!loaded && (
            <div className="absolute inset-0 flex items-center justify-center bg-ink-900 rounded"
              style={{ minHeight: 400 }}>
              <span className="text-xs text-slate-500">Rendering page {page}…</span>
            </div>
          )}

          <img
            src={src}
            alt={`Invoice page ${page}`}
            className="w-full block rounded"
            onLoad={() => setLoaded(true)}
          />

          {/* Overlay. viewBox 0..1 matches the normalised bbox coordinates. */}
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            className="absolute inset-0 w-full h-full"
            style={{ pointerEvents: 'none' }}
            aria-hidden="true"
          >
            {pageFields.map((field) => {
              const tier = CONFIDENCE_TIER[confidenceTier(field.confidence)];
              const isActive = activeField === field.field_path;
              return (
                <g key={field.field_path}>
                  <rect
                    x={field.bbox.x} y={field.bbox.y}
                    width={field.bbox.w} height={field.bbox.h}
                    fill={isActive ? 'rgba(79,142,247,0.30)' : tier.fill}
                    stroke={isActive ? '#4f8ef7' : tier.stroke}
                    strokeWidth={isActive ? 0.004 : 0.002}
                    vectorEffect="non-scaling-stroke"
                    className={isActive ? 'animate-flash' : ''}
                    style={{ pointerEvents: 'auto', cursor: 'pointer' }}
                    onMouseEnter={() => onFieldHover?.(field.field_path)}
                    onMouseLeave={() => onFieldHover?.(null)}
                    onClick={() => onFieldClick?.(field.field_path)}
                  >
                    <title>
                      {`${fieldLabel(field.field_path)}: ${field.normalised_value}\n`}
                      {`Confidence ${(Number(field.confidence) * 100).toFixed(1)}% · ${field.extraction_method}`}
                    </title>
                  </rect>
                </g>
              );
            })}
          </svg>
        </div>

        {pageFields.length === 0 && loaded && (
          <p className="text-center text-xs text-slate-600 mt-4">
            No field locations were resolved on this page. Values were still
            extracted — only the on-page position is unknown.
          </p>
        )}
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-2.5 text-[10px] text-slate-500">
      {[
        ['high', '≥90%'],
        ['medium', '70–89%'],
        ['low', '<70%'],
      ].map(([tier, label]) => (
        <span key={tier} className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm"
            style={{ background: CONFIDENCE_TIER[tier].fill, border: `1px solid ${CONFIDENCE_TIER[tier].stroke}` }} />
          {label}
        </span>
      ))}
    </div>
  );
}
