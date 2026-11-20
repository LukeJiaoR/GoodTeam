# GoodTeam v2 Agent Guidelines

GoodTeam v2 is a lean, local-first AI team orchestrator built around one core loop:

1. **Manager Chat**: Persistent `codex app-server` thread with streaming output, mid-turn steering, attachments, and autonomous multi-round iteration.
2. **Scoped Sub-agents**: Custom or builtin Roles (`prompt + execution_policy + write_domains + allowed_tools + adapter/provider/model + skills[]`) running in isolated `git worktree`s via `codex` or `claude` CLI.
3. **Skill Empowerment**: Standard `SKILL.md` packages (builtin, GitHub URL, `.zip` upload, local folder, or auto-discovered) explicitly bound per Role and injected cleanly into Manager, Codex, and Claude without polluting worktree git status.
4. **Human Delivery Gate**: Sub-agents never mutate `main` directly; humans review diffs and explicitly merge locally or push/open a PR via `gh`.

## Code & Architecture Rules

- Keep Python modules focused and modular under `src/goodteam/` (target ≤ 300 lines per file, single-purpose functions ≤ 50 lines).
- Use parameterized SQLite queries only (`src/goodteam/store.py`). Never interpolate untrusted inputs into SQL or shell commands (`shell=False` list args always).
- Never store or return plaintext API keys in API responses or logs (`src/goodteam/providers.py` stores keys `0600` locally and injects via process env only).
- Never place Skill files inside a tracked `git worktree` directory; stage runtime skills under `<state_dir>/runtime-skills/<run_id>/` and pass `--add-dir`.
- Preserve untracked workspace directories (`output/`, `state/`, `artifacts/`, `projects/`, `.worktrees/`, `.venv/`).

## Verification Commands

- Backend tests & lint: `.venv/bin/pytest && .venv/bin/ruff check src tests`
- Frontend checks: `cd frontend && npm run test:unit && npm run build`
