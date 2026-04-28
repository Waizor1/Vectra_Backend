# RTK.md — Vectra Backend

<!-- codex-global-rtk:start -->
## Global RTK shell-output layer

@/Users/waizor/.codex/RTK.md

Load `/Users/waizor/.codex/RTK.md` first for the current global RTK policy: prefer `rtk` for shell commands, keep terminal output bounded, and fall back to raw commands only when exact output or tool behavior requires it. Local notes below add project-specific routing and do not replace the global RTK rules.
<!-- codex-global-rtk:end -->


## Local RTK notes

- Python/backend repo inside Vectra Connect workspace.
- Also read parent `/Users/waizor/Projects/active/vpn/Vectra Connect Project/RTK.md` when working from this subrepo.
- Before package-manager/build/test commands, inspect the repo lockfile and scripts.
- Prefer `rtk git status --short --branch`, `rtk npm run build`, `rtk pytest -q`, `rtk rg ...`, or `rtk bash -lc "..."` where appropriate.
- Do not use RTK to hide errors; if a compact command fails, rerun the smallest relevant raw command needed to inspect the failure.
