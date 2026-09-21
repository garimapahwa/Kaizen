"""FastAPI application: the reviewer UI's backend. Local only; no external calls."""

import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pymupdf
from fastapi import Body, Cookie, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Response as FastResponse
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from kaizen import __version__
from kaizen.models import Classification, Run, Thresholds
from kaizen.pipeline import load_run, run_folder, save_run
from kaizen.reporting.annotated_bom import write_annotated_bom
from kaizen.reporting.certificate import write_certificate, write_run_certificate
from kaizen.reporting.excel import ReviewBundle, export_with_review
from kaizen.reporting.excel_import import import_decisions
from kaizen.review.action_items import ActionItemStore
from kaizen.review.auth import AuthError, LocalAccounts, SupabaseAccounts, accounts_for
from kaizen.review.business import BusinessAssumptions, business_case
from kaizen.review.mining import approve_suggestion, mine_suggestions, reject_suggestion, terminology_worklist
from kaizen.review.rundiff import diff_runs
from kaizen.review.sessions import POLICY_REQUIRED, ReviewSession, SessionStore
from kaizen.review.store import ReviewStore
from kaizen.terminology.exchange import export_xlsx, import_csv, import_xlsx
from kaizen.workspace import Workspace

SESSION_COOKIE = "kaizen_session"
SIGN_IN_HINT = "Sign in first: POST /api/auth/signin with your BD email, password and slot."
BLIND_REFUSAL = "Not available while you are reviewing blind: it would reveal reviewer 1's decisions. End your blind session or ask reviewer 1."

# ---- sign-in -----------------------------------------------------------------------------------
ALLOWED_EMAIL_DOMAINS = {"bd.com"}
BAD_DOMAIN = "Only BD email addresses can sign in."


def _allowed_domains() -> set[str]:
    """Read at call time so a deployment can set KAIZEN_ALLOWED_DOMAINS without rebuilding the app."""
    raw = os.environ.get("KAIZEN_ALLOWED_DOMAINS") or ",".join(sorted(ALLOWED_EMAIL_DOMAINS))
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _bd_email(raw: str) -> str:
    """The address, normalised, or a 400. The domain must match a whole allowed domain: splitting on the
    last `@` and comparing for equality is what stops `someone@bd.com.evil.io` from passing as BD."""
    email = (raw or "").strip().lower()
    local, sep, domain = email.rpartition("@")
    if not sep or not local or domain not in _allowed_domains():
        raise HTTPException(400, BAD_DOMAIN)
    return email


SEVERITY_RANK = {"BLOCKER": 0, "MAJOR": 1, "MINOR": 2, "INFO": 3, None: 4}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _demo_dataset_path() -> Path | None:
    import os

    env = os.environ.get("KAIZEN_DEMO_DATA")
    candidates = [Path(env)] if env else []
    candidates.append(Path(__file__).resolve().parents[3] / "datasets" / "golden")
    candidates.append(Path.cwd() / "datasets" / "golden")
    return next((c for c in candidates if (c / "ground-truth.json").exists()), None)


class RunCache:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self._runs: dict[str, Run] = {}

    def get(self, run_id: str) -> Run:
        if run_id in self._runs:
            return self._runs[run_id]
        rec = self.ws.runs.get(run_id)
        if rec is None:
            raise HTTPException(404, f"run {run_id} not found")
        run = load_run(rec["json_path"])
        self._runs[run_id] = run
        return run

    def put(self, run: Run) -> Path:
        path = save_run(run, self.ws.runs_dir / run.metadata.run_id / "run.json")
        self.ws.register_run(run, path)
        self._runs[run.metadata.run_id] = run
        return path


def _summary(run: Run, review: ReviewStore, viewer_slot: int = 1, blind: bool = False) -> dict[str, Any]:
    from kaizen.review.business import reviewable

    rev = reviewable(run)
    counts = {c.value: sum(1 for r in rev if r.classification is c) for c in Classification}
    needs = sum(1 for r in rev if r.requires_validation)
    header_needs = sum(1 for r in run.results if r.role in ("header", "reference", "coverage") and r.requires_validation)
    return {
        "run_id": run.metadata.run_id, "timestamp": run.metadata.timestamp.isoformat(), "input_root": run.metadata.input_root, "tool_version": run.metadata.tool_version,
        "skus": len(run.groups), "documents": len(run.documents), "documents_by_type": {t: sum(1 for d in run.documents if d.doc_type.value == t) for t in ("BOM", "LABEL", "DRAWING", "PCO")},
        "unrecognised_files": [i.path for i in run.metadata.inputs if i.doc_type is None], "rows": len(run.results), "reviewable_rows": len(rev), "exempt_rows": sum(1 for r in run.results if r.role == "exempt"), "header_rows_needing_validation": header_needs,
        "counts": counts, "needs_validation": needs, "auto_cleared": len(rev) - needs,
        "per_check": {ct: sum(1 for r in run.results if r.check.value == ct) for ct in ("BOM_LABEL", "BOM_DRAWING", "LABEL_DRAWING", "PCO_BOM", "LABEL_REVISION")},
        "blockers": sum(1 for r in run.results for d in r.discrepancies if d.severity.value == "BLOCKER"),
        "coverage": [c.model_dump() for c in run.coverage], "groups": [g.model_dump() for g in run.groups], "warnings": run.warnings,
        "parser_warnings": [{"document": Path(d.path).name, "doc_id": d.id, "warnings": d.warnings} for d in run.documents if d.warnings],
        "low_confidence_rows": sum(1 for r in run.results for d in r.discrepancies if d.type.value == "LOW_EXTRACTION_CONFIDENCE"),
        "state_counts": review.state_counts(run.metadata.run_id, run.results, viewer_slot, blind), "terminology_version": run.metadata.terminology_version, "terminology_count": run.metadata.terminology_count,
        "relationships_used": run.relationships_used, "capabilities": run.metadata.capabilities, "thresholds": run.metadata.thresholds.model_dump(),
        "inputs": [i.model_dump() for i in run.metadata.inputs],
    }


def _evidence(item) -> dict[str, Any] | None:
    if item is None:
        return None
    ev = item.evidence
    return {"doc_id": item.doc_id, "doc_type": item.doc_type.value, "file": ev.file, "file_name": Path(ev.file).name, "sha256": ev.file_sha256, "page": ev.page, "bbox": ev.bbox.model_dump() if ev.bbox else None, "locator": ev.locator, "raw_text": ev.raw_text, "sheet": ev.sheet,
            "item_number": item.item_number, "description": item.description, "quantity": str(item.quantity) if item.quantity is not None else None, "uom": item.uom, "oper_seq": item.oper_seq, "category": item.category.value, "category_reason": item.category_reason, "confidence": item.extraction_confidence, "attributes": item.attributes, "sub_quantity": item.sub_quantity.model_dump() if item.sub_quantity else None}


def _queue_sort_key(r):
    sev = r.severity.value if r.severity else None
    types = {d.type.value for d in r.discrepancies}
    return (SEVERITY_RANK[sev], 0 if "AMBIGUOUS_MATCH" in types else 1, 0 if r.classification is Classification.POTENTIAL else 1, 0 if "LOW_EXTRACTION_CONFIDENCE" in types else 1, r.sku, r.row_id)


def create_app(workspace: Workspace | None = None, ui_dir: Path | None = None, accounts: LocalAccounts | SupabaseAccounts | None = None) -> FastAPI:
    ws = workspace or Workspace.resolve(None)
    app = FastAPI(title="Kaizen Cross-Check", version=__version__)
    cache = RunCache(ws)
    review = ReviewStore(ws.db)
    items = ActionItemStore(ws.db)
    sessions = SessionStore(ws.db)
    # Where email + password are checked: this workspace, or Supabase (see kaizen.review.auth). Only the
    # credentials move; sessions, slots, blind mode and decisions always stay in the workspace.
    accounts = accounts or accounts_for(ws.db)

    # ---- reviewer sessions --------------------------------------------------------------------
    # Identity, slot and blind mode are server-side. The token lives in an HttpOnly cookie so page
    # scripts cannot read or forge it, and `viewer`/`blind` query parameters are ignored whenever a
    # session is present.

    def _open_session(kaizen_session: str | None = Cookie(default=None)) -> ReviewSession | None:
        return sessions.resolve(kaizen_session)

    def _identified(session: ReviewSession | None = Depends(_open_session)) -> ReviewSession | None:
        if session is None and sessions.policy() == POLICY_REQUIRED:
            raise HTTPException(401, SIGN_IN_HINT)
        return session

    def _view_of(session: ReviewSession | None, viewer: int = 1, blind: bool = False) -> tuple[int, bool]:
        """The slot and blind flag actually used. A session always wins over the query string."""
        if session is not None:
            return session.slot, session.blind
        return (viewer if viewer in (1, 2) else 1), bool(blind)

    def _actor(session: ReviewSession | None, fallback: str) -> str:
        return session.reviewer if session is not None else (fallback or "reviewer")

    def _refuse_if_blind(session: ReviewSession | None) -> None:
        if session is not None and session.blind:
            raise HTTPException(403, BLIND_REFUSAL)

    def run_path(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise HTTPException(400, f"folder not found: {path}")
        run = run_folder(path.resolve(), ws.repository.store(), Thresholds())
        cache.put(run)
        return _summary(run, review)

    # ---- sign-up and sign-in --------------------------------------------------------------------
    # A session can only be opened by someone holding an account on a BD address. Only /api/auth/signin
    # reaches SessionStore.open, so there is one door.

    def _start_session(response: FastResponse, reviewer: str, payload: dict) -> dict:
        try:
            s = sessions.open(reviewer, int(payload.get("slot", 1)), payload.get("blind"))
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        response.set_cookie(SESSION_COOKIE, s.token, httponly=True, samesite="lax", path="/")
        return s.to_dict(sessions.policy())

    @app.post("/api/auth/signup")
    def signup(payload: dict = Body(...)):
        """Create an account for a BD address. Open registration: the domain is a format check, not proof
        of employment — see docs/security-review.md."""
        email = _bd_email(payload.get("email", ""))
        try:
            accounts.sign_up(email, str(payload.get("password", "")))
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        return {"ok": True, "email": email}

    @app.post("/api/auth/signin")
    def signin(response: FastResponse, payload: dict = Body(...)):
        """Exchange an email and password for a review session, which carries the slot and blind flag."""
        email = _bd_email(payload.get("email", ""))
        try:
            accounts.sign_in(email, str(payload.get("password", "")))
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        return _start_session(response, email, payload)

    # ---- runs ---------------------------------------------------------------------------------
    @app.post("/api/sessions")
    def open_session():
        """Kept only so an old cached bundle gets an explanation rather than a 404. There is no bypass."""
        raise HTTPException(403, "Use /api/auth/signin")

    @app.get("/api/sessions/current")
    def current_session(session: ReviewSession | None = Depends(_open_session)):
        return {"session": session.to_dict(sessions.policy()) if session else None, "blind_review_policy": sessions.policy(), "accounts": accounts.name}

    @app.delete("/api/sessions/current")
    def end_session(response: FastResponse, kaizen_session: str | None = Cookie(default=None)):
        ended = sessions.end(kaizen_session)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ended": ended}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "workspace": str(ws.path)}

    @app.get("/api/runs")
    def list_runs():
        return ws.runs.list()

    @app.post("/api/runs/from-path")
    def run_from_path(payload: dict = Body(...)):
        return run_path(Path(payload["path"]))

    @app.post("/api/runs/upload")
    async def run_upload(files: list[UploadFile] = File(...)):
        target = ws.path / "uploads" / uuid.uuid4().hex[:12]
        for f in files:
            rel = Path(f.filename or "file")
            if rel.is_absolute() or ".." in rel.parts:
                raise HTTPException(400, f"invalid upload path {f.filename}")
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(await f.read())
        return run_path(target)

    @app.post("/api/demo/load")
    def demo_load():
        src = _demo_dataset_path()
        if src is None:
            from kaizen.datasets.build import build_golden

            src = build_golden(ws.path / "demo-data")
        return run_path(src)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, session: ReviewSession | None = Depends(_identified)):
        slot, blind = _view_of(session)
        return _summary(cache.get(run_id), review)

    @app.get("/api/runs/{run_id}/results")
    def get_results(run_id: str, check: str | None = None, sku: str | None = None, classification: str | None = None, severity: str | None = None, discrepancy: str | None = None, needs_validation: bool | None = None, state: str | None = None, role: str | None = None, viewer: int = 1, blind: bool = False, limit: int = 500, offset: int = 0, search: str | None = None, session: ReviewSession | None = Depends(_identified)):
        slot, blind = _view_of(session, viewer, blind)
        run = cache.get(run_id)
        rows = run.results
        if check:
            rows = [r for r in rows if r.check.value == check]
        if sku:
            rows = [r for r in rows if r.sku == sku]
        if classification:
            rows = [r for r in rows if r.classification.value == classification]
        if severity:
            rows = [r for r in rows if (r.severity.value if r.severity else None) == severity]
        if discrepancy:
            rows = [r for r in rows if any(d.type.value == discrepancy for d in r.discrepancies)]
        if needs_validation is not None:
            rows = [r for r in rows if r.requires_validation == needs_validation]
        if role:
            rows = [r for r in rows if r.role == role]
        if search:
            s = search.lower()
            rows = [r for r in rows if s in (r.source_a.description if r.source_a else "").lower() or s in (r.source_b.description if r.source_b else "").lower() or s in (r.source_a.item_number or "" if r.source_a else "").lower() or s in r.explanation.lower()]
        rows = sorted(rows, key=_queue_sort_key)
        merged = review.rows_for_viewer(run_id, rows, viewer_slot=slot, blind=blind)
        if state:
            merged = [m for m in merged if m["state"] == state]
        total = len(merged)
        page = merged[offset : offset + limit]
        by_id = {r.row_id: r for r in rows}
        for m in page:
            r = by_id[m["row_id"]]
            m["a"] = {"item_number": r.source_a.item_number, "description": r.source_a.description, "quantity": str(r.source_a.quantity) if r.source_a.quantity is not None else None, "page": r.source_a.evidence.page, "locator": r.source_a.evidence.locator, "file_name": Path(r.source_a.evidence.file).name} if r.source_a else None
            m["b"] = {"item_number": r.source_b.item_number, "description": r.source_b.description, "quantity": str(r.source_b.quantity) if r.source_b.quantity is not None else None, "page": r.source_b.evidence.page, "locator": r.source_b.evidence.locator, "file_name": Path(r.source_b.evidence.file).name} if r.source_b else None
            m["discrepancies"] = [d.model_dump() for d in r.discrepancies]
            m["action_items"] = [a.id for a in items.for_row(run_id, r.row_id)]
        return {"total": total, "offset": offset, "limit": limit, "rows": page, "viewer": {"slot": slot, "blind": blind, "reviewer": session.reviewer if session else None}}

    def _find(run: Run, row_id: str):
        r = next((r for r in run.results if r.row_id == row_id), None)
        if r is None:
            raise HTTPException(404, f"row {row_id} not found")
        return r

    @app.get("/api/runs/{run_id}/results/{row_id}")
    def get_row(run_id: str, row_id: str, viewer: int = 1, blind: bool = False, session: ReviewSession | None = Depends(_identified)):
        slot, blind = _view_of(session, viewer, blind)
        run = cache.get(run_id)
        r = _find(run, row_id)
        merged = review.rows_for_viewer(run_id, [r], viewer_slot=slot, blind=blind)[0]
        hidden = ReviewStore.is_blind_hidden(review.decisions(run_id, row_id), slot, blind)
        if session is not None:
            review.mark_opened(run_id, row_id, session.slot)  # starts the effort clock for this reviewer
        return {"result": r.model_dump(mode="json"), "evidence": {"a": _evidence(r.source_a), "b": _evidence(r.source_b)}, "decisions": {str(k): v for k, v in merged["decisions"].items()}, "state": merged["state"], "final": merged["final"], "effective_classification": merged["effective_classification"], "history": [{"event": "engine", "detail": "engine recommendation recorded with the run"}] if hidden else review.history(run_id, row_id), "viewer": {"slot": slot, "blind": blind, "reviewer": session.reviewer if session else None}, "action_items": [asdict(a) for a in items.for_row(run_id, row_id)]}

    @app.get("/api/runs/{run_id}/documents")
    def get_documents(run_id: str):
        run = cache.get(run_id)
        return [{"id": d.id, "doc_type": d.doc_type.value, "file": d.path, "file_name": Path(d.path).name, "sku": d.sku, "sha256": d.sha256, "parser": d.parser_name, "parser_version": d.parser_version, "items": len(d.items), "warnings": d.warnings, "header": d.header, "pages": _page_count(d.path)} for d in run.documents]

    def _doc(run: Run, doc_id: str):
        d = next((d for d in run.documents if d.id == doc_id), None)
        if d is None:
            raise HTTPException(404, f"document {doc_id} not found")
        return d

    @app.get("/api/runs/{run_id}/documents/{doc_id}/items")
    def get_document_items(run_id: str, doc_id: str):
        d = _doc(cache.get(run_id), doc_id)
        return {"doc_id": d.id, "doc_type": d.doc_type.value, "file_name": Path(d.path).name, "header": d.header, "warnings": d.warnings, "pages": _page_count(d.path), "items": [_evidence(i) | {"id": i.id, "is_active": i.is_active} for i in d.items]}

    @app.get("/api/runs/{run_id}/documents/{doc_id}/pages/{page_no}")
    def get_page_image(run_id: str, doc_id: str, page_no: int, highlight: str | None = None, dpi: int = 110):
        run = cache.get(run_id)
        d = _doc(run, doc_id)
        if not d.path.lower().endswith(".pdf"):
            raise HTTPException(404, "document has no page images (spreadsheet source)")
        pdf = pymupdf.open(d.path)
        if page_no < 1 or page_no > len(pdf):
            raise HTTPException(404, "page out of range")
        page = pdf[page_no - 1]
        if highlight:
            r = _find(run, highlight)
            for item in (r.source_a, r.source_b):
                if item is not None and item.doc_id == doc_id and item.evidence.bbox is not None and item.evidence.page == page_no:
                    b = item.evidence.bbox
                    shape = page.new_shape()
                    shape.draw_rect(pymupdf.Rect(b.x0 - 2, b.y0 - 2, b.x1 + 2, b.y1 + 2))
                    shape.finish(color=(0.9, 0.1, 0.1), fill=(1, 0.85, 0.2), fill_opacity=0.25, width=1.2)
                    shape.commit()
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        pdf.close()
        return Response(content=png, media_type="image/png")

    # ---- review -------------------------------------------------------------------------------
    @app.post("/api/runs/{run_id}/decisions")
    def post_decision(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        run = cache.get(run_id)
        _find(run, payload["row_id"])
        slot, blind = _view_of(session, int(payload.get("slot", 1)), bool(payload.get("blind", False)))
        if session is not None and "slot" in payload and int(payload["slot"]) != session.slot:
            raise HTTPException(400, f"this session reviews in slot {session.slot}; open a new session to review in slot {payload['slot']}")
        try:
            review.decide(run_id, payload["row_id"], slot, _actor(session, payload.get("reviewer", "")), payload["decision"], payload.get("comment", ""), payload.get("override_classification"), blind)
        except ValueError as e:
            raise HTTPException(400, str(e))
        merged = review.rows_for_viewer(run_id, [_find(run, payload["row_id"])], viewer_slot=slot, blind=blind)[0]
        return {"row_id": payload["row_id"], "state": merged["state"], "decisions": {str(k): v for k, v in merged["decisions"].items()}, "effective_classification": merged["effective_classification"]}

    @app.post("/api/runs/{run_id}/finalize")
    def post_final(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        cache.get(run_id)
        slot, blind = _view_of(session)
        if ReviewStore.is_blind_hidden(review.decisions(run_id, payload["row_id"]), slot, blind):
            raise HTTPException(403, "Record your own decision on this row before closing it: you cannot see reviewer 1's yet.")
        try:
            review.finalize(run_id, payload["row_id"], payload["final_decision"], _actor(session, payload.get("by", "")), payload.get("note", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"row_id": payload["row_id"], "state": review.row_state(run_id, payload["row_id"]).state}

    @app.post("/api/runs/{run_id}/bulk-accept")
    def post_bulk(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        run = cache.get(run_id)
        slot, _ = _view_of(session, int(payload.get("slot", 1)))
        return {"accepted": review.bulk_accept_clean(run_id, run.results, slot, _actor(session, payload.get("reviewer", "")))}

    @app.post("/api/runs/{run_id}/relationships/from-row")
    def relationship_from_row(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        run = cache.get(run_id)
        r = _find(run, payload["row_id"])
        if r.source_a is None or r.source_b is None:
            raise HTTPException(400, "row has no pair to save as a relationship")
        canonical = payload.get("canonical") or r.source_b.description
        aliases = payload.get("aliases") or [r.source_a.description]
        anchors = [r.source_a.item_number] if payload.get("anchor") and r.source_a.item_number else []
        rel = ws.repository.create(canonical=canonical, aliases=aliases, scope=payload.get("scope", "global"), doc_types=payload.get("doc_types", []), item_anchors=anchors, provenance="learned", created_by=_actor(session, payload.get("by", "")), notes=payload.get("notes") or f"Saved from run {run_id} row {r.row_id} ({r.check.value}, {r.sku}); engine said {r.classification.value}")
        ws.db.audit(_actor(session, payload.get("by", "")), "relationship.learned", f"{rel.id} from {run_id} {r.row_id}")
        ws.db.conn.commit()
        return rel.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/terminology-worklist")
    def get_worklist(run_id: str, session: ReviewSession | None = Depends(_identified)):
        """Unconfirmed pairings ranked by the rows they would auto-clear once approved (human approval still required)."""
        run = cache.get(run_id)
        return terminology_worklist(run, review, ws.repository).to_dict()

    @app.get("/api/runs/{run_id}/mining")
    def get_mining(run_id: str, min_skus: int = 2):
        run = cache.get(run_id)
        return [asdict(s) | {"evidence": s.evidence, "pair_key": s.pair_key} for s in mine_suggestions(run, review, ws.repository, min_skus=min_skus)]

    def _suggestion(run_id: str, a_key: str, b_key: str):
        run = cache.get(run_id)
        s = next((s for s in mine_suggestions(run, review, ws.repository, min_skus=1) if s.a_key == a_key and s.b_key == b_key), None)
        if s is None:
            raise HTTPException(404, "suggestion not found (already approved or rejected?)")
        return s

    @app.post("/api/runs/{run_id}/mining/approve")
    def approve(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        s = _suggestion(run_id, payload["a_key"], payload["b_key"])
        return approve_suggestion(ws.repository, s, _actor(session, payload.get("by", "")), payload.get("scope", "global"), bool(payload.get("anchor", False)), payload.get("notes", "")).model_dump(mode="json")

    @app.post("/api/runs/{run_id}/mining/reject")
    def reject(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        s = _suggestion(run_id, payload["a_key"], payload["b_key"])
        reject_suggestion(ws.db, s, _actor(session, payload.get("by", "")), payload.get("note", ""))
        return {"rejected": s.pair_key}

    # ---- action items ------------------------------------------------------------------------
    @app.post("/api/runs/{run_id}/action-items")
    def create_action_item(run_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        run = cache.get(run_id)
        r = _find(run, payload["row_id"])
        try:
            return asdict(items.create_from_result(run, r, _actor(session, payload.get("reviewer", "")), payload.get("owner", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/action-items")
    def list_action_items(status: str | None = None, run_id: str | None = None):
        out = items.for_run(run_id) if run_id else items.list(status)
        return [asdict(a) for a in out]

    @app.patch("/api/action-items/{ai_id}")
    def patch_action_item(ai_id: str, payload: dict = Body(...), session: ReviewSession | None = Depends(_identified)):
        try:
            return asdict(items.update(ai_id, _actor(session, payload.get("by", "")), payload.get("status"), payload.get("owner"), payload.get("note", "")))
        except KeyError:
            raise HTTPException(404, "action item not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/runs/{run_id}/verify-and-close")
    def verify_and_close(run_id: str):
        return asdict(items.verify_and_close(cache.get(run_id)))

    @app.get("/api/runs/{run_id}/business-case")
    def get_business_case(run_id: str, baseline_minutes_per_sku: float = 60.0, hourly_rate: float = 37.5, skus_per_project: int = 100, projects_per_year: int = 20, reviewers: int = 2, minutes_per_validation_row: float = 1.5, minutes_per_cleared_row: float = 0.1, target_reduction_pct: float = 50.0):
        return business_case(cache.get(run_id), BusinessAssumptions(baseline_minutes_per_sku, hourly_rate, skus_per_project, projects_per_year, reviewers, minutes_per_validation_row, minutes_per_cleared_row, target_reduction_pct), timing=review.timing(run_id)).to_dict()

    # ---- exports -----------------------------------------------------------------------------
    @app.get("/api/runs/{run_id}/export.xlsx")
    def export_run(run_id: str, session: ReviewSession | None = Depends(_identified)):
        _refuse_if_blind(session)
        run = cache.get(run_id)
        path = export_with_review(ws, run, ws.runs_dir / run_id / "report.xlsx")
        return FileResponse(path, media_type=XLSX, filename=f"kaizen-{run_id}.xlsx")

    @app.get("/api/runs/{run_id}/annotated-bom/{doc_id}")
    def annotated_bom(run_id: str, doc_id: str, session: ReviewSession | None = Depends(_identified)):
        _refuse_if_blind(session)
        run = cache.get(run_id)
        d = _doc(run, doc_id)
        decisions = review.all_decisions(run_id)
        names = sorted({dec.reviewer for slots in decisions.values() for dec in slots.values()})
        states: dict[str, str] = {}
        for row_id, slots in decisions.items():
            last = slots.get(2) or slots.get(1)
            if last and last.decision == "ACCEPT":
                states[row_id] = "clear"
            elif last and last.decision == "CONFIRM_DISCREPANCY":
                states[row_id] = "discrepancy"
        out = write_annotated_bom(run, d, ws.runs_dir / run_id / f"annotated-{re.sub(r'[^A-Za-z0-9]+', '_', d.sku or doc_id)}.pdf", checked_by=names or None, review_states=states)
        return FileResponse(out.path, media_type="application/pdf", filename=out.path.name, headers={"X-Kaizen-Fallback": "true" if out.fallback else "false", "X-Kaizen-Marks": str(out.marks)})

    # ---- certificate, run diff, optional Excel round-trip ---------------------------------------
    def _bundle(run: Run) -> ReviewBundle:
        rid = run.metadata.run_id
        decisions, finals = review.all_decisions(rid), review.finals(rid)
        states = {r.row_id: ReviewStore.state_of(decisions.get(r.row_id, {}), finals.get(r.row_id)) for r in run.results}
        return ReviewBundle(decisions=decisions, finals=finals, states=states, action_items=items.for_run(rid))

    @app.get("/api/runs/{run_id}/certificate.pdf")
    def certificate(run_id: str, sku: str | None = None, session: ReviewSession | None = Depends(_identified)):
        """One page per SKU (or one SKU): run id, file hashes, counts, named reviewers, open action items."""
        _refuse_if_blind(session)  # it lists both reviewers' decisions
        run = cache.get(run_id)
        target = ws.runs_dir / run_id / (f"certificate-{re.sub(r'[^A-Za-z0-9]+', '_', sku)}.pdf" if sku else "certificate.pdf")
        try:
            out = write_certificate(run, sku, target, _bundle(run)) if sku else write_run_certificate(run, target, _bundle(run))
        except ValueError as e:
            raise HTTPException(404, str(e))
        return FileResponse(out, media_type="application/pdf", filename=out.name)

    @app.get("/api/runs/{run_id}/diff")
    def run_diff(run_id: str, against: str, session: ReviewSession | None = Depends(_identified)):
        """What changed from run `against` (before) to `run_id` (after): resolved, new, still open."""
        after, before = cache.get(run_id), cache.get(against)
        return diff_runs(before, after).to_dict()

    @app.post("/api/runs/{run_id}/decisions/import")
    def import_from_excel(run_id: str, file: UploadFile = File(...), dry_run: bool = Form(False), force: bool = Form(False), session: ReviewSession | None = Depends(_identified)):
        """Optional: apply the signed-in reviewer's decisions from an exported workbook. Slot and name come from the session."""
        if session is None:
            raise HTTPException(401, SIGN_IN_HINT)
        run = cache.get(run_id)
        folder = ws.runs_dir / run_id / "imports"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{uuid.uuid4().hex[:8]}-{re.sub(r'[^A-Za-z0-9._-]+', '_', Path(file.filename or 'decisions.xlsx').name)}"
        dest.write_bytes(file.file.read())
        try:
            return import_decisions(ws, run, dest, session.slot, session.reviewer, dry_run=dry_run, force=force).to_dict()
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---- terminology --------------------------------------------------------------------------
    def _rel(r, usage):
        return r.model_dump(mode="json") | {"usage": usage.get(r.id, 0)}

    @app.get("/api/terminology")
    def list_terminology(search: str | None = None, scope: str | None = None, all: bool = False):
        usage = ws.repository.usage_counts()
        return [_rel(r, usage) for r in ws.repository.list(active_only=not all, scope=scope, search=search)]

    @app.get("/api/terminology/export.xlsx")
    def terminology_export():
        path = export_xlsx(ws.repository, ws.path / "exports" / "relationships.xlsx")
        return FileResponse(path, media_type=XLSX, filename="relationships.xlsx")

    @app.post("/api/terminology/import")
    async def terminology_import(file: UploadFile = File(...), by: str = Form("import")):
        data = await file.read()
        tmp = ws.path / "uploads" / f"import-{uuid.uuid4().hex[:8]}-{Path(file.filename or 'rels.xlsx').name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        result = import_csv(ws.repository, tmp, by) if tmp.suffix.lower() == ".csv" else import_xlsx(ws.repository, tmp, by)
        return {"summary": result.summary(), "created": result.created, "updated": result.updated, "unchanged": result.unchanged, "errors": result.errors}

    @app.post("/api/terminology")
    def create_relationship(payload: dict = Body(...)):
        try:
            rel = ws.repository.create(canonical=payload["canonical"], aliases=payload.get("aliases", []), scope=payload.get("scope", "global"), doc_types=payload.get("doc_types", []), item_anchors=payload.get("item_anchors", []), provenance=payload.get("provenance", "manual"), created_by=payload.get("by", "ui"), notes=payload.get("notes", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return rel.model_dump(mode="json")

    @app.get("/api/terminology/{rel_id}")
    def get_relationship(rel_id: str):
        rel = ws.repository.get(rel_id)
        if rel is None:
            raise HTTPException(404, "relationship not found")
        return _rel(rel, ws.repository.usage_counts())

    @app.put("/api/terminology/{rel_id}")
    def update_relationship(rel_id: str, payload: dict = Body(...)):
        fields = {k: payload[k] for k in ("canonical", "aliases", "scope", "doc_types", "item_anchors", "notes") if k in payload}
        try:
            return ws.repository.update(rel_id, payload.get("by", "ui"), payload.get("note", ""), **fields).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "relationship not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/terminology/{rel_id}/deactivate")
    def deactivate_relationship(rel_id: str, payload: dict = Body(default={})):
        return ws.repository.deactivate(rel_id, payload.get("by", "ui"), payload.get("note", "")).model_dump(mode="json")

    @app.post("/api/terminology/{rel_id}/activate")
    def activate_relationship(rel_id: str, payload: dict = Body(default={})):
        return ws.repository.activate(rel_id, payload.get("by", "ui"), payload.get("note", "")).model_dump(mode="json")

    @app.delete("/api/terminology/{rel_id}")
    def delete_relationship(rel_id: str, by: str = "ui", note: str = ""):
        try:
            ws.repository.delete(rel_id, by, note)
        except KeyError:
            raise HTTPException(404, "relationship not found")
        return {"deleted": rel_id}

    @app.get("/api/terminology/{rel_id}/history")
    def relationship_history(rel_id: str):
        return [{"version": h.version, "change_type": h.change_type, "changed_by": h.changed_by, "changed_at": h.changed_at.isoformat(), "change_note": h.change_note, "payload": h.payload.model_dump(mode="json")} for h in ws.repository.history(rel_id)]

    @app.get("/api/audit")
    def audit(limit: int = 200, session: ReviewSession | None = Depends(_identified)):
        _refuse_if_blind(session)
        return [dict(r) for r in ws.db.conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))]

    if ui_dir and ui_dir.exists():

        @app.middleware("http")
        async def no_cache_index(request, call_next):
            """The bundle's asset names are hashed, but index.html is not: a stale cached index would keep
            pointing at an old bundle after a rebuild. Ask the browser to revalidate it every time."""
            response = await call_next(request)
            if request.url.path in ("/", "/index.html"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    return app


def _page_count(path: str) -> int:
    if not path.lower().endswith(".pdf"):
        return 0
    try:
        pdf = pymupdf.open(path)
        n = len(pdf)
        pdf.close()
        return n
    except Exception:
        return 0
