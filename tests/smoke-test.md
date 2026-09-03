# Smoke tests

1. `local_ai_status` — confirm backend reachability.
2. `list_local_models` — confirm models are visible.
3. `ask_local_ai(web_mode="never")` — ask for a tiny pure function; no files should be modified.
4. `search_local_web` — find a known official documentation page and inspect returned URLs/titles/snippets.
5. `ask_local_ai(web_mode="required")` — ask a current factual question and require source evidence.
6. Failure test — ask for a deliberately obscure/nonexistent current page; the helper should fail cleanly rather than invent a URL.
