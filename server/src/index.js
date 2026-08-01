/**
 * API / BFF — PRD 14.
 *
 * Responsibilities: authentication, role-based authorisation, SSE relay, upload
 * handling, and shaping responses for the React client.
 *
 * Explicitly NOT a responsibility: any arithmetic on money, any threshold
 * comparison, any decision. Those live in the Python engine so they happen once,
 * in Decimal, in code an auditor can read (PRD 2.2.1, 6.2).
 */

import express from 'express';
import cors from 'cors';
import morgan from 'morgan';
import multer from 'multer';
import cookieParser from 'cookie-parser';

import {
  ROLES, authenticate, envelopeError, issueToken, permissionsFor, requirePermission,
} from './auth.js';
import {
  EngineError, call, engineHealth, relayBinary, relayStream, upload,
} from './engine.js';

const PORT = Number(process.env.PORT || 4000);
const app = express();

app.use(cors({
  origin: [
    process.env.FRONTEND_URL, 'http://localhost:5173',
    'http://localhost:5173', 'http://127.0.0.1:5173',
    'http://localhost:4173', 'http://127.0.0.1:4173',
  ].filter(Boolean),
  credentials: true,
}));
app.use(express.json({ limit: '2mb' }));
app.use(cookieParser());
app.use(morgan(':method :url :status :response-time[0]ms'));

const uploads = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 50 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    // A convenience filter, not a control: the engine sniffs magic bytes in its
    // pre-flight and ING-01 is what actually rejects a bad file. Browsers also
    // report inconsistent MIME types for the same format, so the extension is
    // accepted as a fallback rather than blocking a legitimate upload.
    const ACCEPTED = /^(application\/pdf|image\/(png|jpeg|jpg|tiff|bmp|webp|gif)|application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document|application\/octet-stream)$/;
    const EXT = /\.(pdf|png|jpe?g|tiff?|bmp|webp|gif|docx)$/i;
    if (ACCEPTED.test(file.mimetype) || EXT.test(file.originalname || '')) {
      return cb(null, true);
    }
    return cb(new Error(
      'Accepted formats: PDF, images (PNG, JPEG, TIFF, BMP, WEBP) and Word .docx.',
    ));
  },
});

const ok = (data, meta = {}) => ({ data, meta, errors: [] });

/** Wrap an async handler so engine errors surface with their original status. */
const route = (handler) => async (req, res, next) => {
  try {
    await handler(req, res, next);
  } catch (err) {
    if (err instanceof EngineError) {
      return res.status(err.status).json(err.body);
    }
    return next(err);
  }
};

// ======================================================================
// Public
// ======================================================================
app.get('/api/v1/health', route(async (_req, res) => {
  const engine = await engineHealth();
  res.json(ok({
    bff: 'ok',
    engine,
    degraded: engine.reachable ? !engine.llm_available : true,
  }));
}));

/**
 * Demo sign-in: pick a seeded persona. A real deployment puts SSO in front of
 * this and never issues a token without an identity provider.
 */
app.post('/api/v1/auth/login', route(async (req, res) => {
  const { username } = req.body || {};
  const { data: users } = await call('/users');
  const user = users.find((u) => u.username === username || u.id === username);

  if (!user) {
    return res.status(401).json(envelopeError('BAD_CREDENTIALS', 'Unknown user.'));
  }
  return res.json(ok({
    token: issueToken(user),
    user: {
      id: user.id,
      name: user.display_name,
      username: user.username,
      role: user.role,
      roleLabel: ROLES[user.role]?.label,
      approvalLimit: user.approval_limit,
      permissions: permissionsFor(user.role),
    },
  }));
}));

app.get('/api/v1/auth/personas', route(async (_req, res) => {
  const { data: users } = await call('/users');
  res.json(ok(users.map((u) => ({
    id: u.id,
    username: u.username,
    name: u.display_name,
    role: u.role,
    roleLabel: ROLES[u.role]?.label,
    approvalLimit: u.approval_limit,
    description: u._persona,
  }))));
}));

// Everything below requires a session.
app.use('/api/v1', authenticate);

app.get('/api/v1/auth/me', (req, res) => {
  res.json(ok({
    id: req.user.sub,
    name: req.user.name,
    role: req.user.role,
    roleLabel: ROLES[req.user.role]?.label,
    approvalLimit: req.user.approvalLimit,
    permissions: permissionsFor(req.user.role),
  }));
});

// ======================================================================
// Ingestion
// ======================================================================
app.post(
  '/api/v1/invoices',
  requirePermission('invoice:upload'),
  uploads.single('file'),
  route(async (req, res) => {
    if (!req.file) {
      return res.status(400).json(envelopeError('NO_FILE', 'Attach a PDF to upload.'));
    }
    const payload = await upload('/invoices', {
      buffer: req.file.buffer,
      filename: req.file.originalname,
      mimetype: req.file.mimetype,
      fields: { source: req.body.source || 'MANUAL_UPLOAD', uploaded_by: req.user.sub },
    });
    return res.status(202).json(payload);
  }),
);

app.post(
  '/api/v1/ingest/folder-scan',
  requirePermission('invoice:upload'),
  route(async (req, res) => {
    const form = new FormData();
    form.append('uploaded_by', req.user.sub);
    const response = await fetch(`${process.env.ENGINE_URL || 'http://127.0.0.1:8000'}/ingest/folder-scan`, {
      method: 'POST', body: form,
    });
    res.status(response.status).json(await response.json());
  }),
);

// ======================================================================
// Streaming
// ======================================================================
app.get(
  '/api/v1/invoices/:id/stream',
  requirePermission('invoice:read'),
  route(async (req, res) => {
    const trigger = req.query.trigger || 'INITIAL';
    await relayStream(
      `/invoices/${encodeURIComponent(req.params.id)}/stream?trigger=${encodeURIComponent(trigger)}`,
      req, res,
    );
  }),
);

// ======================================================================
// Retrieval
// ======================================================================
const READ_ROUTES = [
  ['/invoices', '/invoices'],
  ['/dashboard/summary', '/dashboard/summary'],
  ['/rules', '/rules'],
  ['/config', '/config'],
  ['/vendors', '/vendors'],
  ['/pos', '/pos'],
  ['/reason-codes', '/reason-codes'],
  ['/masters/procurement', '/masters/procurement'],
  ['/fixtures', '/fixtures'],
];

for (const [external, internal] of READ_ROUTES) {
  app.get(`/api/v1${external}`, requirePermission('invoice:read'), route(async (req, res) => {
    const query = new URLSearchParams(req.query).toString();
    res.json(await call(`${internal}${query ? `?${query}` : ''}`));
  }));
}

const invoiceSubRoutes = ['extraction', 'runs', 'duplicates', 'match'];
for (const sub of invoiceSubRoutes) {
  app.get(`/api/v1/invoices/:id/${sub}`, requirePermission('invoice:read'),
    route(async (req, res) => {
      res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/${sub}`));
    }));
}

app.get('/api/v1/invoices/:id', requirePermission('invoice:read'), route(async (req, res) => {
  res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}`));
}));

app.get('/api/v1/invoices/:id/runs/:runId/rules', requirePermission('invoice:read'),
  route(async (req, res) => {
    res.json(await call(
      `/invoices/${encodeURIComponent(req.params.id)}/runs/${encodeURIComponent(req.params.runId)}/rules`,
    ));
  }));

app.get('/api/v1/invoices/:id/document', requirePermission('invoice:read'),
  route(async (req, res) => {
    await relayBinary(`/invoices/${encodeURIComponent(req.params.id)}/document`, res);
  }));

// The procurement spreadsheet, as CSV or an Excel workbook.
app.get('/api/v1/masters/download/:sheet', requirePermission('invoice:read'),
  route(async (req, res) => {
    await relayBinary(`/masters/download/${encodeURIComponent(req.params.sheet)}`, res);
  }));

// Page images back the bbox overlay in the extraction review.
app.get('/api/v1/invoices/:id/page/:page.png', requirePermission('invoice:read'),
  route(async (req, res) => {
    const dpi = Number(req.query.dpi) || 150;
    await relayBinary(
      `/invoices/${encodeURIComponent(req.params.id)}/page/${encodeURIComponent(req.params.page)}.png?dpi=${dpi}`,
      res,
    );
  }));

app.get('/api/v1/pos/:poNumber/ledger', requirePermission('invoice:read'),
  route(async (req, res) => {
    res.json(await call(`/pos/${encodeURIComponent(req.params.poNumber)}/ledger`));
  }));

app.get('/api/v1/audit', requirePermission('audit:read'), route(async (req, res) => {
  const query = new URLSearchParams(req.query).toString();
  res.json(await call(`/audit${query ? `?${query}` : ''}`));
}));

app.get('/api/v1/audit/verify', requirePermission('audit:read'), route(async (_req, res) => {
  res.json(await call('/audit/verify'));
}));

// ======================================================================
// Human actions
// ======================================================================
app.patch('/api/v1/invoices/:id/fields', requirePermission('invoice:correct'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/fields`, {
      method: 'PATCH',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/decision', requirePermission('invoice:confirm'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/decision`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/override/requirements', requirePermission('invoice:override'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/override/requirements`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/override', requirePermission('invoice:override'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/override`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/duplicate-release', requirePermission('duplicate:release'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/duplicate-release`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/duplicate-confirm', requirePermission('duplicate:review'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/duplicate-confirm`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/request-info', requirePermission('invoice:request-info'),
  route(async (req, res) => {
    res.json(await call(`/invoices/${encodeURIComponent(req.params.id)}/request-info`, {
      method: 'POST',
      body: { ...req.body, actorId: req.user.sub },
    }));
  }));

app.post('/api/v1/invoices/:id/replay', requirePermission('audit:read'),
  route(async (req, res) => {
    const query = new URLSearchParams(req.query).toString();
    res.json(await call(
      `/invoices/${encodeURIComponent(req.params.id)}/replay${query ? `?${query}` : ''}`,
      { method: 'POST' },
    ));
  }));

app.post('/api/v1/admin/reset', requirePermission('admin:write'), route(async (_req, res) => {
  res.json(await call('/admin/reset', { method: 'POST' }));
}));

// ======================================================================
app.use((req, res) => {
  res.status(404).json(envelopeError('NOT_FOUND', `No route for ${req.method} ${req.path}`));
});

app.use((err, _req, res, _next) => {
  if (err?.code === 'LIMIT_FILE_SIZE') {
    return res.status(413).json(envelopeError('FILE_TOO_LARGE', 'Maximum upload size is 50MB.'));
  }
  console.error('[bff]', err);
  return res.status(500).json(envelopeError('INTERNAL_ERROR', err.message || 'Unexpected error.'));
});

app.listen(PORT, () => {
  console.log(`[bff] listening on http://localhost:${PORT}`);
  console.log(`[bff] engine at ${process.env.ENGINE_URL || 'http://127.0.0.1:8000'}`);
});
