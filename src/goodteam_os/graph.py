from pathlib import Path

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.state import AgileDevState


def run_until_next_gate(state: AgileDevState, workspace: Path) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    prd_path = store.write_markdown(
        state.run_id,
        "prd",
        "prd.md",
        f"# PRD\n\n## Background\n\n{state.idea}\n",
    )
    state.artifacts["prd"] = str(prd_path)
    state.current_node = "prd_review_gate"
    state.next_node = gate_next_node(state, gate="prd", approved_next="architect")
    return state
