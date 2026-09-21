# Kaizen Cross-Check — reviewer UI

Static React front end for the local Kaizen API (`docs/api-contract.md`). Vite + React 18 + TypeScript + Tailwind; no UI kit, no charting library. Hash routing (`#/…`) so the bundle works from the backend's static mount.

## Develop

```bash
# terminal 1 — API on 127.0.0.1:8765
cd .. && .venv/bin/kaizen --workspace /tmp/kaizen-ui-ws serve --port 8765

# terminal 2 — Vite dev server on http://localhost:5173 (proxies /api to 8765)
cd ui && npm install && npm run dev
```

Open http://localhost:5173, create an account with any `@bd.com` address, sign in, then click
**Load demo dataset** on the Runs page.

## Signing in

Reviewers sign themselves up with a BD email address and a password (`POST /api/auth/signup`), then sign
in and pick a reviewer slot (`POST /api/auth/signin`) — see `docs/api-contract.md`. `GET
/api/sessions/current` says where accounts live (`accounts: "local" | "supabase"`):

- **local**: a forgotten password is cleared by an administrator with `kaizen users reset <email>`, and
  the reviewer signs up again.
- **supabase**: one account works on every laptop. No email is sent, so the sign-in page tells a reviewer
  who forgot their password to ask the Kaizen admin, who sets a new one in Supabase.

A session is still what the backend enforces: identity, slot and blind mode live in the `kaizen_session`
HttpOnly cookie, unreadable here.

Identity is the email address, so it is what decisions and exports carry. `displayName()` in
`src/lib/format.ts` turns it into something readable for headers (`dharma.reddy@bd.com` → "Dharma
Reddy"); the address itself stays in the `title` tooltip.

## Build

```bash
cd ui && npm run build     # type-checks (tsc --noEmit) then writes ui/dist
```

`kaizen serve` mounts `ui/dist` at `/` automatically when it exists (restart the server after the first build). All API calls use relative `/api/...` URLs.

## Layout

- `src/api.ts` — typed client, one function per endpoint; `src/types.ts` — contract types.
- `src/lib/reviewer.tsx` — reviewer identity (verified email, slot 1/2, blind) from the server-side session; `src/components/SignIn.tsx` — the sign-in / sign-up gate; `src/lib/queue.ts` — review-queue filter state in the URL.
- `src/components/` — badges (classification/severity/state/decision), `PageImage` (page PNG + client-side bbox outline, scale = rendered px / natural px × 110/72), `EvidenceCard`, `DecisionPanel`, `RowActions` (save as relationship, action item), `Layout`.
- `src/pages/` — one file per route: Runs, Dashboard, ReviewQueue, Evidence, Documents, Document (extraction verification), Terminology, Mining, ActionItems, BusinessCase.

Routes: `#/`, `#/runs/:runId`, `#/runs/:runId/review`, `#/runs/:runId/rows/:rowId`, `#/runs/:runId/documents[/:docId]`, `#/runs/:runId/mining`, `#/runs/:runId/business`, `#/terminology`, `#/action-items`.
