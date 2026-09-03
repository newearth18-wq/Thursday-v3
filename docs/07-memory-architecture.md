# 7. Memory Architecture (Second Brain)

## 7.1 Rule zero

**A vector database is not a memory system** (§7). Embeddings are one retrieval index over
one part of memory. Thursday's memory is layered, typed, sourced, and curated.

Equally: **conversation context ≠ long-term memory** (§5). The chat window is working
material that expires. Memory is a deliberate write.

## 7.2 Layers

| Layer | Holds | Lifetime | Written by |
|---|---|---|---|
| **Working** | facts scoped to the current task | task end / 24 h | automatic |
| **Episodic** | what happened: actions, outcomes, decisions | 400 d, decays | automatic on task completion |
| **Semantic** | facts and knowledge | indefinite | curated |
| **Preference** | how the user likes things done | indefinite, supersedable | inferred + confirmed |
| **Procedural** | step sequences that worked | indefinite | on verified success |
| **Project** | scoped brain per project (§54) | project lifetime | mixed |
| **Knowledge Base** | documents, notes, files | until deleted | indexing |

## 7.3 Write policy — what gets remembered (§96 "don't store every message")

`MemoryManager.should_write()` requires **at least one**:
- the user asserted a durable fact or preference ("call me X", "always use Y")
- a decision was made (→ also Decision Journal, §55)
- a task completed with a reusable procedure
- a correction to an existing memory
- an entity/relationship new to the knowledge graph

and **none** of:
- `sensitivity >= SECRET`
- the content matches a credential/token pattern
- `memory_disabled` privacy zone is active (§68)
- it is small talk, an ephemeral pointer, or already stored (dedupe by embedding ≥0.95 + key match)

## 7.4 Quality control (§11)

Every record carries `importance`, `confidence`, `source`, `source_ref`, `valid_from/to`.

```
retrieval_score = 0.35*semantic_sim + 0.20*recency_decay + 0.20*importance
                + 0.15*confidence   + 0.10*usage_frequency
recency_decay   = exp(-age_days / half_life[layer])
```
Half-lives: working 0.5 d, episodic 45 d, semantic ∞, preference ∞, procedural 180 d.

**Conflict handling.** New information contradicting old is never blended. The manager
writes a `memory_conflicts` row and Thursday answers with both:

> "จากบันทึกเดิม (7 มี.ค. จากอีเมล) ค่านี้คือ 42 แต่ไฟล์ที่คุณเปิดวันนี้ระบุ 45
>  ผมยังไม่ได้รวมสองค่านี้เข้าด้วยกัน — ให้ใช้ค่าใหม่เป็นหลักหรือไม่"

Auto-supersede happens only when: same key, new source ranks higher
(`user > file > email > web > agent-inference`), and new confidence ≥ old + 0.15.

## 7.5 Retrieval pipeline

```
query → intent+entity extraction → parallel:
        ├ vector search (per-layer k, filtered by project/time)
        ├ structured lookup (keys, entities, tasks)
        ├ knowledge-graph traversal (≤2 hops)
        └ document/file index
      → merge → dedupe → rescore → budget-trim (token-aware) → ContextPackage.memories
```

## 7.6 Obsidian as the human-readable brain (§8)

Postgres is the machine's memory; the vault is the human's. They are synced, not merged.

```
THURSDAY VAULT/
  00 Inbox/     01 Projects/  02 Areas/   03 Knowledge/  04 People/
  05 Meetings/  06 Decisions/ 07 Skills/  08 Daily/      09 Archive/
```
Notes carry YAML frontmatter (`thursday_id`, `layer`, `source`, `confidence`, `updated`)
so a note edited by hand can be re-ingested. **Never written to the vault:** passwords,
API keys, raw tokens, session secrets — the `SecretRedactor` runs on every vault write and
the writer refuses on a hit.

## 7.7 Knowledge graph (§10) and timeline (§56)

Entities and relationships are extracted on task completion and document indexing, with a
confidence and a source. The timeline view is `events + tasks + decisions` ordered by time,
which answers "what did we do last week", "what changed this month", "what have we already
tried" without a separate store.

## 7.8 Spatial memory (§26)

Vision writes `observations` rows (label, confidence, location context, timestamp) — never
frames by default. Answers are always framed as last-known-sighting, with time and
confidence, never as a guarantee:

> "ครั้งล่าสุดที่ระบบเห็นคือบนโต๊ะทำงาน เวลา 18:22 (ความมั่นใจ 0.92) — ยังไม่ยืนยันว่าตอนนี้ยังอยู่ตรงนั้น"


---

## V5 additions

### Retrieval score

```
0.30 · semantic similarity
0.15 · recency          (half-life by layer; infinite for SEMANTIC and PREFERENCE)
0.18 · importance
0.15 · project relevance
0.14 · source confidence (trust by provenance × the source's own confidence)
0.08 · usage             (how often this memory has actually proved useful)
+0.15 if pinned
```

Project relevance is a **soft** term, distinct from the `project_id` hard filter. Asked how
these reports are usually written, this project's answer comes first — but a general habit
is still a real answer, and filtering it out would hide the thing that shaped it. Use
`prefer_project_id` for the preference and `project_id` when the question genuinely does not
extend beyond one project.

Source confidence is weighted by `SOURCE_TRUST`: the owner asserting something outranks an
agent's inference about the same subject even when the inference is more confident in
itself. Confidence measures how sure a source is, not how much it is worth believing.

### Procedural memory is applied, not merely recalled

A "remember that…" statement describing *how work should be done* is filed as `PROCEDURAL`,
and `Planner._apply_remembered_procedures` attaches it to the steps that produce something.
`Plan.following` records what is being followed, so the owner can see why the output looks
the way it does and correct the memory rather than the output. See
[ADR 0018](architecture/decisions/0018-memory-that-changes-behaviour.md).

### Explicit memory commands

| The owner says | What happens |
|---|---|
| "จำไว้ว่า X" / "remember that X" | Stored, at the layer the statement implies, with the owner as its source |
| "ลืมเรื่อง X" / "forget what I said about X" | Deleted — embedding similarity *or* literal overlap, at a higher threshold than recall uses |
| "อย่าจำเรื่องนี้" / "don't remember this" | This conversation's writes removed, and further implicit writes stopped |
| "forget it" | Refused — a figure of speech far more often than an instruction |

None of these override §35: being told to remember an API key still does not store one.
A later explicit "remember X" after a suppression is honoured; suppression was about what
had just been said, not a standing gag. See
[ADR 0019](architecture/decisions/0019-forgetting-is-a-first-class-operation.md).

### The vault mirror

Postgres is where Thursday remembers; Obsidian is where the *owner* does — plain Markdown
they can read, edit, search and take with them if Thursday is ever switched off. Mirroring
everything from one into the other would ruin the second: a vault with a note for every
episodic trace is a vault nobody opens.

So `VaultMirror` is selective, and the rule is a question about the reader rather than about
the data: *would a person, six months from now, be glad this was written down?* That means
the durable layers (semantic, procedural, preference, project, knowledge) above an
importance floor, or anything the owner pinned. Working scratch and episodic traces stay in
the database.

It subscribes to `memory.created` rather than being called inside the write path, so the
memory manager does not need to know the vault exists, and switching the vault off (§68)
removes a subscriber instead of leaving a dead branch. It reads the record back by id: the
event carries an id and a layer, and putting memory *content* on the bus would hand every
subscriber the text of every memory for no gain.

### Vault operations

| Call | Folder | Notes |
|---|---|---|
| `inbox` | 00 Inbox | Something with no home yet |
| `project_page` | 01 Projects | Goal, status, sections |
| `memory_note` | 03 Knowledge | What the mirror writes |
| `person_note` | 04 People | |
| `meeting_note` | 05 Meetings | Attendees and notes |
| `decision_log` | 06 Decisions | Decision, reason, alternatives, impact (§55) |
| `skill_note` | 07 Skills | Numbered steps |
| `daily_note` | 08 Daily | Appends by time rather than overwriting |
| `archive` | 09 Archive | Moves, never deletes |
| `update_note` | any | Merges frontmatter, so `thursday_id` survives |
| `link_notes` | any | One direction, idempotent — Obsidian shows the backlink |
| `tag_note` | any | Tags are a set |

Every one of them goes through the same write path, so the credential refusal applies to all
of them, and every one is a no-op when the vault is disabled rather than an error.

Archiving is not forgetting. The vault is the owner's notebook, and Thursday removing pages
from it is not its call — memory deletion is handled by the memory manager, where "forget
this" means gone.
