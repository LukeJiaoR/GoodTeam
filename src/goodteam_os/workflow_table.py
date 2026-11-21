"""[INPUT]: 历史运行的配置名和审批标识。
[OUTPUT]: 读取阶段标签及顾问角色。
[POS]: 历史记录的展示元数据。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from dataclasses import dataclass
from typing import Mapping



@dataclass(frozen=True)
class GateTransition:
    gate: str
    on_approved_next: str
    advisors: tuple[str, ...] = ()


_GATE_TRANSITIONS = {
    "takeover": GateTransition(
        gate="takeover",
        on_approved_next="product_requirement",
    ),
    "prd": GateTransition(
        gate="prd",
        on_approved_next="architect",
    ),
    "rfc": GateTransition(
        gate="rfc",
        on_approved_next="security",
        advisors=("qa",),
    ),
    "staging": GateTransition(
        gate="staging",
        on_approved_next="release",
    ),
    "security": GateTransition(
        gate="security",
        on_approved_next="planner",
    ),
    "release": GateTransition(
        gate="release",
        on_approved_next="retro_memory",
    ),
}

_FIX_GATE_TRANSITIONS = {
    "fix_plan": GateTransition(
        gate="fix_plan",
        on_approved_next="fix_implement",
    ),
    "code_review": GateTransition(
        gate="code_review",
        on_approved_next="fix_qa",
    ),
    "qa": GateTransition(
        gate="qa",
        on_approved_next="fix_close",
    ),
}

@dataclass(frozen=True)
class LoadProfile:
    name: str
    gates: Mapping[str, GateTransition]

    def gate_names(self) -> tuple[str, ...]:
        return tuple(self.gates)

    def approved_next_for_gate(self, gate: str) -> str:
        if gate not in self.gates:
            raise ValueError(f"Unknown approval gate: {gate}")
        return self.gates[gate].on_approved_next


def resolve_profile(name: str = "") -> LoadProfile:
    if name == "fix":
        return LoadProfile("fix", _FIX_GATE_TRANSITIONS)
    return LoadProfile("greenfield", _GATE_TRANSITIONS)


def advisors_for_gate(gate: str) -> tuple[str, ...]:
    transition = _GATE_TRANSITIONS.get(gate)
    return transition.advisors if transition else ()
