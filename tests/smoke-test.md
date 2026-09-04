# Smoke tests

## Single-worker checks

1. Call `local_ai_status`.
   - Confirm the intended `backend` and `backend_url`.
   - Confirm `one_inference_per_worker_enforced: true`.
   - Confirm the reported `mutex_name`.

2. Call `list_local_models`.
   - Confirm the configured model is actually available.

3. Call `ask_local_ai(web_mode="never")`.
   - Ask for a tiny pure function.
   - No files should be modified by the local worker.

4. For a worker with deterministic search enabled, call `search_local_web`.
   - Find a known official documentation page.
   - Inspect returned URLs/titles/snippets.

5. Call `ask_local_ai(web_mode="required")` on a search-capable worker.
   - Ask a current factual question.
   - Require source evidence.

6. Failure test.
   - Ask for a deliberately obscure/nonexistent current page.
   - The helper should fail cleanly rather than invent a URL.

## Dual-worker concurrency check

Configure two MCP workers with different mutex names and independent compute.

Ask Codex to:

- start a bounded coding task on `laptop_coding_agent`;
- without waiting, start a different bounded task on `server_research_agent`;
- when one finishes first, evaluate its output and give that same worker a second bounded task while the other worker continues.

Expected result:

- both workers can be active concurrently;
- each worker has at most one active inference of its own;
- a worker's second task starts only after its first task completes;
- one worker does not wait merely because the other worker is busy.

## `AGENTS.md` activation check

Ask Codex:

> Do not modify files or start a task. Read the active project instructions and confirm the two worker names, one-slot-per-worker rule, parallel-use rule, adaptive routing rule, context-efficiency rule, and that Codex retains final verification.

If Codex cannot see the policy, check that `AGENTS.md` is in a directory whose scope includes the current workspace.
