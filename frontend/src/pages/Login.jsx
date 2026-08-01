/**
 * Persona picker standing in for SSO.
 *
 * The roles are shown with their approval limits because the limits are real —
 * they are enforced server-side at the moment the ledger settles, not merely
 * used to grey out a button.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileSearch, ShieldCheck } from 'lucide-react';

import { api, session } from '../lib/api';
import { ErrorBanner, Spinner } from '../components/ui';
import { money } from '../lib/format';

export default function Login() {
  const navigate = useNavigate();
  const [personas, setPersonas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.personas()
      .then(({ data }) => setPersonas(data))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  async function signIn(username) {
    setBusy(username);
    setError(null);
    try {
      const { data } = await api.login(username);
      session.set(data);
      navigate('/dashboard');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-2xl">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2.5 mb-3">
            <FileSearch className="w-7 h-7 text-accent" aria-hidden="true" />
            <h1 className="text-xl font-semibold text-slate-100">
              Intelligent Invoice Processing &amp; Decisioning
            </h1>
          </div>
          <p className="text-sm text-slate-400">
            Ingestion → extraction → validation → explainable decision → human review
          </p>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2 mb-1">
            <ShieldCheck className="w-4 h-4 text-slate-500" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-slate-200">Choose a role</h2>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            Standing in for single sign-on. Permissions and approval limits are enforced
            on the server, not in this browser.
          </p>

          {error && <div className="mb-4"><ErrorBanner error={error} onDismiss={() => setError(null)} /></div>}

          {loading ? (
            <div className="flex justify-center py-10"><Spinner className="w-5 h-5 text-accent" /></div>
          ) : (
            <div className="space-y-2">
              {personas.map((p) => (
                <button
                  key={p.id}
                  onClick={() => signIn(p.username)}
                  disabled={!!busy}
                  className="w-full text-left p-3 rounded-md border border-ink-800 bg-ink-850
                             hover:border-accent/50 hover:bg-ink-800 transition-colors
                             disabled:opacity-50 flex items-start justify-between gap-4"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-100">{p.name}</span>
                      <span className="chip border-ink-700 bg-ink-900 text-slate-400">
                        {p.roleLabel}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{p.description}</p>
                  </div>
                  <div className="text-right shrink-0">
                    {busy === p.username ? (
                      <Spinner className="w-4 h-4 text-accent" />
                    ) : (
                      <>
                        <p className="text-[11px] text-slate-500">Approval limit</p>
                        <p className="mono text-xs text-slate-300">
                          {Number(p.approvalLimit) > 0 ? money(p.approvalLimit) : 'none'}
                        </p>
                      </>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <p className="text-center text-[11px] text-slate-600 mt-5">
          The rule engine and every monetary comparison are deterministic code.
          The language model extracts and explains; it never decides.
        </p>
      </div>
    </div>
  );
}
