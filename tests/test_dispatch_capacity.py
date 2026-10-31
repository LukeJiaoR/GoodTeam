"""Ten is a real direct-dispatch concurrency bound, not a planning limit."""

from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock

import pytest

import goodteam_os.dispatch as dispatch_module
from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.parallel_capacity import DEFAULT_MAX_CONCURRENT_WORKERS, dispatch_pool_size
from test_dispatch_isolation import repository


def test_real_dispatch_queues_the_eleventh_worker_without_losing_it(tmp_path, monkeypatch):
    """Exercise the existing isolated dispatch seam with credential-free workers.

    All jobs must be represented in the result; at most ten may enter the
    worker runner simultaneously. The test double does not claim real-model
    quality, actual code integration or project-level multi-run concurrency.
    """
    source = repository(tmp_path / "source")
    roles = [
        "project_intake", "research", "product_requirement", "architect",
        "review", "planner", "ui", "implementation", "qa",
        "release", "retro_memory",
    ]
    tracks = [{"role":role, "adapter":"fake", "task":"Do not change the checkout."}
              for role in roles]

    recorded_pool_sizes = []

    class CapturingPool(RealThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            recorded_pool_sizes.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    worker_runner = dispatch_module._run_dispatched_job
    gate = Barrier(DEFAULT_MAX_CONCURRENT_WORKERS, timeout=15)
    lock = Lock()
    active = 0
    peak = 0
    started = []

    def observed_runner(project_root, job, verify_commands, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            started.append(job["id"])
            arrival = len(started)
        try:
            # Only the first ten synchronize; the queued eleventh must not
            # be forced into a second ten-party barrier.
            if arrival <= DEFAULT_MAX_CONCURRENT_WORKERS:
                try:
                    gate.wait()
                except BrokenBarrierError as exc:
                    pytest.fail(f"Expected ten worker slots to be schedulable: {exc}")
            return worker_runner(project_root, job, verify_commands, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(dispatch_module, "ThreadPoolExecutor", CapturingPool)
    monkeypatch.setattr(dispatch_module, "_run_dispatched_job", observed_runner)

    result = dispatch_tracks(project_root=source, tracks=tracks,
                             logical_task_id="limit-ten-concurrent-workers")
    assert recorded_pool_sizes == [10]
    assert peak == 10
    assert len(started) == len(roles) == len(result["jobs"]) == 11
    assert len({job["job_id"] for job in result["jobs"]}) == 11
    assert result["worker_capacity"] == {
        "configured_max_concurrent_workers": 10,
        "pool_size": 10,
        "scheduled_jobs": 11,
        "jobs_beyond_initial_capacity": 1,
    }
