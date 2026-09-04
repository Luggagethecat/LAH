# Changelog

## 0.2.0

Multi-agent orchestration and generic backend update:

- Added direct OpenWebUI, Ollama and GPT4All inference backends.
- Separated model inference from deterministic search configuration.
- Changed concurrency wording and status metadata from a misleading global lock to one inference slot per worker/resource mutex.
- Documented that separate workers with different mutex names and independent compute may run concurrently.
- Added a tested two-worker Codex configuration example.
- Added a persistent adaptive `AGENTS.md` orchestration policy.
- Added guidance for keeping local output compact to reduce unnecessary Codex context use while prioritising final quality.
- Added dual-worker concurrency and `AGENTS.md` activation smoke tests.
- Preserved the project's low-privilege security boundary.
- Project remains licensed under GNU GPL v3.0.

## 0.1.0

Initial public package:
- OpenWebUI-based MCP helper.
- Offline delegated work.
- Deterministic OpenWebUI/SearXNG research.
- Bounded iterative evidence review.
- Windows single-inference mutex.
- Security-first low-privilege model.
- Documentation for direct Ollama and GPT4All deployment patterns.
