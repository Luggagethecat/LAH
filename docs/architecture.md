# Architecture

The helper is designed as additional compute, not as a replacement primary agent.

```text
Codex
  |-- architecture / integration / verification / final output
  |
  `-- local_ai MCP
       |-- local LLM: first passes, review, tests, summaries, reasoning
       `-- deterministic search: real URLs/titles/snippets for current research
```

The model may request follow-up research, but the Python bridge performs the actual search. A model saying "I searched the web" is not evidence.

The bridge intentionally serializes local inference so multiple Codex tasks do not accidentally load/run several local models concurrently on the same Windows host.
