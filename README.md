# GoodTeam v2

A local-first AI engineering team orchestrator: **Manager chats with you → dispatches scoped Sub-agents in isolated `git worktree`s → empowers every agent with `SKILL.md` skills → hands back clean diffs for human merge or PR.**

## Core Capabilities

- **Projects**: Import local Git repositories, inspect branch/run status, switch freely, and safely remove projects from GoodTeam without touching local files.
- **Manager Chat & Autopilot**: Persistent `codex app-server` conversation with streaming replies, mid-turn steering, file/image attachments, automatic wake-up when Sub-agents finish, and bounded multi-round autonomous iteration (`goodteam/iter-*` integration branch).
- **Scoped Sub-agents**: Pre-seeded and custom Roles with configurable prompts, `read_only` vs `workspace_write` policies, `write_domains` path validation, tool restrictions, and `codex` / `claude` execution adapters.
- **Unified Skill Library**: Builtin skills, GitHub repo URL installer, drag-and-drop `.zip` uploader, local folder import, and discovery of `.agents/skills/`, `~/.claude/skills/`, `~/.codex/skills/` — genuinely injected into Manager, Codex, and Claude on bound Roles.
- **Delivery, Memory & Providers**: Diff preview and one-click merge to main or `gh` PR creation; project `.goodteam/memory/*.md` knowledge base; multi-provider API connections (`codex` & `claude`) plus native CLI auth fallback and token usage tracking.

## Quick Start

```bash
# Backend (from repo root)
.venv/bin/goodteam doctor
.venv/bin/goodteam serve --port 8788

# Frontend dev (from frontend/)
npm run dev
```

*Legacy v1 code is preserved intact at git tag `archive/v1` (`main`).*
