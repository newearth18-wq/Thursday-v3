import { useCallback, useEffect, useState } from "react";
import { Spotlight } from "@/components/Spotlight";
import {
  type LearningCentre,
  type Lesson,
  type LessonStep,
  type PracticeOffer,
  api,
} from "@/lib/api";

/**
 * §10 — "เรียนรู้ Thursday", rendered at last.
 *
 * The API has served this since Sprint 67 and nothing drew it, so the learning path, the
 * suggestion engine and the lessons with real verification existed and no owner could reach
 * them. §23 named it as an open gap; this closes it.
 *
 * **There is no control here that marks a lesson done.** `done` arrives from the server and
 * nowhere else, because a lesson's step reads the machine and decides — `/attempt` takes
 * *evidence* of what happened, not a verdict that it did (ADR 0012, the same shape as
 * `/setup/verify`). A "mark as complete" button is the one affordance that would turn this
 * screen from a record of what the owner can do into a record of what they clicked.
 *
 * Two more things it will not do. It shows **no score and no points** (§10 is explicit: never
 * a score to accumulate), and a lesson this machine cannot run shows the reason rather than
 * a grey box — the same rule the workflow builder follows for a trigger with no runner.
 */

const DECISION_COLOUR: Record<string, string> = {
  AUTO: "text-state-speaking",
  ASK_ONCE: "text-slate-300",
  ASK_ALWAYS: "text-state-warning",
  BLOCK: "text-state-error",
};

export function LearningCenter({ lastReply }: { lastReply?: string }) {
  const [centre, setCentre] = useState<LearningCentre | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [step, setStep] = useState<LessonStep | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(
    () =>
      api
        .learn()
        .then(setCentre)
        .catch((e) => setError(String(e))),
    [],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const start = async (lesson: Lesson) => {
    setError(null);
    setOpen(lesson.id);
    try {
      setStep(await api.startLesson(lesson.id));
    } catch (e) {
      setError(String(e));
    }
  };

  const check = async (id: string) => {
    setError(null);
    try {
      // What the owner and Thursday actually exchanged. The step decides what that proves.
      setStep(await api.attemptLesson(id, lastReply ? { reply: lastReply } : null));
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const skip = async (id: string) => {
    setError(null);
    try {
      await api.skipLesson(id);
      setOpen(null);
      setStep(null);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  if (!centre) {
    return (
      <div className="p-4 text-[11px] text-slate-600">
        {error ?? "กำลังโหลด…"}
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4 text-slate-300">
      <p className="text-xs text-slate-300">{centre.summary}</p>

      {centre.next && (
        <section className="rounded-lg bg-ink-900 p-2">
          <h3 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">ลองอันนี้ต่อ</h3>
          <button
            onClick={() => start({ ...centre.next!, done: false, available: true } as Lesson)}
            // The same lesson also appears in the path below. Without this, a screen reader
            // hears the same name twice with no way to tell the shortcut from the list.
            aria-label={`เริ่มบทเรียนที่แนะนำ: ${centre.next.name}`}
            className="text-left text-[11px] text-thursday hover:underline"
          >
            {centre.next.name}
          </button>
          <p className="mt-0.5 text-[10px] text-slate-500">{centre.next.reason}</p>
          <p className="text-[10px] text-slate-600">
            {centre.next.stage_title} · {centre.next.minutes} นาที
          </p>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">เส้นทางการเรียน</h3>
        {centre.path.map((stage) => (
          <div key={stage.stage} className="mb-2">
            <h4 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-600">
              {stage.title}
            </h4>
            <ul className="space-y-0.5">
              {stage.lessons.map((lesson) => (
                <li key={lesson.id}>
                  <div className="flex items-center gap-2 px-1 py-1">
                    <span
                      aria-hidden
                      className={`text-[10px] ${
                        lesson.done ? "text-state-speaking" : "text-slate-700"
                      }`}
                    >
                      {lesson.done ? "✓" : "○"}
                    </span>
                    <button
                      onClick={() => start(lesson)}
                      disabled={!lesson.available}
                      title={lesson.reason}
                      className="text-left text-[11px] text-slate-300 hover:text-thursday
                                 disabled:cursor-not-allowed disabled:text-slate-700"
                    >
                      {lesson.name}
                    </button>
                    <span className="ml-auto shrink-0 text-[10px] text-slate-600">
                      {lesson.minutes} นาที
                    </span>
                  </div>
                  {/* A lesson this machine cannot run says why, rather than being a grey box
                      the owner has to guess at. */}
                  {!lesson.available && lesson.reason && (
                    <p className="pl-5 text-[10px] text-state-warning">{lesson.reason}</p>
                  )}
                  {open === lesson.id && step && step.lesson === lesson.id && (
                    <LessonPanel
                      step={step}
                      onCheck={() => check(lesson.id)}
                      onSkip={() => skip(lesson.id)}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      {centre.practice.length > 0 && (
        <section>
          <h3 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">
            ลองแบบไม่ทำจริง
          </h3>
          <ul className="space-y-1">
            {centre.practice.map((offer) => (
              <PracticeRow key={offer.action} offer={offer} />
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">ทำอะไรได้บ้าง</h3>
        <ul className="space-y-1">
          {centre.areas.map((area) => (
            <li key={area.area} className="rounded bg-ink-900 px-2 py-1.5">
              <p className="text-[11px] text-slate-300">{area.title}</p>
              <p className="text-[10px] text-slate-600">{area.features.join(" · ")}</p>
              <p className="mt-0.5 text-[10px] italic text-slate-500">“{area.example}”</p>
            </li>
          ))}
        </ul>
      </section>

      {error && <p className="text-[11px] text-state-warning">{error}</p>}
    </div>
  );
}

function LessonPanel(props: { step: LessonStep; onCheck: () => void; onSkip: () => void }) {
  const { step } = props;
  const [missing, setMissing] = useState(false);
  const points_at = step.next.points_at;

  return (
    <div className="ml-5 space-y-1 rounded-lg bg-ink-900 p-2">
      {points_at && (
        <Spotlight name={points_at} label={step.next.show || step.message} onUnavailable={setMissing} />
      )}
      {/* The control is not on screen. Said out loud rather than drawn approximately: an
          arrow is a claim about where something is, and a wrong one costs the next one too. */}
      {points_at && missing && (
        <p className="text-[10px] text-state-warning">
          ปุ่มที่บทเรียนนี้พูดถึงยังไม่อยู่บนหน้าจอตอนนี้ — ลองเปิดหน้าต่างหลักของ Thursday ดูครับ
        </p>
      )}
      <p className="text-[11px] text-slate-300">{step.next.show || step.message}</p>
      {step.next.try && (
        <p className="font-mono text-[10px] text-thursday">“{step.next.try}”</p>
      )}
      {/* The server's word on the attempt, verbatim. Nothing here interprets it into a
          different verdict, and nothing here can produce one without asking. */}
      {step.passed && <p className="text-[10px] text-state-speaking">{step.message}</p>}
      <div className="flex items-center gap-2">
        <button onClick={props.onCheck} className="text-[10px] text-slate-500 hover:text-thursday">
          ตรวจว่าทำได้แล้ว
        </button>
        <button onClick={props.onSkip} className="text-[10px] text-slate-600 hover:text-slate-400">
          ข้ามก่อน
        </button>
        {step.done && <span className="ml-auto text-[10px] text-state-speaking">เรียบร้อย</span>}
      </div>
    </div>
  );
}

function PracticeRow({ offer }: { offer: PracticeOffer }) {
  return (
    <li className="rounded bg-ink-900 px-2 py-1.5">
      <div className="flex items-center gap-2">
        <span className="rounded bg-ink-950 px-1 text-[9px] text-slate-500">ซ้อม</span>
        <span className="font-mono text-[10px] text-slate-400">{offer.action}</span>
        <span className={`ml-auto text-[10px] ${DECISION_COLOUR[offer.decision] ?? "text-slate-500"}`}>
          {offer.decision}
        </span>
      </div>
      <p className="mt-0.5 text-[10px] text-slate-400">{offer.would}</p>
      <p className="text-[10px] text-slate-600">{offer.why}</p>
      {!offer.reversible && <p className="text-[10px] text-state-warning">{offer.risk}</p>}
    </li>
  );
}
