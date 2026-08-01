import { useEffect, useState } from 'react';
import {
  HashRouter, Navigate, NavLink, Route, Routes, useNavigate,
} from 'react-router-dom';
import {
  Activity, BarChart3, FileSearch, LayoutDashboard, ListChecks, LogOut,
  ScrollText, ShieldCheck, Table2, Upload, WifiOff,
} from 'lucide-react';

import { api, session } from './lib/api';
import { Spinner } from './components/ui';

import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Ingestion from './pages/Ingestion';
import Procurement from './pages/Procurement';
import Workspace from './pages/Workspace';
import PoLedger from './pages/PoLedger';
import DuplicateCompare from './pages/DuplicateCompare';
import RuleConfig from './pages/RuleConfig';
import AuditTrail from './pages/AuditTrail';
import Analytics from './pages/Analytics';

const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/ingest', label: 'Ingestion', icon: Upload },
  { to: '/procurement', label: 'Procurement', icon: Table2 },
  { to: '/rules', label: 'Rule catalogue', icon: ListChecks, permission: 'rules:read' },
  { to: '/analytics', label: 'Analytics', icon: BarChart3, permission: 'analytics:read' },
  { to: '/audit', label: 'Audit trail', icon: ScrollText, permission: 'audit:read' },
];

function Shell({ children }) {
  const navigate = useNavigate();
  const [health, setHealth] = useState(null);
  const user = session.user;

  useEffect(() => {
    let alive = true;
    const poll = () => api.health()
      .then(({ data }) => alive && setHealth(data))
      .catch(() => alive && setHealth({ engine: { reachable: false } }));
    poll();
    const timer = setInterval(poll, 30000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const degraded = health && !health.engine?.llm_available;
  const offline = health && !health.engine?.reachable;

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 bg-ink-900 border-r border-ink-800 flex flex-col">
        <div className="px-4 py-4 border-b border-ink-800">
          <div className="flex items-center gap-2">
            <FileSearch className="w-5 h-5 text-accent" aria-hidden="true" />
            <span className="font-semibold text-slate-100 text-sm leading-tight">
              Invoice Decisioning
            </span>
          </div>
          <p className="text-[11px] text-slate-500 mt-1">
            Deterministic rules · AI at the edges
          </p>
        </div>

        <nav className="flex-1 p-2 space-y-0.5">
          {NAV.filter((item) => !item.permission || session.can(item.permission)).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm
                transition-colors ${isActive
                  ? 'bg-accent/15 text-accent border border-accent/30'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-ink-850 border border-transparent'}`}
            >
              <item.icon className="w-4 h-4" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="p-3 border-t border-ink-800 space-y-2">
          {offline && (
            <div className="flex items-center gap-2 px-2 py-1.5 rounded text-[11px]
                            text-rose-300 bg-rose-500/10 border border-rose-500/30">
              <WifiOff className="w-3 h-3" aria-hidden="true" />
              Engine unreachable
            </div>
          )}
          {!offline && degraded && (
            <div className="flex items-center gap-2 px-2 py-1.5 rounded text-[11px]
                            text-amber-300 bg-amber-500/10 border border-amber-500/30"
              title="No language model is configured. Extraction falls back to recorded payloads and rules still evaluate — the system reaches a defensible decision without the model.">
              <ShieldCheck className="w-3 h-3" aria-hidden="true" />
              Deterministic-only mode
            </div>
          )}
          {health?.engine?.rules && (
            <p className="text-[11px] text-slate-600 px-2">
              {health.engine.rules.active} active rules · v{health.engine.ruleset_version}
            </p>
          )}

          <div className="flex items-center justify-between px-2 pt-1">
            <div className="min-w-0">
              <p className="text-xs text-slate-300 truncate">{user?.name}</p>
              <p className="text-[11px] text-slate-500 truncate">{user?.roleLabel}</p>
            </div>
            <button
              onClick={() => { session.clear(); navigate('/login'); }}
              className="text-slate-500 hover:text-slate-200 p-1"
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
    </div>
  );
}

function Protected({ children }) {
  const [checked, setChecked] = useState(false);
  const [valid, setValid] = useState(false);

  useEffect(() => {
    if (!session.token) { setChecked(true); return; }
    api.me()
      .then(() => { setValid(true); setChecked(true); })
      .catch(() => { session.clear(); setChecked(true); });
  }, []);

  if (!checked) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner className="w-6 h-6 text-accent" />
      </div>
    );
  }
  if (!valid) return <Navigate to="/login" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Protected><Dashboard /></Protected>} />
        <Route path="/ingest" element={<Protected><Ingestion /></Protected>} />
        <Route path="/invoices/:id" element={<Protected><Workspace /></Protected>} />
        <Route path="/invoices/:id/duplicates" element={<Protected><DuplicateCompare /></Protected>} />
        <Route path="/pos/:poNumber" element={<Protected><PoLedger /></Protected>} />
        <Route path="/procurement" element={<Protected><Procurement /></Protected>} />
        <Route path="/rules" element={<Protected><RuleConfig /></Protected>} />
        <Route path="/audit" element={<Protected><AuditTrail /></Protected>} />
        <Route path="/analytics" element={<Protected><Analytics /></Protected>} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </HashRouter>
  );
}
