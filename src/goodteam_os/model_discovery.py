"""Process-local bounded discovery, without caching authentication or failures."""

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock

PROVIDERS = ("agy", "claude", "codex", "gemini", "opencode", "pi")
_pool = ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="model-discovery")
_lock = Lock()
_pending: dict[str, Future] = {}


def _probe(provider):
    from goodteam_os.workers import _discover_installed_agents

    result = _discover_installed_agents(provider)
    for agent in result:
        agent["observed_at"] = datetime.now(UTC).isoformat()
    return result


def _release(provider, future):
    with _lock:
        if _pending.get(provider) is future:
            del _pending[provider]


def discover(provider=""):
    provider = {"anthropic": "claude", "antigravity": "agy", "pi-rpc": "pi"}.get(provider, provider)
    if provider and provider not in PROVIDERS:
        raise ValueError("Unknown model catalog provider")
    futures = []
    for name in ((provider,) if provider else PROVIDERS):
        with _lock:
            future = _pending.get(name)
            created = future is None
            if created:
                future = _pool.submit(_probe, name)
                _pending[name] = future
        # Register outside the lock: an already finished future calls inline.
        if created:
            future.add_done_callback(lambda done, key=name: _release(key, done))
        futures.append(future)
    return deepcopy([agent for future in futures for agent in future.result()])


def validate_runtime_choice(runtime, previous=None, *, catalogs=None):
    """Validate changed manual effort only; automatic/historical reads stay offline."""
    keys = ("provider", "model", "reasoning_depth")
    if previous is not None and all(runtime.get(key) == previous.get(key) for key in keys):
        return
    depth = runtime.get("reasoning_depth", "default")
    if depth == "default":
        return
    provider = runtime.get("provider") or "codex"
    # One atomic team decision uses one observation per provider, including
    # failures. This is a local batch snapshot, never a cross-request auth cache.
    if catalogs is None:
        catalogs = {}
    if provider not in catalogs:
        catalogs[provider] = discover(provider)
    agents = catalogs[provider]
    agent = agents[0]
    model = runtime.get("model") or agent.get("model_default")
    capabilities = agent.get("model_capabilities", {}).get(model, {})
    depths = capabilities.get("reasoning_depths", agent.get("reasoning_depths", []))
    if depth not in depths:
        raise ValueError(f"Reasoning depth {depth!r} is not confirmed for {provider}/{model}; refresh or use automatic.")
