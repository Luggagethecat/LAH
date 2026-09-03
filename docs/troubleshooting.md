# Troubleshooting

## MCP configured but not visible

Check Python, the MCP package, script path, environment variables and restart Codex fully after editing `~/.codex/config.toml`.

A stdio MCP server may appear to sit silently when launched manually because it is waiting for its host.

## Status is healthy but inference times out

Health/model discovery does not prove a model can generate. Test the model backend directly.

For an Ollama-backed model:

```bash
curl http://127.0.0.1:11434/api/chat -d '{
  "model":"YOUR_MODEL",
  "messages":[{"role":"user","content":"Reply exactly OK"}],
  "stream":false
}'
```

If direct generation hangs, troubleshoot Ollama/GPU/backend state before changing MCP code.

## Web research returns unrelated evidence

Check `evidence_sufficient`, `sources`, `search_attempts` and `research_rounds`. Very recent material may not yet be indexed upstream. The correct behaviour is a clean failure/escalation, not an invented URL.
