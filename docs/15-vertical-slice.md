# 15. First Vertical Slice

> "ผู้ใช้พูดกับ Thursday → Thursday เข้าใจ → สั่ง Windows PC → ตรวจผล → ตอบกลับด้วยเสียง"

One path, complete, honest, and testable — not a demo that fakes the hard parts (§88).

## 15.1 The path

```
 mic/keyboard
     │
 [1] WakeWordProvider ── "Thursday" ──► session opens
     │
 [2] VAD → STTProvider ──────────────► transcript: "Thursday เปิด Chrome"
     │
 [3] ConversationEngine → ContextEngine
         world state (active device = Office-PC), device list, memory hits
     │
 [4] PrivacyClassifier → INTERNAL   → ModelRouter → LOCAL/FAST tier
     │
 [5] ReasoningEngine → Intent{kind=DEVICE_ACTION, action=open_app, app=chrome,
                              target_device="this", confidence=0.93}
     │
 [6] Planner → 1 step: computer_agent · open_app(chrome) on Office-PC
     │
 [7] PermissionEngine → level=OPEN(1) → AUTO   (send_email here would be ASK)
     │
 [8] DeviceRouter → resolve "this" → node session
     │
 [9] Node: ACT open_app → **VERIFY** process running + window present
     │
[10] Supervisor: contract success criteria met? → PASS
     │
[11] Audit row + undo record (close_app) + episodic memory (only if noteworthy)
     │
[12] ResponseComposer → "เปิด Chrome แล้วครับ" · VoiceMode.SUCCESS
     │
[13] TTSProvider → speaker on the origin device
```

## 15.2 What is real vs stubbed in this repo

| Piece | State |
|---|---|
| Core loop, context, planner, orchestrator, supervisor | **real** |
| Permission engine, policy, approvals, audit, undo | **real** |
| Task state machine, event bus, world state | **real** |
| Device node protocol + Linux/macOS/Windows adapters | **real** (Windows adapter needs a Windows host to exercise) |
| Memory layers + write policy + conflict detection | **real** (SQLite + hash embeddings by default) |
| Obsidian writer + secret redaction | **real** |
| LLM | `RuleBasedLLM` (offline, deterministic) is default; Anthropic/Ollama adapters included |
| STT/TTS/wake word | text-driven stubs by default; interfaces + adapter slots for whisper/piper/openWakeWord |
| Vision/gesture | interfaces + stubs only (Phase 3) |

Nothing is faked at a layer where faking hides a design problem: permission checks,
verification, audit, and the device round-trip are all genuine.

## 15.3 Run it

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
alembic upgrade head                      # SQLite by default
python -m apps.server                     # core on :8000
python -m apps.node   --name Office-PC    # device node (second terminal)
python -m apps.cli                        # talk to Thursday (third terminal)
```

```
you> Thursday เปิด xcalc
Thursday> รับทราบ กำลังเปิด xcalc บน Office-PC
Thursday> เปิด xcalc แล้ว (ยืนยันแล้ว: pid 48213, หน้าต่าง "xcalc")   [SUCCESS]

you> Thursday ลบไฟล์ทั้งหมดใน Downloads
Thursday> การดำเนินการนี้ต้องขออนุมัติ                                [WARNING]
          action: delete · device: Office-PC · resource: ~/Downloads/* (128 files)
          risk: HIGH · reversible: partial (recycle bin)
          [approve / reject]
```

## 15.4 The automated proof

`tests/integration/test_vertical_slice.py` drives the whole path with an in-process node
and asserts: intent parsed → permission AUTO → action dispatched → **verification observed**
→ task `COMPLETED` → audit row written → undo record present → response composed with
`VoiceMode.SUCCESS`. A companion test forces the verification to fail and asserts Thursday
does **not** claim success (§76).
