# Architecture

LAH treats Codex as the senior orchestrator and local models as low-privilege workers.

```text
                                  Codex
                    architecture / integration / verification
                                    |
                  +-----------------+-----------------+
                  |                                   |
        server_research_agent               laptop_coding_agent
                  |                                   |
              OpenWebUI                           Ollama
                  |                                   |
           general/research                    coding / tests
          + deterministic web                  review / debug
                  |
               SearXNG
```

Each MCP server instance is an independent **worker**.

## Concurrency model

Each worker has one inference slot, enforced by its Windows named mutex:

```text
server_research_agent  -> Local\ServerResearchAgentInference -> max 1 active inference
laptop_coding_agent    -> Local\LaptopCodingAgentInference   -> max 1 active inference
```

Because the mutex names differ, the workers may run concurrently if their compute resources can do so safely.

If several MCP server entries point at the same physical GPU and must not overlap, deliberately give them the **same** `LOCAL_AI_MUTEX_NAME`. They will then serialize against the same lock.

The lock is therefore **per resource/worker group**, not a universal global lock.

## Evidence boundary

A local model may reason about whether current information is required, but a model claiming that it searched the web is not evidence. When deterministic search is enabled, the Python bridge performs the search and records the returned URLs/titles/snippets before giving that evidence to the model.

## Privilege boundary

Local workers do not need shell, SSH or arbitrary filesystem privileges. Codex remains responsible for project modifications, actual command execution, testing, integration, security-sensitive decisions and final output.
