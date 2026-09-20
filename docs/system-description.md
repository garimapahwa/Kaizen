# Kaizen Cross-Check — system description

What was built, module by module, for an engineer or technical judge who has not seen the code. Every
statement here was checked against the source. Plain-language version for a general audience:
[`solution-overview.md`](solution-overview.md). Design rationale: [`final-architecture.md`](final-architecture.md).

---

## 1. Purpose and scope

**The problem.** For each SKU in a change-control project, four documents must agree: the JDE Bill of
Materials, the product label, the packaging drawing and the Product Change Order. Today two people compare
them by eye at roughly an hour per SKU per reviewer. The comparison is hard to automate because the
documents use different words for the same part.

**What the system does.** It reads those documents, pairs the lines, classifies every comparison, explains
each conclusion with the page and bounding box it came from, records two reviewers' decisions, and writes
the Excel tracker and a marked-up BOM.

**What it deliberately does not do.**

| Not done | Why |
|---|---|
| Change JDE or MasterControl | Out of scope in the brief. Integration means reading the files people already download. |
| Approve or finalise anything by itself | The reviewer is the decision-maker; the engine only recommends. |
| Depend on a language model | The engine is deterministic and offline. AI is an opt-in annotator that can never change a classification. |
| Claim regulatory compliance | The system is built for traceability and reproducibility. Formal validation is a separate exercise. |
| Read scanned BOMs or drawings, or hand-drawn redlines | Not implemented. See [`known-limitations.md`](known-limitations.md). |

---

## 2. Repository map

| Path | Responsibility | Entry points |
|---|---|---|
| `src/kaizen/models/` | Pydantic data model and enums shared by everything | `Document`, `DocumentItem`, `CheckResult`, `Run` |
| `src/kaizen/ingest/` | Document detection and parsers | `detect.parse_document`, `grouping.group_by_sku` |
| `src/kaizen/matching/` | Normalisation, similarity, the match ladder, assignment | `ladder.MatchLadder`, `assignment.assign` |
| `src/kaizen/terminology/` | Relationship model, in-memory store, SQLite repository, import/export | `RelationshipStore`, `TerminologyRepository` |
| `src/kaizen/checks/` | The five cross-checks and the shared pairing engine | `pairing.run_pairing_check` |
| `src/kaizen/pipeline.py` | Ingest → check → run assembly, run identity, capabilities | `run_folder`, `run_checks` |
| `src/kaizen/reporting/` | Excel workbook, annotated BOM PDF, cell styles | `write_report`, `write_annotated_bom` |
| `src/kaizen/evaluation/` | Ground truth model, scoring harness, reports | `evaluate`, `load_ground_truth` |
| `src/kaizen/datasets/` | Synthetic document generator and scenario definitions | `build_golden` |
| `src/kaizen/review/` | Sessions, decisions, action items, mining, business case | `ReviewStore`, `SessionStore` |
| `src/kaizen/ai/` | Optional adjudication providers and the L5 matcher | `get_provider` |
| `src/kaizen/storage/` | SQLite schema, connection, write lock, run index | `Database`, `RunIndex` |
| `src/kaizen/workspace.py` | Workspace = database + saved runs | `Workspace.resolve` |
| `src/kaizen/api/` | FastAPI backend for the reviewer UI | `create_app` |
| `src/kaizen/cli/` | Typer command line | `kaizen` |
| `ui/` | React + Vite reviewer interface | `ui/src/App.tsx` |
| `tests/` | 428 unit, integration and golden test cases | `pytest` |

---

## 3. Data model

All models are pydantic v2 (`src/kaizen/models/`). Every value that reaches a report carries its provenance.

### Enums (`models/enums.py`)

| Enum | Values | Meaning |
|---|---|---|
| `DocType` | BOM, LABEL, DRAWING, PCO | What kind of document a file is |
| `ItemCategory` | PHYSICAL_COMPONENT, PACKAGING, LABEL, DOCUMENT, PROCESS, QUALITY, ADMINISTRATIVE, UNKNOWN | What a BOM line *is*. Only physical components are expected on a label |
| `Classification` | EXACT, EQUIVALENT, POTENTIAL, MISMATCH, MISSING | The verdict on one comparison |
| `MatchLevel` | L1_EXACT, L2_RELATIONSHIP, L3_FUZZY, L4_SEMANTIC, L5_LLM, NONE | Which rung of the ladder produced the pairing |
| `Severity` | BLOCKER, MAJOR, MINOR, INFO | How much a discrepancy matters |
| `CheckType` | BOM_LABEL, BOM_DRAWING, LABEL_DRAWING, PCO_BOM, LABEL_REVISION | Which cross-check produced a row |
| `DiscrepancyType` | 15 values, listed in §6 | The specific problem found |

### Evidence (`models/evidence.py`)

`Evidence` is the traceability record attached to every extracted value: `file`, `file_sha256`, `page`,
`bbox` (a `BBox` of PDF points at 72 dpi, or `None` for spreadsheets), `locator` (a human-readable pointer
such as a row number or cell), `raw_text` (exactly what was on the page), `sheet`, and `extraction_method`
(`text`, `table`, `ocr`, `annotation`). Nothing enters a report without one.

### Documents (`models/document.py`)

`DocumentItem` is one line of one document: `id`, `doc_id`, `doc_type`, `item_number`, `description`,
`quantity` (`Decimal`), `uom`, `oper_seq`, `category` and `category_reason`, `sub_quantity` (for label
idioms such as "2 Each (3 per)"), `is_active`, `extraction_confidence`, `attributes` (parser extras such as
captured redlines) and `evidence`.

`Document` holds `id`, `doc_type`, `path`, `sha256`, `sku`, `header`, `items`, `parser_name`,
`parser_version` and `warnings`.

### Results (`models/result.py`)

`CheckResult` is one comparison row: `row_id`, `sku`, `check`, `role`, `source_a` / `source_b` (the two
`DocumentItem`s), `normalized_a` / `normalized_b`, `classification`, `match_level`, `score`,
`relationship_id`, `explanation` (validated non-empty), `discrepancies`, `requires_validation`, and the
legacy reviewer fields. `severity` is a property returning the highest severity among the discrepancies.

`role` decides how a row is counted and displayed:

| Role | Meaning |
|---|---|
| `item` | A real component comparison. Counts towards accuracy and the business case |
| `header` | A document-level check such as REF against BOM parent |
| `reference` | A cross-document identifier check such as the drawing number and revision |
| `coverage` | Is there a BOM at all for a SKU the PCO names |
| `exempt` | A BOM line excluded by category, quantity or effectivity, shown so the exclusion is visible |
| `change` | A row from the old-to-new label comparison |

`Discrepancy` carries `type`, `severity`, `detail` and `recommended_action`.

### Run (`models/run.py`)

`Run` holds `metadata`, `documents`, `groups`, `results`, `coverage`, `relationship_usage`,
`terminology_snapshot` and `warnings`. `RunMetadata` records `run_id`, `timestamp`, `input_root`,
`tool_version`, `inputs` (each with SHA-256 and size), `thresholds`, `terminology_version`,
`terminology_count`, `capabilities`, `parser_versions` and `ai_provider`.

`Thresholds` defaults: `potential` 0.85, `floor` 0.60, `ambiguity_delta` 0.05, `low_confidence` 0.70.

---

## 4. Document ingestion

`ingest/detect.py` decides what each file is and dispatches to a parser, returning a `ParseOutcome` that
carries either a `Document` or a reason the file was not recognised. Nothing is guessed silently: an
unreadable file produces a warning and, where it matters, a blocking result row.

### BOM, JDE print PDF (`ingest/bom_pdf.py`, parser version `1+cat2`)

Reads a JDE R30460 "Multi-Level Bill of Material" print. `ingest/pdf_words.py` extracts positioned words,
groups them into lines, splits lines on wide gaps and assigns words to columns using bands derived from
the header labels, so column drift between pages does not shift the data. The parser reads the header
(parent item, description, branch, batch quantity, bill revision), then each component row: level, item,
description, quantity per, UOM, effective from and thru, operation sequence. Rows whose effectivity has
expired become `is_active = False`. FreeText annotations are captured into `attributes.redlines` and
excluded from the page text so a redline is not read as a component. A blank or non-numeric quantity is
recorded with reduced confidence rather than defaulted to 1.

### BOM, spreadsheet export (`ingest/bom_table.py`)

XLSX and CSV with alias-tolerant header matching (for example "Component Item", "Item Number", "Item").
Evidence records the sheet and cell.

### Label PDF (`ingest/label_pdf.py`, parser version `1`)

Finds the REF by frequency across the page (labels repeat it on peel-off sub-labels), locates the "Full Kit
Contents" region, and reads the contents in columns anchored on the "N Each" line starters, so a
two-column kit list is not interleaved. Wrapped continuation lines are joined. `ingest/quantity.py` parses
each line into quantity, unit and description, and understands sub-quantity idioms such as "(3 per)" and
"(1 pair)". If a page has no text layer, `ingest/ocr.py` is used when `rapidocr-onnxruntime` is installed;
otherwise the page is reported as unreadable and the affected rows become blockers.

### Packaging drawing PDF (`ingest/drawing_pdf.py`, parser version `1`)

Reads the title block (drawing number, revision, title, plant), then clusters callout text into
English/Spanish pairs, keeping the English text as the item. Conditional callouts ("IF APPLICABLE PER BOM")
and placement-only notes are marked so they are not treated as required items. Cavity labels and drawing
furniture are filtered by pattern.

### PCO form (`ingest/pco.py`, parser version `1`)

Reads form FM00835 from XLSX, CSV or PDF: the affected codes, and each change row's actual and proposed
item number, description, quantity and operation sequence. Each row is classified into a `change_kind` of
ADD, DELETE, SUBSTITUTE or MODIFY with a `change_key` used later to match the change against the BOM and
against label revisions.

### Categorisation (`ingest/bom_categorize.py`, version `2`)

Phrase-level rules in `bom_category_rules.json` assign an `ItemCategory` and a human-readable reason to
every BOM line. This is what stops "EN LOD, CASE LABEL" or "PACKAGING QUALITY" from being reported as
missing from a label. Every excluded line still appears in the workbook as an `exempt` row with its reason.

### Grouping and identity (`ingest/grouping.py`, `ingest/hashing.py`)

A folder is treated as one SKU set only if it holds exactly one BOM; otherwise sets are formed by product
family derived from the parent item. PCOs are batch-wide, not per-SKU. Label files are assigned old or new
revision roles from filename tokens. Document ids combine a type prefix, the file SHA-256 and a hash of the
path, so the same file in two folders does not collide.

---

## 5. Normalisation and the match ladder

### Normalisation (`matching/normalize.py`, version `1`)

`normalize()` lowercases, strips trademark and registered symbols, expands a fixed abbreviation table,
normalises units and dimensions, joins percentages, removes trailing `.0`, and singularises plurals,
returning a `NormalizedText` that keeps the original alongside the normalised form and its tokens.

### Similarity (`matching/fuzzy.py`)

`similarity()` blends `token_set_ratio` and `token_sort_ratio` from rapidfuzz over stop-word-filtered
tokens. A **numeric guard** applies the subset rule: if both sides contain numbers and one side's numbers
are not a subset of the other's, the pair is a numeric conflict and cannot be matched. This is what keeps
"SAFETY NEEDLE 21G" from pairing with "Safety Needle, 25 G".

### The ladder (`matching/ladder.py`)

| Rung | Matcher | Highest classification |
|---|---|---|
| L0 | Normalise both sides | — |
| L1 | `ExactMatcher`: normalised strings identical | EXACT |
| L2 | `RelationshipMatcher`: a terminology relationship covers both wordings, respecting scope and item anchors | EQUIVALENT |
| L3 | `FuzzyMatcher`: similarity above the `potential` threshold, no numeric conflict | POTENTIAL |
| L4 | `SemanticMatcher` (opt-in, `matching/semantic.py`): cosine similarity from an injected embedder | POTENTIAL |
| L5 | `AiAdjudicationMatcher` (opt-in, `ai/matcher.py`): a labelled suggestion | POTENTIAL, `weak` |

The ladder stops at the first rung that produces an outcome. Rungs 3 to 5 set `needs_confirmation`, so they
can never produce EXACT or EQUIVALENT — only a person, or a relationship a person approved, can assert that
two different wordings mean the same part. An item-anchored relationship whose A-side wording is unknown
also sets `needs_confirmation` rather than asserting equivalence.

### Assignment (`matching/assignment.py`)

`assign()` performs greedy one-to-one assignment over the candidate scores. When the top two candidates for
a row are within `ambiguity_delta`, the row is reported as AMBIGUOUS with both candidates named rather than
resolved by a coin flip. Unmatched rows on either side get hints naming the closest candidate that was
rejected and why.

---

## 6. The checks

### The shared pairing engine (`checks/pairing.py`)

Four of the six checks are the same algorithm with a different `CheckPolicy`, which declares: whether
quantity is compared, which discrepancy types represent "missing on side A" and "missing on side B", which
B-side categories are conditional or exempt, and which A-side categories are soft (reported as INFO). The
engine merges duplicate BOM rows for the same item before comparing, runs the ladder and assignment, and
emits one `CheckResult` per pairing plus rows for the unmatched on both sides.

`checks/base.py` maps match level to classification, holds the recommended action for every discrepancy
type, and issues row ids through `RowIdFactory` in the form `R-<sku>-<pair>-<seq>` so two labels in one set
cannot collide.

### The six checks

| Check | Module | What it compares | Extra rows |
|---|---|---|---|
| BOM ↔ Label | `checks/bom_label.py` | Physical BOM components against label contents lines, including quantity | A `header` row comparing label REF to BOM parent; `exempt` rows for every excluded BOM line; a BLOCKER if the label could not be read |
| BOM ↔ Drawing | `checks/bom_drawing.py` | Presence of every physical component as a callout. Quantity is not compared, because drawings state placement only | A `reference` row checking the drawing number and revision against the BOM |
| Label ↔ Drawing | `checks/label_drawing.py` | Label contents against callouts | — |
| PCO ↔ BOM | `checks/pco_bom.py` | Each PCO change against the BOM: ADD present with the right quantity and sequence, DELETE gone or struck through, SUBSTITUTE swapped, MODIFY applied | `coverage` rows first: is there a BOM for every affected code |
| Old ↔ New label | `checks/label_revision.py` | The previous label revision against the new one, against changes expected from the PCOs | `change` rows |
| Coverage | `pipeline.py` | Every PCO affected code has a BOM in the batch | `coverage` |

PCO to BOM is deliberately strict. A DELETE is only "applied" if the item was actually found and removed or
struck; an item that was never in the BOM at all is reported, not quietly passed. Fuzzy resemblance never
satisfies a PCO expectation. Redlines captured from annotations are taken into account.

### Discrepancy types and default severities

| Type | Typical severity | Raised when |
|---|---|---|
| `QTY_MISMATCH` | MAJOR, or MINOR behind an unconfirmed pairing or a label idiom | Quantities differ |
| `DESC_MISMATCH` | MINOR | Paired but the wording differs materially |
| `MISSING_IN_LABEL` / `MISSING_IN_BOM` / `MISSING_IN_DRAWING` | MAJOR by default, per policy | A component on one side has no counterpart |
| `EXTRA_ON_DRAWING` | MAJOR, or soft where the policy says so | A callout with no BOM line |
| `REF_PARENT_MISMATCH` | BLOCKER | The label REF does not belong to the BOM parent's family |
| `AMBIGUOUS_MATCH` | MAJOR | Two candidates fit equally well |
| `LOW_EXTRACTION_CONFIDENCE` | INFO, BLOCKER when a whole document could not be read | A value was read with confidence below the threshold |
| `PCO_CHANGE_NOT_APPLIED` | MAJOR | An approved change is not in the BOM |
| `PCO_QTY_SEQ_MISMATCH` | MAJOR | Applied, but with a different quantity or operation sequence |
| `BOM_MISSING_FOR_AFFECTED_CODE` | BLOCKER | A SKU on the PCO has no BOM in the batch |
| `UNEXPECTED_LABEL_CHANGE` | MAJOR | The new label differs in a way no PCO approves |
| `EXPECTED_CHANGE_ABSENT` | MAJOR | An approved change never reached the label |
| `DRAWING_REV_MISMATCH` | MAJOR | The drawing number or revision disagrees with the BOM |

---

## 7. Terminology relationships

A `Relationship` (`models/relationship.py`) holds `id`, `canonical`, `aliases`, `scope`, `doc_types`,
`item_anchors`, `provenance`, `created_by`, `created_at`, `updated_at`, `active`, `notes` and `version`.

**Scope** is `global`, `family:<prefix>` or `sku:<code>`; a validator rejects malformed scopes. **Item
anchors** bind a relationship to specific BOM item numbers, so a wording that means one part in one family
cannot silently match a different part elsewhere.

`terminology/store.py` is the read path used during matching: lookup prefers the most specific scope that
applies and honours anchors. It also computes the snapshot hash recorded in every run.
`default_relationships.json` ships nine seeded relationships (REL-001 to REL-009).

`terminology/repository.py` is the SQLite write path. Relationships are versioned append-only: every
create, update, deactivate, activate or delete writes a new row to `relationship_versions` with the actor,
timestamp and change note, and deletes are tombstones. `record_run_usage` pins the exact version each run
used, so `kaizen runs relationships <run>` reconstructs what the engine saw on the day.

`terminology/exchange.py` imports and exports XLSX and CSV, returning an `ImportResult` that reports
created, updated, skipped and rejected rows with reasons.

---

## 8. Pipeline and run identity

`pipeline.discover_files` walks the input folder, `ingest_folder` parses everything and groups it into SKU
sets, and `run_checks` runs the six checks, assembles coverage, records relationship usage and builds the
`Run`.

The **run id** is a SHA-256 over: every input file hash, the terminology snapshot hash, the thresholds, the
tool version and every parser and checker version. Identical inputs and identical code therefore produce an
identical run id and identical row ids, which is what lets reviewer decisions re-attach across a rerun; a
code change produces a new id, so stale decisions are never silently reused.

`CAPABILITIES` is a dictionary of feature name to implementation status, recorded in run metadata and shown
in the workbook and the UI, so the tool states plainly what it does and does not do.

---

## 9. Review layer

### Reviewer sessions and blind mode

Identity, slot and blind mode are server-side (`review/sessions.py`). Reviewers sign up with a BD email
address and a password (stored as salted scrypt in the `users` table); `POST /api/auth/signin` is the only
route that opens a session and issues an opaque token in an HttpOnly cookie, so page scripts cannot read or
forge it. The `viewer` and `blind` query parameters are ignored whenever a session exists. A forgotten
password is cleared by an administrator (`kaizen users reset`), after which the reviewer signs up again —
there is no reset email because the tool has no mail server.

The **blind flag is derived, not requested**. Reviewer 1 is never blind. Reviewer 2 is always blind while
the workspace policy is `required`, which is the default, and may only be unblinded when the policy is
`optional`. The policy lives in the `settings` table and is changed only from the command line
(`kaizen review policy --set …`), never over HTTP, and every change is audited.

While a blind reviewer 2 has not yet decided a row, they cannot see reviewer 1's work through any channel:

| Channel | Behaviour |
|---|---|
| `decisions["1"]` | `null` |
| `state` | Reported as `ENGINE_RECOMMENDED`, not `REVIEWER_1_COMPLETE` |
| `effective_classification` | Shows the engine value, so an override does not leak |
| `final` | `null` if a final was recorded without them |
| Row history | Only the engine event |
| Excel export, annotated BOM, audit log | Refused with 403 |
| Finalising that row | Refused with 403 |

The engine's own recommendation stays fully visible throughout: blind review hides the other reviewer, not
the evidence. Once reviewer 2 records their decision, everything becomes visible and the row state resolves
to AGREED or DISAGREEMENT.

This is identification, not authentication. Anyone with access to the machine can sign in under any name.
It makes the reviewer's identity and the blind flag server-side, explicit and audited, which is what an
independent-review process needs on a single reviewer workstation. A shared deployment needs SSO.

### Measured review effort (`review/store.py`, `review/business.py`)

Opening a row through the API with a session records `row_views(run, row, slot, opened_at)`. The next
decision by that slot on that row stores `seconds_spent` when the gap is between `MIN_TIMED_SECONDS`
(5 seconds, below which it is a click, not a review) and `MAX_TIMED_SECONDS` (15 minutes); bulk accepts and Excel imports pass `timed=False` and are never counted. `ReviewStore.timing`
returns the sample count, median and mean. `business_case(run, assumptions, timing)` uses the measured
median as minutes per validated row once `MIN_TIMED_SAMPLES` (10) decisions are timed, reports
`effort_basis` as `measured` or `assumed`, and always states the sample size.

### Decisions (`review/store.py`)

A decision is `{slot, reviewer, decision, comment, override_classification, decided_at, blind}` where the
decision is ACCEPT, OVERRIDE, CONFIRM_DISCREPANCY or NEEDS_MORE_INFORMATION. Decisions are stored *beside*
the engine recommendation and never overwrite it.

State machine: `ENGINE_RECOMMENDED` → `REVIEWER_1_COMPLETE` or `REVIEWER_2_COMPLETE` → `AGREED` when both
reviewers decided the same way, `DISAGREEMENT` when they did not → `FINALIZED` once a final decision is
recorded with a note. `bulk_accept_clean` records ACCEPT on every row with no discrepancy that does not
need validation, each carrying an explicit comment saying so.

### Action items (`review/action_items.py`)

An action item is created from a discrepancy row and carries a **comparison key** of
`check|sku|A:item` or `check|sku|B:text`. When corrected documents are run again, `verify_and_close`
matches items by that key: an item whose row is now clean is closed, one that still fails stays open. This
is how the tool proves a fix rather than assuming it.

### Terminology worklist (`review/mining.py`)

`terminology_worklist` runs mining with a minimum of one SKU, adds to each suggestion the rows that would
auto-clear once it is approved (`would_clear`: rows with no discrepancy) and the rows that stay with a
reviewer regardless (`still_review`: quantity mismatches, ambiguities), sorts by impact and accumulates a
running total and percentage of all rows needing validation. `Worklist.top(n)` gives the sentence the
reviewer needs: approve the top five, this many rows clear. Approval still goes through
`approve_suggestion`; nothing is applied automatically.

### Run-to-run diff (`review/rundiff.py`)

`diff_runs(before, after)` aligns rows of the two runs by the same comparison key action items use and
classifies each: `resolved` (discrepancy gone), `new`, `still_open`, `changed` (classification changed,
no discrepancy either side), `unchanged` (counted, not listed), `gone` (the comparison itself disappeared,
explicitly not treated as fixed) and `not_covered` (SKUs present in only one run). It also lists input
files whose hash changed, and SKUs added or removed.

### Optional Excel round-trip (`reporting/excel_import.py`)

`import_decisions(workspace, run, path, slot, reviewer, dry_run, force)` reads the exported workbook back.
The Run ID on `Run_Metadata` must match; only the given slot's "Reviewer Decision" and comment columns are
read; `OVERRIDE→<classification>` carries the override; invalid values and unknown row ids are reported, not
applied; a decision the database changed after the workbook's `Exported at` time is a conflict and is
skipped unless `force`; a dry run writes nothing. Applied decisions go through `ReviewStore.decide`
(untimed, audited) and the import itself is an audit event. This path is optional: the UI and the CLI
work identically without it.

### Mining (`review/mining.py`)

Scans a run for fuzzy pairings that recur across at least two SKUs, counting how often reviewers confirmed
or contradicted each. Suggestions are proposals only: `approve_suggestion` creates a real, versioned
relationship attributed to the approver, and `reject_suggestion` records the rejection so the same
suggestion does not come back.

### Business case (`review/business.py`)

Computed from the actual run, over reviewable rows only (roles `item` and `change`). Defaults come from the
brief: 60 baseline minutes per SKU per reviewer, $37.50 an hour, 100 SKUs a project, 20 projects a year,
2 reviewers, 1.5 minutes to read the evidence and decide a row that needs validation, 0.1 minutes to skim an
auto-cleared row, and a 50 percent reduction target.

Two figures are reported and labelled separately: the **measured** reduction for this run as it stands, and
a **projected** reduction after reviewers confirm the strong POTENTIAL pairings (score at or above 0.95
with no discrepancy) as relationships. On the golden dataset those are 27 percent and 73 percent.

---

## 10. Outputs

### Excel workbook (`reporting/excel.py`)

Thirteen sheets, fourteen when accuracy metrics are available:

| Sheet | Contents |
|---|---|
| `Summary` | Run identity, counts by classification, blockers, capabilities |
| `BOM_Label`, `BOM_Drawing`, `Label_Drawing`, `Label_Revision` | One row per comparison |
| `PCO_BOM` | Coverage rows then each PCO change against the BOM |
| `Action_Items` | Open and closed items with owners and comparison keys |
| `Coverage` | A BOM for every affected code |
| `Manual_Checklist` | The visual checks that cannot be automated, such as dot sticker placement |
| `Documents` | Every input with SHA-256, parser, parser version, page count, warnings |
| `Relationships_Used` | Each relationship with its exact version and use count |
| `Run_Metadata` | Thresholds, terminology version, parser versions, AI provider |
| `Audit_Log` | Every recorded action with actor and timestamp |
| `Terminology_Worklist` | Unconfirmed pairings ranked by rows they would auto-clear, with cumulative totals |
| `Accuracy` | Precision and recall per check when a ground truth was supplied |

Check-sheet columns cover both compared values, the classification, match level, score, the relationship
applied, discrepancy type, severity, explanation, evidence pointers, review state, both reviewers'
decisions and the linked action item. `export_with_review` merges live reviewer state from the workspace at
export time, so the workbook is current without the engine ever being re-run.

Decision cells are written as `OVERRIDE→EQUIVALENT` when a reviewer overrode the classification, so the
workbook round-trips; the data-validation list offers the same forms. `Run_Metadata` carries `Exported at`,
which the importer uses to detect conflicts.

### Cross-check certificate (`reporting/certificate.py`)

One A4 page per SKU, drawn with PyMuPDF: SKU and family, run id and timestamp, terminology version and
thresholds, every input document with its SHA-256, counts by classification, needs-validation and blockers,
header and coverage findings, both reviewers by name with decision counts, finalized and disagreement
counts, open action items, a statement that the engine output is a recommendation and the decisions are
the reviewers', and signature lines pre-filled from the recorded names and dates. `write_run_certificate`
produces one page per SKU set in group order.

### Annotated BOM (`reporting/annotated_bom.py`)

Copies the original BOM PDF and draws a coloured mark in the margin beside each line, one column per check,
green for cleared, amber for needs validation, red for a discrepancy, plus a "checked by" block listing the
reviewers. If the BOM was a spreadsheet, a rendered fallback page is produced instead, and the outcome
reports which path was taken and how many marks were drawn.

### `run.json`

The complete `Run` model: every document, item, evidence record, result and audit event. It is the input to
`kaizen report` and `kaizen eval`, so a workbook can be regenerated without re-parsing.

---

## 11. HTTP API

`create_app(workspace, ui_dir)` in `src/kaizen/api/app.py`. Local only, no external calls. Full contract in
[`api-contract.md`](api-contract.md).

| Group | Endpoints |
|---|---|
| Auth and sessions | `POST /api/auth/signup`, `POST /api/auth/signin`, `GET`/`DELETE /api/sessions/current` |
| Runs | health, list, create from a local path, create from an upload, load the demo, get a summary |
| Results | filtered and ordered result page, row detail with evidence and history |
| Documents | document list, extracted items, page PNG with the evidence box highlighted |
| Decisions | record, finalise, bulk-accept |
| Terminology | list, get, create, update, activate, deactivate, delete, history, import, export |
| Mining | suggestions, approve, reject |
| Action items | create, list, update, verify-and-close |
| Reports | business case (measured or assumed effort), `export.xlsx`, annotated BOM PDF, certificate PDF, audit log |
| Worklist and diff | `terminology-worklist`, `diff?against=` |
| Optional import | `POST .../decisions/import` (multipart, dry run, force) |

The built UI is mounted at `/` when present.

---

## 12. Reviewer UI

React 18, Vite, TypeScript and Tailwind, using `HashRouter` so deep links work from static files. `api.ts`
is a thin typed client over relative URLs; the same bundle works behind the Vite dev proxy and served by the
backend.

The visual system is written down in [`design/DESIGN.md`](design/DESIGN.md) (BD blue and orange from the
corporate identity, one blue-tinted neutral family, semantic colours for classification, severity and
state that are always paired with a word, Geist and Geist Mono self-hosted so the tool works offline,
Phosphor icons, entrance transitions from `@starting-style` so the resting state is always visible, no
animation on keyboard-driven actions). Product truth for the interface is in
[`design/PRODUCT.md`](design/PRODUCT.md). Shared primitives live in `ui/src/components/ui.tsx`
(buttons, cards, page header, fields, skeletons, empty states, dialog) and `ui/src/lib/toast.tsx`.

The interface has a light and a dark theme. Every colour token is a CSS variable holding RGB channels
(`ui/src/index.css` defines the light set on `:root` and the dark set on `:root[data-theme="dark"]`,
`ui/tailwind.config.js` maps the Tailwind colour utilities onto them), so components never name a
theme. `ui/src/lib/theme.ts` owns the rule (stored choice under `kaizen.theme`, otherwise the operating
system's preference, followed live) and is unit-tested with vitest (`npm test`, also run in CI); a
boot script in `ui/index.html` applies the same rule before first paint so there is no flash;
`ui/src/components/ThemeToggle.tsx` is the moon/sun button in the top bar and on the sign-in page.
The dark set is composed rather than inverted (BD blue lightened until it reads as text, meaning
colours brightened, orange unchanged with ink text), and every text pair used by the interface was
measured at 4.5:1 or better in both themes.

| Page | Purpose |
|---|---|
| Runs | Load the demo set, drop a folder, or run a local path; lists previous runs |
| Dashboard | Groups, coverage blockers, counts, auto-cleared against needs-review, parser warnings |
| Review queue | Filterable, sortable rows, blockers first, bulk accept, blind banner, keyboard navigation (j/k/Enter, ? help), optional Excel import panel |
| Evidence | The two compared values with both page images and the evidence boxes highlighted, the decision panel, save-as-relationship, create action item, history |
| Documents / Document | Extraction verification: every parsed line with its evidence, so a reviewer can check the parser |
| Terminology | Create, edit, deactivate, import and export relationships; version history |
| Mining | Terminology worklist (ranked by rows cleared, cumulative %) and mining suggestions to approve or reject |
| Compare runs | Resolved, new, still-open and gone comparisons against an earlier run, changed documents |
| Action items | Track and close |
| Business case | Measured and projected figures, effort basis (measured after ten timed decisions, else assumed), assumptions exposed |

`lib/hotkeys.ts` binds single-key shortcuts that never fire while typing; `components/HotkeyHelp.tsx` is the `?` reference. `components/SignIn.tsx` gates the app until a session exists. `lib/reviewer.tsx` holds the session and
exposes sign-in and sign-out; it cannot change the slot or the blind flag, only ask the server for a new
session.

---

## 13. Command line

| Command | Purpose |
|---|---|
| `run` | Ingest, check and report in one step |
| `ingest` | Parse and print what was found, no comparison |
| `check` | Ingest and check, write `run.json` only |
| `report` | Build the workbook from a saved run, merging reviewer state |
| `eval` | Measure accuracy against a ground truth; `--fail-under` for CI |
| `demo` | Deterministic demo run on the golden dataset |
| `serve` | Local API and reviewer UI |
| `perf` | Timing and peak memory across dataset sizes |
| `dataset build`, `dataset corrected` | Regenerate the byte-stable synthetic dataset; write a corrected copy of one SKU set (seeded discrepancies removed) |
| `terminology …` | Ten subcommands managing relationships |
| `runs list`, `runs relationships` | Runs in the workspace; the exact relationship versions a run used |
| `certificate` | One-page certificate per SKU, or one SKU |
| `diff` | Resolved, new and still-open discrepancies between two runs |
| `review import` | Optional Excel round-trip with dry run and conflict handling |
| `review policy`, `review sessions` | Blind-review policy and open sessions |

A global `--workspace` and the `KAIZEN_WORKSPACE` environment variable select the workspace.

---

## 14. Synthetic datasets and evaluation

### Generation (`datasets/`)

`catalog.py` defines 33 components, each with its BOM wording, label wording, drawing callout and expected
classification, plus non-physical lines and drawing extras. `scenarios.py` defines ten SKU scenarios; the
renderers in `pdf_bom.py`, `pdf_label.py`, `drawing_docs.py` and `pco_docs.py` produce real PDFs and
spreadsheets laid out like the examples in the brief.

| Scenario | SKU | What it exercises |
|---|---|---|
| s01 | 1295108NS | Clean baseline: reordering, relationships, fuzzy potentials, wrapped lines, the "(3 per)" idiom, twelve non-physical rows |
| s02 | 1295108FNS | Two quantity mismatches, one behind a relationship match; an unexpected label change |
| s03 | 1395108QNS | Item missing from the label; an expired line that must not be reported; a PCO delete not applied |
| s04 | 1175108NS | Label item with no BOM line; PCO quantity mismatch |
| s05 | 1275108NS | REF against parent mismatch (blocker); drawing revision mismatch |
| s06 | 9295108FNS | Ambiguous match; label never updated; expected change absent |
| s07 | 2131910NS | Two needles differing only by gauge (numeric guard); look-alike syringes that must not pair; extra callout |
| s08 | 2131910FNS | XLSX BOM; label sub-quantity idioms; PCO substitute not applied |
| s09 | 3131910NS | Blank quantity cell (low confidence); family-scoped relationship; FreeText redline |
| s10 | 3131910FNS | CSV BOM; the same family relationship applied again |

Two PCOs are generated: PCO34590 as XLSX covering seven affected codes, one of which deliberately has no
BOM, and PCO34591 as PDF.

**Byte stability.** `_stable.py` rewrites the random `/ID` trailer that MuPDF emits and normalises zip
timestamps in the spreadsheets, so `kaizen dataset build` is reproducible byte for byte and a golden test
can assert it.

### Ground truth and harness (`evaluation/`)

The ground truth is declared by hand in the scenario definitions and written to `ground-truth.json`. It
covers the expected classification and discrepancy types per row, per check, plus PCO expectations, label
revision expectations and coverage expectations.

`harness.py` scores each check separately, producing precision, recall and F1 per discrepancy type and per
check, and reports any row where the engine and the ground truth disagree.

### Measured results

| Measure | Result |
|---|---|
| Seeded discrepancies found | 30 of 30, with 0 false positives and 0 false negatives |
| Precision and recall | 1.000 across all six scored checks |
| Rows scored | 1,049 |
| Golden run | 38 documents, 10 SKU sets, 1,473 rows, 1,237 reviewable, 82 percent auto-cleared |
| Performance | 8 / 25 / 50 / 100 SKUs in 1.5 / 4.7 / 9.4 / 19.6 s; about 0.19 s per SKU; 325 MB peak at 100 SKUs |

These numbers are measured on synthetic documents modelled on the brief, not on real BD files.

---

## 15. Optional AI and semantic layers

`ai/providers.py` defines a `Suggestion` record and three providers: `NullProvider` (the default; makes no
external calls and returns nothing), `AnthropicProvider` (opt-in, reads its key from the environment, never
from source) and a stub for Azure OpenAI. `get_provider()` **fails closed**: any misconfiguration or missing
dependency yields a `NullProvider` with a reason, never a crash and never a silent network call.

`ai/matcher.py` wraps a provider as the L5 rung. Its output is marked `weak`, labelled "AI SUGGESTION"
everywhere it appears, and cannot exceed POTENTIAL. The provider, model and prompt version are recorded in
run metadata, so a run made with AI enabled is distinguishable from one without. Turning it off reproduces
identical results.

`matching/semantic.py` is the L4 rung: an injectable embedder with cosine similarity. No model is bundled;
without one the rung is inactive. It too can only yield POTENTIAL.

---

## 16. Storage

`storage/db.py` creates a single SQLite file in the workspace and wraps the connection in `LockedConnection`:
every statement, reads included, runs under a process-wide re-entrant lock and returns materialised rows, so
no cursor is ever stepped from two API threads at once (the failure mode is `sqlite3.InterfaceError: bad
parameter or other API misuse`, seen once when the mining page issued two reads together). `_ensure_column` performs additive
migrations for columns added after the first release.

| Table | Holds |
|---|---|
| `relationships`, `relationship_versions` | Current relationships and every historical version |
| `runs`, `run_relationships` | The run index and the relationship versions each run used |
| `decisions`, `finals` | Reviewer decisions by run, row and slot; final decisions |
| `action_items` | Items with owner, status, comparison key and resolution time |
| `mining_rejections` | Suggestions a reviewer has declined |
| `sessions` | Open review sessions: reviewer, slot, blind flag, timestamps |
| `settings` | Workspace policy, currently the blind-review setting |
| `row_views` | When each reviewer slot last opened a row (the start of the effort clock) |
| `audit` | Every action with actor, timestamp and detail |

---

## 17. Tests and continuous integration

| Location | Tests | Covers |
|---|---|---|
| `tests/unit/` | 328 functions in 49 files | Models, parsers, normalisation, ladder, assignment, each check, terminology, review, sessions, review timing, worklist, run diff, certificate, Excel round-trip, reporting, evaluation, datasets, AI providers, adversarial and audit-regression cases |
| `tests/integration/` | 29 functions in 3 files | CLI end to end, terminology CLI, HTTP API contract |
| `tests/golden/` | 8 functions in 1 file | Accuracy floors against the golden ground truth, byte-stable dataset build |

Total 365 test functions, which pytest expands to **428 test cases** because some are parametrised. All pass, in about a minute, fully offline. `tests/conftest.py` forces an isolated
workspace per test so no test can write into the repository.

`.github/workflows/ci.yml` runs on every pull request and every push to main: ruff lint, the full suite on
Python 3.12 and 3.13, a golden accuracy gate that fails below precision or recall of 1.0, and a clean
install and production build of the reviewer UI. Lint rules are pinned in `pyproject.toml` so local runs and
CI agree.

---

## 18. Security and data handling

Everything runs locally. No dependency makes a network call at runtime unless an AI provider is explicitly
configured. No secrets in source; the API key is read from the environment. Document content is never
logged. Upload paths are sanitised against absolute paths and `..` segments. Database writes are serialised
under a lock, verified by a 25-thread test. Full review, including the accepted gaps, in
[`security-review.md`](security-review.md).

---

## 19. Known limitations

The honest list lives in [`known-limitations.md`](known-limitations.md). The headline items: no OCR for
scanned BOMs or drawings and no hand-drawn redlines; truncated JDE descriptions are matched by similarity
rather than a prefix rule; thresholds were tuned on synthetic data; sign-in identifies rather than
authenticates; the workspace is single-user; case labels are not parsed.

The single largest risk is that every accuracy figure in this document was measured on synthetic documents.
The parsers have never seen a real JDE print or MasterControl label.
