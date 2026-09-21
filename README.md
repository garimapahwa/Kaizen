# Kaizen Cross-Check

[![CI](https://github.com/Rahulreddy-23/Kaizen/actions/workflows/ci.yml/badge.svg)](https://github.com/Rahulreddy-23/Kaizen/actions/workflows/ci.yml)

Deterministic, explainable, traceable cross-checking of JDE BOMs against product labels, packaging drawings,
PCOs and label revisions, with the reviewer as the final decision-maker. Built for the Innovation Week 2026
"BOM Cross-Check Automation" hackathon (BD / AAD).

> The system recommends. The reviewer decides. Every recommendation says why, points at the page and box it
> came from, and is reproducible from the recorded inputs, thresholds and terminology version.

> New here? Read [docs/solution-overview.md](docs/solution-overview.md): the problem and the solution in plain language.

## What it does
- Reads the documents reviewers already download: JDE BOM prints (PDF) or exports (XLSX/CSV), product labels
  (PDF, multi-column kit contents, old and new revisions), packaging drawings (vector PDF, EN/ES callouts) and
  PCO forms (FM00835 as XLSX/CSV/PDF).
- Runs the five cross-checks from the brief: BOM ↔ Label, BOM ↔ Drawing, Label ↔ Drawing, PCO ↔ BOM,
  Old ↔ New label, plus coverage (a BOM for every PCO affected code).
- Classifies every comparison EXACT / EQUIVALENT / POTENTIAL / MISMATCH / MISSING with a typed discrepancy,
  severity, explanation and evidence (file, SHA-256, page, bounding box, raw text).
- Uses explicit, versioned terminology relationships (global / product-family / SKU, item-anchored) that
  reviewers create, edit, import/export, and that runs pin by version.
- Signs reviewers in with a BD email address and a password (scrypt in the workspace, or optionally a
  shared Supabase project so one account works on every laptop), and holds identity, slot and blind
  mode in a **server-side session**, so a second reviewer cannot
  unblind themselves from the browser, and every decision carries a real name.
- Supports two reviewers with blind independent review, disagreement detection, finalisation, action items
  and verify-and-close on corrective reruns; suggests new relationships from repeated pairings (human approval
  required).
- Turns the business-case projection into a **terminology worklist** (approve these pairings, in this order, and this many rows clear), **measures** review effort per row instead of assuming it, and keeps the reviewer on the keyboard (j/k/Enter, a/c/o/n, ? for help).
- Produces an audit-grade Excel workbook, an annotated BOM PDF with coloured marks, measured accuracy against
  ground truth, and a business case computed from the actual run (measured figure plus a labelled projection
  for after reviewers confirm strong pairings as relationships).
- Works fully offline. An AI provider can be enabled explicitly; its output is labelled as a suggestion and
  never changes a classification, decision or relationship.

## Install (clean checkout)
```bash
python3.13 -m venv .venv                 # Python 3.12+ works
.venv/bin/pip install -e ".[dev]"        # engine + API + test tools
cd ui && npm install && npm run build && cd ..   # reviewer UI (optional; API works without it)
```

## Run
```bash
.venv/bin/kaizen run <folder> --out out/myrun   # one SKU set per sub-folder (bom.*, label.pdf, label_old.pdf, drawing.pdf) plus pco/*.xlsx|pdf
.venv/bin/kaizen serve                          # local API + UI at http://127.0.0.1:8765 (sign up with your BD email, then sign in and pick a slot)
```
Outputs: `run.json` (documents, items, evidence, results, audit), `report.xlsx`. Reviewer decisions,
terminology and action items live in the workspace database (`./kaizen-workspace/kaizen.db`; change with
`--workspace` or `KAIZEN_WORKSPACE`).

## Demo
```bash
rm -rf kaizen-workspace
.venv/bin/kaizen demo          # golden dataset: run + measured accuracy + workbook
.venv/bin/kaizen serve         # then follow docs/demo-script.md in the browser
```

## Tests and evaluation
```bash
.venv/bin/pytest                                   # unit + integration + golden accuracy floors (~1 min)
.venv/bin/kaizen eval datasets/golden --out out/e  # precision / recall per check vs ground-truth.json
.venv/bin/kaizen perf --skus 8 --skus 100          # timing and memory on synthetic datasets
```

## Commands
| Command | Purpose |
|---|---|
| `run` / `check` / `report` / `ingest` | Run everything; check only; workbook from a saved run (reviews merged); parse only. |
| `eval` | Run a dataset with `ground-truth.json` and report accuracy; `--fail-under` for CI. |
| `demo`, `serve`, `perf` | Demo run; local API + UI; performance series. |
| `terminology list/show/add/update/deactivate/activate/delete/history/import/export/sync-defaults` | Manage relationships (every change is a new version). |
| `certificate <run.json> [--sku]` | One-page cross-check certificate per SKU (run id, file hashes, counts, reviewers, open action items). |
| `diff <before.json> <after.json> [--json]` | Resolved, new and still-open discrepancies between two runs. |
| `review import <run.json> <file.xlsx> --slot --reviewer [--dry-run] [--force]` | Optional Excel round-trip: apply decisions recorded in the exported workbook. |
| `review policy [--set required\|optional]`, `review sessions [--end <name>]` | Blind-review policy (server-side, never changeable from the UI); open reviewer sessions. |
| `auth show`, `auth supabase <url> <key>`, `auth local` | Where sign-in accounts are checked: this workspace (default) or a shared Supabase project. |
| `users list`, `users reset <email>` | Local accounts only: list them; clear one so the reviewer can sign up again. |
| `runs list`, `runs relationships <run>` | Runs in the workspace; reconstruct the exact relationship versions a run used. |
| `dataset build`, `dataset corrected <sku>` | Regenerate the synthetic golden dataset (byte-stable); write a corrected copy of one SKU set for rehearsing verify-and-close. |

## Architecture
```
files → detect → parsers → canonical model (evidence) → categorise → group/coverage
      → match ladder (normalise · exact · relationship · fuzzy+numeric guard · [semantic] · [AI suggestion])
      → one-to-one assignment → checks (pairing engine + policies; PCO; revision) → results with roles
      → run (hashes, thresholds, terminology snapshot, audit) → review store (2 reviewers, blind, states)
      → action items / mining / business case → Excel, annotated PDF, evaluation, API + UI
```
Details and the full diagram: `docs/final-architecture.md`. API contract: `docs/api-contract.md`.

## Supported document types
| Type | Formats | Notes |
|---|---|---|
| BOM | JDE "Bill of Material Print" PDF; XLSX/CSV export | Multi-page, FreeText redline annotations captured, non-physical lines categorised with reasons. |
| Label | PDF (text); image-only PDF via optional OCR | REF, product name, two/three-column kit contents, wrapped lines, `(3 per)` / `(1 pair)` idioms; `label_old.pdf` = previous revision. |
| Drawing | Vector PDF | Title block (number, rev, title, plant), EN/ES callouts, conditional callouts, cavity/notes/title-block noise removed. Presence source only. |
| PCO | FM00835 XLSX/CSV/PDF | Affected codes, ADD/DELETE/SUBSTITUTE/MODIFY rows with qty and operation sequence. |

## Limitations
See `docs/known-limitations.md`. Headline items: no scanned-BOM OCR, no hand-drawn redlines, case labels not
parsed, thresholds tuned on synthetic data, single-user local workspace, no compliance claim.

## Optional: shared sign-in with Supabase
By default accounts live in each laptop's workspace, so an account made on one laptop does not work on
another. To give each reviewer one account that works on every laptop, keep the accounts in a Supabase
project. **Only the email address and password go to Supabase.** Sessions, reviewer slots, blind mode,
decisions, runs and documents stay on the laptop, as before. Sign-in then needs internet access.
**No email is ever sent**: BD mail blocks external senders, so there are no confirmation or reset emails.

In the Supabase dashboard (once per project):
1. **Authentication → Sign In / Providers:** keep *Allow new users to sign up* on and turn **Confirm
   email off** (a confirmation email would never arrive, and the account could not sign in).
   Optionally set the email provider's *Minimum password length* to 10, to match Kaizen.
2. Copy the **Project URL** and the **anon / publishable** key (Project Settings → API Keys). Never use
   the `service_role` / secret key; Kaizen refuses it.

Then on each laptop (once):
```bash
.venv/bin/kaizen auth supabase https://<project>.supabase.co <anon-or-publishable-key>
.venv/bin/kaizen auth show      # confirms where accounts are checked; `kaizen auth local` switches back
```
The environment variables `KAIZEN_SUPABASE_URL` and `KAIZEN_SUPABASE_KEY` do the same and take
precedence. Accounts created locally beforehand are not copied: each reviewer signs up once. Accounts are
listed in the dashboard under Authentication → Users. Kaizen only lets `@bd.com` addresses in; anyone
holding the public key could create a non-BD account directly in Supabase, but it cannot sign in to Kaizen.

### Forgotten passwords (Supabase)
The admin sets a new password and tells the reviewer. In the Supabase dashboard open **SQL Editor**, run
this with the reviewer's address and a new password of at least 10 characters, and check it reports one
row updated:
```sql
update auth.users
set encrypted_password = extensions.crypt('New-password-123', extensions.gen_salt('bf')),
    updated_at = now()
where email = 'dharma.reddy@bd.com';
```
Alternatively delete the user (Authentication → Users) and let them sign up again. Decisions already
recorded keep their address either way.

## Optional AI configuration
Off by default (`NullProvider`; no external calls). To enable suggestions on unresolved pairs:
```bash
pip install anthropic
export KAIZEN_AI_PROVIDER=anthropic ANTHROPIC_API_KEY=...   # optional: KAIZEN_AI_MODEL
```
Only the two descriptions and the SKU/item number are sent. Every suggestion is labelled "AI SUGGESTION",
recorded with provider, model, prompt version and timestamp in the run, and never pairs items on its own.
Semantic matching (L4) accepts any local embedding function (`kaizen.matching.semantic.SemanticMatcher`).
OCR uses `rapidocr-onnxruntime` when installed (offline).

## Documents
`docs/solution-overview.md` (plain-language overview for presenting), `docs/system-description.md` (what was built, module by module),
`docs/design/DESIGN.md` and `docs/design/PRODUCT.md` (the reviewer interface's visual system and product truth),
`docs/problem-understanding.md` (problem and proposal), `docs/implementation-plan.md` (plan and status),
`docs/final-architecture.md`, `docs/api-contract.md`, `docs/demo-script.md`, `docs/test-strategy.md`,
`docs/known-limitations.md`, `docs/security-review.md`, `datasets/golden/SCENARIOS.md`.
