# 21. Agents and skills (V9)

    "Thursday ทำรายงานคะแนนแบบที่เคยทำ"
      → retrieve the skill → plan → run several agents → Supervisor validates → report

## The agent bench

| Agent | Does | Holds tools? |
|---|---|---|
| `computer` | device actions through a node, ACT→VERIFY | yes |
| `browser` | role-and-name selectors, never coordinates | yes |
| `research` | memory → vault → web, cheapest and most trustworthy first | yes |
| `data` | statistics over a table, computed in Python | **no** |
| `document` | assembles a report from what earlier steps produced | **no** |

`data` and `document` hold no tools on purpose. They work on what an earlier step already
read, so an analysis cannot widen into a file read nobody planned and a report writer cannot
overwrite a file.

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

## Not built yet

Nine of the spec's fourteen agent types: coding, design, media, calendar, communication,
vision-as-an-agent, automation-as-an-agent, and a separate file agent (the computer agent
covers file work today). Each would be a new `AgentSpec` and an `execute`; none is claimed.

Skill *learning* by observation — watching the owner work and proposing a draft — is not
built. `SkillRegistry.capture` takes a step list; something has to produce it, and today that
is a caller, not an observer. The lifecycle, sandbox, composition and execution around it are
built and tested.

GPU budgeting is declared in the spec and not modelled; `Budget` covers tokens, cost, time,
tool calls and agent calls.
