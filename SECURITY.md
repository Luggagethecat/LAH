# Security model

The local helper is deliberately low privilege.

It may:
- call a configured local model API;
- list local models;
- perform deterministic web searches through the configured OpenWebUI search backend;
- return text, evidence, reviews and suggestions to Codex.

It does not need and should not be granted:
- arbitrary shell execution;
- SSH access;
- arbitrary filesystem access;
- destructive system-management privileges;
- unnecessary credentials.

Codex remains responsible for project changes, shell commands, integration, actual testing and final verification.

## Network exposure

Prefer localhost or a trusted management network for local model APIs. Do not expose unauthenticated Ollama or GPT4All APIs directly to the public Internet.

Treat OpenWebUI API keys as secrets. A dedicated low-privilege OpenWebUI account is preferable where practical.

## Web research

The helper does not trust a model merely because it says it searched the web. The bridge records deterministic search results and gives those results to the model as evidence.

Search results can still be stale, incomplete or malicious. Important claims require verification by the primary agent.
