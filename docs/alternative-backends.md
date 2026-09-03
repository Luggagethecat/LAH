# Alternative local model backends

The reference MCP bridge uses OpenWebUI because it combines model access and deterministic search cleanly. The same architecture can also use local Ollama or GPT4All for inference.

## Direct Ollama

Ollama's local API normally listens at:

```text
http://127.0.0.1:11434/api
```

List models:

```bash
curl http://127.0.0.1:11434/api/tags
```

Chat:

```bash
curl http://127.0.0.1:11434/api/chat -d '{
  "model": "YOUR_MODEL",
  "messages": [{"role":"user","content":"Reply exactly OK"}],
  "stream": false,
  "keep_alive": "60s"
}'
```

Ollama supports `keep_alive`, which is useful when a GPU is shared with other workloads. `0` requests immediate unload after the response; a short value such as `60s` keeps the model warm briefly.

A direct Ollama adapter for this project needs to replace the OpenWebUI model-list/chat functions with `/api/tags` and `/api/chat`. The MCP security and orchestration model can remain unchanged.

## GPT4All Desktop

GPT4All provides a local OpenAI-compatible API server.

In GPT4All Desktop:
1. Open **Settings**.
2. Go to **Application**.
3. Scroll to **Advanced**.
4. Enable **Local API Server**.
5. The default base URL is:

```text
http://127.0.0.1:4891/v1
```

List models:

```bash
curl http://127.0.0.1:4891/v1/models
```

Chat completion:

```bash
curl http://127.0.0.1:4891/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "YOUR_MODEL",
    "messages": [{"role":"user","content":"Reply exactly OK"}]
  }'
```

GPT4All's local API is designed for local use and normally listens on `127.0.0.1`. If Codex runs on another machine, secure the connection instead of simply exposing the API publicly.

## Web research with direct Ollama or GPT4All

Inference and retrieval are separate concerns.

A useful mixed architecture is:

```text
Codex -> MCP -> Ollama or GPT4All  (local inference)
             -> OpenWebUI/SearXNG (deterministic search)
```

This lets users keep their preferred local inference engine while preserving the project's evidence-based web-research design.

If no deterministic search backend is available, local inference should remain offline and fail cleanly when current external evidence is required rather than allowing the model to pretend it searched.
