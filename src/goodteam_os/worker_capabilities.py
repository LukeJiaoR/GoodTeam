"""[INPUT]: 接收各执行器的推理档位约定。
[OUTPUT]: 提供持久化档位、Manager 可选档位与 Worker 后备值。
[POS]: 将 Manager 设置与 Worker 目录分开。
[PROTOCOL]: Update this header on change, then check AGENTS.md

Portable persisted effort values and conservative CLI discovery fallbacks.

Model catalogs narrow these options in the picker; persisted execution snapshots
must remain readable even when a CLI or its catalog is temporarily unavailable.
"""

REASONING_DEPTHS = ("default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

MANAGER_DEFAULT_REASONING_DEPTH = "high"
MANAGER_REASONING_DEPTHS = ("low", "medium", MANAGER_DEFAULT_REASONING_DEPTH, "xhigh")

# Only adapters with a wired runtime argument advertise a fallback control.
BACKEND_REASONING_DEPTHS = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "agy": ("low", "medium", "high"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh"),
}
