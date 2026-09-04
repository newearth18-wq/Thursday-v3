import { useCallback, useEffect, useRef, useState } from "react";
import { WS_ORIGIN } from "@/lib/origin";
import type {
  AgentStatus,
  Approval,
  Expression,
  Message,
  Mood,
  Posture,
  RealtimeMessage,
} from "@/lib/types";

const WS_URL = `${WS_ORIGIN}/api/v1/realtime`;
const RECONNECT_MAX_MS = 15_000;
/** Close calls this many times in a row before asking whether Thursday is even at this
 * address — long enough that a sidecar still migrating a fresh install is not mistaken for
 * "there is nothing here" (Sprint 83's backend can take several seconds to answer), short
 * enough that a phone with no address configured is not left guessing for a full minute. */
const SETUP_AFTER_FAILURES = 3;

/**
 * What to show before the server has said anything.
 *
 * Deliberately the quietest state rather than a cheerful one: until Thursday has reported
 * its condition, the honest thing to draw is "nothing is happening", not "all is well".
 */
export const UNKNOWN: Expression = {
  mood: "CALM",
  posture: "STILL",
  // Never true without the server having said so. An indicator that lights up on no
  // evidence is worse than one that is late (§10).
  listening: false,
  activity: "",
  because: "",
  intensity: 0.15,
  running: 0,
  waiting: 0,
  unhealthy: 0,
};

/**
 * One `expression` frame, read field by field.
 *
 * The whole-object `as Expression` this replaces (Sprint 85) was a hole: it satisfied the
 * compiler no matter which fields were present, so `posture` and `listening` could have
 * been added to the contract and silently never read. Typing the literal instead means a
 * field added to `Expression` is a build error here until somebody decides what it reads.
 *
 * Every narrowing defaults to the quiet answer. A frame that is malformed or from an older
 * server should under-claim, not invent a microphone that is not on.
 */
function readExpression(message: RealtimeMessage): Expression {
  return {
    mood: (message.mood as Mood) ?? UNKNOWN.mood,
    posture: (message.posture as Posture) ?? UNKNOWN.posture,
    listening: message.listening === true,
    activity: typeof message.activity === "string" ? message.activity : "",
    because: typeof message.because === "string" ? message.because : "",
    intensity: typeof message.intensity === "number" ? message.intensity : UNKNOWN.intensity,
    running: typeof message.running === "number" ? message.running : 0,
    waiting: typeof message.waiting === "number" ? message.waiting : 0,
    unhealthy: typeof message.unhealthy === "number" ? message.unhealthy : 0,
  };
}

/**
 * The single connection to Thursday.
 *
 * Reconnects with backoff, because a desktop app sleeps with the laptop lid and must come
 * back without the owner restarting it.
 *
 * Sprint 81 removed the client-side avatar state machine that used to live here. It set
 * SPEAKING on a reply, WORKING on a task event and IDLE on a 1.2-second timer — a second,
 * guessed answer to a question the server now derives and pushes (ADR 0054). Two answers to
 * "how does Thursday look right now" is how the HUD and the avatar window end up showing
 * different faces at the same moment. The wire still carries `avatar_state`; nothing on
 * this screen reads it.
 *
 * Sprint 84 added `needsSetup`. A phone has no local Thursday to default to (ADR 0057), and
 * the signal for that is not "what platform is this" — it is repeated, real connection
 * failure with nowhere else to try: `SETUP_AFTER_FAILURES` closes in a row, and nothing has
 * ever been typed into the connect screen. The same signal fires on any platform, which is
 * the point: desktop's sidecar makes it vanishingly unlikely there, and a phone that has
 * never been told an address hits it on the very first attempt.
 */
export function useRealtime() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [thinking, setThinking] = useState(false);
  const [expression, setExpression] = useState<Expression>(UNKNOWN);
  const [needsSetup, setNeedsSetup] = useState(false);

  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(1000);
  const sessionId = useRef<string | null>(null);
  const failures = useRef(0);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    socket.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setNeedsSetup(false);
      backoff.current = 1000;
      failures.current = 0;
    };

    ws.onclose = () => {
      setConnected(false);
      // A mood outlives its connection otherwise: the last thing the socket said would
      // keep glowing on the screen long after Thursday stopped being able to say it.
      setExpression(UNKNOWN);
      failures.current += 1;
      // Fires whether or not an address was already given: a stored override that is
      // wrong or has gone offline deserves the same screen, pre-filled rather than blank
      // (`ServerConnect` reads `serverOverride()` itself) — the alternative is a silent
      // reconnect loop with no way for a person to notice a typo was ever the problem.
      if (failures.current >= SETUP_AFTER_FAILURES) {
        setNeedsSetup(true);
      }
      setTimeout(connect, backoff.current);
      backoff.current = Math.min(backoff.current * 2, RECONNECT_MAX_MS);
    };

    ws.onmessage = (raw) => {
      const message = JSON.parse(raw.data) as RealtimeMessage;

      switch (message.type) {
        case "ready":
          sessionId.current = message.session_id as string;
          break;

        case "assistant.delta": {
          setThinking(false);
          setMessages((prior) => [
            ...prior,
            {
              id: crypto.randomUUID(),
              role: "thursday",
              text: message.text as string,
              voiceMode: message.voice_mode as Message["voiceMode"],
              verified: message.verified as boolean,
              confidence: message.confidence as number,
              at: new Date().toISOString(),
            },
          ]);
          if (Array.isArray(message.approvals) && message.approvals.length) {
            setApprovals(message.approvals as Approval[]);
          }
          break;
        }

        case "approval.required":
          setApprovals((prior) => {
            const incoming = message.payload as Approval;
            return prior.some((a) => a.id === incoming.id) ? prior : [...prior, incoming];
          });
          break;

        case "approval.resolved":
          setApprovals((prior) =>
            prior.filter((a) => a.id !== (message.payload as { id?: string })?.id),
          );
          break;

        case "agent.updated": {
          const payload = message.payload as { agent?: string; activity?: string; ok?: boolean };
          if (!payload?.agent) break;
          setAgents((prior) => {
            const state: AgentStatus["state"] =
              message.kind === "agent.started" ? "working" : payload.ok ? "completed" : "failed";
            const rest = prior.filter((a) => a.name !== payload.agent);
            // `activity` is what gets drawn; `name` only tells two jobs apart. Both are
            // in the payload for exactly that reason (see `BaseAgent.run`).
            return [...rest, { name: payload.agent!, activity: payload.activity ?? "", state }];
          });
          break;
        }

        case "expression":
          // Taken whole from the server. The client has no opinion about how Thursday
          // feels, which is what stops the HUD and the avatar drifting apart.
          setExpression(readExpression(message));
          break;
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => socket.current?.close();
  }, [connect]);

  const send = useCallback((text: string, deviceId?: string) => {
    if (!socket.current || socket.current.readyState !== WebSocket.OPEN) return;
    setMessages((prior) => [
      ...prior,
      { id: crypto.randomUUID(), role: "owner", text, at: new Date().toISOString() },
    ]);
    setThinking(true);
    setAgents([]);
    socket.current.send(
      JSON.stringify({ type: "turn", text, session_id: sessionId.current, device_id: deviceId }),
    );
  }, []);

  /** PART 98 — stop outranks everything, including a turn already in flight. */
  const interrupt = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "interrupt" }));
    setThinking(false);
  }, []);

  return {
    connected,
    messages,
    expression,
    approvals,
    agents,
    thinking,
    needsSetup,
    send,
    interrupt,
    setApprovals,
  };
}
