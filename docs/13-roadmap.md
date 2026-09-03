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
