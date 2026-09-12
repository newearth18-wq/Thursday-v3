# 62. A school document is arithmetic somebody is held to

Date: Sprint 93

## Status

Accepted. Fills Phase 9 of [the roadmap](../../13-roadmap.md), which was empty: no teacher,
library or event agent existed.

## Context

The brief asks for three specialist agents and lists what each should produce. Between them
the lists name twenty-odd artefacts — lesson plans, worksheets, tests, answer keys, rubrics,
presentations, usage statistics, collection tasks, publicity, timelines, run sheets, stage
cues, checklists, evaluations.

Taken literally that is twenty prompts, and twenty prompts is the failure this project keeps
naming. An agent that asks a model for a rubric gets a rubric: plausible criteria, plausible
weights, and a total that is 95% about a fifth of the time. Nobody checks, because the
document looks exactly like a correct one.

What separates the artefacts worth building from the rest is not how useful they are. It is
**whether somebody else holds the author to them.** An inspector asks for the
ตารางวิเคราะห์ข้อสอบ. A parent asks why the mark was 17 and not 18. A bus leaves at 15:30
whether or not the closing ceremony has finished. Those documents are read as fact by people
who did not write them, and a plausible number in one is worse than no document at all.

## Decision

**Build the artefacts whose correctness is arithmetic, and leave the rest alone.** Seven,
not twenty:

| | The number that must come out right |
|---|---|
| Rubric | Weights total 100; bands map to marks |
| Exam blueprint | Cells match the declared items and marks |
| Lesson plan | Activities fit the period |
| Circulation report | Loans counted by group, month, state |
| Collection analysis | Copies by category; age over the *dated* stock |
| Collection gaps | Held share against a target that totals 100 |
| Run sheet | Clock times from durations; a hard finish |

Everything else on the brief's lists — worksheets, publicity, stage cues, evaluations — is
writing, and writing is what the document and design agents already do. Adding a second
prose path here would be twenty more surfaces with nothing to check them against.

**The numbers go up as evidence, not as claims.** `percentages` and `count` are the keys
`Supervisor._check_arithmetic` recomputes for itself (§18), so every one of these agents
hands over the figures behind its own assertion. A test fakes a teacher result claiming
weights of 40 and 40 and watches the Supervisor fail it — the check has teeth independent of
the agent that produced the output.

**Refuse, never correct.** This is the decision the three agents share and the one most
likely to be argued with, because correcting is friendlier:

- Weights that miss 100 are not rescaled. A rubric quietly normalised is one its author can
  no longer recognise, and they are the person who has to explain it to a parent.
- A lesson that overruns its period is not trimmed. Which activity loses five minutes is
  teaching, and a cut made silently is discovered while teaching it.
- A run sheet that overruns is not shortened. Every item belongs to somebody who was asked
  to prepare it.
- A blueprint contradicting its declared totals is refused with **both** numbers, because
  "the blueprint is wrong" is useless and "you said 40 items and the cells hold 38" is
  something a person can act on in a minute.

**Report the comparison; do not rule on it.** The blueprint shows each topic's share of the
marks beside its share of the teaching periods, and states the gap. It does not call a gap
wrong. Whether a unit taught for eight periods should carry a third of the paper is
professional judgement, and a threshold invented here would be a standard this project made
up and nobody adopted. Same for weeding: the collection analysis reports that 62% of the
science holdings predate the cut-off and stops.

**Three distinctions kept that are each one line of code and one wrong number:**

*Overdue is not returned-late.* One is a list to chase, the other is a term reviewed.
Collapsing them undercounts or overcounts depending which way you lean.

*Per-pupil needs the roll.* Given no enrolment figure the ratio is omitted rather than
divided by the children who happened to borrow — which flatters a library exactly in
proportion to how few used it.

*An unrecorded publication year is not a young book.* Age shares are taken over the dated
stock, with the undated count reported beside them.

**A clash is a computed fact, not a field.** A teacher rostered onto two consecutive run
sheet items has zero minutes to move. That is invisible in a list — the rows are far apart
on the page and only the computed times collide — so it is found rather than asked for, and
named in the summary line rather than buried in a field nobody opens.

## Consequences

All three agents are READ with no tools. They compute and return a document; saving one is
`file.write` through the ordinary permission path, by whoever asked for it. All three are
`local_only`: loan records name children and marks name pupils, and neither leaves.

**What is deliberately not built.** No worksheet generator, no publicity copy, no stage
script, no evaluation form. Those are prose, they have no checkable property, and the brief
listing them is not a reason to add a surface that can only be wrong in ways nothing
notices. A teacher wanting a worksheet has the document agent.

The `thursday_school` package holds the arithmetic and the agents stay thin, so the next
school artefact is a module and a case rather than a new agent — and the refusals live with
the arithmetic that justifies them rather than in the agent that reports them.
