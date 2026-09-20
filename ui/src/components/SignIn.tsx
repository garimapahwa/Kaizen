// Sign-in gate, with sign-up beside it. Reviewers identify themselves before they can see or record
// decisions: every decision is stored against a BD email address, and blind review only means something
// if the server decides who is blind. Forgotten passwords are cleared by an administrator
// (`kaizen users reset`), after which the reviewer signs up again — there is no mail server to send a
// reset link from.
import { Eye, EyeSlash, UserCircle, UsersThree } from "@phosphor-icons/react";
import { useState } from "react";
import { useReviewer } from "../lib/reviewer";
import { ThemeToggle } from "./ThemeToggle";
import { Button, Field } from "./ui";

const BD_DOMAIN = "@bd.com";
const BAD_DOMAIN = "Only BD email addresses can sign in";
const MIN_PASSWORD = 10;

type Mode = "signin" | "signup";

/** A password box with a reveal toggle. Typing a long password blind is where most typos come from. */
function PasswordInput({ id, value, onChange, autoComplete }: { id: string; value: string; onChange: (v: string) => void; autoComplete: string }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input id={id} type={shown ? "text" : "password"} className="input w-full pr-10" autoComplete={autoComplete} value={value} onChange={(e) => onChange(e.target.value)} />
      <button
        type="button"
        onClick={() => setShown((s) => !s)}
        className="absolute inset-y-0 right-0 w-10 grid place-items-center text-ink-3 hover:text-ink rounded-r-md"
        aria-label={shown ? "Hide password" : "Show password"}
        aria-pressed={shown}
        title={shown ? "Hide password" : "Show password"}
      >
        {shown ? <EyeSlash size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );
}

export function SignIn() {
  const { signUp, signIn, policy, error } = useReviewer();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [slot, setSlot] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const required = policy === "required";

  const address = email.trim().toLowerCase();
  const looksBd = address.endsWith(BD_DOMAIN) && address.length > BD_DOMAIN.length;
  const wrongDomain = address.length > 0 && !looksBd;
  const tooShort = mode === "signup" && password.length > 0 && password.length < MIN_PASSWORD;
  const mismatch = mode === "signup" && confirm.length > 0 && password !== confirm;
  const ready = mode === "signin" ? looksBd && password.length > 0 : looksBd && password.length >= MIN_PASSWORD && password === confirm;

  function switchTo(next: Mode) {
    setMode(next);
    setPassword("");
    setConfirm("");
    setFailed(null);
    setNotice(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFailed(null);
    setNotice(null);
    try {
      if (mode === "signup") {
        await signUp(address, password);
        setMode("signin");
        setPassword("");
        setConfirm("");
        setNotice("Account created. Sign in to start reviewing.");
      } else {
        await signIn(address, password, slot);
      }
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const roles: { n: 1 | 2; title: string; body: string; icon: React.ReactNode }[] = [
    { n: 1, title: "Reviewer 1 · BOM facilitator", body: "Prepares the review and records the first decision on each row.", icon: <UserCircle size={22} /> },
    {
      n: 2,
      title: "Reviewer 2 · independent",
      body: required ? "Reviews blind: reviewer 1's decision on a row stays hidden until you record your own." : "Second review. Blind mode is optional in this workspace.",
      icon: <UsersThree size={22} />,
    },
  ];

  return (
    <div className="min-h-screen bg-canvas flex items-center justify-center p-6">
      <div className="fixed top-3 right-3">
        <ThemeToggle />
      </div>
      <form onSubmit={submit} className="w-full max-w-[30rem] card shadow-pop p-7 enter-pop">
        <div className="flex items-center gap-3 mb-6">
          <span className="w-9 h-9 rounded-md bg-accent-500 grid place-items-center font-semibold text-accent-ink" aria-hidden>
            K
          </span>
          <div className="leading-tight">
            <div className="text-lg font-semibold tracking-tight">Kaizen Cross-Check</div>
            <div className="text-xs text-ink-3">BOM · label · drawing · PCO review</div>
          </div>
        </div>

        <h1 className="text-2xl mb-1">{mode === "signin" ? "Sign in to review" : "Create your account"}</h1>
        <p className="text-sm text-ink-3 mb-5">
          {mode === "signin"
            ? "Every decision is recorded against your BD email address. Choose the slot you are reviewing in."
            : `Use your BD email address. Pick a password of at least ${MIN_PASSWORD} characters.`}
        </p>

        <Field label="BD email address" htmlFor="reviewer-email" className="mb-4" error={wrongDomain ? BAD_DOMAIN : undefined}>
          <input
            id="reviewer-email"
            type="email"
            className="input w-full"
            autoFocus
            autoComplete="username"
            spellCheck={false}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="e.g. dharma.reddy@bd.com"
          />
        </Field>

        <Field label="Password" htmlFor="reviewer-password" className="mb-4" error={tooShort ? `At least ${MIN_PASSWORD} characters` : undefined}>
          <PasswordInput id="reviewer-password" value={password} onChange={setPassword} autoComplete={mode === "signin" ? "current-password" : "new-password"} />
        </Field>

        {mode === "signup" && (
          <Field label="Confirm password" htmlFor="reviewer-confirm" className="mb-4" error={mismatch ? "The two passwords do not match" : undefined}>
            <PasswordInput id="reviewer-confirm" value={confirm} onChange={setConfirm} autoComplete="new-password" />
          </Field>
        )}

        {mode === "signin" && (
          <fieldset className="mb-5">
            <legend className="label">Reviewer slot</legend>
            <div className="grid gap-2">
              {roles.map((r) => {
                const on = slot === r.n;
                return (
                  <label
                    key={r.n}
                    className={`flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-[border-color,background-color,box-shadow] duration-150 ease-out ${
                      on ? "border-accent-500 bg-accent-50 glow-accent" : "border-line hover:border-line-2 hover:bg-surface-2/60"
                    }`}
                  >
                    <input type="radio" name="slot" className="sr-only" checked={on} onChange={() => setSlot(r.n)} />
                    <span className={`shrink-0 mt-0.5 ${on ? "text-accent-600" : "text-ink-3"}`}>{r.icon}</span>
                    <span>
                      <span className="block text-sm font-medium text-ink">{r.title}</span>
                      <span className="block text-xs text-ink-3 mt-0.5">{r.body}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        )}

        {mode === "signin" && (
          <div className="flex items-start gap-2 text-xs text-ink-3 mb-5">
            <EyeSlash size={16} className="shrink-0 mt-0.5" />
            <span>
              Blind review is <b className="text-ink">{required ? "required" : "optional"}</b> in this workspace. The policy is server-side; change it with <span className="mono">kaizen review policy</span>.
            </span>
          </div>
        )}

        {notice && (
          <div role="status" className="text-sm text-ok mb-3">
            {notice}
          </div>
        )}
        {(failed || error) && (
          <div role="alert" className="text-sm text-bad mb-3">
            {failed ?? error}
          </div>
        )}

        <Button variant="primary" size="lg" className="w-full" type="submit" loading={busy} disabled={!ready}>
          {mode === "signin" ? "Start reviewing" : "Create account"}
        </Button>

        <div className="mt-4 text-xs text-ink-3 text-center">
          {mode === "signin" ? (
            <>
              No account yet?{" "}
              <button type="button" className="underline hover:text-ink" onClick={() => switchTo("signup")}>
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" className="underline hover:text-ink" onClick={() => switchTo("signin")}>
                Sign in
              </button>
            </>
          )}
        </div>
        {mode === "signin" && (
          <p className="mt-3 text-2xs text-ink-3 text-center leading-relaxed">
            Forgotten your password? Ask whoever runs this workspace to clear your account with <span className="mono">kaizen users reset</span>, then create it again.
          </p>
        )}
      </form>
    </div>
  );
}
