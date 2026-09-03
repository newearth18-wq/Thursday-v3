import { useCallback, useEffect, useRef, useState } from "react";
import { WS_ORIGIN } from "@/lib/origin";
import type { AgentStatus, Approval, AvatarState, Message, RealtimeMessage } from "@/lib/types";

const WS_URL = `${WS_ORIGIN}/api/v1/realtime`;
const RECONNECT_MAX_MS = 15_000;

/**
 * The single connection to Thursday.
 *
 * Reconnects with backoff, because a desktop app sleeps with the laptop lid and must come
 * back without the owner restarting it.
 */
export function useRealtime() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [avatar, setAvatar] = useState<AvatarState>("IDLE");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [thinking, setThinking] = useState(false);

  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(1000);
  const sessionId = useRef<string | null>(null);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);
    socket.current = ws;

    ws.onopen = () => {
      setConnected(true);
      backoff.current = 1000;
    };

    ws.onclose = () => {
      setConnected(false);
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
          setAvatar((message.avatar_state as AvatarState) ?? "SPEAKING");
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
          // Settle back to idle once Thursday has finished speaking.
          setTimeout(() => setAvatar("IDLE"), 1200);
          break;
        }

        case "approval.required":
          setAvatar("WAITING_APPROVAL");
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
          const payload = message.payload as { agent?: string; ok?: boolean };
          if (!payload?.agent) break;
          setAgents((prior) => {
            const state: AgentStatus["state"] =
              message.kind === "agent.started" ? "working" : payload.ok ? "completed" : "failed";
            const rest = prior.filter((a) => a.name !== payload.agent);
            return [...rest, { name: payload.agent!, state }];
          });
          break;
        }

        case "task.updated":
          if (message.kind === "task.running" || message.kind === "task.planning") {
            setAvatar("WORKING");
          }
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
    setAvatar("THINKING");
    setAgents([]);
    socket.current.send(
      JSON.stringify({ type: "turn", text, session_id: sessionId.current, device_id: deviceId }),
    );
  }, []);

  /** PART 98 — stop outranks everything, including a turn already in flight. */
  const interrupt = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "interrupt" }));
    setThinking(false);
    setAvatar("IDLE");
  }, []);

  return { connected, messages, avatar, approvals, agents, thinking, send, interrupt, setApprovals };
}
