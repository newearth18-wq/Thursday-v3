# 21. Agents and skills (V9)

    "Thursday ทำรายงานคะแนนแบบที่เคยทำ"
      → retrieve the skill → plan → run several agents → Supervisor validates → report

## The agent bench

Thirteen specialists plus the Supervisor. The **ceiling** column is the interesting one:
most of the bench is READ, and that is a design rather than an accident — see below.

| Agent | Does | Ceiling |
|---|---|---|
| `computer` | device actions through a node, ACT→VERIFY | MODIFY |
| `browser` | role-and-name selectors, never coordinates | MODIFY |
| `research` | memory → vault → web, cheapest and most trustworthy first | READ |
| `data` | statistics over a table, computed in Python | READ |
| `document` | assembles a report from what earlier steps produced | READ |
| `file` | questions *about sets of files*: what is here, what is newest, what is duplicated | READ |
| `coding` | reads code and proposes a patch — never applies or runs one | READ |
| `vision` | looks at the camera or screen through `VisionService` | OPEN |
| `calendar` | reads time, finds conflicts, prepares entries | READ |
| `communication` | **drafts** messages; there is no path from here to a sent one | MODIFY |
| `automation` | explains what runs by itself; proposes, never enables | READ |
| `design` | a design **specification** — tokens, layout, components. Not an image | READ |
| `media` | identifies files from their headers. Cannot edit them | READ |

Several hold **no tools at all** (`data`, `document`, `vision`, `calendar`, `communication`,
`automation`, `design`). They work on what an earlier step read or on a service that owns
its own consent, so an analysis cannot widen into an unplanned file read, a report writer
cannot overwrite a file, and an agent cannot open a camera around `VisionService`.

Three boundaries are worth naming because the obvious design goes the other way:

- **`coding` proposes, never applies.** A proposed patch gets read before it lands; an
  applied one gets read afterwards, if at all. It holds no shell either — an agent that
  "just checks the tests pass" is asking for approval of arbitrary execution on the strength
  of a summary nobody can verify.
- **`file` finds, never deletes.** Finding duplicates and removing them are different jobs,
  and the second is one bad grouping away from deleting the only copy of something.
- **`communication` drafts, never sends.** Every other action here has an undo or a
  verification. A message in someone else's inbox has neither.

Each declares what the router needs to choose without asking the owner — capabilities, tools,
cost, latency, privacy profile, permission ceiling, default budget — and now also
`output_schema`, the fields its output always carries.

## A DAG that passes data

`JobContract.upstream` carries what a step's dependencies produced, keyed by step name.
Without it a plan is a *sequence*, not a DAG: gather → analyse → report ran in the right
order and the analyse step never saw the data. It is a separate field from `inputs` so an
agent can always tell what the planner asked for apart from what another agent handed it —
the second is a result, and results are wrong sometimes.

Independent steps run together (`asyncio.gather` over the DAG frontier); dependent ones wait.

## Every figure was computed

The tempting design hands the table to a model and asks for the report. It produces good
prose and it is wrong, for a reason that only shows at the worst moment: a model asked for an
average returns a *plausible* number, and a plausible wrong number survives every check
except the one nobody ran — in a document someone forwards.

So ([ADR 0025](architecture/decisions/0025-a-figure-in-a-report-was-computed.md)):

- `DataAgent` computes every figure in Python and returns the **rows** with them, so
  `Supervisor._check_arithmetic` can confirm the count matches and the percentages sum to 100.
- `DocumentAgent` inserts figures from upstream and shows them to the model as fixed text.
- **A report must carry its figures.** `grounded()` asks whether any computed number reached
  the prose. Running offline, the model once returned "I cannot answer analytical questions
  right now" — non-empty, so it passed `output.document is not empty`, and the owner got a
  document shaped like a report with none of the analysis in it and every check saying PASS.
  Where the text is not grounded, both agents fall back to a plainer output built from the
  figures, which is true by construction.

Thursday can therefore produce a correct report with no model available at all. It is duller.
It is right.

## Running something Thursday learned

A skill is a workflow captured from a demonstration — which is to say **code the owner did
not write and nobody reviewed**
([ADR 0026](architecture/decisions/0026-a-learned-skill-is-code-nobody-reviewed.md)).

```
draft ──(sandbox tests pass)──► testing ──(owner approves)──► active
                                                                 │
                                                      v2, v3 … ──┘  rollback available
```

Retrieval is lexical — character trigrams over name, tags and description, because Thai is
written without spaces and "คะแนน" inside a longer word is a real mention. Not embeddings: a
skill run is an *action*, so the difference between the right skill and a nearly-related one
is not a difference of degree, and lexical matching fails visibly rather than plausibly.

The comparison direction is chosen per field. A short **name** is asked how much of it appears
in the sentence; a **description** is asked the reverse. The description carries cross-language
matches — a skill called "School Grade Report" is asked for in Thai.

| Situation | What happens |
|---|---|
| One skill matches | It runs; the plan's objective is the skill's name |
| Two match equally | A question naming both. The sentence did not identify one of them |
| A match exists but is not approved | Thursday says so. Planning something else quietly would do work nobody asked for |
| Nothing matches | Falls through to ordinary planning, where a remembered instruction applies (ADR 0018) |

That last row matters: "แบบเดิม" means two things — *run the skill* and *the way I told you*
— and which applies depends on what Thursday actually knows.

Supplied inputs merge **under** each step's own arguments. A caller that could overwrite them
could turn "read this file" into "delete that one" while still calling it by the skill's
trusted name.

A demonstration converts to a **linear** chain. A sequence is the only dependency structure
honestly recoverable from watching; guessing that adjacent steps were independent would
reorder someone's workflow on no evidence.

## Composition

`File Search + Data Analysis + Report Generation` → `Grade Report`, and the result is a
**draft**. Three workflows approved separately are not one workflow that does all three in
sequence, and the second is what the owner would be agreeing to. Only ACTIVE skills compose;
chaining a draft in would give it a way to run that the lifecycle exists to deny it. Each
step records which skill it came from.

## Supervision

Unchanged from V1 in shape, reachable for the first time in substance: the arithmetic checks
existed and nothing produced the `percentages`, `count` and `rows` keys they read.

    ran → schema → verification flags → provenance → arithmetic → criteria → (LLM critique)
                                                            ↓
                                            PASS · RETRY · ESCALATE

RETRY is for a failure more effort could plausibly fix; ESCALATE for one needing a decision,
a permission, or a person.

## Learning a workflow by watching

`SkillRegistry.capture` takes a step list, and `SkillObserver` is what produces one. It
watches completed **plan steps** — not raw tool calls, which would miss every agent job and
see a one-step workflow where there were three — and proposes a draft when it has seen the
same ordered sequence more than once.

It is not `RoutineLearner`, which sits next door and looks similar. That one finds habits in
*time* ("you run these around nine"); this one finds workflows in *order*. "Read, filter,
report" and "report, filter, read" contain identical steps and only one is a thing anybody
does.

| Rule | Why |
|---|---|
| Only runs where every step verified | A failed run teaches a workflow that reliably does not work |
| Only sequences seen twice or more | Once is an event, not a routine |
| Arguments that varied become **inputs** | What turns a recording into something reusable — and it is free, it falls out of comparing the runs |
| The result is always a **draft** | It was watched, not reviewed. The sandbox and approval exist for exactly this |
| Arguments are redacted at the boundary | A workflow carrying a credential in its steps *is* a stored credential (§35) |

`SkillRegistry.adopt` turns a proposal into that draft, with the varying arguments written
down as its input schema.

## Not built yet

**Real calendar and mail accounts.** `CalendarProvider` and `MessageProvider` are ports with
local adapters — real behaviour, nothing leaving the machine, and *not the owner's actual
calendar or inbox*. A Google or Outlook adapter is a new class behind the same protocol.

**Media editing of any kind.** There is no Pillow, no ffmpeg and no codec here, so the media
agent identifies files from their headers and says outright that it cannot convert, resize
or generate. Editing belongs behind a port when the libraries exist; it is not bolted onto
that agent and not claimed.

**Images from the design agent.** It writes the specification — tokens, layout, components,
with contrast actually computed — which is the half of design work that survives being
written down. It does not draw.

GPU budgeting is declared in the spec and not modelled; `Budget` covers tokens, cost, time,
tool calls and agent calls.
