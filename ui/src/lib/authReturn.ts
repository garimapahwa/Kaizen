// Links in Supabase's emails (confirm your address, reset your password) send the reviewer back to this
// app with the outcome in the URL fragment: `#access_token=…&type=recovery`, or `#error=…`. The app routes
// with the fragment too (HashRouter), so it is read and cleared once, before the router sees it.

export type AuthReturn =
  | { kind: "recovery"; token: string }
  | { kind: "confirmed" }
  | { kind: "error"; message: string };

const EXPIRED = "That link has expired or was already used. Use “Forgot password” to get a new one.";

/** What a Supabase email link brought back, or null for an ordinary app URL such as `#/runs/…`. */
export function parseAuthReturn(hash: string): AuthReturn | null {
  const raw = hash.replace(/^#/, "");
  if (!raw || raw.startsWith("/")) return null;
  const p = new URLSearchParams(raw);
  if (p.get("error") || p.get("error_code")) {
    const code = p.get("error_code") ?? "";
    const text = p.get("error_description")?.replace(/\+/g, " ");
    return { kind: "error", message: code === "otp_expired" || !text ? EXPIRED : text };
  }
  const token = p.get("access_token");
  if (!token) return null;
  return p.get("type") === "recovery" ? { kind: "recovery", token } : { kind: "confirmed" };
}

let captured: AuthReturn | null = null;

/** Called once in main.tsx before the first render: keep the outcome, strip the token from the address bar
 *  (and so from history), and leave the router a normal `#/`. */
export function captureAuthReturn(): void {
  captured = parseAuthReturn(window.location.hash);
  if (captured) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/`);
}

export function takeCapturedAuthReturn(): AuthReturn | null {
  const out = captured;
  captured = null;
  return out;
}
