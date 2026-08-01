/**
 * Client for the Python processing service.
 *
 * This layer forwards and relays. It never parses an amount, never compares a
 * threshold, and never decides anything — every monetary value stays a string
 * from the moment Python emits it until React renders it. JavaScript `Number`
 * must not touch money (PRD 6.2, 18).
 */

const ENGINE_URL = process.env.ENGINE_URL || 'http://127.0.0.1:8000';

export class EngineError extends Error {
  constructor(status, body) {
    super(body?.errors?.[0]?.detail || `Engine returned ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function parse(response) {
  const text = await response.text();
  if (!text) return { data: null, meta: {}, errors: [] };
  try {
    return JSON.parse(text);
  } catch {
    return { data: null, meta: {}, errors: [{ code: 'BAD_GATEWAY', detail: text.slice(0, 500) }] };
  }
}

export async function call(path, { method = 'GET', body, headers = {} } = {}) {
  const response = await fetch(`${ENGINE_URL}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json', ...headers } : headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  const payload = await parse(response);
  if (!response.ok) throw new EngineError(response.status, payload);
  return payload;
}

/** Multipart passthrough for uploads. */
export async function upload(path, { buffer, filename, mimetype, fields = {} }) {
  const form = new FormData();
  form.append('file', new Blob([buffer], { type: mimetype || 'application/pdf' }), filename);
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) form.append(key, String(value));
  }

  const response = await fetch(`${ENGINE_URL}${path}`, { method: 'POST', body: form });
  const payload = await parse(response);
  if (!response.ok) throw new EngineError(response.status, payload);
  return payload;
}

/**
 * Relay a server-sent event stream straight through to the browser.
 *
 * The body is piped rather than buffered so rules appear in the UI as they are
 * evaluated. If the client disconnects mid-stream the upstream request is
 * aborted, so a reviewer navigating away does not leave the engine working.
 */
export async function relayStream(path, req, res) {
  const controller = new AbortController();
  req.on('close', () => controller.abort());

  let upstream;
  try {
    upstream = await fetch(`${ENGINE_URL}${path}`, {
      headers: { Accept: 'text/event-stream' },
      signal: controller.signal,
    });
  } catch (err) {
    if (controller.signal.aborted) return;
    res.status(502);
    res.setHeader('Content-Type', 'text/event-stream');
    res.write(`event: error\ndata: ${JSON.stringify({
      message: 'Processing service unavailable', detail: err.message,
    })}\n\n`);
    return res.end();
  }

  if (!upstream.ok || !upstream.body) {
    const payload = await parse(upstream);
    res.setHeader('Content-Type', 'text/event-stream');
    res.write(`event: error\ndata: ${JSON.stringify(
      payload.errors?.[0] || { message: `Engine returned ${upstream.status}` },
    )}\n\n`);
    return res.end();
  }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders?.();

  const reader = upstream.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
  } catch (err) {
    if (!controller.signal.aborted) {
      res.write(`event: error\ndata: ${JSON.stringify({ message: err.message })}\n\n`);
    }
  } finally {
    res.end();
  }
}

/** Binary passthrough, used for the PDF the reviewer looks at. */
export async function relayBinary(path, res) {
  const upstream = await fetch(`${ENGINE_URL}${path}`);
  if (!upstream.ok) {
    const payload = await parse(upstream);
    throw new EngineError(upstream.status, payload);
  }
  res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/pdf');
  const disposition = upstream.headers.get('content-disposition');
  if (disposition) res.setHeader('Content-Disposition', disposition);
  res.send(Buffer.from(await upstream.arrayBuffer()));
}

export async function engineHealth() {
  try {
    const payload = await call('/health');
    return { reachable: true, ...payload.data };
  } catch (err) {
    return { reachable: false, error: err.message };
  }
}

export { ENGINE_URL };
