// App shell: a BD-navy navigation rail, a top bar with the run switcher, the theme toggle and the
// reviewer's identity, and the page. Routes and behaviour are unchanged from the first build.
import { ChartBar, ClipboardText, Files, Folders, GitDiff, Lightbulb, ListChecks, SignOut, SquaresFour, TextAa } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, matchPath, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { displayName, enc } from "../lib/format";
import { useReviewer } from "../lib/reviewer";
import { useAsync } from "../lib/useAsync";
import { SignIn } from "./SignIn";
import { ThemeToggle } from "./ThemeToggle";
import { Button } from "./ui";

const LAST_RUN_KEY = "kaizen.lastRun";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

export function Layout() {
  const loc = useLocation();
  const nav = useNavigate();
  const match = matchPath("/runs/:runId/*", loc.pathname) ?? matchPath("/runs/:runId", loc.pathname);
  const routeRun = match?.params.runId ?? null;
  const [lastRun, setLastRun] = useState<string | null>(() => {
    try {
      return localStorage.getItem(LAST_RUN_KEY);
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!routeRun) return;
    setLastRun(routeRun);
    try {
      localStorage.setItem(LAST_RUN_KEY, routeRun);
    } catch {
      /* ignore */
    }
  }, [routeRun]);
  const runId = routeRun ?? lastRun;
  const runs = useAsync(() => api.listRuns(), [routeRun]);
  const { session, policy, loading, signOut } = useReviewer();

  if (loading) return <div className="p-6 text-sm text-ink-3">Loading…</div>;
  if (!session && policy === "required") return <SignIn />;

  const r = (suffix: string) => (runId ? `/runs/${enc(runId)}${suffix}` : null);
  const item = (to: string | null, label: string, icon: ReactNode, end = false) =>
    to ? (
      <NavLink
        to={to}
        end={end}
        title={label}
        className={({ isActive }) =>
          `relative flex items-center justify-center lg:justify-start gap-2.5 h-9 px-0 lg:pl-4 lg:pr-3 mx-2 rounded-md text-sm no-underline transition-colors duration-150 ${
            isActive ? "bg-rail-ink/10 text-rail-ink font-medium" : "text-rail-muted hover:bg-rail-ink/5 hover:text-rail-ink"
          }`
        }
      >
        {({ isActive }) => (
          <>
            {isActive && <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r bg-accent-500" aria-hidden />}
            <span className="shrink-0 opacity-90">{icon}</span>
            <span className="truncate hidden lg:inline">{label}</span>
          </>
        )}
      </NavLink>
    ) : (
      <span className="flex items-center justify-center lg:justify-start gap-2.5 h-9 px-0 lg:pl-4 lg:pr-3 mx-2 rounded-md text-sm text-rail-dim/70 cursor-default" title={`${label}: select a run first`}>
        <span className="shrink-0">{icon}</span>
        <span className="truncate hidden lg:inline">{label}</span>
      </span>
    );

  return (
    <div className="min-h-screen flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] btn btn-primary">
        Skip to content
      </a>
      <nav className="rail w-16 lg:w-[232px] shrink-0 bg-rail text-rail-ink flex flex-col sticky top-0 h-screen transition-[width] duration-200 ease-out" aria-label="Main">
        <NavLink to="/" className="flex items-center gap-2.5 h-14 px-4 lg:px-5 no-underline text-rail-ink" title="Kaizen Cross-Check">
          <span className="w-7 h-7 rounded-md bg-accent-500 grid place-items-center font-semibold text-accent-ink text-sm shrink-0" aria-hidden>
            K
          </span>
          <span className="leading-tight hidden lg:block">
            <span className="block text-sm font-semibold tracking-tight">Kaizen</span>
            <span className="block text-2xs text-rail-muted tracking-wide">Cross-Check</span>
          </span>
        </NavLink>
        <div className="mt-2 text-2xs font-medium tracking-wider text-rail-dim px-6 pb-1 hidden lg:block">WORKSPACE</div>
        {item("/", "Runs", <Folders size={18} />, true)}
        {item("/terminology", "Terminology", <TextAa size={18} />)}
        {item("/action-items", "Action items", <ClipboardText size={18} />)}
        <div className="mt-5 text-2xs font-medium tracking-wider text-rail-dim px-6 pb-1 hidden lg:block">THIS RUN</div>
        <div className="px-6 pb-1.5 mono text-2xs text-rail-muted/80 truncate hidden lg:block" title={runId ?? undefined}>
          {runId ?? "none selected"}
        </div>
        {item(r(""), "Dashboard", <SquaresFour size={18} />, true)}
        {item(r("/review"), "Review queue", <ListChecks size={18} />)}
        {item(r("/documents"), "Documents", <Files size={18} />)}
        {item(r("/mining"), "Worklist and mining", <Lightbulb size={18} />)}
        {item(r("/business"), "Business case", <ChartBar size={18} />)}
        {item(r("/diff"), "Compare runs", <GitDiff size={18} />)}
        <div className="mt-auto px-5 py-4 text-2xs text-rail-dim leading-4 hidden lg:block">The engine recommends. The reviewer decides. Every value is traceable to a page and a box.</div>
      </nav>

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 bg-surface border-b border-line flex items-center gap-4 px-5 sticky top-0 z-30">
          <label className="flex items-center gap-2 text-sm min-w-0 flex-1 max-w-[26rem]">
            <span className="text-ink-3 shrink-0 hidden sm:inline">Run</span>
            <select className="input input-sm mono min-w-0 w-full" value={runId ?? ""} onChange={(e) => e.target.value && nav(`/runs/${enc(e.target.value)}`)} aria-label="Current run">
              <option value="">Select a run</option>
              {(runs.data ?? []).map((x) => (
                <option key={x.run_id} value={x.run_id}>
                  {x.run_id} · {x.summary.skus} SKUs · {x.summary.rows} rows
                </option>
              ))}
              {runId && !(runs.data ?? []).some((x) => x.run_id === runId) && <option value={runId}>{runId}</option>}
            </select>
          </label>
          <div className="ml-auto flex items-center gap-3 shrink-0">
            <ThemeToggle />
            {session ? (
              <div className="flex items-center gap-2.5" title={`${session.reviewer} · reviewer ${session.slot}${session.blind ? " · blind" : ""}. Identity, slot and blind mode are held by the server for this session.`}>
                <span className="w-8 h-8 rounded-full bg-brand-100 text-brand-700 grid place-items-center text-xs font-semibold shrink-0" aria-hidden>
                  {initials(displayName(session.reviewer))}
                </span>
                <span className="leading-tight hidden md:block whitespace-nowrap">
                  <span className="block text-sm font-medium text-ink">{displayName(session.reviewer)}</span>
                  <span className="block text-2xs text-ink-3">{session.slot === 1 ? "Reviewer 1 · facilitator" : "Reviewer 2 · independent"}</span>
                </span>
                {session.blind && <span className="chip bg-brand-100 text-brand-700 border-brand-200">Blind</span>}
                <Button variant="ghost" size="sm" onClick={() => void signOut()} icon={<SignOut size={16} />} title="End this review session">
                  Sign out
                </Button>
              </div>
            ) : (
              <span className="text-sm text-ink-3">Not signed in</span>
            )}
          </div>
        </header>
        <main id="main" className="flex-1 min-w-0 w-full max-w-page mx-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
