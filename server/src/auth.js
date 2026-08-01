/**
 * Authentication and role-based authorisation — PRD 16.
 *
 * Approval limits are enforced in two places on purpose. This layer rejects an
 * action the role may never perform; the Python engine re-checks the amount
 * against the actor's limit at the moment the ledger is settled. A UI gate is
 * not a control, and a caller who skips the UI must still be stopped.
 */

import jwt from 'jsonwebtoken';

// Demo secret. A real deployment reads this from a secret manager and rotates it.
const SECRET = process.env.JWT_SECRET || 'invoice-platform-dev-secret-do-not-ship';
const TOKEN_TTL = '12h';

export const ROLES = {
  AP_PROCESSOR: {
    label: 'AP Processor',
    can: ['invoice:read', 'invoice:upload', 'invoice:correct', 'invoice:confirm',
      'invoice:override', 'invoice:request-info', 'duplicate:review', 'rules:read'],
  },
  AP_MANAGER: {
    label: 'AP Manager',
    can: ['invoice:read', 'invoice:upload', 'invoice:correct', 'invoice:confirm',
      'invoice:override', 'invoice:request-info', 'invoice:authorise',
      'duplicate:review', 'duplicate:release', 'rules:read', 'analytics:read'],
  },
  CONTROLLER: {
    label: 'Finance Controller',
    can: ['invoice:read', 'invoice:upload', 'invoice:correct', 'invoice:confirm',
      'invoice:override', 'invoice:request-info', 'invoice:authorise',
      'duplicate:review', 'duplicate:release', 'rules:read', 'rules:write',
      'analytics:read', 'audit:read'],
  },
  // Read-only by construction: an auditor who can change anything is not an
  // auditor. Every mutating route checks a permission this role does not hold.
  AUDITOR: {
    label: 'Internal Auditor',
    can: ['invoice:read', 'rules:read', 'audit:read', 'analytics:read'],
  },
  ADMIN: {
    label: 'System Administrator',
    can: ['invoice:read', 'rules:read', 'rules:write', 'audit:read',
      'analytics:read', 'admin:write'],
  },
};

export function issueToken(user) {
  return jwt.sign(
    {
      sub: user.id,
      username: user.username,
      name: user.display_name,
      role: user.role,
      approvalLimit: user.approval_limit,
    },
    SECRET,
    { expiresIn: TOKEN_TTL },
  );
}

export function authenticate(req, res, next) {
  const header = req.headers.authorization || '';
  const bearer = header.startsWith('Bearer ') ? header.slice(7) : null;
  // EventSource cannot set headers, so the SSE route accepts the token as a
  // query parameter. It is short-lived and scoped to this origin.
  const token = bearer || req.query.token || req.cookies?.token;

  if (!token) {
    return res.status(401).json(envelopeError('UNAUTHENTICATED', 'Sign in required.'));
  }

  try {
    req.user = jwt.verify(token, SECRET);
    return next();
  } catch (err) {
    const code = err.name === 'TokenExpiredError' ? 'TOKEN_EXPIRED' : 'INVALID_TOKEN';
    return res.status(401).json(envelopeError(code, 'Your session is no longer valid.'));
  }
}

export function requirePermission(permission) {
  return (req, res, next) => {
    const role = ROLES[req.user?.role];
    if (!role) {
      return res.status(403).json(
        envelopeError('UNKNOWN_ROLE', `Role ${req.user?.role} is not recognised.`),
      );
    }
    if (!role.can.includes(permission)) {
      return res.status(403).json(
        envelopeError(
          'FORBIDDEN',
          `${role.label} cannot perform ${permission}.`,
          { role: req.user.role, required: permission },
        ),
      );
    }
    return next();
  };
}

export function envelopeError(code, detail, extra = {}) {
  return { data: null, meta: {}, errors: [{ code, detail, ...extra }] };
}

export function permissionsFor(role) {
  return ROLES[role]?.can ?? [];
}
