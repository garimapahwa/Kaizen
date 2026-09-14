import { EyeSlash } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { displayName, fmtDate } from "../lib/format";
import { useHotkeys } from "../lib/hotkeys";
import { useReviewer } from "../lib/reviewer";
import { useToast } from "../lib/toast";
import { errorMessage } from "../lib/useAsync";
import { CLASSIFICATIONS, DECISION_KINDS, DECISION_LABELS, type Classification, type Decision, type DecisionKind, type HistoryEvent, type RowDetail, type Slot } from "../types";
import { Badge, ClassificationBadge, DecisionBadge, StateBadge } from "./Badges";
import { ErrorBox, Notice } from "./Feedback";
import { Button, Card, CardHead, Field, Kbd } from "./ui";

interface Props {
  runId: string;
  rowId: string;
  detail: RowDetail;
  onChanged: () => void;
}

const KEY: Record<DecisionKind, string> = { ACCEPT: "a", OVERRIDE: "o", CONFIRM_DISCREPANCY: "c", NEEDS_MORE_INFORMATION: "n" };

/** Reviewer decisions are stored beside the engine recommendation, never over it. */
export function DecisionPanel({ runId, rowId, detail, onChanged }: Props) {
  const { session, viewerParams, name } = useReviewer();
  const toast = useToast();
  const slot: 1 | 2 = session?.slot ?? 1;
  const slotKey = String(slot) as Slot;
  const mine = detail.decisions[slotKey];
  const isFinal = detail.state === "FINALIZED";

  const [decision, setDecision] = useState<DecisionKind | null>(mine?.decision ?? null);
  const [override, setOverride] = useState<Classification>(mine?.override_classification ?? "EQUIVALENT");
  const [comment, setComment] = useState(mine?.comment ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [finalDecision, setFinalDecision] = useState<DecisionKind>("ACCEPT");
  const [finalNote, setFinalNote] = useState("");
  const [finalBusy, setFinalBusy] = useState(false);
  const [finalErr, setFinalErr] = useState<string | null>(null);

  useEffect(() => {
    setDecision(mine?.decision ?? null);
    setOverride(mine?.override_classification ?? "EQUIVALENT");
    setComment(mine?.comment ?? "");
    setErr(null);
    setFinalErr(null);
    const d2 = detail.decisions["2"];
    const d1 = detail.decisions["1"];
    setFinalDecision(detail.final?.final_decision ?? d2?.decision ?? d1?.decision ?? "ACCEPT");
    setFinalNote(detail.final?.note ?? "");
  }, [rowId, slot, mine?.decision, mine?.override_classification, mine?.comment, detail.decisions, detail.final]);

  const submit = async () => {
    if (!decision || !name) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.postDecision(runId, {
        row_id: rowId,
        decision,
        comment,
        override_classification: decision === "OVERRIDE" ? override : undefined,
      });
      toast({ tone: "ok", title: `Recorded as reviewer ${slot}`, description: `Row state is now ${res.state.replace(/_/g, " ").toLowerCase()}.` });
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  // a / c / o / n choose; Enter submits the chosen decision (never fires while typing in the comment box).
  useHotkeys(
    {
      a: () => !isFinal && setDecision("ACCEPT"),
      c: () => !isFinal && setDecision("CONFIRM_DISCREPANCY"),
      o: () => !isFinal && setDecision("OVERRIDE"),
      n: () => !isFinal && setDecision("NEEDS_MORE_INFORMATION"),
      Enter: () => {
        if (decision && name && !busy && !isFinal) void submit();
      },
    },
    [decision, name, busy, isFinal, comment, override, rowId],
  );

  const finalize = async () => {
    if (!name) return;
    setFinalBusy(true);
    setFinalErr(null);
    try {
      await api.finalize(runId, { row_id: rowId, final_decision: finalDecision, note: finalNote });
      toast({ tone: "ok", title: "Row finalized", description: "The two-reviewer loop on this row is closed." });
      onChanged();
    } catch (e) {
      setFinalErr(errorMessage(e));
    } finally {
      setFinalBusy(false);
    }
  };

  const blindHidden = viewerParams.blind && detail.decisions["2"] === null;

  return (
    <Card>
      <CardHead
        title="Reviewer decision"
        description={
          <span className="flex items-center gap-1.5">
            Deciding as <b className="text-ink" title={name}>{displayName(name)}</b> · reviewer {slot}
            {viewerParams.blind && (
              <Badge tone="info" dot={false}>
                <EyeSlash size={12} /> Blind
              </Badge>
            )}
          </span>
        }
        actions={<StateBadge value={detail.state} />}
      />
      {detail.state === "DISAGREEMENT" && (
        <div className="px-5 pt-4">
          <Notice kind="bad">
            <b>Reviewer 1 and reviewer 2 decided differently.</b> Discuss, then record the final decision below.
          </Notice>
        </div>
      )}
      <div className="p-5 grid gap-6 lg:grid-cols-2">
        {/* ---- your decision */}
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            {DECISION_KINDS.map((k) => {
              const on = decision === k;
              return (
                <button
                  key={k}
                  type="button"
                  disabled={isFinal}
                  onClick={() => setDecision(k)}
                  className={`flex items-center justify-between gap-2 h-11 px-3 rounded-md border text-sm font-medium text-left transition-[background-color,border-color,color,transform] duration-150 ease-out active:scale-[0.97] disabled:opacity-50 disabled:cursor-not-allowed ${
                    on ? "border-accent-500 bg-accent-50 text-ink glow-accent" : "border-line-2 bg-surface text-ink-2 hover:border-ink-3 hover:bg-surface-2"
                  }`}
                  aria-pressed={on}
                >
                  {DECISION_LABELS[k]}
                  <Kbd>{KEY[k]}</Kbd>
                </button>
              );
            })}
          </div>
          {decision === "OVERRIDE" && (
            <Field label="Override the classification to">
              <select className="input w-full" value={override} onChange={(e) => setOverride(e.target.value as Classification)}>
                {CLASSIFICATIONS.map((c) => (
                  <option key={c} value={c}>
                    {c.charAt(0) + c.slice(1).toLowerCase()}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="Comment" hint="Why. Recorded verbatim in the audit trail.">
            <textarea className="input w-full" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} disabled={isFinal} placeholder="Optional" />
          </Field>
          <div className="flex items-center gap-3">
            <Button variant="primary" disabled={!decision || !name || isFinal} loading={busy} onClick={submit}>
              {mine ? "Update my decision" : "Record decision"}
            </Button>
            <span className="text-xs text-ink-3">
              {mine ? (
                <>
                  You decided <b>{DECISION_LABELS[mine.decision]}</b> at {fmtDate(mine.decided_at)}
                </>
              ) : (
                <>
                  <Kbd>Enter</Kbd> records the chosen decision
                </>
              )}
            </span>
          </div>
          {err && <ErrorBox error={err} />}
        </div>

        {/* ---- both reviewers, the final decision, the history */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <ReviewerBox label="Reviewer 1 · facilitator" d={detail.decisions["1"]} hidden={blindHidden} />
            <ReviewerBox label="Reviewer 2 · independent" d={detail.decisions["2"]} hidden={false} />
          </div>
          <div className="text-sm flex items-center gap-2 flex-wrap">
            <span className="text-ink-3">Effective classification</span>
            <ClassificationBadge value={detail.effective_classification} />
            {detail.effective_classification !== detail.result.classification && <span className="text-xs text-ink-3">(engine said {detail.result.classification.toLowerCase()})</span>}
          </div>
          <div className="border-t border-line pt-4">
            <div className="text-xs font-medium text-ink-2 mb-1.5">Final decision</div>
            {detail.final ? (
              <div className="text-sm">
                <DecisionBadge value={detail.final.final_decision} /> by <b>{detail.final.finalized_by}</b> at {fmtDate(detail.final.finalized_at)}
                {detail.final.note && <div className="text-ink-2 mt-1">“{detail.final.note}”</div>}
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <select className="input input-sm" value={finalDecision} onChange={(e) => setFinalDecision(e.target.value as DecisionKind)} aria-label="Final decision">
                  {DECISION_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {DECISION_LABELS[k]}
                    </option>
                  ))}
                </select>
                <input className="input input-sm flex-1 min-w-[8rem]" placeholder="Note from the cross-check meeting" value={finalNote} onChange={(e) => setFinalNote(e.target.value)} aria-label="Final note" />
                <Button size="sm" disabled={!name} loading={finalBusy} onClick={finalize} title="Records the final decision for this row and closes the two-reviewer loop">
                  Finalize
                </Button>
              </div>
            )}
            {finalErr && (
              <div className="mt-2">
                <ErrorBox error={finalErr} />
              </div>
            )}
          </div>
          <Timeline history={detail.history} />
        </div>
      </div>
    </Card>
  );
}

function ReviewerBox({ label, d, hidden }: { label: string; d: Decision | null; hidden: boolean }) {
  return (
    <div className="text-sm">
      <div className="text-xs font-medium text-ink-2 mb-1.5">{label}</div>
      {hidden ? (
        <div className="flex items-center gap-1.5 text-brand-600">
          <EyeSlash size={16} /> Hidden until you decide
        </div>
      ) : d ? (
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <DecisionBadge value={d.decision} />
            {d.override_classification && <ClassificationBadge value={d.override_classification} />}
            {d.blind && <span className="chip">blind</span>}
          </div>
          <div className="text-xs text-ink-3">
            {d.reviewer} · {fmtDate(d.decided_at)}
          </div>
          {d.comment && <div className="text-ink-2">“{d.comment}”</div>}
        </div>
      ) : (
        <div className="text-ink-4">No decision yet</div>
      )}
    </div>
  );
}

const STEPS = [
  { key: "engine", label: "Engine" },
  { key: "reviewer_1", label: "Reviewer 1" },
  { key: "reviewer_2", label: "Reviewer 2" },
  { key: "final", label: "Final" },
];

function Timeline({ history }: { history: HistoryEvent[] }) {
  const byEvent = new Map(history.map((h) => [h.event, h]));
  return (
    <div>
      <div className="text-xs font-medium text-ink-2 mb-2">History</div>
      <ol className="text-sm space-y-0">
        {STEPS.map((s, i) => {
          const h = byEvent.get(s.key);
          const last = i === STEPS.length - 1;
          return (
            <li key={s.key} className="relative pl-6 pb-3">
              {!last && <span className={`absolute left-[5px] top-3 bottom-0 w-px ${h ? "bg-brand-300" : "bg-line"}`} aria-hidden />}
              <span className={`absolute left-0 top-1.5 w-[11px] h-[11px] rounded-full border-2 ${h ? "bg-brand-600 border-brand-600" : "bg-surface border-line-2"}`} aria-hidden />
              <span className={h ? "text-ink" : "text-ink-4"}>
                <b className="font-medium">{s.label}</b>
                {!h && <span className="text-ink-4"> · pending</span>}
                {h && s.key === "engine" && <span className="text-ink-2"> · {h.detail ?? "recommendation recorded with the run"}</span>}
                {h && (s.key === "reviewer_1" || s.key === "reviewer_2") && (
                  <span className="text-ink-2">
                    {" "}
                    · {DECISION_LABELS[h.decision as DecisionKind] ?? h.decision}
                    {h.override_classification ? ` → ${h.override_classification.toLowerCase()}` : ""} by {h.reviewer}, {fmtDate(h.decided_at)}
                    {h.blind ? " (blind)" : ""}
                    {h.comment ? ` · “${h.comment}”` : ""}
                  </span>
                )}
                {h && s.key === "final" && (
                  <span className="text-ink-2">
                    {" "}
                    · {DECISION_LABELS[h.final_decision as DecisionKind] ?? h.final_decision} by {h.finalized_by}, {fmtDate(h.finalized_at)}
                    {h.note ? ` · “${h.note}”` : ""}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
