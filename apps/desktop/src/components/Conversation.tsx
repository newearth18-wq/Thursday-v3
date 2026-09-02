import { useEffect, useRef } from "react";
import type { Message } from "@/lib/types";

const MODE_ACCENT: Record<string, string> = {
  SUCCESS: "border-state-speaking/40",
  WARNING: "border-state-warning/50",
  URGENT: "border-state-error/60",
  QUIET: "border-ink-600",
  THINKING: "border-state-thinking/40",
  NORMAL: "border-ink-700",
};

/** PART 64. The conversation is the interface. Everything else is a drawer. */
export function Conversation({ messages, thinking }: { messages: Message[]; thinking: boolean }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages, thinking]);

  return (
    <div className="flex-1 space-y-3 overflow-y-auto px-6 py-4">
      {messages.length === 0 && (
        <p className="mt-8 text-center text-sm text-slate-600">
          Say what you need. Thursday decides how to do it.
        </p>
      )}

      {messages.map((message) =>
        message.role === "owner" ? (
          <div key={message.id} className="flex justify-end">
            <div className="max-w-[78%] rounded-2xl rounded-br-sm bg-thursday-dim/25 px-4 py-2
                            text-sm text-slate-100">
              {message.text}
            </div>
          </div>
        ) : (
          <div key={message.id} className="flex justify-start">
            <div
              className={`max-w-[85%] rounded-2xl rounded-bl-sm border bg-ink-900/80 px-4 py-2
                          text-sm text-slate-200 ${MODE_ACCENT[message.voiceMode ?? "NORMAL"]}`}
            >
              <p className="whitespace-pre-wrap">{message.text}</p>

              {/* PART 5.1 made visible: an unverified result must not look like a success. */}
              {message.verified === false && (
                <p className="mt-1.5 text-[11px] uppercase tracking-wide text-state-warning">
                  unverified — the effect could not be confirmed
                </p>
              )}
              {message.detail && (
                <p className="mt-1.5 text-xs text-slate-500">{message.detail}</p>
              )}
              {typeof message.confidence === "number" && message.confidence < 0.7 && (
                <p className="mt-1 text-[11px] text-slate-500">
                  confidence {(message.confidence * 100).toFixed(0)}%
                </p>
              )}
            </div>
          </div>
        ),
      )}

      {thinking && (
        <div className="flex justify-start">
          <div className="rounded-2xl rounded-bl-sm border border-ink-700 bg-ink-900/60 px-4 py-2">
            <span className="inline-flex gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-500"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </div>
        </div>
      )}
      <div ref={bottom} />
    </div>
  );
}
