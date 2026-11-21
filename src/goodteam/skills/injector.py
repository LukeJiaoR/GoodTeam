"""Unified runtime Skill injector for Manager, Codex CLI, and Claude CLI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from goodteam.config import runtime_skills_dir
from goodteam.skills.models import parse_skill_markdown, safe_subpath
from goodteam.store import Store


@dataclass(frozen=True)
class SkillInjection:
    prompt_text: str
    staged_dir: Path | None
    required_ids: tuple[str, ...]
    on_demand_ids: tuple[str, ...]
    staged_paths: dict[str, Path]


def resolve_role_skills(store: Store, role: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required: list[dict[str, Any]] = []
    on_demand: list[dict[str, Any]] = []
    seen: set[str] = set()
    for binding in role.get("skill_bindings") or []:
        sid = str(binding.get("skill_id") or "")
        if not sid or sid in seen:
            continue
        skill = store.get_skill(sid)
        if not skill or not skill.get("enabled"):
            continue
        seen.add(sid)
        if binding.get("mode") == "on_demand":
            on_demand.append(skill)
        else:
            required.append(skill)
    return required, on_demand


def stage_and_render(
    store: Store,
    role: dict[str, Any],
    run_id: str,
    *,
    state_dir: str | Path | None = None,
) -> SkillInjection:
    required, on_demand = resolve_role_skills(store, role)
    if not required and not on_demand:
        return SkillInjection(prompt_text="", staged_dir=None, required_ids=(), on_demand_ids=(), staged_paths={})

    stage_root = (runtime_skills_dir(state_dir) / run_id).resolve()
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    staged_paths: dict[str, Path] = {}
    sections: list[str] = ["## Bound Skills"]

    for group_label, items, include_body in (
        ("Required Skills (follow strictly)", required, True),
        ("On-Demand Skills (read SKILL.md when task-relevant)", on_demand, False),
    ):
        if not items:
            continue
        sections.append(f"### {group_label}")
        for skill in items:
            src = Path(skill["path"]).resolve()
            dest = stage_root / skill["id"].replace(":", "__")
            if src.is_dir():
                shutil.copytree(src, dest, symlinks=False)
            staged_paths[skill["id"]] = dest
            skill_md = safe_subpath(dest, "SKILL.md")
            manifest = parse_skill_markdown(skill_md.read_text(encoding="utf-8"), fallback_name=skill["name"])
            if include_body:
                sections.append(
                    f"#### Skill: {manifest.name} (`{skill['id']}`)\n"
                    f"Resource directory: `{dest}`\n\n{manifest.body.strip()}"
                )
            else:
                sections.append(
                    f"- **{manifest.name}** (`{skill['id']}`): {manifest.description} "
                    f"— path: `{skill_md}` (dir: `{dest}`)"
                )

    return SkillInjection(
        prompt_text="\n\n".join(sections).strip(),
        staged_dir=stage_root,
        required_ids=tuple(s["id"] for s in required),
        on_demand_ids=tuple(s["id"] for s in on_demand),
        staged_paths=staged_paths,
    )


def cleanup_staged_skills(staged_dir: Path | None) -> None:
    if staged_dir and staged_dir.exists():
        shutil.rmtree(staged_dir, ignore_errors=True)
