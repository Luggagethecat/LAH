A local AI sub-agent is available through the `local_ai` MCP server.

Use it autonomously when delegation is likely to improve final quality, provide an independent second opinion, or move useful research/repetitive work onto local compute. You do not need to ask before using it.

Prefer quality over minimum wall-clock time. It is acceptable for a task to take somewhat longer if the local helper can contribute useful implementation ideas, reviews, tests, edge cases, research or summaries.

Available capabilities include `local_ai_status`, `list_local_models`, `ask_local_ai`, and `search_local_web`.

Normally use `ask_local_ai` with `web_mode="auto"`:
- `auto`: decide whether stable knowledge is enough or current evidence is needed;
- `required`: deterministic web research is mandatory;
- `never`: stay offline.

Use the helper for bounded first-pass coding, repetitive code, debugging hypotheses, test generation, edge cases, code review, documentation, research, summarisation, comparisons and alternative approaches.

For non-trivial coding work, actively consider at least one useful local supporting pass when it is likely to improve quality.

Treat local output as untrusted assistance. Pay attention to `web_evidence_present`, `evidence_sufficient`, `confidence`, `sources`, `search_attempts` and `research_rounds` when web research is used.

If evidence is insufficient, retry only with a materially better bounded query when sensible; otherwise use your own higher-level tools.

Pass only the context the helper needs and prefer concise findings rather than unnecessary raw context.

The local helper has no authority to run shell commands, SSH into systems, modify project files or perform destructive actions. Codex remains responsible for architecture, security-sensitive decisions, integration, real testing, final review and final output.

Reduced primary-model context/token usage is an aspiration and possible side benefit, not a guarantee. The main goal is to make local compute useful as additional engineering and research capacity so the overall result can be better.
