# 12. MVP Scope — Must / Should / Future

Selected against one test: *does the vertical slice (§88) stay honest without it?*

## MUST HAVE — Phase 1 (implemented in this repo)

| # | Capability | Why it cannot wait |
|---|---|---|
| 1 | Thursday Core: conversation → context → intent → plan → execute → verify → report | the spine |
| 2 | Text conversation + streaming | the only interface that always works |
| 3 | Voice loop with pluggable wake/STT/TTS (offline stubs shipped) | §89 demo |
| 4 | Permission Engine + action policy + approvals | retrofitting safety is how safety fails |
| 5 | Device Node Protocol + node with Windows/macOS/Linux adapters | the "OS" in Personal AI OS |
| 6 | SEE→THINK→ACT→**VERIFY** on every device action | §76 |
| 7 | Task system with the full state machine + queue | §41–43 |
| 8 | Layered memory (working/episodic/semantic/preference/procedural) with write policy | §7 |
| 9 | Obsidian vault writer with secret redaction | §8 |
| 10 | Agent orchestrator + capability registry + Supervisor | §14, §18 |
| 11 | `research` + `computer` + `supervisor` agents | §84 |
| 12 | Model Router with FAST/STANDARD/REASONING/LOCAL tiers | §33, cost control |
| 13 | Privacy classifier + Secret Vault | §34, §35 |
| 14 | Audit log (hash-chained) + undo registry | §39, §40 |
| 15 | Event bus + world state projector | §12, §79 |

## SHOULD HAVE — Phase 2

Browser control · file agent + full file ops · screen understanding · Google
(Calendar/Gmail/Drive) · automation engine v1 · mobile remote (voice + approvals) ·
notification intelligence · project brain · decision journal · cross-device continuity.

## SHOULD HAVE — Phase 3

Camera + object detection · gesture (MediaPipe) · spatial memory · multimodal fusion ·
screen annotation · multi-device routing + follow-me · proactive assistant.

## FUTURE — Phase 4+

Knowledge graph at scale · dynamic agents · skill learning + versioning + sandbox testing ·
routine learning · self-evaluation · local/cloud model routing by measured quality ·
AR glasses, wearables, smart home, robots, multi-user, multi-location (§97).

## Explicit non-goals for v1

Multi-user tenancy (single owner, multi-device) · a plugin marketplace · training or
fine-tuning models · replacing the user's IDE/browser/OS UI · an agent-collection UI.
The user must feel there is **one assistant** (§94), so no screen ever asks them to pick one.
