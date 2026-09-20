// Thin typed client for the local Kaizen API (docs/api-contract.md). Relative URLs only, so the same bundle
// works from the Vite dev server (proxy) and when served by the backend at "/".
import type {
  ActionItem,
  ActionStatus,
  BusinessCase,
  BusinessParams,
  Classification,
  DecisionKind,
  DecisionResponse,
  DocumentItems,
  DocumentSummary,
  Health,
  ImportResult,
  MiningSuggestion,
  Relationship,
  RelationshipHistory,
  ResultsPage,
  ResultsQuery,
  Worklist,
  RunDiff,
  RoundTripResult,
  CurrentSession,
  ReviewSession,
  ReviewState,
  RowDetail,
  RunListItem,
  RunSummary,
  VerifyOutcome,
  ViewerParams,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function qs(params: Params): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, `Cannot reach the API (${path}). Is \`kaizen serve\` running on port 8765? ${(e as Error).message}`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

const enc = encodeURIComponent;

export interface DecisionBody {
  row_id: string;
  decision: DecisionKind;
  comment?: string;
  override_classification?: Classification;
}
export interface FinalizeBody {
  row_id: string;
  final_decision: DecisionKind;
  note?: string;
}
export interface FromRowBody {
  row_id: string;
  scope?: string;
  anchor?: boolean;
  canonical?: string;
  aliases?: string[];
  doc_types?: string[];
  notes?: string;
}
export interface RelationshipCreateBody {
  canonical: string;
  aliases: string[];
  scope: string;
  doc_types: string[];
  item_anchors: string[];
  provenance?: string;
  by: string;
  notes: string;
}
export interface RelationshipUpdateBody {
  canonical?: string;
  aliases?: string[];
  scope?: string;
  doc_types?: string[];
  item_anchors?: string[];
  notes?: string;
  by: string;
  note: string;
}

export const api = {
  health: () => request<Health>("/api/health"),

  // ---- runs
  listRuns: () => request<RunListItem[]>("/api/runs"),
  loadDemo: () => request<RunSummary>("/api/demo/load", { method: "POST" }),
  runFromPath: (path: string) => request<RunSummary>("/api/runs/from-path", json("POST", { path })),
  uploadRun: (files: File[]) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.webkitRelativePath || f.name);
    return request<RunSummary>("/api/runs/upload", { method: "POST", body: fd });
  },
  getRun: (runId: string) => request<RunSummary>(`/api/runs/${enc(runId)}`),

  // ---- results / evidence
  getResults: (runId: string, q: ResultsQuery) => request<ResultsPage>(`/api/runs/${enc(runId)}/results${qs(q as Params)}`),
  // The session decides which decisions are visible; `_v` only forces a refetch when it changes.
  getRow: (runId: string, rowId: string, _v: ViewerParams) =>
    request<RowDetail>(`/api/runs/${enc(runId)}/results/${enc(rowId)}`),
  pageImageUrl: (runId: string, docId: string, page: number, highlight?: string | null, dpi = 110) =>
    `/api/runs/${enc(runId)}/documents/${enc(docId)}/pages/${page}${qs({ highlight: highlight ?? undefined, dpi })}`,

  // ---- decisions
  postDecision: (runId: string, body: DecisionBody) => request<DecisionResponse>(`/api/runs/${enc(runId)}/decisions`, json("POST", body)),
  finalize: (runId: string, body: FinalizeBody) => request<{ row_id: string; state: ReviewState }>(`/api/runs/${enc(runId)}/finalize`, json("POST", body)),
  bulkAccept: (runId: string) => request<{ accepted: number }>(`/api/runs/${enc(runId)}/bulk-accept`, json("POST", {})),

  // ---- reviewer session: identity, slot and blind mode are held by the server
  // Sign-up creates the account; sign-in exchanges the password for a session. The password is only
  // ever sent, never stored or echoed here — the session lives in an HttpOnly cookie this code cannot read.
  currentSession: () => request<CurrentSession>("/api/sessions/current"),
  signUp: (email: string, password: string) => request<{ ok: boolean; email: string }>("/api/auth/signup", json("POST", { email, password })),
  signIn: (email: string, password: string, slot: 1 | 2, blind?: boolean) =>
    request<ReviewSession>("/api/auth/signin", json("POST", { email, password, slot, blind })),
  signOut: () => request<{ ended: boolean }>("/api/sessions/current", { method: "DELETE" }),
  relationshipFromRow: (runId: string, body: FromRowBody) => request<Relationship>(`/api/runs/${enc(runId)}/relationships/from-row`, json("POST", body)),

  // ---- documents
  getDocuments: (runId: string) => request<DocumentSummary[]>(`/api/runs/${enc(runId)}/documents`),
  getDocumentItems: (runId: string, docId: string) => request<DocumentItems>(`/api/runs/${enc(runId)}/documents/${enc(docId)}/items`),
  annotatedBomUrl: (runId: string, docId: string) => `/api/runs/${enc(runId)}/annotated-bom/${enc(docId)}`,

  // ---- mining
  certificateUrl: (runId: string, sku?: string) => `/api/runs/${enc(runId)}/certificate.pdf${qs({ sku })}`,
  getDiff: (runId: string, against: string) => request<RunDiff>(`/api/runs/${enc(runId)}/diff${qs({ against })}`),
  importDecisions: (runId: string, file: File, opts: { dryRun?: boolean; force?: boolean } = {}) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("dry_run", opts.dryRun ? "true" : "false");
    fd.append("force", opts.force ? "true" : "false");
    return request<RoundTripResult>(`/api/runs/${enc(runId)}/decisions/import`, { method: "POST", body: fd });
  },
  getWorklist: (runId: string) => request<Worklist>(`/api/runs/${enc(runId)}/terminology-worklist`),
  getMining: (runId: string, minSkus = 2) => request<MiningSuggestion[]>(`/api/runs/${enc(runId)}/mining${qs({ min_skus: minSkus })}`),
  approveMining: (runId: string, body: { a_key: string; b_key: string; by: string; scope?: string; anchor?: boolean; notes?: string }) =>
    request<Relationship>(`/api/runs/${enc(runId)}/mining/approve`, json("POST", body)),
  rejectMining: (runId: string, body: { a_key: string; b_key: string; by: string; note?: string }) =>
    request<{ rejected: string }>(`/api/runs/${enc(runId)}/mining/reject`, json("POST", body)),

  // ---- action items, verify & close, business case
  createActionItem: (runId: string, body: { row_id: string; reviewer: string; owner?: string }) =>
    request<ActionItem>(`/api/runs/${enc(runId)}/action-items`, json("POST", body)),
  listActionItems: (q: { status?: string; run_id?: string }) => request<ActionItem[]>(`/api/action-items${qs(q)}`),
  patchActionItem: (id: string, body: { status?: ActionStatus; owner?: string; by: string; note?: string }) =>
    request<ActionItem>(`/api/action-items/${enc(id)}`, json("PATCH", body)),
  verifyAndClose: (runId: string) => request<VerifyOutcome>(`/api/runs/${enc(runId)}/verify-and-close`, { method: "POST" }),
  businessCase: (runId: string, p: BusinessParams) => request<BusinessCase>(`/api/runs/${enc(runId)}/business-case${qs(p as Params)}`),

  // ---- exports
  exportUrl: (runId: string) => `/api/runs/${enc(runId)}/export.xlsx`,

  // ---- terminology
  terminology: {
    list: (q: { search?: string; scope?: string; all?: boolean }) => request<Relationship[]>(`/api/terminology${qs(q)}`),
    get: (id: string) => request<Relationship>(`/api/terminology/${enc(id)}`),
    create: (body: RelationshipCreateBody) => request<Relationship>("/api/terminology", json("POST", body)),
    update: (id: string, body: RelationshipUpdateBody) => request<Relationship>(`/api/terminology/${enc(id)}`, json("PUT", body)),
    activate: (id: string, by: string) => request<Relationship>(`/api/terminology/${enc(id)}/activate`, json("POST", { by })),
    deactivate: (id: string, by: string) => request<Relationship>(`/api/terminology/${enc(id)}/deactivate`, json("POST", { by })),
    history: (id: string) => request<RelationshipHistory[]>(`/api/terminology/${enc(id)}/history`),
    exportUrl: () => "/api/terminology/export.xlsx",
    importFile: (file: File, by: string) => {
      const fd = new FormData();
      fd.append("file", file, file.name);
      fd.append("by", by);
      return request<ImportResult>("/api/terminology/import", { method: "POST", body: fd });
    },
  },
};
