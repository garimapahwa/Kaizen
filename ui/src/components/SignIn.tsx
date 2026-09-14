// Sign-in gate, in two steps: a BD email address, then the one-time code the server mails to it.
// Reviewers identify themselves before they can see or record decisions: every decision is stored
// against a verified address, and blind review only means something if the server decides who is blind.
import { ArrowLeft, EnvelopeSimple, EyeSlash, UserCircle, UsersThree } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useReviewer } from "../lib/reviewer";
import { ThemeToggle } from "./ThemeToggle";
import { Button, Field } from "./ui";

const BD_DOMAIN = "@bd.com";
const BAD_DOMAIN = "Only BD email addresses can sign in";
const RESEND_SECONDS = 30;

export function SignIn() {
  const { requestOtp, signIn, policy, error } = useReviewer();
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [slot, setSlot] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const required = policy === "required";

  // The server rate-limits per address anyway; this only keeps the button from inviting the 429.
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const address = email.trim().toLowerCase();
  const looksBd = address.endsWith(BD_DOMAIN) && address.length > BD_DOMAIN.length;
  const wrongDomain = address.length > 0 && !looksBd;

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setFailed(null);
    try {
      await requestOtp(address);
      setStep("code");
      setCode("");
      setCooldown(RESEND_SECONDS);
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFailed(null);
    try {
      await signIn(address, code.trim(), slot);
    } catch (err) {
      setFailed((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function backToEmail() {
    setStep("email");
    setCode("");
    setFailed(null);
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
      <form onSubmit={step === "email" ? send : verify} className="w-full max-w-[30rem] card shadow-pop p-7 enter-pop">
        <div className="flex items-center gap-3 mb-6">
          <span className="w-9 h-9 rounded-md bg-accent-500 grid place-items-center font-semibold text-accent-ink" aria-hidden>
            K
          </span>
          <div className="leading-tight">
            <div className="text-lg font-semibold tracking-tight">Kaizen Cross-Check</div>
            <div className="text-xs text-ink-3">BOM · label · drawing · PCO review</div>
          </div>
        </div>

        {step === "email" ? (
          <>
            <h1 className="text-2xl mb-1">Sign in to review</h1>
            <p className="text-sm text-ink-3 mb-5">We email a one-time code to your BD address. Every decision is recorded against it. Choose the slot you are reviewing in.</p>

            <Field label="BD email address" htmlFor="reviewer-email" className="mb-4" error={wrongDomain ? BAD_DOMAIN : undefined}>
              <input
                id="reviewer-email"
                type="email"
                className="input w-full"
                autoFocus
                autoComplete="email"
                spellCheck={false}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="e.g. dharma.reddy@bd.com"
              />
            </Field>

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

            <div className="flex items-start gap-2 text-xs text-ink-3 mb-5">
              <EyeSlash size={16} className="shrink-0 mt-0.5" />
              <span>
                Blind review is <b className="text-ink">{required ? "required" : "optional"}</b> in this workspace. The policy is server-side; change it with <span className="mono">kaizen review policy</span>.
              </span>
            </div>

            {(failed || error) && (
              <div role="alert" className="text-sm text-bad mb-3">
                {failed ?? error}
              </div>
            )}
            <Button variant="primary" size="lg" className="w-full" type="submit" loading={busy} disabled={!looksBd} icon={<EnvelopeSimple size={18} />}>
              Send code
            </Button>
          </>
        ) : (
          <>
            <h1 className="text-2xl mb-1">Enter your code</h1>
            <p className="text-sm text-ink-3 mb-5">
              We sent a six-digit code to <b className="text-ink">{address}</b>. It expires in 10 minutes and works once.
            </p>

            <Field label="Six-digit code" htmlFor="reviewer-code" className="mb-5">
              <input
                id="reviewer-code"
                className="input w-full mono tracking-[0.4em] text-lg"
                autoFocus
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={6}
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="000000"
              />
            </Field>

            <div className="text-xs text-ink-3 mb-5">
              Signing in as <b className="text-ink">{slot === 1 ? "Reviewer 1 · facilitator" : "Reviewer 2 · independent"}</b>.
            </div>

            {(failed || error) && (
              <div role="alert" className="text-sm text-bad mb-3">
                {failed ?? error}
              </div>
            )}

            <Button variant="primary" size="lg" className="w-full" type="submit" loading={busy} disabled={code.length !== 6}>
              Verify and start reviewing
            </Button>

            <div className="flex items-center justify-between gap-3 mt-4 text-xs">
              <button type="button" className="underline inline-flex items-center gap-1 text-ink-3 hover:text-ink" onClick={backToEmail}>
                <ArrowLeft size={14} /> Use a different email
              </button>
              <button type="button" className="underline text-ink-3 hover:text-ink disabled:opacity-50 disabled:cursor-not-allowed disabled:no-underline" onClick={() => void send()} disabled={busy || cooldown > 0}>
                {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
