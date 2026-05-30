# QA — Memory write-back M1 verification

You are the **QA / verification** agent for GoodTeam M1 automatic memory
write-back. The backend agent should have implemented parser + worker write-back
in `src/goodteam_os/workers.py`. Your job is verification only.

## Rules

- **Verification only — do not edit feature code.**
- **Do not commit.**
- Do not touch `frontend/`; M1 has no frontend task.
- Use a throwaway workspace under `/private/tmp`.
- Do not run `goodteam_os.cli smoke` in any active project workspace; smoke
  mutates state.
- If a step fails, capture the exact symptom and identify the likely owner:
  backend (`src/goodteam_os/workers.py` / `src/goodteam_os/memory.py`) or test
  setup.

## Workspace

- **Repo root (run all commands from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **Throwaway QA workspace:** `/private/tmp/goodteam-memory-writeback-m1-qa`

## Expected behavior to keep in mind

- A completed job writes parsed `result.md` takeaways into the role memory WAL:
  `.goodteam/memory/roles/<role>/events.jsonl`.
- Horizon mapping:
  - `## Decisions` -> `long-term`
  - `## Learned` -> `mid-term`
  - `## Caveats` -> `mid-term`
  - `## Open Questions` -> `short-term`
- Source is exactly `"<job_id>/result.md"`.
- Re-running the same job/result must not duplicate events.
- `fake` and `noop` jobs that emit no write-back sections are no-ops.
- `human` returns `needs_review`, so write-back does not trigger.
- Same-run propagation is **not expected**. In `graph.py`,
  `run_implementation_workers(...)` creates `ui`, `implementation`, and `qa`
  jobs before running any of them. Each job's `role_pickup_packet.json` is built
  at `create_job(...)` time. If a job writes memory, already-created downstream
  jobs in that same run will not see it. That is a pass condition for M1, not a
  failure.

## Baseline verification

Run the backend agent's required commands first:

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
PYTHONPATH=src python -m pytest tests -q
ruff check src/goodteam_os/ tests/
```

Both should pass before the live write-back check below.

## Live write-back check with a seeded CLI result

This uses a throwaway project root and monkeypatches `subprocess.Popen` inside a
one-off Python process. It does not call the real Codex/Claude CLI and does not
modify repo code.

Run:

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
rm -rf /private/tmp/goodteam-memory-writeback-m1-qa
PYTHONPATH=src python - <<'PY'
import json
from pathlib import Path

import goodteam_os.workers as workers
from goodteam_os.memory import RoleMemoryStore

workspace = Path("/private/tmp/goodteam-memory-writeback-m1-qa")
workspace.mkdir(parents=True, exist_ok=True)

result_text = """# Worker Result

Implemented and verified memory write-back.

## Learned
- Parser tests should cover freeform LLM result text.
- Pickup packets are built at job creation time.

## Caveats:
* Same-run memory write-back does not reach already-created downstream jobs.
• Workers must not put secrets in result.md.

### Decisions
- Memory write-back uses events.jsonl as the only durable write path.

### Open Questions:
No bullets here, so this prose paragraph should become one short-term event.

## Verification
- This unrelated section must not be captured.
"""

class FakePopen:
    pid = 7001
    returncode = 0

    def __init__(self, command, *, cwd, stdin, stdout, stderr, text):
        self.command = command
        self.cwd = cwd

    def communicate(self, input, timeout):
        output_path = self.command[self.command.index("--output-last-message") + 1]
        Path(output_path).write_text(result_text, encoding="utf-8")
        return ("seeded stdout", "")

    def kill(self):
        self.returncode = -9

workers.which = lambda name: f"/fake/bin/{name}"
workers.subprocess.Popen = FakePopen

store = workers.WorkerJobStore(workspace)
job = store.create_job(
    run_id="run-memory-writeback-qa",
    role="qa",
    adapter="codex",
    task="Verify memory write-back.",
    context="QA seeded result.md with memory sections.",
)

first = store.run_job(job["id"])
if first["status"] != "completed":
    raise SystemExit(f"expected completed status, got {first}")
if first.get("memory_writeback", {}).get("appended") != 6:
    raise SystemExit(f"expected 6 appended events, got {first.get('memory_writeback')}")

role_store = RoleMemoryStore(workspace)
events_path = role_store.event_path("qa")
events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
print("events_path", events_path)
print("event_count_after_first", len(events))
print("horizons_after_first", [event["horizon"] for event in events])

expected_horizons = ["mid-term", "mid-term", "mid-term", "mid-term", "long-term", "short-term"]
if [event["horizon"] for event in events] != expected_horizons:
    raise SystemExit(f"unexpected horizons: {[event['horizon'] for event in events]}")
if any(event["source"] != f"{job['id']}/result.md" for event in events):
    raise SystemExit(f"unexpected sources: {[event['source'] for event in events]}")
if any("Verification" in event["text"] for event in events):
    raise SystemExit("unrelated Verification section was captured")

second = store.run_job(job["id"])
events_after_second = [
    json.loads(line)
    for line in events_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
print("event_count_after_second", len(events_after_second))
print("second_memory_writeback", second.get("memory_writeback"))

if len(events_after_second) != len(events):
    raise SystemExit("rerunning the same job produced duplicate memory events")
if second.get("memory_writeback", {}).get("appended") != 0:
    raise SystemExit(f"expected rerun appended=0, got {second.get('memory_writeback')}")

for horizon in ("long-term", "mid-term", "short-term"):
    content = role_store.memory_path("qa", horizon).read_text(encoding="utf-8")
    print(f"{horizon}_has_memory_events", "## Memory Events" in content)

status = json.loads((workspace / ".goodteam" / "workers" / "jobs" / job["id"] / "status.json").read_text(encoding="utf-8"))
print("status_memory_writeback", status.get("memory_writeback"))
PY
```

Pass criteria:

- The script exits 0.
- `event_count_after_first` is `6`.
- Horizons print as:
  `['mid-term', 'mid-term', 'mid-term', 'mid-term', 'long-term', 'short-term']`.
- Every event source is `"<job_id>/result.md"`.
- `event_count_after_second` stays `6`.
- The second run reports `appended: 0` because dedup skipped existing exact text.
- Horizon markdown files contain `## Memory Events`.

## No-op adapter checks

Run:

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
PYTHONPATH=src python - <<'PY'
from pathlib import Path

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.workers import WorkerJobStore

workspace = Path("/private/tmp/goodteam-memory-writeback-m1-qa-noop")
if workspace.exists():
    import shutil
    shutil.rmtree(workspace)
workspace.mkdir(parents=True)

store = WorkerJobStore(workspace)

for adapter in ("fake", "noop"):
    job = store.create_job(
        run_id=f"run-{adapter}",
        role="planner",
        adapter=adapter,
        task="No-op write-back check.",
        context="No structured memory sections expected.",
    )
    result = store.run_job(job["id"])
    print(adapter, result["status"], result.get("memory_writeback"))

human_job = store.create_job(
    run_id="run-human",
    role="planner",
    adapter="human",
    task="Manual job.",
    context="Human adapter should not write back.",
)
human_result = store.run_job(human_job["id"])
print("human", human_result["status"], human_result.get("memory_writeback"))

events_path = RoleMemoryStore(workspace).event_path("planner")
print("events_exists", events_path.exists())
if events_path.exists() and events_path.read_text(encoding="utf-8").strip():
    raise SystemExit("fake/noop/human no-op checks unexpectedly wrote memory events")
if human_result["status"] != "needs_review":
    raise SystemExit(f"human adapter expected needs_review, got {human_result['status']}")
PY
```

Pass criteria:

- `fake` and `noop` complete with no appended events.
- `human` reports `needs_review`.
- No non-empty `events.jsonl` is created for these no-op checks.

## Same-run propagation expectation

Do not fail M1 because a downstream job created in the same graph run does not
see an upstream job's freshly written memory. That behavior is expected.

Optional sanity check for reports:

- If you inspect `.goodteam/workers/jobs/<job>/role_pickup_packet.json`, remember
  it is a snapshot from `create_job(...)`.
- A newly written event should appear in pickup packets only for jobs created
  after the write-back.

## Report format

Produce a short markdown report with:

- Baseline command results.
- Live write-back script PASS/FAIL and key printed lines.
- No-op adapter script PASS/FAIL and key printed lines.
- Any failure symptoms with the likely owning file.
- Explicit note that same-run non-propagation was treated as expected behavior.

## Cleanup

```bash
rm -rf /private/tmp/goodteam-memory-writeback-m1-qa \
       /private/tmp/goodteam-memory-writeback-m1-qa-noop
```
