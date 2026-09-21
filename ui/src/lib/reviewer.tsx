// Reviewer identity is a server-side session, not browser state. Identity is a BD email address backed
// by a password; the slot and the blind flag are decided when the session is opened and are enforced by
// the backend for every request; the token lives in an HttpOnly cookie this code cannot read. Blind mode
// therefore cannot be switched off from the browser — that is the whole point. See docs/security-review.md.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import type { AccountsBackend, ReviewSession, ViewerParams } from "../types";

interface Ctx {
  session: ReviewSession | null;
  policy: string;
  loading: boolean;
  error: string | null;
  /** Where passwords are checked: this workspace ("local") or Supabase. Decides the forgotten-password advice. */
  accounts: AccountsBackend;
  /** Create an account for a BD address. Does not sign in: the slot is chosen on sign-in. */
  signUp: (email: string, password: string) => Promise<void>;
  signIn: (email: string, password: string, slot: 1 | 2, blind?: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  /** Refetch key: changes when the session changes, so pages reload with the right visibility. */
  viewerParams: ViewerParams;
  /** Reviewer identity — the verified email address — or "" when nobody is signed in. */
  name: string;
}

const ReviewerContext = createContext<Ctx | null>(null);

export function ReviewerProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [policy, setPolicy] = useState<string>("required");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<AccountsBackend>("local");

  useEffect(() => {
    let live = true;
    api
      .currentSession()
      .then((r) => {
        if (!live) return;
        setSession(r.session);
        setPolicy(r.blind_review_policy);
        setAccounts(r.accounts ?? "local");
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    setError(null);
    await api.signUp(email, password);
  }, []);

  const signIn = useCallback(async (email: string, password: string, slot: 1 | 2, blind?: boolean) => {
    setError(null);
    const s = await api.signIn(email, password, slot, blind);
    setSession(s);
    setPolicy(s.blind_review_policy);
  }, []);

  const signOut = useCallback(async () => {
    await api.signOut();
    setSession(null);
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      session,
      policy,
      loading,
      error,
      accounts,
      signUp,
      signIn,
      signOut,
      viewerParams: { viewer: session?.slot ?? 1, blind: session?.blind ?? false },
      name: session?.reviewer ?? "",
    }),
    [session, policy, loading, error, accounts, signUp, signIn, signOut],
  );
  return <ReviewerContext.Provider value={value}>{children}</ReviewerContext.Provider>;
}

export function useReviewer(): Ctx {
  const c = useContext(ReviewerContext);
  if (!c) throw new Error("ReviewerProvider is missing");
  return c;
}
