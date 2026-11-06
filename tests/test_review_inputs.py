"""The tool-free local Reviewer still receives the complete inline input."""
from copy import deepcopy
import json
from types import SimpleNamespace

from goodteam_os.skills.codex_local import ADAPTER
from goodteam_os.skills.review_execution import _input


def test_text_only_review_preserves_inline_sources_and_original_contract():
    base = "Exact task: review the approved change; keep the existing scope.\n"
    private = {"original_job_input": {"files": {"review_prompt_base.md": base},
                                      "operator_path": "/private/operator-only"},
               "required": [], "catalog": []}
    provider = SimpleNamespace(private_input=lambda _ref: deepcopy(private),
                               limits=SimpleNamespace(max_input_bytes=1024 * 1024))
    record = {"runtime_session": {"adapter_version": ADAPTER},
              "target": {"kind": "git_change", "base_sha": "a" * 40, "head_sha": "b" * 40},
              "sources": {"base/src/main.py": "value = '旧值'\n", "head/src/main.py": "value = '新值'\n"}}
    prompt, _private = _input(provider, {}, record)
    text = prompt.decode("utf-8")
    inline = json.loads(text.split("# Captured sources (logical names; files under sources/ for Git targets)\n", 1)[1]
                        .split("\n# Result channel contract", 1)[0])
    assert inline == record["sources"]
    assert text.startswith(base)
    assert record["target"]["base_sha"] in text and record["target"]["head_sha"] in text
    assert "/private/operator-only" not in text
