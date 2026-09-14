# Kaizen Cross-Check — local API contract (for the reviewer UI)

Base URL when running `kaizen serve`: `http://127.0.0.1:8765`. All endpoints are local. JSON unless stated.
Errors: 400 (invalid input), 401 (no review session, or a bad code), 403 (refused for a blind reviewer),
404 (unknown id), 429 (too many codes for one address), 502 (the server cannot send mail).

## Signing in (email one-time code)
A session can only be opened by someone who received a code at an allowed email address. Sign-in is two
calls: the server mails a six-digit code, then the code is exchanged for a session. The code is stored
only as a SHA-256 hash, expires after 10 minutes, works once, and dies after 5 wrong guesses.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/request-otp` | `{email}` | `{ok: true}` — always the same response, whoever the address belongs to |
| POST | `/api/auth/verify-otp` | `{email, code, slot:1\|2, blind?}` | `{reviewer, slot, blind, created_at, blind_review_policy}` + `Set-Cookie` |

The address must end in a whole allowed domain — `bd.com` by default, so `user@gmail.com` and
`user@bd.com.evil.io` are both **400** `Only BD email addresses can sign in.` A wrong, expired, reused or
exhausted code is **401** `Invalid or expired code.`; a fourth code inside ten minutes for one address is
**429**. The verified address becomes the reviewer's identity: it is what decisions, exports, certificates
and the audit trail are recorded against.

### Environment
| Variable | Purpose |
|---|---|
| `KAIZEN_ALLOWED_DOMAINS` | Comma-separated domains that may sign in. Default `bd.com`. |
| `KAIZEN_SMTP_HOST`, `KAIZEN_SMTP_PORT` | Mail server; port defaults to 587. STARTTLS is always used. |
| `KAIZEN_SMTP_USER`, `KAIZEN_SMTP_PASSWORD` | SMTP credentials. Omit both for an unauthenticated relay. |
| `KAIZEN_SMTP_FROM` | Envelope sender. Required — without it `request-otp` returns 502. |
| `KAIZEN_OTP_DEV_MODE=1` | Demos and tests: log the code instead of mailing it, and reopen `POST /api/sessions`. Never set it on a shared deployment. |
| `KAIZEN_OTP_RATE_LIMIT` | Codes per address per 10 minutes. Default 3. |

## Reviewer sessions
Reviewer identity, slot and blind mode are held by the server, not by the client. The session token is
returned in an HttpOnly cookie (`kaizen_session`), so page scripts cannot read or forge it, and the
`viewer` / `blind` query parameters below are **ignored whenever a session is present**.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/sessions` | `{reviewer, slot:1\|2, blind?}` | **403** `Use /api/auth/verify-otp` unless `KAIZEN_OTP_DEV_MODE=1`. Superseded by the OTP flow above. |
| GET | `/api/sessions/current` | | `{session: {...}\|null, blind_review_policy}` |
| DELETE | `/api/sessions/current` | | `{ended: bool}` and clears the cookie |

`blind` is decided by the server: reviewer 1 is never blind; reviewer 2 is always blind while the
workspace policy is `required` (the default) and may pass `blind:false` only when it is `optional`. The
policy is changed from the command line (`kaizen review policy --set required|optional`), never over HTTP.

While the policy is `required`, these endpoints return **401** without a session: run summary, results,
row detail, decisions, finalize, bulk-accept, action items, relationships from a row, mining approve and
reject, terminology writes, export, annotated BOM and audit. Run creation and health do not need one.

A **blind** session is refused (**403**) on `export.xlsx`, `annotated-bom` and `/api/audit`, because all
three would reveal reviewer 1's decisions. It is also refused on `finalize` for a row it cannot see.

## Runs
| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{status, version, workspace}` |
| GET | `/api/runs` | | `[{run_id, created_at, input_root, tool_version, terminology_version, json_path, summary:{rows, skus, documents, needs_validation, EXACT, EQUIVALENT, POTENTIAL, MISMATCH, MISSING}}]` (flat classification keys; counts over all rows as registered) |
| POST | `/api/runs/from-path` | `{path}` (local folder) | run summary (below) |
| POST | `/api/runs/upload` | multipart `files[]`; each filename may include a relative path such as `sku-001/bom.pdf` (use `webkitRelativePath` for folder drops) | run summary |
| POST | `/api/demo/load` | | run summary (golden dataset) |
| GET | `/api/runs/{run_id}` | | run summary |

Run summary: `{run_id, timestamp, input_root, tool_version, skus, documents, documents_by_type:{BOM,LABEL,DRAWING,PCO}, unrecognised_files[], rows (all result rows), reviewable_rows (roles item + change), exempt_rows, header_rows_needing_validation (header/reference/coverage rows that need validation, e.g. REF mismatch or missing BOM), counts:{EXACT,EQUIVALENT,POTENTIAL,MISMATCH,MISSING} (reviewable rows), needs_validation and auto_cleared (reviewable rows; the review queue with needs_validation=true additionally lists header/coverage rows, so it can be larger by header_rows_needing_validation), per_check:{BOM_LABEL,BOM_DRAWING,LABEL_DRAWING,PCO_BOM,LABEL_REVISION}, blockers, coverage:[{kind, sku, status: OK|MISSING_BOM|MISSING_LABEL, detail, source}], groups:[{sku, family, document_ids[], warnings[]}], warnings[], parser_warnings:[{document, doc_id, warnings[]}], low_confidence_rows, state_counts:{ENGINE_RECOMMENDED, REVIEWER_1_COMPLETE, REVIEWER_2_COMPLETE, AGREED, DISAGREEMENT, FINALIZED}, terminology_version, terminology_count, relationships_used[], capabilities:{name: status}, thresholds, inputs:[{path, sha256, size_bytes, doc_type}]}`

## Results (review queue)
`GET /api/runs/{run_id}/results` params: `check` (BOM_LABEL|BOM_DRAWING|LABEL_DRAWING|PCO_BOM|LABEL_REVISION), `sku`, `classification` (EXACT|EQUIVALENT|POTENTIAL|MISMATCH|MISSING), `severity` (BLOCKER|MAJOR|MINOR|INFO), `discrepancy` (type name), `needs_validation` (true/false), `state`, `role` (item|header|reference|coverage|exempt|change), `search`, `limit` (default 500), `offset`. (`viewer` and `blind` are still accepted but ignored when a session is present.)
Default order: blocker → major → ambiguous → potential → low-confidence → sku → row id.
Returns `{total, offset, limit, rows:[{row_id, sku, check, role, engine:{classification, match_level, score, relationship_id, requires_validation, severity, discrepancies[], explanation}, decisions:{"1": Decision|null, "2": Decision|null}, state, final, effective_classification, a:{item_number, description, quantity, page, locator, file_name}|null, b:{...}|null, discrepancies:[{type, severity, detail, recommended_action}], action_items[]}]}`.
Also returns `viewer: {slot, blind, reviewer}` — the visibility actually applied.
Decision: `{slot, reviewer, decision, comment, override_classification, decided_at, blind}`. For a blind
reviewer 2, until they have decided a row, that row's `decisions["1"]` is `null`, `state` reads
`ENGINE_RECOMMENDED`, `final` is `null` and `effective_classification` shows the engine value — reviewer
1's work is hidden completely, not just in the decision field.

`GET /api/runs/{run_id}/results/{row_id}` → `{result: full CheckResult (source_a/source_b with evidence), evidence:{a: Evidence|null, b: Evidence|null}, decisions, state, final, effective_classification, history:[{event: engine|reviewer_1|reviewer_2|final, ...}], viewer, action_items[]}`. For a blind reviewer 2 the history contains only the engine event.
Evidence: `{doc_id, doc_type, file, file_name, sha256, page, bbox:{x0,y0,x1,y1}|null, locator, raw_text, sheet, item_number, description, quantity, uom, oper_seq, category, category_reason, confidence, attributes, sub_quantity}`.

## Documents and evidence images
| GET | `/api/runs/{run_id}/documents` | `[{id, doc_type, file, file_name, sku, sha256, parser, parser_version, items, warnings[], header, pages}]` |
| GET | `/api/runs/{run_id}/documents/{doc_id}/items` | `{doc_id, doc_type, file_name, header, warnings, pages, items:[Evidence + {id, is_active}]}` (extraction verification view) |
| GET | `/api/runs/{run_id}/documents/{doc_id}/pages/{n}?highlight={row_id}&dpi=110` | `image/png` of page n (1-based); with `highlight`, the evidence box of that row on this document is outlined. Bbox coordinates in Evidence are PDF points at 72 dpi; the image is rendered at `dpi`, so scale = dpi/72. 404 for spreadsheet sources. |

## Decisions
| POST | `/api/runs/{run_id}/decisions` | `{row_id, decision: ACCEPT|OVERRIDE|CONFIRM_DISCREPANCY|NEEDS_MORE_INFORMATION, comment?, override_classification? (required for OVERRIDE)}` → `{row_id, state, decisions, effective_classification}`. Slot, reviewer name and the blind flag come from the session; a `slot` that contradicts the session is a 400. |
| POST | `/api/runs/{run_id}/finalize` | `{row_id, final_decision, note?}` → `{row_id, state}` |
| POST | `/api/runs/{run_id}/bulk-accept` | `{}` → `{accepted}` (only rows with no discrepancy and not needing validation) |
| POST | `/api/runs/{run_id}/relationships/from-row` | `{row_id, scope? (global|family:<prefix>|sku:<code>), anchor? (bind to the BOM item number), canonical?, aliases?, doc_types?, notes?}` → Relationship |

## Terminology worklist
`GET /api/runs/{run_id}/terminology-worklist` → `{needs_validation, potential_rows, items:[Suggestion + {would_clear, still_review, cumulative_clear, cumulative_pct}], top5:{n, rows, pct}, top10:{...}}`.
Unconfirmed fuzzy pairings ranked by the rows they would auto-clear once approved as relationships (rows carrying a discrepancy or an ambiguity are `still_review`). Approval goes through `mining/approve`; nothing is created automatically.

## Review effort (measured)
Opening a row (`GET .../results/{row_id}`) with a session records the time for that reviewer's slot; the next decision on that row by the same slot stores `seconds_spent` (only when the gap is between 5 seconds and 15 minutes; bulk accept and Excel imports are never timed). `GET .../business-case` uses the median of timed decisions once there are at least 10 (`effort_basis: "measured"`, `timed_decisions`, `measured_minutes_per_validation_row`, `minutes_per_validation_row_used`); below that it keeps the brief's assumption and says so.

## Certificate, run diff, optional Excel round-trip
| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/api/runs/{run_id}/certificate.pdf?sku=` | optional `sku` | One A4 page per SKU (or one SKU): run id, timestamp, terminology version, thresholds, every input file with its SHA-256, counts by classification, blockers, named reviewers with decision counts, finalized and disagreement counts, open action items, statement and signature lines. 403 for a blind session; 404 for an unknown SKU. |
| GET | `/api/runs/{run_id}/diff?against={earlier_run_id}` | | `{before_run_id, after_run_id, skus:{added,removed,common}, documents:{changed:[{path, before_sha256, after_sha256}], added, removed}, counts:{resolved,new,still_open,changed,unchanged,gone,not_covered}, rows:[{key, sku, check, status, before, after, note}]}`. Rows are matched by comparison key; `unchanged` rows are counted, not listed; `gone` means the comparison disappeared, which is not the same as fixed. |
| POST | `/api/runs/{run_id}/decisions/import` | multipart `file` (.xlsx exported by this tool), `dry_run` (default false), `force` (default false) | `RoundTripResult`: `{run_id, slot, reviewer, dry_run, force, exported_at, applied[], unchanged[], conflicts:[{row_id, sheet, workbook, database, database_decided_at, database_reviewer}], invalid:[{row_id, sheet, value, reason}], unknown_rows[], summary}`. Slot and reviewer come from the session. The workbook must carry this run's id (400 otherwise); only the session's own columns are read; a decision changed in the database after `Exported at` is a conflict and is skipped unless `force`. Optional feature. |

## Relationship mining
| GET | `/api/runs/{run_id}/mining?min_skus=2` | `[{a_text, b_text, a_key, b_key, pair_key, sku_count, skus[], check_types[], row_ids[], confirmed, contradicted, item_anchors[], relationship_id, evidence}]` |
| POST | `/api/runs/{run_id}/mining/approve` | `{a_key, b_key, by, scope?, anchor?, notes?}` → Relationship |
| POST | `/api/runs/{run_id}/mining/reject` | `{a_key, b_key, by, note?}` → `{rejected}` |

## Action items, verify & close, business case
| POST | `/api/runs/{run_id}/action-items` | `{row_id, reviewer, owner?}` → ActionItem |
| GET | `/api/action-items?status=&run_id=` | `[ActionItem]` |
| PATCH | `/api/action-items/{id}` | `{status? (OPEN|IN_PROGRESS|RESOLVED|CLOSED|REJECTED), owner?, by, note?}` → ActionItem |
| POST | `/api/runs/{run_id}/verify-and-close` | → `{run_id, resolved[], still_open[], not_covered[]}` |
| GET | `/api/runs/{run_id}/business-case` | params override assumptions: `baseline_minutes_per_sku, hourly_rate, skus_per_project, projects_per_year, reviewers, minutes_per_validation_row, minutes_per_cleared_row, target_reduction_pct` → `{skus, rows, auto_cleared, needs_validation, estimated_minutes_per_sku, minutes_saved_per_sku, reduction_pct, hours_saved_per_project, annual_savings, meets_target, assumptions[], per_sku, confirmable_rows, needs_validation_after_confirmation, estimated_minutes_per_sku_after_confirmation, reduction_pct_after_confirmation, hours_saved_per_project_after_confirmation, annual_savings_after_confirmation, meets_target_after_confirmation}` — the `*_after_confirmation` fields are a labelled projection (strong POTENTIAL pairings confirmed as relationships), not a measurement |

ActionItem: `{id, run_id, row_id, sku, check_type, discrepancy_type, severity, detail, recommended_action, owner, status, reviewer, created_at, updated_at, resolved_in_run, resolved_at, comparison_key}`.

## Exports
| GET | `/api/runs/{run_id}/export.xlsx` | workbook with reviewer decisions merged |
| GET | `/api/runs/{run_id}/annotated-bom/{doc_id}` | PDF; headers `X-Kaizen-Fallback` (true when the BOM had no page geometry) and `X-Kaizen-Marks` |

## Terminology
| GET | `/api/terminology?search=&scope=&all=` | `[Relationship + {usage}]` |
| POST | `/api/terminology` | `{canonical, aliases[], scope, doc_types[], item_anchors[], provenance?, by, notes}` |
| GET | `/api/terminology/{id}` · PUT `/api/terminology/{id}` `{canonical?, aliases?, scope?, doc_types?, item_anchors?, notes?, by, note}` · POST `/{id}/deactivate` `{by}` · POST `/{id}/activate` · DELETE `/{id}?by=` · GET `/{id}/history` |
| GET | `/api/terminology/export.xlsx` · POST `/api/terminology/import` (multipart `file`, form `by`) → `{summary (string, e.g. "created 1, updated 0, unchanged 9, errors 0"), created, updated, unchanged, errors[]}` |
| GET | `/api/audit?limit=200` | recent audit events |

Relationship: `{id, canonical, aliases[], scope, doc_types[], item_anchors[], provenance (manual|learned|imported), created_by, created_at, updated_at, active, notes, version}`.
