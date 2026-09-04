# Codex task prompt

Keep the multi-agent orchestration policy in `AGENTS.md`; do not paste the full policy into every task.

For a normal task, give Codex the actual job and optionally add:

> Follow the active `AGENTS.md` policy. Use both local workers as extensively as useful,
> keep their independent single-task slots productively occupied, adapt task allocation
> based on the quality of their outputs, and keep final integration and verification with Codex.

When replacing a previous task in the same conversation, use an explicit boundary:

> **NEW TASK — stop the previous task.** Do not continue or infer work from the previous task
> unless I explicitly reference it below.

This avoids an orchestration-policy update being mistaken for an instruction to continue an older task.
