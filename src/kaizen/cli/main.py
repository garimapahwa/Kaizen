"""Kaizen Cross-Check command line. The CLI is the source of truth: every capability is reachable here."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from kaizen import __version__
from kaizen.datasets.build import build_golden
from kaizen.evaluation.ground_truth import load_ground_truth
from kaizen.evaluation.harness import evaluate
from kaizen.evaluation.report import render_console, write_json
from kaizen.ingest.detect import parse_document
from kaizen.models import Classification, Run, Thresholds
from kaizen.pipeline import discover_files, load_run, run_folder, save_run
from kaizen.reporting.excel import write_report
from kaizen.terminology.exchange import export_csv, export_xlsx, import_csv, import_xlsx
from kaizen.terminology.store import RelationshipStore
from kaizen.workspace import Workspace

app = typer.Typer(help=f"Kaizen Cross-Check v{__version__} — deterministic, explainable BOM ↔ Label cross-checking.", no_args_is_help=True, add_completion=False)
dataset_app = typer.Typer(help="Synthetic golden dataset commands.", no_args_is_help=True)
terminology_app = typer.Typer(help="Terminology relationships: explicit, versioned business rules.", no_args_is_help=True)
runs_app = typer.Typer(help="Runs recorded in the workspace.", no_args_is_help=True)
review_app = typer.Typer(help="Review policy and open reviewer sessions.", no_args_is_help=True)
users_app = typer.Typer(help="Reviewer accounts. Reviewers sign themselves up; this is how an account is cleared.", no_args_is_help=True)
auth_app = typer.Typer(help="Where sign-in accounts live: this workspace (default) or a Supabase project.", no_args_is_help=True)
app.add_typer(dataset_app, name="dataset")
app.add_typer(auth_app, name="auth")
app.add_typer(terminology_app, name="terminology")
app.add_typer(runs_app, name="runs")
app.add_typer(review_app, name="review")
app.add_typer(users_app, name="users")
console = Console()


@app.callback()
def main(
    ctx: typer.Context,
    workspace: Annotated[Optional[Path], typer.Option("--workspace", "-w", envvar="KAIZEN_WORKSPACE", help="Workspace folder holding kaizen.db (terminology, runs, decisions). Default ./kaizen-workspace")] = None,
) -> None:
    ctx.obj = {"workspace": workspace}


def _ws(ctx: typer.Context) -> Workspace:
    return Workspace.resolve((ctx.obj or {}).get("workspace"))

RelOpt = Annotated[Optional[Path], typer.Option("--relationships", "-r", help="Relationships JSON file (default: packaged defaults).")]
PotOpt = Annotated[Optional[float], typer.Option("--potential", help="Min token similarity for a POTENTIAL match (default 0.85).")]
FloorOpt = Annotated[Optional[float], typer.Option("--floor", help="Candidate floor below which pairs are not considered (default 0.60).")]
DeltaOpt = Annotated[Optional[float], typer.Option("--ambiguity-delta", help="Top-2 candidates closer than this are AMBIGUOUS (default 0.05).")]


def _store(ctx: typer.Context, path: Path | None) -> RelationshipStore:
    return RelationshipStore.load(path) if path else _ws(ctx).repository.store()


def _thresholds(potential: float | None, floor: float | None, delta: float | None) -> Thresholds:
    t = Thresholds()
    updates = {k: v for k, v in (("potential", potential), ("floor", floor), ("ambiguity_delta", delta)) if v is not None}
    return t.model_copy(update=updates) if updates else t


def _print_summary(run: Run) -> None:
    typer.echo(f"Run {run.metadata.run_id}: {len(run.documents)} documents, {len(run.groups)} SKU sets, {len(run.results)} rows, terminology {run.metadata.terminology_version[:12]} ({run.metadata.terminology_count} relationships)")
    for g in run.groups:
        rows = [r for r in run.results if r.sku == g.sku]
        counts = {c.value: sum(1 for r in rows if r.classification is c) for c in Classification}
        needs = sum(1 for r in rows if r.requires_validation)
        blockers = sum(1 for r in rows for d in r.discrepancies if d.severity.value == "BLOCKER")
        status = "BLOCKED" if blockers else "NEEDS REVIEW" if needs else "AUTO-CLEARED" if rows else "NOT CHECKED"
        typer.echo(f"  SKU {g.sku}: {len(rows)} rows | EXACT {counts['EXACT']} EQUIVALENT {counts['EQUIVALENT']} POTENTIAL {counts['POTENTIAL']} MISMATCH {counts['MISMATCH']} MISSING {counts['MISSING']} | needs validation {needs} | {status}")
        for w in g.warnings:
            typer.echo(f"      warning: {w}")
    for w in run.warnings:
        typer.echo(f"  warning: {w}")


def _out_dir(out: Path | None, run: Run) -> Path:
    return out if out is not None else Path("out") / run.metadata.run_id


@app.command()
def run(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(help="Folder with BOM/label documents (one SKU set per sub-folder, or flat).")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Output folder (default out/<run-id>).")] = None,
    relationships: RelOpt = None,
    potential: PotOpt = None,
    floor: FloorOpt = None,
    ambiguity_delta: DeltaOpt = None,
) -> None:
    """Ingest, check and report in one step: writes run.json and report.xlsx."""
    r = run_folder(folder, _store(ctx, relationships), _thresholds(potential, floor, ambiguity_delta))
    out_dir = _out_dir(out, r)
    path = save_run(r, out_dir / "run.json")
    write_report(r, out_dir / "report.xlsx")
    _ws(ctx).register_run(r, path, record_usage=relationships is None)
    _print_summary(r)
    typer.echo(f"Wrote {out_dir / 'run.json'} and {out_dir / 'report.xlsx'}")


@app.command()
def ingest(folder: Annotated[Path, typer.Argument(help="Folder to scan.")]) -> None:
    """Detect and parse documents; print what was found (no comparison)."""
    files = discover_files(folder)
    if not files:
        typer.echo("no supported files found (.pdf, .xlsx, .csv)")
        raise typer.Exit(code=1)
    for f in files:
        outcome = parse_document(f)
        rel = f.relative_to(folder).as_posix() if f.is_relative_to(folder) else str(f)
        if outcome.document is None:
            typer.echo(f"{rel}: {outcome.doc_type.value if outcome.doc_type else 'UNKNOWN'} — skipped: {outcome.reason}")
            continue
        d = outcome.document
        typer.echo(f"{rel}: {d.doc_type.value} sku/ref={d.sku} parser={d.parser_name} v{d.parser_version} items={len(d.items)} sha256={d.sha256[:12]}")
        for w in d.warnings:
            typer.echo(f"    warning: {w}")


@app.command()
def check(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(help="Folder with BOM/label documents.")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o")] = None,
    relationships: RelOpt = None,
    potential: PotOpt = None,
    floor: FloorOpt = None,
    ambiguity_delta: DeltaOpt = None,
) -> None:
    """Ingest and check; write run.json only (use `report` to produce the workbook)."""
    r = run_folder(folder, _store(ctx, relationships), _thresholds(potential, floor, ambiguity_delta))
    out_dir = _out_dir(out, r)
    path = save_run(r, out_dir / "run.json")
    _ws(ctx).register_run(r, path, record_usage=relationships is None)
    _print_summary(r)
    typer.echo(f"Wrote {out_dir / 'run.json'}")


@app.command()
def report(
    ctx: typer.Context,
    run_json: Annotated[Path, typer.Argument(help="run.json written by `check` or `run`.")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Workbook path (default report.xlsx next to run.json).")] = None,
) -> None:
    """Write the Excel workbook for a saved run (reviewer decisions from the workspace are merged in)."""
    from kaizen.reporting.excel import export_with_review

    r = load_run(run_json)
    ws = _ws(ctx)
    target = out or run_json.with_name("report.xlsx")
    path = export_with_review(ws, r, target) if ws.runs.get(r.metadata.run_id) else write_report(r, target)
    typer.echo(f"Wrote {path}")


@app.command()
def certificate(
    ctx: typer.Context,
    run_json: Annotated[Path, typer.Argument(help="run.json written by `check` or `run`.")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="PDF path (default certificate.pdf next to run.json).")] = None,
    sku: Annotated[Optional[str], typer.Option("--sku", help="One SKU only (default: one page per SKU in the run).")] = None,
) -> None:
    """Cross-check certificate: one page per SKU with run id, file hashes, counts, reviewers and open action items."""
    from kaizen.reporting.certificate import write_certificate, write_run_certificate
    from kaizen.reporting.excel import ReviewBundle
    from kaizen.review.action_items import ActionItemStore
    from kaizen.review.store import ReviewStore

    r = load_run(run_json)
    ws = _ws(ctx)
    review = ReviewStore(ws.db)
    rid = r.metadata.run_id
    decisions, finals = review.all_decisions(rid), review.finals(rid)
    bundle = ReviewBundle(decisions=decisions, finals=finals, states={x.row_id: ReviewStore.state_of(decisions.get(x.row_id, {}), finals.get(x.row_id)) for x in r.results}, action_items=ActionItemStore(ws.db).for_run(rid))
    target = out or run_json.with_name(f"certificate-{sku}.pdf" if sku else "certificate.pdf")
    try:
        path = write_certificate(r, sku, target, bundle) if sku else write_run_certificate(r, target, bundle)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo(f"Wrote {path}")


@app.command()
def diff(
    before_json: Annotated[Path, typer.Argument(help="run.json of the earlier run.")],
    after_json: Annotated[Path, typer.Argument(help="run.json of the later run (e.g. after corrected documents).")],
    as_json: bool = typer.Option(False, "--json", help="Print the full diff as JSON."),
    limit: int = typer.Option(50, "--limit", help="Rows to print per status."),
) -> None:
    """What changed between two runs: resolved, new and still-open discrepancies, changed documents."""
    import json as _json

    from kaizen.review.rundiff import STATUSES, diff_runs

    d = diff_runs(load_run(before_json), load_run(after_json))
    if as_json:
        typer.echo(_json.dumps(d.to_dict(), indent=2))
        return
    typer.echo(f"{d.before_run_id} → {d.after_run_id}")
    typer.echo("  " + "  ".join(f"{k} {d.counts[k]}" for k in STATUSES))
    typer.echo(f"  SKUs: {len(d.skus['common'])} common, {len(d.skus['added'])} added, {len(d.skus['removed'])} removed; documents changed {len(d.documents['changed'])}, added {len(d.documents['added'])}, removed {len(d.documents['removed'])}")
    for doc in d.documents["changed"]:
        typer.echo(f"  changed: {doc['path']}")
    for status in ("resolved", "new", "still_open", "gone", "changed"):
        rows = [c for c in d.rows if c.status == status][:limit]
        for c in rows:
            side = c.after or c.before or {}
            typer.echo(f"  [{status}] {c.sku} {c.check} {c.key.split('|')[-1]}: {', '.join(side.get('discrepancies', [])) or side.get('classification', '')} — {c.note}")


@app.command("eval")
def eval_cmd(
    ctx: typer.Context,
    dataset: Annotated[Path, typer.Argument(help="Dataset folder containing ground-truth.json and SKU folders.")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Output folder (default out/eval-<run-id>).")] = None,
    ground_truth: Annotated[Optional[Path], typer.Option("--ground-truth", help="Ground truth file (default <dataset>/ground-truth.json).")] = None,
    relationships: RelOpt = None,
    potential: PotOpt = None,
    floor: FloorOpt = None,
    ambiguity_delta: DeltaOpt = None,
    fail_under: Annotated[Optional[float], typer.Option("--fail-under", help="Exit non-zero if discrepancy precision or recall is below this.")] = None,
) -> None:
    """Run against a golden dataset and measure accuracy versus ground truth."""
    gt = load_ground_truth(ground_truth or dataset / "ground-truth.json")
    r = run_folder(dataset, _store(ctx, relationships), _thresholds(potential, floor, ambiguity_delta))
    m = evaluate(r, gt)
    out_dir = out if out is not None else Path("out") / f"eval-{r.metadata.run_id}"
    path = save_run(r, out_dir / "run.json")
    _ws(ctx).register_run(r, path, record_usage=relationships is None)
    write_json(m, out_dir / "metrics.json")
    write_report(r, out_dir / "report.xlsx", metrics=m.to_dict())
    render_console(m, console)
    typer.echo(f"Wrote {out_dir / 'metrics.json'}, {out_dir / 'run.json'}, {out_dir / 'report.xlsx'}")
    if fail_under is not None and (m.overall.precision < fail_under or m.overall.recall < fail_under):
        typer.echo(f"FAIL: precision {m.overall.precision:.3f} / recall {m.overall.recall:.3f} below {fail_under}")
        raise typer.Exit(code=2)


# ---- terminology ------------------------------------------------------------------------------------------
def _print_rel(rel, usage: dict[str, int] | None = None) -> None:
    used = f" | used in {usage.get(rel.id, 0)} comparisons" if usage is not None else ""
    typer.echo(f"{rel.id} v{rel.version} [{rel.scope}] {'active' if rel.active else 'INACTIVE'}: {rel.canonical} = {' = '.join(rel.aliases) or '(no aliases)'}{used}")
    extras = []
    if rel.item_anchors:
        extras.append(f"anchors {', '.join(rel.item_anchors)}")
    if rel.doc_types:
        extras.append(f"doc types {', '.join(d.value for d in rel.doc_types)}")
    extras.append(f"{rel.provenance} by {rel.created_by} {rel.created_at.date()}")
    if rel.notes:
        extras.append(rel.notes)
    typer.echo("    " + " | ".join(extras))


@terminology_app.command("list")
def terminology_list(ctx: typer.Context, scope: Optional[str] = typer.Option(None, "--scope"), search: Optional[str] = typer.Option(None, "--search", "-s"), show_all: bool = typer.Option(False, "--all", help="Include inactive relationships")) -> None:
    """List relationships (active by default)."""
    repo = _ws(ctx).repository
    usage = repo.usage_counts()
    rels = repo.list(active_only=not show_all, scope=scope, search=search)
    for rel in rels:
        _print_rel(rel, usage)
    typer.echo(f"{len(rels)} relationship(s)")


@terminology_app.command("show")
def terminology_show(ctx: typer.Context, rel_id: str) -> None:
    """Show one relationship and why it exists."""
    repo = _ws(ctx).repository
    rel = repo.get(rel_id)
    if rel is None:
        typer.echo(f"{rel_id} not found (it may have been deleted; see `terminology history {rel_id}`)")
        raise typer.Exit(code=1)
    _print_rel(rel, repo.usage_counts())


@terminology_app.command("add")
def terminology_add(
    ctx: typer.Context,
    canonical: str = typer.Option(..., "--canonical", "-c"),
    alias: list[str] = typer.Option([], "--alias", "-a", help="Repeatable"),
    scope: str = typer.Option("global", "--scope"),
    doc_type: list[str] = typer.Option([], "--doc-type", help="Repeatable: BOM, LABEL, DRAWING, PCO"),
    anchor: list[str] = typer.Option([], "--anchor", help="Repeatable BOM item numbers"),
    provenance: str = typer.Option("manual", "--provenance"),
    by: str = typer.Option("cli", "--by"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Create a relationship."""
    rel = _ws(ctx).repository.create(canonical=canonical, aliases=alias, scope=scope, doc_types=[d.upper() for d in doc_type], item_anchors=anchor, provenance=provenance, created_by=by, notes=notes)
    typer.echo(f"Created {rel.id} v{rel.version}: {rel.canonical} = {' = '.join(rel.aliases)}")


@terminology_app.command("update")
def terminology_update(
    ctx: typer.Context,
    rel_id: str,
    canonical: Optional[str] = typer.Option(None, "--canonical", "-c"),
    alias: list[str] = typer.Option([], "--alias", "-a", help="Repeatable; replaces the alias list when given"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    anchor: list[str] = typer.Option([], "--anchor", help="Repeatable; replaces anchors when given"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    by: str = typer.Option("cli", "--by"),
    note: str = typer.Option("", "--note", help="Why this change was made"),
) -> None:
    """Update a relationship (records a new version)."""
    fields = {k: v for k, v in (("canonical", canonical), ("scope", scope), ("notes", notes)) if v is not None}
    if alias:
        fields["aliases"] = alias
    if anchor:
        fields["item_anchors"] = anchor
    rel = _ws(ctx).repository.update(rel_id, changed_by=by, change_note=note, **fields)
    typer.echo(f"Updated {rel.id} → v{rel.version}")


@terminology_app.command("deactivate")
def terminology_deactivate(ctx: typer.Context, rel_id: str, by: str = typer.Option("cli", "--by"), note: str = typer.Option("", "--note")) -> None:
    rel = _ws(ctx).repository.deactivate(rel_id, changed_by=by, change_note=note)
    typer.echo(f"Deactivated {rel.id} → v{rel.version}")


@terminology_app.command("activate")
def terminology_activate(ctx: typer.Context, rel_id: str, by: str = typer.Option("cli", "--by"), note: str = typer.Option("", "--note")) -> None:
    rel = _ws(ctx).repository.activate(rel_id, changed_by=by, change_note=note)
    typer.echo(f"Activated {rel.id} → v{rel.version}")


@terminology_app.command("delete")
def terminology_delete(ctx: typer.Context, rel_id: str, by: str = typer.Option("cli", "--by"), note: str = typer.Option("", "--note")) -> None:
    """Delete a relationship (history is kept; historical runs still reconstruct)."""
    _ws(ctx).repository.delete(rel_id, changed_by=by, change_note=note)
    typer.echo(f"Deleted {rel_id} (history retained)")


@terminology_app.command("history")
def terminology_history(ctx: typer.Context, rel_id: str) -> None:
    """Show every version of a relationship."""
    for h in _ws(ctx).repository.history(rel_id):
        p = h.payload
        typer.echo(f"v{h.version} {h.change_type} by {h.changed_by} at {h.changed_at.isoformat(timespec='seconds')}: {p.canonical} = {' = '.join(p.aliases)} [{p.scope}] {'active' if p.active else 'inactive'} — {h.change_note}")


@terminology_app.command("sync-defaults")
def terminology_sync(ctx: typer.Context, by: str = typer.Option("cli", "--by")) -> None:
    """Add packaged default relationships missing from this workspace; refresh unedited ones. Never touches edited relationships."""
    added, refreshed = _ws(ctx).repository.sync_defaults(changed_by=by)
    typer.echo(f"Synced packaged defaults: {added} added, {refreshed} refreshed")


@terminology_app.command("export")
def terminology_export(ctx: typer.Context, path: Path) -> None:
    """Export relationships to .xlsx or .csv."""
    repo = _ws(ctx).repository
    out = export_csv(repo, path) if path.suffix.lower() == ".csv" else export_xlsx(repo, path)
    typer.echo(f"Exported {len(repo.list())} relationships to {out}")


@terminology_app.command("import")
def terminology_import(ctx: typer.Context, path: Path, by: str = typer.Option("import", "--by")) -> None:
    """Import relationships from .xlsx or .csv (creates or versions)."""
    repo = _ws(ctx).repository
    result = import_csv(repo, path, imported_by=by) if path.suffix.lower() == ".csv" else import_xlsx(repo, path, imported_by=by)
    typer.echo(f"Import {path.name}: {result.summary()}")
    for e in result.errors:
        typer.echo(f"  error: {e}")


# ---- runs -------------------------------------------------------------------------------------------------
@review_app.command("policy")
def review_policy(
    ctx: typer.Context,
    set_to: Optional[str] = typer.Option(None, "--set", help="required (reviewer 2 is always blind) or optional (reviewer 2 may unblind)."),
    by: str = typer.Option("cli", "--by", help="Who is changing the policy (recorded in the audit log)."),
) -> None:
    """Show or change the blind-review policy. Only reachable from the command line, never from the UI."""
    from kaizen.review.sessions import SessionStore

    sessions = SessionStore(_ws(ctx).db)
    if set_to:
        try:
            sessions.set_policy(set_to.strip().lower(), by=by)
        except ValueError as e:
            raise typer.BadParameter(str(e))
    current = sessions.policy()
    console.print(f"Blind review policy: [bold]{current}[/bold]")
    console.print("  required — reviewer 2 never sees reviewer 1's decision on a row until they have recorded their own." if current == "required"
                  else "  optional — reviewer 2 may choose to review unblinded. Independence is no longer enforced.")


@review_app.command("import")
def review_import(
    ctx: typer.Context,
    run_json: Annotated[Path, typer.Argument(help="run.json of the run the workbook was exported from.")],
    workbook: Annotated[Path, typer.Argument(help="The exported .xlsx with decisions filled in.")],
    slot: int = typer.Option(..., "--slot", help="Reviewer slot whose columns to read: 1 or 2."),
    reviewer: str = typer.Option(..., "--reviewer", help="Name recorded on every applied decision."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing."),
    force: bool = typer.Option(False, "--force", help="Overwrite decisions the database changed after the export."),
) -> None:
    """Optional Excel round-trip: apply decisions recorded in an exported workbook (validated; conflicts are never silently overwritten)."""
    from kaizen.reporting.excel_import import import_decisions

    r = load_run(run_json)
    try:
        res = import_decisions(_ws(ctx), r, workbook, slot, reviewer, dry_run=dry_run, force=force)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo(res.summary())
    for c in res.conflicts:
        typer.echo(f"  conflict {c['row_id']}: workbook {c['workbook']!r} vs database {c['database']!r} by {c['database_reviewer']} at {c['database_decided_at']}")
    for i in res.invalid:
        typer.echo(f"  invalid {i['row_id']} ({i['sheet']}): {i['reason']}")
    for u in res.unknown_rows:
        typer.echo(f"  unknown row {u}")
    if res.conflicts and not force:
        raise typer.Exit(code=2)


@review_app.command("sessions")
def review_sessions(ctx: typer.Context, end: Optional[str] = typer.Option(None, "--end", help="End the session for this reviewer name.")) -> None:
    """List open reviewer sessions (and optionally end one)."""
    from kaizen.review.sessions import SessionStore

    sessions = SessionStore(_ws(ctx).db)
    if end:
        ended = [s for s in sessions.active() if s.reviewer.lower() == end.strip().lower()]
        for s in ended:
            sessions.end(s.token)
        console.print(f"Ended {len(ended)} session(s) for {end}.")
    active = sessions.active()
    for s in active:
        typer.echo(f"{s.reviewer:<20} slot {s.slot}  blind {'yes' if s.blind else 'no ':<3}  opened {s.created_at}")
    typer.echo(f"{len(active)} open session(s)")


def _local_accounts_only(ctx: typer.Context) -> None:
    """`users` commands manage local accounts; with Supabase the accounts are in its dashboard."""
    from kaizen.review.auth import SupabaseAccounts, accounts_for

    try:
        backend = accounts_for(_ws(ctx).db)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    if isinstance(backend, SupabaseAccounts):
        console.print(f"Accounts are in Supabase ({backend.url}). Manage them in the Supabase dashboard (Authentication → Users). To set a new password for someone, see “Forgotten passwords” in the README.")
        raise typer.Exit(code=1)


@auth_app.command("show")
def auth_show(ctx: typer.Context) -> None:
    """Where accounts are checked, and where that setting comes from."""
    from kaizen.review.auth import env_config, saved_config

    db = _ws(ctx).db
    try:
        env = env_config()
    except ValueError as e:
        raise typer.BadParameter(str(e))
    saved = saved_config(db)
    if env:
        typer.echo(f"Accounts: Supabase at {env[0]} (from KAIZEN_SUPABASE_URL / KAIZEN_SUPABASE_KEY)")
        if saved:
            typer.echo(f"  (the workspace also has {saved[0]} saved; the environment wins)")
    elif saved:
        typer.echo(f"Accounts: Supabase at {saved[0]} (saved in this workspace)")
    else:
        typer.echo("Accounts: local, in this workspace's database. Use `kaizen auth supabase <url> <key>` to share them through Supabase.")


@auth_app.command("supabase")
def auth_supabase(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Project URL, e.g. https://abcdefgh.supabase.co (Project Settings → API).")],
    key: Annotated[str, typer.Argument(help="The anon / publishable key. Never the service_role / secret key.")],
) -> None:
    """Check emails and passwords with a Supabase project. Only accounts move; all review data stays here.

    Run once per laptop. Restart `kaizen serve` afterwards.
    """
    from kaizen.review.auth import save_config

    try:
        url, _ = save_config(_ws(ctx).db, url, key, by="cli")
    except ValueError as e:
        raise typer.BadParameter(str(e))
    console.print(f"Accounts will be checked by Supabase at [bold]{url}[/bold]. Restart `kaizen serve` for it to take effect.")
    console.print("Accounts created before this in the workspace are not copied: each reviewer signs up once in Supabase.")


@auth_app.command("local")
def auth_local(ctx: typer.Context) -> None:
    """Go back to accounts stored in this workspace (works offline). Restart `kaizen serve` afterwards."""
    from kaizen.review.auth import clear_config

    clear_config(_ws(ctx).db, by="cli")
    console.print("Accounts are local to this workspace again. Restart `kaizen serve` for it to take effect.")


@users_app.command("list")
def users_list(ctx: typer.Context) -> None:
    """Reviewer accounts in this workspace."""
    from kaizen.review.sessions import UserStore

    _local_accounts_only(ctx)
    accounts = UserStore(_ws(ctx).db).list()
    for u in accounts:
        state = f"locked until {u.locked_until}" if u.locked_until else "active"
        typer.echo(f"{u.email:<34} created {u.created_at[:19]}  {state}")
    typer.echo(f"{len(accounts)} account(s)")


@users_app.command("reset")
def users_reset(
    ctx: typer.Context,
    email: Annotated[str, typer.Argument(help="The reviewer's BD email address.")],
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Clear an account so the reviewer can sign up again and choose a new password.

    This is the forgotten-password path. There is no reset email because the tool has no mail server;
    clearing the account instead of setting a password for them means nobody else ever learns it.
    Decisions already recorded keep the reviewer's address and are untouched.
    """
    from kaizen.review.sessions import UserStore

    _local_accounts_only(ctx)
    users = UserStore(_ws(ctx).db)
    address = email.strip().lower()
    if not users.exists(address):
        console.print(f"[yellow]No account for {address}.[/yellow] They can sign up directly.")
        raise typer.Exit(code=1)
    if not yes:
        typer.confirm(f"Clear the account for {address} so they can sign up again?", abort=True)
    users.delete(address, by="cli")
    console.print(f"Cleared [bold]{address}[/bold]. Ask them to sign up again and choose a new password.")


@runs_app.command("list")
def runs_list(ctx: typer.Context) -> None:
    """List runs recorded in the workspace."""
    for r in _ws(ctx).runs.list():
        s = r["summary"]
        typer.echo(f"{r['run_id']} {r['created_at'][:19]} {r['input_root']} | {s['skus']} SKUs, {s['rows']} rows, needs validation {s['needs_validation']} | terminology {r['terminology_version'][:12]} | {r['json_path']}")


@runs_app.command("relationships")
def runs_relationships(ctx: typer.Context, run_id: str) -> None:
    """Reconstruct the exact relationship versions a run used, even if they were edited or deleted since."""
    rows = _ws(ctx).repository.relationships_for_run(run_id)
    if not rows:
        typer.echo("no relationship usage recorded for this run")
        raise typer.Exit(code=1)
    for rr in rows:
        p = rr.relationship
        typer.echo(f"{p.id} v{p.version}: {p.canonical} = {' = '.join(p.aliases)} [{p.scope}] used in {rr.used_count} row(s)")


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (local by default; no external exposure)."),
    port: int = typer.Option(8765, "--port"),
    ui: Optional[Path] = typer.Option(None, "--ui", help="Folder with the built reviewer UI (default: bundled ui/dist if present)."),
) -> None:
    """Start the local API + reviewer UI (http://127.0.0.1:8765). Everything stays on this machine."""
    import uvicorn

    from kaizen.api.app import create_app

    ui_dir = ui or Path(__file__).resolve().parents[3] / "ui" / "dist"
    try:
        application = create_app(_ws(ctx), ui_dir if ui_dir.exists() else None)
    except ValueError as e:  # a half-set or unusable Supabase configuration
        raise typer.BadParameter(f"sign-in configuration: {e}. See `kaizen auth show`.")
    from kaizen.review.auth import accounts_for

    where = accounts_for(_ws(ctx).db).name  # already validated by create_app
    typer.echo(f"Kaizen Cross-Check API on http://{host}:{port}  (workspace {_ws(ctx).path}; accounts {where}; UI {'served' if ui_dir.exists() else 'not built — API only'})")
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def demo(
    ctx: typer.Context,
    out: Annotated[Optional[Path], typer.Option("--out", "-o")] = None,
    dataset: Annotated[Optional[Path], typer.Option("--dataset", help="Demo dataset folder (default: datasets/golden or a fresh build).")] = None,
) -> None:
    """Deterministic demo run on the golden dataset: run + eval + workbook, registered in the workspace."""
    from kaizen.api.app import _demo_dataset_path

    src = dataset or _demo_dataset_path()
    if src is None:
        src = build_golden(_ws(ctx).path / "demo-data")
    ws = _ws(ctx)
    r = run_folder(src, ws.repository.store(), Thresholds())
    out_dir = out if out is not None else ws.runs_dir / r.metadata.run_id
    path = save_run(r, out_dir / "run.json")
    ws.register_run(r, path)
    gt = src / "ground-truth.json"
    metrics = evaluate(r, load_ground_truth(gt)).to_dict() if gt.exists() else None
    if metrics:
        import json as _json

        (out_dir / "accuracy.json").write_text(_json.dumps(metrics, indent=2), encoding="utf-8")  # keeps the Accuracy sheet on later exports
    from kaizen.reporting.excel import export_with_review

    export_with_review(ws, r, out_dir / "report.xlsx", metrics=metrics)
    _print_summary(r)
    if metrics:
        typer.echo(f"Measured against ground truth: precision {metrics['overall']['precision']:.3f}, recall {metrics['overall']['recall']:.3f}, {metrics['scored_rows']} scored rows")
    typer.echo(f"Demo run {r.metadata.run_id} ready: {out_dir / 'report.xlsx'}. Start the UI with `kaizen serve`.")


@app.command()
def perf(
    skus: list[int] = typer.Option([8, 25, 50, 100], "--skus", help="Dataset sizes to measure (repeatable)."),
    out: Path = typer.Option(Path("out/perf"), "--out"),
) -> None:
    """Measure ingest / matching / report time (and peak memory) on synthetic datasets of N SKUs."""
    from kaizen.perf import run_series

    results = run_series(out, tuple(skus))
    typer.echo(f"{'SKUs':>5} {'docs':>5} {'rows':>7} {'ingest s':>9} {'match s':>8} {'report s':>9} {'total s':>8} {'s/SKU':>6} {'peak MB':>8}")
    for m in results:
        d = m.to_dict()
        typer.echo(f"{m.skus:>5} {m.documents:>5} {m.rows:>7} {m.ingest_seconds:>9.2f} {m.matching_seconds:>8.2f} {m.report_seconds:>9.2f} {m.total_seconds:>8.2f} {d['seconds_per_sku']:>6.2f} {str(m.peak_memory_mb):>8}")
    typer.echo(f"Wrote {out / 'perf-results.json'}")


@dataset_app.command("corrected")
def dataset_corrected(
    sku: Annotated[str, typer.Argument(help="Parent item of the SKU set to correct, e.g. 1295108FNS.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Destination folder (the SKU folder is written inside it).")] = Path("out/corrected"),
) -> None:
    """Write a corrected copy of one golden SKU set (seeded discrepancies removed) for rehearsing verify-and-close."""
    from kaizen.datasets.build import build_corrected

    try:
        root = build_corrected(out, sku)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo(f"Corrected copy of {sku} written under {root}. Run it with `kaizen run {root}` or the UI's 'Run a local folder path', then Verify & close.")


@dataset_app.command("build")
def dataset_build(out: Annotated[Path, typer.Option("--out", "-o", help="Destination folder.")] = Path("datasets/golden")) -> None:
    """Generate the synthetic golden dataset (documents, ground truth, SCENARIOS.md)."""
    root = build_golden(out)
    typer.echo(f"Built golden dataset in {root} ({sum(1 for p in root.iterdir() if p.is_dir() and p.name.startswith('sku-'))} SKUs, {sum(1 for _ in (root / 'pco').glob('*'))} PCOs)")


if __name__ == "__main__":
    app()
