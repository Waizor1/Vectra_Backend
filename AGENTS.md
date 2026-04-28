# AGENTS.md — Vectra Backend

<!-- codex-global-bridge:start -->
## Global Codex efficiency layer

@/Users/waizor/.codex/AGENTS.md

Before applying project-local rules, load `/Users/waizor/.codex/AGENTS.md` and its includes (`MEMORY.md`, `ICM.md`, `WORKFLOW.md`, `TOOLS.md`, `PROJECTS.md`, `RTK.md`, `BROWSER.md`). If absolute includes are not expanded, read that file manually. Project-local rules below stay authoritative for repo-specific commands and boundaries, but must not weaken the global memory, privacy, context-budget, or verification guardrails.
<!-- codex-global-bridge:end -->

## Local scope

- Python/backend repo inside Vectra Connect workspace.
- Also read parent workspace guide: `/Users/waizor/Projects/active/vpn/Vectra Connect Project/AGENTS.md`.
- Before edits, run `git status --short --branch` in this repository and preserve unrelated user changes.
- Read the nearest `README.md`, package/config files, and existing tests before implementation.
- Use repository-native verification commands; if full verification is too expensive or blocked, run a targeted smoke check and state what was not run.
- Do not print secrets from `.env`, deployment output, logs, cookies, tokens, or admin URLs.
