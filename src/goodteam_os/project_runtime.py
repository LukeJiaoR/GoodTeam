"""[INPUT]: 项目标识与配置中的仓库路径。
[OUTPUT]: 解析项目运行根目录和规范项目身份。
[POS]: 项目配置、会话与批次共用的运行定位。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from pathlib import Path


def resolve_project_runtime_root(repos: dict[object, object]) -> Path | None:
    roots = [
        Path(str(value))
        for value in repos.values()
        if value and Path(str(value)).is_dir()
    ]
    if not roots:
        return None
    parents = {root.parent for root in roots}
    if len(roots) > 1 and len(parents) == 1:
        return next(iter(parents))
    return roots[0]


def normalize_project_key(value: str) -> str:
    key = "".join(char.upper() if char.isalnum() else "-" for char in value.strip())
    key = "-".join(part for part in key.split("-") if part)
    if not key:
        raise ValueError("Project key is required.")
    return key
