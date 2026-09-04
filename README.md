# Local AI Helper for Codex

LAH is a low-privilege MCP helper that lets Codex use local LLM resources as additional coding, research, review and testing capacity.

The main goal is **better final outcomes through useful local compute**. Reducing unnecessary primary-model context/token use is a secondary optimisation, not a guarantee.

## What changed in 0.2.0

- Generic inference backend support: OpenWebUI, direct Ollama and GPT4All.
- Multi-worker setups with one inference slot **per worker/resource**, rather than a universal global lock.
- Separate mutex names allow independent workers on separate compute to run concurrently.
- A tested two-worker reference architecture:
  - `server_research_agent` for research/general reasoning and deterministic web evidence.
  - `laptop_coding_agent` for focused coding, tests, review and debugging.
- A persistent `AGENTS.md` orchestration policy for adaptive delegation and context efficiency.
- Explicit guidance to keep Codex as the senior orchestrator and final verifier.

## Architecture

```text
                                  Codex
                         senior orchestrator
                                  |
                 +----------------+----------------+
                 |                                 |
       server_research_agent             laptop_coding_agent
                 |                                 |
             OpenWebUI                          Ollama
                 |                                 |
       research / review / web           code / tests / debug
                 |
              SearXNG
```

Both workers can run at the same time when they use different mutex names and independent compute resources. Each worker still permits only one active inference of its own.

See `docs/architecture.md` and `docs/multi-agent-orchestration.md`.

## What LAH does

- Gives Codex one or more local AI workers through MCP.
- Delegates bounded first-pass coding, code review, tests, debugging hypotheses, summarisation and research.
- Supports OpenWebUI, direct Ollama and GPT4All inference backends.
- Keeps deterministic web search separate from model claims.
- Lets research-capable workers inspect recorded search evidence and request bounded follow-up searches.
- Keeps Codex responsible for architecture, integration, actual execution/testing, security-sensitive decisions and final output.
- Gives local helpers no shell, SSH or arbitrary filesystem privileges.

## Concurrency model

The mutex is **per worker/resource group**.

```text
server_research_agent -> Local\ServerResearchAgentInference -> max 1 inference
laptop_coding_agent   -> Local\LaptopCodingAgentInference   -> max 1 inference
```

Different mutex names can run concurrently.

If multiple MCP workers share one GPU and must serialize, deliberately give them the same `LOCAL_AI_MUTEX_NAME`.

## Backends

### OpenWebUI

Best fit for the research worker when deterministic OpenWebUI/SearXNG search is desired.

### Direct Ollama

Useful for a focused coding worker without requiring OpenWebUI or an API key.

### GPT4All

Supported through GPT4All Desktop's local OpenAI-compatible API server.

See `docs/alternative-backends.md`.

## Quick start

1. Install Python 3.10+.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `src/local_ai_mcp.py` to a stable local path.
4. Add one or both MCP examples from `config/codex-config.example.toml` to `~/.codex/config.toml`.
5. Keep secrets such as `OPENWEBUI_API_KEY` in environment variables; do not commit them.
6. Copy `prompts/AGENTS.example.md` to `AGENTS.md` in a location that applies to your Codex workspace, then customise worker names/roles as needed.
7. Fully restart Codex so it reloads MCP servers and instructions.
8. Run the checks in `tests/smoke-test.md`.

For ordinary work, keep the persistent orchestration policy in `AGENTS.md` and give Codex only the actual task. See `prompts/codex-project-prompt.md`.

## Security

The local helper intentionally does not receive shell, SSH or arbitrary filesystem access. It researches, drafts, reviews and suggests; Codex remains the privileged orchestrator.

Do not expose unauthenticated local model APIs directly to the public Internet. Treat OpenWebUI API keys as secrets.

See `SECURITY.md`.

## Project maturity

This is an early community/vibe-coded project, iteratively tested with real local hardware. Review the code and test it in your own environment before relying on it.

## Licence

Licensed under the **GNU General Public License v3.0**. See `LICENSE`.
