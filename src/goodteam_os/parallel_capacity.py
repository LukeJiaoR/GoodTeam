"""One bounded default for real simultaneous Worker execution.

Project planning may select more jobs than this capacity; execution queues
overflow rather than losing task identity or silently starting more workers.
This is a per-dispatch worker-pool bound, not a global host/process semaphore.
"""

DEFAULT_MAX_CONCURRENT_WORKERS = 10


def dispatch_pool_size(job_count: int) -> int:
    """Size a real worker pool without imposing a limit on planned jobs."""
    if type(job_count) is not int or job_count < 1:
        raise ValueError("Worker dispatch needs a positive job count.")
    return min(job_count, DEFAULT_MAX_CONCURRENT_WORKERS)
