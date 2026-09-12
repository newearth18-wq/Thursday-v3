# 13. Implementation Roadmap

Sequenced so that every phase ends with something a person can actually use, and so no
phase requires rewriting the previous one.

## Phase 0 — Foundations (done in this repo)
Ports & DI container · settings · SQLAlchemy models + Alembic · event bus · structured
logging with `trace_id` · test harness with fakes for every port.
**Exit:** `pytest` green with zero infrastructure.

## Phase 1 — The Vertical Slice (done in this repo)
Voice/text in → intent → permission → device action → **verify** → voice/text out.
Core loop, permission engine, node protocol, Linux/macOS/Windows adapters, audit, undo,
task state machine, memory v1, Obsidian writer, orchestrator + supervisor, research and
computer agents, model router with offline rule-based tier.
**Exit:** §89 demo passes as an automated integration test.

## Media editing (done in this repo, out of sequence)
Local, deterministic video assembly behind the `MediaEditor` port — trim, join, resize to
16:9/9:16/1:1, burn in subtitles, dub narration and music, normalise loudness, strip
silence, overlay, crossfade, cover frame — with checkpointed plans, a quality gate that
judges the finished file, and an honest refusal on a machine with no ffmpeg. Brought
forward because it is the capability the brief leads with; see [§24](24-media.md) and
[ADR 0060](architecture/decisions/0060-an-edit-never-writes-over-its-input.md).
**Exit:** a promotional video assembled, narrated, subtitled and rendered, then verified by
opening the output — `tests/e2e/test_v11_media_acceptance.py`.
**Not done:** generation of any kind — no text-to-image, text-to-video or TTS, so pictures
and narration are inputs.

## Phase 2 — Real work (4–6 weeks)
Postgres+pgvector in place of SQLite · Redis bus/queue · real STT (faster-whisper) and TTS
(Piper) · Windows node hardening (UIA, COM for Office) · Data + Document agents · file
operations with dry-run · browser agent (CDP) · Google connectors · Tauri desktop shell
with orb + approvals.
**Exit:** §90 demo — "open the latest grades file and analyze it" end-to-end, verified.

## Phase 3 — Perception (4–6 weeks)
Screen understanding · screen annotation · camera pipeline · OCR · object detection ·
spatial memory · MediaPipe hands + gesture mode · multimodal fusion.
**Exit:** §91 demo — point at the screen and say "what is this".

## Phase 4 — Presence (4–6 weeks)
Multi-device continuity · device router with confidence-gated questions · mobile app ·
follow-me output routing · notification intelligence · proactive assistant with
proactivity levels · automation engine + routine learning proposals.
**Exit:** §92 demo — from the phone, "is the home PC still on?"

## School agents (done in this repo)
Teacher, library and event — the three the brief names, built on one rule: **the artefacts
whose correctness is arithmetic, and no more.** Rubrics, exam blueprints, timed lesson
plans, circulation and collection reports, run sheets. Each refuses rather than corrects,
and hands its figures to the Supervisor to recompute; see
[ADR 0062](architecture/decisions/0062-a-school-document-is-arithmetic-somebody-is-held-to.md).
**Exit:** a rubric whose weights the Supervisor checks, a run sheet that finds the teacher
rostered onto two consecutive items.
**Not done:** worksheets, publicity, stage scripts, evaluation forms — prose with no
checkable property, and the document agent's job.

## Trading (done in this repo, and it cannot trade)
The optional module §15 asks for, built so that the one irreversible thing in it is
impossible rather than guarded: **there is no live adapter and the verb is blocked**, two
independent refusals of the same act. Deterministic backtests, a risk manager that is the
only place an `Order` is constructed, and a ladder that reports `"ran"` and never
`"passed"`; see
[§25](25-trading.md) and
[ADR 0063](architecture/decisions/0063-a-trading-module-that-cannot-trade.md).
**Exit:** a backtest whose trade count the Supervisor recomputes, and an AST walk asserting
no module outside `risk.py` builds an order.
**Not done:** market data, broker connectivity, prediction, portfolio optimisation — and
none of them are next. Unblocking live execution is a reviewed pull request, not a setting.

## Phase 5 — Learning (6–8 weeks)
Knowledge graph · timeline queries · skill capture from demonstration · skill sandbox
testing · skill versioning + rollback · dynamic agents · self-evaluation · quality-aware
model routing.
**Exit:** §93 demo — "do it like last time."

## Phase 6 — Hardening (continuous)
Threat-model items closed · key rotation · sandbox escape testing · chaos tests for
device/network loss · cost dashboards · offline-mode drills · backup/restore rehearsal.

## Cross-cutting rules
- Every phase adds tests in the same PR; no phase lands with a red suite.
- Every new capability registers: a `ToolSpec` (risk, cost, permission), an undo
  operation or an explicit "irreversible" flag, and an audit shape.
- No feature ships without a failure path and a way to turn it off.
