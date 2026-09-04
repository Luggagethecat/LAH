# Local model backends

The bridge supports three inference backends:

- `openwebui`
- `ollama`
- `gpt4all`

Set the backend with:

```text
LOCAL_AI_BACKEND=openwebui
```

or `ollama` / `gpt4all`.

## OpenWebUI

OpenWebUI is the reference research-oriented backend because it can provide both model access and deterministic web retrieval.

Typical base URL:

```text
http://127.0.0.1:3000
```

Set `OPENWEBUI_API_KEY` in the host environment or forward it through Codex. Do not hard-code real API keys in a public repository.

## Direct Ollama

Ollama normally listens at:

```text
http://127.0.0.1:11434
```

The bridge uses:

```text
GET  /api/tags
POST /api/chat
```

Useful tuning variables:

```text
OLLAMA_KEEP_ALIVE=10m
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=1024
```

A modest `OLLAMA_NUM_CTX` can be useful on older/smaller GPUs. `keep_alive` amortises cold model-load time when repeated delegated tasks are expected.

## GPT4All Desktop

GPT4All can expose an OpenAI-compatible local API, commonly at:

```text
http://127.0.0.1:4891/v1
```

Enable the Local API Server in GPT4All Desktop, then use:

```text
LOCAL_AI_BACKEND=gpt4all
GPT4ALL_URL=http://127.0.0.1:4891/v1
```

## Search is separate from inference

`SEARCH_BACKEND` is independent from `LOCAL_AI_BACKEND`.

For example, a worker may use direct Ollama for inference while still using OpenWebUI/SearXNG for deterministic search:

```text
LOCAL_AI_BACKEND=ollama
SEARCH_BACKEND=openwebui
SEARCH_OPENWEBUI_URL=http://YOUR-SERVER:3000
```

Or a coding-only worker can disable search:

```text
SEARCH_BACKEND=none
```

When current evidence is required but no deterministic search backend is configured, the bridge should fail cleanly rather than allowing a local model to pretend it searched.

## Network exposure

Prefer localhost or a trusted management network. Do not expose unauthenticated Ollama or GPT4All APIs directly to the public Internet.
