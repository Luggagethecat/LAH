# Multi-agent orchestration

LAH can be run as more than one MCP worker. A useful pattern is one coding-focused worker and one research/review worker on separate compute.

## Why use `AGENTS.md`

Persistent orchestration rules belong in `AGENTS.md`, not in every task prompt.

Putting the policy in `AGENTS.md` gives Codex a stable answer to **how to work**, while each user prompt can remain focused on **what to do**. This also avoids a policy update being mistaken as a continuation of an older task.

Copy:

```text
prompts/AGENTS.example.md
```

to an `AGENTS.md` location that applies to the workspace you use with Codex, then customise the worker names and roles.

## One task per worker, multiple workers in parallel

The tested scheduling model is:

```text
Laptop lane: [task A] -> [task C] -> [task E]
Server lane: [task B] -> [task D] -> [task F]
```

A worker receives only one active inference at a time. When it finishes, Codex can evaluate the result and give that same worker another bounded task even while the other worker is still busy.

This is intentionally different from a global queue:

```text
task A -> wait -> task B -> wait -> task C
```

## Keep local output compact

Local compute can do broad analysis internally, but the result returned to Codex should usually be compact: code, defects, tests, key findings, evidence, unresolved questions, or ranked recommendations.

This allows the local workers to do substantial inexpensive work without forcing Codex to ingest every intermediate thought or repeat the same first-pass analysis.

## Adaptive routing

Worker names are initial specialisations, not permanent truth. Codex should evaluate actual output quality and adjust routing.

Examples:

- a coding worker that consistently produces strong boundary tests should get more testing work;
- a research worker that excels at adversarial review should be reused for final challenge passes;
- if a task is too large for a small-context worker, split it into smaller functions/diffs rather than immediately abandoning the worker.

## Better outcomes first

The priority order is:

1. correctness and quality;
2. security and verification;
3. useful local-compute utilisation;
4. efficient Codex context/token use;
5. speed.

Local-agent usage can reduce unnecessary primary-model work, but token savings are not guaranteed and must never override correctness.

## Explicit new-task boundary

When changing subjects inside an existing Codex conversation, it can help to start with:

```text
NEW TASK — stop the previous task.
Do not continue or infer work from the previous task unless I explicitly reference it below.
```

This is especially useful after a long coding session where the prior task remains active in conversation state.
