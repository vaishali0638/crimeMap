import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Sign In — CRIMEMAP",
};

interface LoginPageProps {
  searchParams: Promise<{
    returnTo?: string;
    error?: string;
  }>;
}

const DEMO_ROLES = [
  {
    id: "demo.analyst",
    title: "Crime Analyst",
    user: "demo.analyst",
    desc: "Analytical workbench, Puducherry & Karaikal maps, pattern reviews, and evidence inspection",
    badge: "Analyst",
  },
  {
    id: "demo.supervisor",
    title: "Region Supervisor",
    user: "demo.supervisor",
    desc: "Multi-station oversight, trend boards, and action approvals across regions",
    badge: "Supervisor",
  },
  {
    id: "demo.sho",
    title: "Station House Officer",
    user: "demo.sho",
    desc: "Station dashboard, incident details, and operational review",
    badge: "SHO",
  },
  {
    id: "demo.officer",
    title: "Station Officer",
    user: "demo.officer",
    desc: "Assigned station/beat queries and action completions",
    badge: "Officer",
  },
  {
    id: "demo.steward",
    title: "Data Steward",
    user: "demo.steward",
    desc: "Data quality queue, duplicates, and AI insight validation",
    badge: "Steward",
  },
  {
    id: "demo.admin",
    title: "System Administrator",
    user: "demo.admin",
    desc: "System configuration, AI model registry, boundary approvals, and audit review",
    badge: "Admin",
  },
];

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { returnTo = "/", error } = await searchParams;

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col justify-between p-4 sm:p-6 lg:p-8 font-sans">
      <header className="max-w-4xl mx-auto w-full flex items-center justify-between border-b border-slate-800 pb-4">
        <div className="flex items-center space-x-3">
          <div className="h-9 w-9 rounded bg-teal-600 flex items-center justify-center font-bold text-white text-lg tracking-wider">
            PY
          </div>
          <div>
            <h1 className="font-semibold text-base sm:text-lg tracking-tight text-white">CRIMEMAP</h1>
            <p className="text-xs text-slate-400">Puducherry Police Geospatial Intelligence & Decision-Support</p>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <span className="text-xs font-semibold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
            DEMO / SYNTHETIC DATA
          </span>
          <span className="text-xs font-semibold px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
            RESTRICTED
          </span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto w-full my-6">
        <div className="bg-slate-950/90 border border-slate-800 rounded-lg p-6 sm:p-8 shadow-xl backdrop-blur-sm">
          {/* Primary Hero 1-Click Sign In Banner */}
          <div className="mb-8 rounded-lg border border-teal-500/40 bg-teal-950/40 p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <span className="inline-block text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-teal-500/20 text-teal-300 mb-1.5">
                Instant Demo Access
              </span>
              <h2 className="text-xl font-bold text-white tracking-tight">
                1-Click Sign In to CRIMEMAP
              </h2>
              <p className="text-xs text-slate-300 mt-1">
                Sign in immediately with full Puducherry & Karaikal map access, PY Dashboard, Analytics, and AI Insights (auto-verifies OIDC + MFA).
              </p>
            </div>
            <form method="POST" action="/bff/auth/demo-login" className="shrink-0">
              <input type="hidden" name="username" value="demo.analyst" />
              <input type="hidden" name="returnTo" value={returnTo} />
              <button
                type="submit"
                className="w-full sm:w-auto min-h-12 px-6 py-3 rounded-md bg-teal-500 hover:bg-teal-400 text-slate-950 font-bold text-sm tracking-wide shadow-lg transition-all cursor-pointer"
              >
                ⚡ 1-Click Sign In Now →
              </button>
            </form>
          </div>

          {error && (
            <div className="mb-6 p-3 rounded bg-red-950/50 border border-red-800/80 text-red-200 text-sm flex items-start space-x-2">
              <span className="font-bold">⚠️</span>
              <div>
                <p className="font-semibold">Sign-in Notice</p>
                <p className="text-xs text-red-300">{error}</p>
              </div>
            </div>
          )}

          {/* Quick 1-Click Role Login */}
          <div className="mb-8">
            <h3 className="text-xs font-bold uppercase tracking-wider text-teal-400 mb-3">
              Or Choose a Specific Police Role (1-Click Sign In)
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {DEMO_ROLES.map((role) => (
                <form key={role.id} method="POST" action="/bff/auth/demo-login">
                  <input type="hidden" name="username" value={role.user} />
                  <input type="hidden" name="returnTo" value={returnTo} />
                  <button
                    type="submit"
                    className="w-full text-left p-3.5 rounded border border-slate-800 bg-slate-900/90 hover:bg-slate-800 hover:border-teal-500/60 transition-all group flex flex-col justify-between h-32 cursor-pointer"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-sm text-slate-100 group-hover:text-teal-300">
                        {role.title}
                      </span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-teal-300 border border-teal-500/30">
                        1-Click
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 line-clamp-2 mt-1">{role.desc}</p>
                    <div className="text-xs text-teal-400 font-semibold mt-2 flex items-center justify-between">
                      <span>Sign in as {role.badge}</span>
                      <span>→</span>
                    </div>
                  </button>
                </form>
              ))}
            </div>
          </div>

          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-slate-800"></div>
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-slate-950 px-2 text-slate-500 font-semibold tracking-wider">
                Or Sign In With Fixed Username & Password
              </span>
            </div>
          </div>

          {/* Standard Form */}
          <form method="POST" action="/bff/auth/demo-login" className="max-w-md mx-auto space-y-4">
            <input type="hidden" name="returnTo" value={returnTo} />
            <div>
              <label htmlFor="username" className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1">
                Username
              </label>
              <input
                id="username"
                name="username"
                type="text"
                defaultValue="analyst"
                placeholder="e.g. analyst or admin"
                required
                className="w-full px-3 py-2 rounded bg-slate-900 border border-slate-700 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
              <p className="text-[11px] text-slate-400 mt-1">
                Accepted: <code className="text-teal-300">analyst</code>, <code className="text-teal-300">admin</code>, <code className="text-teal-300">supervisor</code>, <code className="text-teal-300">sho</code>, <code className="text-teal-300">officer</code>, <code className="text-teal-300">steward</code>
              </p>
            </div>

            <div>
              <label htmlFor="password" className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                defaultValue="demo123"
                placeholder="Password"
                required
                className="w-full px-3 py-2 rounded bg-slate-900 border border-slate-700 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
              <p className="text-[11px] text-slate-400 mt-1">
                Fixed Demo Password: <code className="text-teal-300">demo123</code> (or <code className="text-teal-300">admin</code>)
              </p>
            </div>

            <button
              type="submit"
              className="w-full py-2.5 px-4 rounded bg-teal-600 hover:bg-teal-500 text-white font-semibold text-sm transition-colors shadow-sm cursor-pointer"
            >
              Sign In with Credentials
            </button>
          </form>

          <div className="mt-8 pt-4 border-t border-slate-800/80 text-center text-xs text-slate-500">
            <span>Need advanced OIDC testing? </span>
            <Link
              href={`/bff/auth/login?returnTo=${encodeURIComponent(returnTo)}`}
              className="text-slate-400 hover:text-teal-300 underline underline-offset-2"
            >
              Use standard Keycloak OIDC login with manual TOTP
            </Link>
          </div>
        </div>
      </main>

      <footer className="max-w-4xl mx-auto w-full text-center text-xs text-slate-500 border-t border-slate-800 pt-4">
        CRIMEMAP — Internal Police Decision-Support Platform · Hackathon Sandbox Mode · No real government records accessed
      </footer>
    </div>
  );
}
