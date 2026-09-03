# Codex Local AI Helper

A low-privilege MCP helper that lets Codex use local LLM resources for bounded coding, review, research, summarisation, test ideas and other supporting work.

The goal is **better use of local compute and better final results**. Reduced primary-model context or token usage may be a side benefit in some workflows, but this project does not promise or guarantee token savings.

## What it does

- Gives Codex a local AI sub-agent through MCP.
- Lets Codex delegate first-pass coding, code review, test ideas, debugging hypotheses, summarisation and research.
- Supports deterministic web research through OpenWebUI/SearXNG.
- Lets the local model inspect evidence and request bounded follow-up searches.
- Keeps Codex responsible for architecture, integration, real testing, security-sensitive decisions and final output.
- Enforces one local LLM inference at a time on Windows.
- Gives the local helper no shell, SSH or arbitrary filesystem privileges.

```text
                         Codex
                   senior/orchestrator
                          |
             +------------+------------+
             |                         |
        primary work                 local_ai MCP
                                       |
                          +------------+------------+
                          |                         |
                     local model              deterministic
                    inference                 web research
                          |                         |
                    OpenWebUI                  SearXNG
                          |                         |
                          +------------+------------+
                                       |
                              concise findings
                                       |
                                      Codex
                               reviews/integrates
```

## Reference setup

The included bridge is built around **OpenWebUI** because it provides:

- model discovery;
- an OpenAI-compatible chat API;
- API-key authentication;
- a convenient deterministic web-search endpoint when OpenWebUI is connected to SearXNG or another supported search engine.

The local models themselves may be hosted by Ollama behind OpenWebUI.

## Alternatives: Ollama or GPT4All directly

You do not have to use OpenWebUI for local inference.

- **Ollama** exposes a native local API, normally at `http://127.0.0.1:11434/api`.
- **GPT4All Desktop** can expose an OpenAI-compatible local API, normally at `http://127.0.0.1:4891/v1` after enabling its Local API Server.

See `docs/alternative-backends.md` for integration patterns and example requests.

The current public bridge uses OpenWebUI for the full feature set, especially deterministic web research. Direct Ollama/GPT4All adapters are intentionally documented separately so users can choose a simpler inference-only deployment or contribute backend adapters without weakening the security boundary.

## Quick start

1. Install Python 3.10+.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `config/env.example` values into your user environment and set your own OpenWebUI address/API key.
4. Add the example MCP block from `config/codex-config.example.toml` to `~/.codex/config.toml`.
5. Fully restart Codex.
6. Run the tests in `tests/smoke-test.md`.
7. Use `prompts/codex-project-prompt.md` while evaluating the helper.

## Security

The helper intentionally does not receive shell, SSH or arbitrary filesystem access. It researches, drafts, reviews and suggests; Codex remains the privileged orchestrator.

Do not expose unauthenticated local model APIs directly to the public Internet. Treat OpenWebUI API keys as secrets.

See `SECURITY.md`.

## Project maturity

This is an early community/vibe-coded project, iteratively tested with real local hardware. Review the code and test it in your own environment before relying on it.

## Licence

GNU GENERAL PUBLIC LICENSE.
