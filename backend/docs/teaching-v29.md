# Teaching and narration V2.9

Select **Version 2.9 · Writer, reviewer and judge**, or submit
`"teaching_version": "v2.9"` to either lesson-generation API. Version 1 remains
the default. `"v2"` still selects the existing V2.8 first-draft policy, and V3
remains independent. Stored courses are not relabelled or regenerated.

For each source section, difficulty adaptation, requested teacher update, and
spoken script, V2.9 runs:

1. Agent A writes a candidate from the authorized source material.
2. Agent B independently compares that candidate with the source and requested
   language/difficulty, and records concrete findings.
3. Agent C receives the source, A's candidate and B's findings. It independently
   adjudicates the comments and produces one final candidate, preserving valid
   teaching and rejecting mistaken criticism. The source outranks both agents.
4. Agent B reviews C's actual output in a fresh request without its first review
   or C's rationale. A passing check proceeds. A failed or unavailable content
   review retains C's text, records only unresolved final findings and continues
   to a human-review DRAFT. There is no second judge/editing round.

A and C use the configured generation model in separate stateless calls; B uses
the configured review model. These are three roles, not three required model
providers. Checkpoints bind the source, original candidate, policies, language,
model IDs and difficulty. A, the initial review, and C are saved before proceeding;
an interrupted final review reuses C. Source/storage/ownership failures are not
converted into content-review flags. If C never returns usable, correctly
identified output, retain A with an explicit judge-unavailable finding.

The judge cannot invent missing source facts or numerical exercises. Fixed-unit
IDs/order and topic boundaries remain enforced. Actual source-extraction warnings
remain visible. Resolved findings from an earlier canonical draft are not blindly
copied onto a newly judged adaptation. Ordinary translation continues to use the
existing faithful-translation checks and retained-review findings.

The whole spoken script uses the same A/B/C/B flow against the written teaching
before TTS. Narration checks do not constitute listening assessment of audio.
Findings are saved with teaching text before synthesis. Existing render fallback,
source access, publication/lease, archive, cancellation and deletion rules apply.
Teacher updates inherit V2.9 from their parent and retain the judged update after
unresolved review; the historical multi-correction alignment loop is bypassed.
All courses remain DRAFT and still require external human approval.

Implementation: `services/teaching_v29/{models,workflow,prompts,speech}.py`.
Coverage: `tests/test_teaching_v29.py`, `tests/test_teaching_v29_integration.py`,
and frontend version-selection/review tests.
