export const DEFAULT_BACKEND_OPTIONS = ["fake", "codex", "claude", "pi", "noop", "human"];
export const DEFAULT_WORKER_ROLES = ["ui", "implementation", "qa"];
export const MODEL_UNAVAILABLE_BACKENDS = ["fake", "noop", "human"];
export const INIT_WORKER_ROUTING_BUCKET_KEYS = ['product', 'implementation', 'quality'];

export const WORKER_ROUTING_BUCKETS = [
  { key: 'product', label: 'Product + Architecture', roles: ['product_requirement', 'architect', 'review', 'planner'] },
  { key: 'implementation', label: 'Implementation', roles: ['ui', 'frontend', 'backend', 'implementation'] },
  { key: 'quality', label: 'Quality + Release', roles: ['qa', 'code_review', 'release'] },
  { key: 'ops', label: 'Orchestration + Memory', roles: ['orchestrator', 'research', 'retro_memory'] },
];

export const FALLBACK_AGENT_MODES = {
  codex: {
    models: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.5"],
    model_default: "gpt-5.4"
  },
  anthropic: {
    models: ["claude-sonnet-4-6", "claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-5-haiku-latest"],
    model_default: "claude-sonnet-4-6"
  },
  claude: {
    models: ["claude-sonnet-4-6", "claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-5-haiku-latest"],
    model_default: "claude-sonnet-4-6"
  },
  gemini: {
    models: ["auto", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash-exp"],
    model_default: "auto"
  },
  agy: {
    models: ["auto"],
    model_default: "auto"
  },
  antigravity: {
    models: ["auto"],
    model_default: "auto"
  },
  opencode: {
    models: ["auto"],
    model_default: "auto"
  },
  pi: {
    models: ["auto"],
    model_default: "auto"
  }
};

export function canonicalBackendForOption(backend) {
  if (backend === 'anthropic') return 'claude';
  if (backend === 'antigravity') return 'agy';
  if (backend === 'pi-rpc') return 'pi';
  return backend;
}
