import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Catalogue, type StoredWorkflow, type WorkflowReport, api } from "@/lib/api";
import {
  CANVAS,
  type Consequence,
  EMPTY,
  type Graph,
  NODE_HEIGHT,
  NODE_WIDTH,
  type Problem,
  type WorkflowNode,
  addNode,
  curve,
  edges,
  hasErrors,
  localProblems,
  moveNode,
  orderedActions,
  problemsFor,
  removeNode,
  reorder,
  setConfig,
  setSubkind,
} from "@/lib/workflow";

/**
 * §48 — the workflow builder, drawn.
 *
 * The canvas can only draw what the engine can run. There is one trigger, an AND-set of
 * conditions and an ordered list of actions, and the wires between them are **derived**
 * rather than dragged: giving the owner arrows to connect would give them arrows the engine
 * ignores, and a picture that disagrees with behaviour is worse than no picture
 * (ADR 0064).
 *
 * What the owner does drag is position, which is theirs and means nothing to the rule. What
 * they change that *does* mean something — the run order — is a pair of explicit buttons,
 * so no rule ever changes because a box was dropped an inch to the left.
 *
 * Every edit re-asks the server what the rule would do: which permission each action
 * resolves to, whether any of them is blocked outright, and whether the handler is even
 * wired up. That panel is the point of the screen. A builder that only showed a diagram
 * would be showing the half that cannot be wrong.
 */

const TRIGGER_LABEL: Record<string, string> = {
  event: "เมื่อเกิดเหตุการณ์",
  schedule: "ตามเวลา",
  manual: "สั่งเอง",
  state_change: "เมื่อสถานะเปลี่ยน",
};

const ACTION_LABEL: Record<string, string> = {
  notify: "แจ้งเตือน",
  task: "สร้างงาน",
  tool: "ใช้เครื่องมือ",
  obsidian_write: "บันทึกลง Obsidian",
};

const OP_LABEL: Record<string, string> = {
  eq: "เท่ากับ",
  ne: "ไม่เท่ากับ",
  gt: "มากกว่า",
  lt: "น้อยกว่า",
  contains: "มีคำว่า",
  matches: "ตรงรูปแบบ",
  in: "อยู่ในรายการ",
};

const KIND_STYLE: Record<string, string> = {
  trigger: "border-thursday/50 bg-thursday/10",
  condition: "border-slate-600 bg-ink-900",
  action: "border-state-speaking/40 bg-state-speaking/5",
};

const DECISION_COLOUR: Record<string, string> = {
  AUTO: "text-state-speaking",
  ASK_ONCE: "text-slate-300",
  ASK_ALWAYS: "text-state-warning",
  BLOCK: "text-state-error",
};

export function WorkflowBuilder() {
  const [graph, setGraph] = useState<Graph>(EMPTY);
  const [saved, setSaved] = useState<StoredWorkflow[]>([]);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [report, setReport] = useState<WorkflowReport | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const surface = useRef<SVGSVGElement | null>(null);

  const refresh = useCallback(
    () =>
      api
        .automations()
        .then((r) => setSaved(r.automations))
        .catch((e) => setError(String(e))),
    [],
  );

  useEffect(() => {
    refresh();
    api
      .automationCatalogue()
      .then(setCatalogue)
      .catch((e) => setError(String(e)));
  }, [refresh]);

  // The server validates the same graph the owner is looking at, on every edit. Local
  // checks mark the obvious while this is in flight; they are never the verdict.
  useEffect(() => {
    if (!graph.nodes.length) {
      setReport(null);
      return;
    }
    let current = true;
    api
      .previewWorkflow(graph)
      .then((r) => current && setReport(r))
      .catch((e) => current && setError(String(e)));
    return () => {
      current = false;
    };
  }, [graph]);

  const problems: Problem[] = report?.problems ?? localProblems(graph);
  const blocked = hasErrors(problems);
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const wires = useMemo(() => edges(graph), [graph]);
  const consequences = useMemo(
    () => new Map((report?.consequences ?? []).map((c: Consequence) => [c.node, c])),
    [report],
  );
  const actionOrder = useMemo(() => orderedActions(graph).map((n) => n.id), [graph]);

  const onPointerDown = (event: React.PointerEvent, node: WorkflowNode) => {
    const box = surface.current?.getBoundingClientRect();
    if (!box) return;
    setSelected(node.id);
    drag.current = {
      id: node.id,
      dx: event.clientX - box.left - node.x,
      dy: event.clientY - box.top - node.y,
    };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const box = surface.current?.getBoundingClientRect();
    if (!drag.current || !box) return;
    const { id, dx, dy } = drag.current;
    setGraph((g) => moveNode(g, id, event.clientX - box.left - dx, event.clientY - box.top - dy));
  };

  const endDrag = () => {
    drag.current = null;
  };

  const open = (stored: StoredWorkflow) => {
    setEditing(stored.automation_id);
    setSelected(null);
    setNote(null);
    setGraph({
      name: stored.name,
      nodes: stored.nodes,
      order: stored.order,
      follow_up_from: stored.follow_up_from,
    });
  };

  const save = async () => {
    setError(null);
    try {
      const result = await api.saveWorkflow(graph, editing ?? undefined);
      setEditing(result.id);
      setNote(
        result.enabled
          ? "บันทึกแล้ว — กฎนี้ยังเปิดใช้งานอยู่"
          : "บันทึกแล้ว และยังไม่เปิดใช้งาน — กด 'เปิดใช้งาน' เมื่อพร้อม",
      );
      await refresh();
    } catch (e) {
      setError(String(e).replace(/^Error: \d+ [^:]+: /, ""));
    }
  };

  return (
    <div className="flex h-full flex-col gap-3 p-4 text-slate-300">
      <header className="flex items-center gap-2">
        <input
          value={graph.name}
          onChange={(event) => setGraph({ ...graph, name: event.target.value })}
          placeholder="ชื่อกฎ"
          aria-label="ชื่อกฎ"
          className="flex-1 rounded-lg bg-ink-900 px-3 py-1.5 text-xs text-slate-200 outline-none
                     placeholder:text-slate-600 focus:ring-1 focus:ring-thursday/40"
        />
        <button
          onClick={save}
          disabled={blocked}
          className="rounded px-2 py-1 text-[11px] text-thursday disabled:cursor-not-allowed
                     disabled:text-slate-700"
        >
          บันทึก
        </button>
        <button
          onClick={() => {
            setGraph(EMPTY);
            setEditing(null);
            setNote(null);
          }}
          className="rounded px-2 py-1 text-[11px] text-slate-500 hover:text-slate-300"
        >
          เริ่มใหม่
        </button>
      </header>

      <div className="flex flex-wrap gap-1 text-[11px]">
        {(catalogue?.triggers ?? []).map((trigger) => (
          <button
            key={trigger.kind}
            disabled={!!trigger.unavailable}
            title={trigger.unavailable || undefined}
            onClick={() => setGraph((g) => addNode(g, "trigger", trigger.kind))}
            className="rounded bg-ink-900 px-2 py-1 text-slate-400 hover:text-thursday
                       disabled:cursor-not-allowed disabled:text-slate-700 disabled:line-through"
          >
            {TRIGGER_LABEL[trigger.kind] ?? trigger.kind}
          </button>
        ))}
        <span className="px-1 text-slate-700">·</span>
        <button
          onClick={() => setGraph((g) => addNode(g, "condition", "eq"))}
          className="rounded bg-ink-900 px-2 py-1 text-slate-400 hover:text-thursday"
        >
          + เงื่อนไข
        </button>
        <span className="px-1 text-slate-700">·</span>
        {(catalogue?.actions ?? []).map((action) => (
          <button
            key={action.kind}
            title={action.unavailable || undefined}
            onClick={() => setGraph((g) => addNode(g, "action", action.kind))}
            className={`rounded bg-ink-900 px-2 py-1 hover:text-thursday ${
              action.unavailable ? "text-state-warning" : "text-slate-400"
            }`}
          >
            + {ACTION_LABEL[action.kind] ?? action.kind}
          </button>
        ))}
      </div>

      <svg
        ref={surface}
        role="presentation"
        viewBox={`0 0 ${CANVAS.width} ${CANVAS.height}`}
        className="w-full flex-1 rounded-lg bg-ink-950 ring-1 ring-ink-900"
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
      >
        <g>
          {wires.map((edge) => {
            const from = byId.get(edge.from);
            const to = byId.get(edge.to);
            if (!from || !to) return null;
            return (
              <path
                key={`${edge.from}->${edge.to}`}
                d={curve(from, to)}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                className="text-slate-700"
              />
            );
          })}
        </g>
        {graph.nodes.map((node) => {
          const consequence = consequences.get(node.id);
          const faults = problemsFor(problems, node.id);
          const position = node.kind === "action" ? actionOrder.indexOf(node.id) : -1;
          const followUp =
            graph.follow_up_from !== null && position >= graph.follow_up_from && position >= 0;
          return (
            <foreignObject
              key={node.id}
              x={node.x}
              y={node.y}
              width={NODE_WIDTH}
              height={NODE_HEIGHT}
            >
              <div
                data-testid={`node-${node.id}`}
                onPointerDown={(event) => onPointerDown(event, node)}
                className={`h-full cursor-grab select-none rounded-md border px-2 py-1 text-[10px]
                            ${KIND_STYLE[node.kind]}
                            ${selected === node.id ? "ring-1 ring-thursday" : ""}
                            ${faults.some((f) => f.severity === "error") ? "border-state-error" : ""}`}
              >
                <div className="flex items-center gap-1">
                  <span className="truncate text-slate-200">
                    {node.kind === "trigger"
                      ? (TRIGGER_LABEL[node.subkind] ?? node.subkind)
                      : node.kind === "condition"
                        ? (OP_LABEL[node.subkind] ?? node.subkind)
                        : (ACTION_LABEL[node.subkind] ?? node.subkind)}
                  </span>
                  {position >= 0 && (
                    <span className="text-[9px] text-slate-600">
                      #{position + 1}
                      {followUp ? " ตาม" : ""}
                    </span>
                  )}
                </div>
                {consequence && (
                  <div className="truncate font-mono text-[9px]">
                    <span className={DECISION_COLOUR[consequence.decision] ?? "text-slate-500"}>
                      {consequence.blocked ? "ห้ามถาวร" : consequence.decision}
                    </span>
                    <span className="text-slate-600"> {consequence.action}</span>
                  </div>
                )}
                {consequence?.unavailable && (
                  <div className="truncate text-[9px] text-state-warning">
                    {consequence.unavailable}
                  </div>
                )}
                {faults.length > 0 && (
                  <div className="truncate text-[9px] text-state-error">{faults[0].message}</div>
                )}
              </div>
            </foreignObject>
          );
        })}
      </svg>

      {selected && byId.has(selected) && (
        <Inspector
          node={byId.get(selected)!}
          catalogue={catalogue}
          canMoveEarlier={actionOrder.indexOf(selected) > 0}
          canMoveLater={
            actionOrder.indexOf(selected) >= 0 &&
            actionOrder.indexOf(selected) < actionOrder.length - 1
          }
          onSubkind={(subkind) => setGraph((g) => setSubkind(g, selected, subkind))}
          onConfig={(config) => setGraph((g) => setConfig(g, selected, config))}
          onReorder={(direction) => setGraph((g) => reorder(g, selected, direction))}
          onRemove={() => {
            setGraph((g) => removeNode(g, selected));
            setSelected(null);
          }}
        />
      )}

      <section className="space-y-1">
        {report?.explanation && (
          <p className="rounded bg-ink-900 px-2 py-1.5 text-[11px] text-slate-300">
            {report.explanation}
          </p>
        )}
        {problems
          .filter((p) => !p.node)
          .map((problem) => (
            <p
              key={problem.message}
              className={`text-[11px] ${
                problem.severity === "error" ? "text-state-error" : "text-state-warning"
              }`}
            >
              {problem.message}
            </p>
          ))}
        {note && <p className="text-[11px] text-state-speaking">{note}</p>}
        {error && <p className="text-[11px] text-state-warning">{error}</p>}
      </section>

      <section>
        <h3 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">กฎที่บันทึกไว้</h3>
        {saved.length === 0 && <p className="text-[11px] text-slate-600">ยังไม่มีกฎ</p>}
        <ul className="space-y-0.5">
          {saved.map((stored) => (
            <li key={stored.automation_id} className="flex items-center gap-2 px-1 py-1">
              <button
                onClick={() => open(stored)}
                className="truncate text-left text-[11px] text-slate-300 hover:text-thursday"
              >
                {stored.name || "(ไม่มีชื่อ)"}
              </button>
              <span className="truncate text-[10px] text-slate-600">{stored.explanation}</span>
              {stored.created_by === "thursday_suggested" && (
                <span className="shrink-0 text-[9px] text-slate-600">Thursday เสนอ</span>
              )}
              <span
                className={`ml-auto shrink-0 text-[10px] ${
                  stored.enabled ? "text-state-speaking" : "text-slate-600"
                }`}
              >
                {stored.enabled ? "เปิดใช้งาน" : "ปิดอยู่"}
              </span>
              <button
                onClick={() =>
                  stored.automation_id &&
                  api.enableWorkflow(stored.automation_id, !stored.enabled).then(refresh)
                }
                className="shrink-0 text-[10px] text-slate-500 hover:text-thursday"
              >
                {stored.enabled ? "ปิด" : "เปิดใช้งาน"}
              </button>
              <button
                onClick={() =>
                  stored.automation_id &&
                  api
                    .runWorkflow(stored.automation_id)
                    .then((r) => setNote(`ลองแล้ว ${r.steps} ขั้นตอน`))
                    .catch((e) => setError(String(e)))
                }
                className="shrink-0 text-[10px] text-slate-500 hover:text-thursday"
              >
                ลองเลย
              </button>
              <button
                onClick={() =>
                  stored.automation_id && api.deleteWorkflow(stored.automation_id).then(refresh)
                }
                className="shrink-0 text-[10px] text-slate-600 hover:text-state-error"
              >
                ลบ
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Inspector(props: {
  node: WorkflowNode;
  catalogue: Catalogue | null;
  canMoveEarlier: boolean;
  canMoveLater: boolean;
  onSubkind: (subkind: string) => void;
  onConfig: (config: Record<string, unknown>) => void;
  onReorder: (direction: -1 | 1) => void;
  onRemove: () => void;
}) {
  const { node, catalogue } = props;
  const [reads, setReads] = useState<string>("");

  const cron = node.kind === "trigger" && node.subkind === "schedule" ? String(node.config.cron ?? "") : "";

  // Read the expression back in words as it is typed. "Is this what I meant" is a question
  // best answered before the rule is saved rather than after it fails to run.
  useEffect(() => {
    if (!cron) {
      setReads("");
      return;
    }
    let current = true;
    api
      .describeCron(cron)
      .then((r) => current && setReads(r.valid ? r.reads_as : (r.problem ?? "")))
      .catch(() => current && setReads(""));
    return () => {
      current = false;
    };
  }, [cron]);

  const options =
    node.kind === "trigger"
      ? (catalogue?.triggers ?? []).map((t) => ({ value: t.kind, disabled: !!t.unavailable }))
      : node.kind === "condition"
        ? (catalogue?.conditions ?? []).map((op) => ({ value: op, disabled: false }))
        : (catalogue?.actions ?? []).map((a) => ({ value: a.kind, disabled: false }));

  const field = (label: string, key: string, placeholder = "") => (
    <label className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-[10px] text-slate-500">{label}</span>
      <input
        aria-label={label}
        value={String(node.config[key] ?? "")}
        placeholder={placeholder}
        onChange={(event) => props.onConfig({ ...node.config, [key]: event.target.value })}
        className="flex-1 rounded bg-ink-950 px-2 py-1 text-[11px] text-slate-200 outline-none
                   placeholder:text-slate-700 focus:ring-1 focus:ring-thursday/40"
      />
    </label>
  );

  return (
    <div className="space-y-1.5 rounded-lg bg-ink-900 p-2">
      <div className="flex items-center gap-2">
        <select
          aria-label="ชนิด"
          value={node.subkind}
          onChange={(event) => props.onSubkind(event.target.value)}
          className="rounded bg-ink-950 px-2 py-1 text-[11px] text-slate-200 outline-none"
        >
          {options.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {TRIGGER_LABEL[option.value] ??
                OP_LABEL[option.value] ??
                ACTION_LABEL[option.value] ??
                option.value}
            </option>
          ))}
        </select>
        {node.kind === "action" && (
          <>
            <button
              onClick={() => props.onReorder(-1)}
              disabled={!props.canMoveEarlier}
              className="text-[11px] text-slate-500 hover:text-thursday disabled:text-slate-700"
            >
              ก่อนหน้า
            </button>
            <button
              onClick={() => props.onReorder(1)}
              disabled={!props.canMoveLater}
              className="text-[11px] text-slate-500 hover:text-thursday disabled:text-slate-700"
            >
              ถัดไป
            </button>
          </>
        )}
        <button
          onClick={props.onRemove}
          className="ml-auto text-[10px] text-slate-600 hover:text-state-error"
        >
          ลบกล่องนี้
        </button>
      </div>

      {node.kind === "trigger" && node.subkind === "schedule" && (
        <>
          {field("ตารางเวลา", "cron", "30 7 * * 1-5")}
          {reads && <p className="pl-[5.5rem] text-[10px] text-slate-500">{reads}</p>}
        </>
      )}
      {node.kind === "trigger" && node.subkind === "event" && field("เหตุการณ์", "event_kind", "file.created")}
      {node.kind === "condition" && (
        <>
          {field("ฟิลด์", "field", "event.path")}
          {field("ค่า", "value")}
        </>
      )}
      {node.kind === "action" && node.subkind === "tool" && field("เครื่องมือ", "name", "file.read")}
      {node.kind === "action" && node.subkind === "notify" && field("หัวข้อ", "title")}
      {node.kind === "action" && node.subkind === "task" && field("งาน", "name")}
    </div>
  );
}
