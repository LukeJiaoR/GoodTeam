/**
 * GoodTeam v2 HTTP API client and File-to-base64 helpers.
 * Matches the shared REST + SSE contract with src/goodteam/api/.
 */

export class ApiError extends Error {
  constructor(message, { status = 0, code = '', details = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function request(path, options = {}) {
  const { body, headers = {}, ...rest } = options;
  const init = {
    ...rest,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
  };
  if (body !== undefined) {
    init.body = typeof body === 'string' ? body : JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError('无法连接到 GoodTeam 本地服务，请确认后端已启动。', {
      status: 0,
      code: 'offline',
    });
  }

  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json')
    ? await response.json().catch(() => ({}))
    : await response.text().catch(() => '');

  if (!response.ok) {
    const message =
      (typeof data === 'object' && data && (data.error || data.message || data.detail)) ||
      (typeof data === 'string' && data.trim()) ||
      `请求失败 (HTTP ${response.status})`;
    const code = typeof data === 'object' && data ? String(data.code || '') : '';
    throw new ApiError(String(message), { status: response.status, code, details: data });
  }

  return data;
}

/**
 * Convert a browser File/Blob to a pure base64 string (without data URL prefix).
 */
export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const commaIdx = result.indexOf(',');
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = () => reject(new Error('读取本地文件失败。'));
    reader.readAsDataURL(file);
  });
}

function enc(value) {
  return encodeURIComponent(String(value));
}

function unwrapList(data, key) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data[key])) return data[key];
  return [];
}

export const api = {
  // ---- System & Filesystem ----
  getDoctor() {
    return request('/api/doctor');
  },
  listFs(path = '') {
    const q = path ? `?path=${enc(path)}` : '';
    return request(`/api/fs/list${q}`);
  },

  // ---- Projects ----
  async listProjects() {
    const data = await request('/api/projects');
    return unwrapList(data, 'projects');
  },
  async createProject(payload) {
    const data = await request('/api/projects', { method: 'POST', body: payload });
    return data?.project || data;
  },
  deleteProject(projectId) {
    return request(`/api/projects/${enc(projectId)}`, { method: 'DELETE' });
  },

  // ---- Chat & Attachments ----
  async listMessages(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/messages`);
    return unwrapList(data, 'messages');
  },
  sendMessage(projectId, { content, attachments = [] }) {
    return request(`/api/projects/${enc(projectId)}/messages`, {
      method: 'POST',
      body: { content, text: content, attachments },
    });
  },
  stopChat(projectId) {
    return request(`/api/projects/${enc(projectId)}/chat/stop`, { method: 'POST', body: {} });
  },
  async uploadAttachment(projectId, file) {
    const content_base64 = await fileToBase64(file);
    const data = await request(`/api/projects/${enc(projectId)}/attachments`, {
      method: 'POST',
      body: {
        filename: file.name || 'attachment',
        content_base64,
        mime_type: file.type || 'application/octet-stream',
      },
    });
    return data?.attachment || data;
  },

  // ---- Autopilot ----
  getAutopilot(projectId) {
    return request(`/api/projects/${enc(projectId)}/autopilot`);
  },
  startAutopilot(projectId, { objective, max_rounds = 5 }) {
    return request(`/api/projects/${enc(projectId)}/autopilot/start`, {
      method: 'POST',
      body: { objective, max_rounds },
    });
  },
  stopAutopilot(projectId) {
    return request(`/api/projects/${enc(projectId)}/autopilot/stop`, { method: 'POST', body: {} });
  },

  // ---- Tasks & GitHub Issues ----
  async listTasks(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/tasks`);
    return unwrapList(data, 'tasks');
  },
  async createTask(projectId, payload) {
    const data = await request(`/api/projects/${enc(projectId)}/tasks`, {
      method: 'POST',
      body: payload,
    });
    return data?.task || data;
  },
  async updateTask(projectId, taskId, payload) {
    const data = await request(`/api/projects/${enc(projectId)}/tasks/${enc(taskId)}`, {
      method: 'PATCH',
      body: payload,
    });
    return data?.task || data;
  },
  async listGitHubIssues(projectId, { state = 'open', limit = 30 } = {}) {
    const data = await request(
      `/api/projects/${enc(projectId)}/github/issues?state=${enc(state)}&limit=${enc(limit)}`,
    );
    return unwrapList(data, 'issues');
  },
  async importGitHubIssue(projectId, number) {
    const data = await request(
      `/api/projects/${enc(projectId)}/github/issues/${enc(number)}/import`,
      { method: 'POST', body: {} },
    );
    return data?.task || data;
  },

  // ---- Runs ----
  async listRuns(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/runs`);
    return unwrapList(data, 'runs');
  },
  getRun(runId) {
    return request(`/api/runs/${enc(runId)}`);
  },
  stopRun(runId) {
    return request(`/api/runs/${enc(runId)}/stop`, { method: 'POST', body: {} });
  },

  // ---- Deliveries ----
  async listDeliveries(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/deliveries`);
    return unwrapList(data, 'deliveries');
  },
  mergeDelivery(deliveryId) {
    return request(`/api/deliveries/${enc(deliveryId)}/merge`, { method: 'POST', body: {} });
  },
  createDeliveryPr(deliveryId, payload = {}) {
    return request(`/api/deliveries/${enc(deliveryId)}/pr`, { method: 'POST', body: payload });
  },
  discardDelivery(deliveryId) {
    return request(`/api/deliveries/${enc(deliveryId)}/discard`, { method: 'POST', body: {} });
  },
  getDeliveryChecks(deliveryId) {
    return request(`/api/deliveries/${enc(deliveryId)}/checks`);
  },

  // ---- Roles ----
  async listRoles(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/roles`);
    return unwrapList(data, 'roles');
  },
  async createRole(projectId, payload) {
    const data = await request(`/api/projects/${enc(projectId)}/roles`, {
      method: 'POST',
      body: payload,
    });
    return data?.role || data;
  },
  async updateRole(projectId, key, payload) {
    const data = await request(`/api/projects/${enc(projectId)}/roles/${enc(key)}`, {
      method: 'PATCH',
      body: payload,
    });
    return data?.role || data;
  },
  deleteRole(projectId, key) {
    return request(`/api/projects/${enc(projectId)}/roles/${enc(key)}`, { method: 'DELETE' });
  },
  async resetRoles(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/roles/reset`, {
      method: 'POST',
      body: {},
    });
    return unwrapList(data, 'roles');
  },

  // ---- Skills ----
  async listSkills() {
    const data = await request('/api/skills');
    return unwrapList(data, 'skills');
  },
  getSkillContent(skillId) {
    return request(`/api/skills/${enc(skillId)}/content`);
  },
  previewGitHubSkills(url) {
    return request('/api/skills/github/preview', { method: 'POST', body: { url } });
  },
  installGitHubSkills(url, selected_paths = null) {
    return request('/api/skills/github/install', {
      method: 'POST',
      body: selected_paths ? { url, selected_paths } : { url },
    });
  },
  previewZipSkills({ filename, content_base64 }) {
    return request('/api/skills/zip/preview', {
      method: 'POST',
      body: { filename, content_base64 },
    });
  },
  installZipSkills({ filename, content_base64, selected_paths = null }) {
    return request('/api/skills/zip/install', {
      method: 'POST',
      body: selected_paths
        ? { filename, content_base64, selected_paths }
        : { filename, content_base64 },
    });
  },
  installLocalSkill(path) {
    return request('/api/skills/local/install', { method: 'POST', body: { path } });
  },
  discoverSkills({ project_id = null, include_user_dirs = false } = {}) {
    return request('/api/skills/discover', {
      method: 'POST',
      body: { project_id, include_user_dirs },
    });
  },
  deleteSkill(skillId) {
    return request(`/api/skills/${enc(skillId)}`, { method: 'DELETE' });
  },

  // ---- Providers & Usage ----
  getProviders() {
    return request('/api/providers');
  },
  saveProvider(payload) {
    return request('/api/providers', { method: 'POST', body: payload });
  },
  deleteProvider(providerId) {
    return request(`/api/providers/${enc(providerId)}`, { method: 'DELETE' });
  },
  activateProvider(agent, provider_id) {
    return request('/api/providers/activate', {
      method: 'POST',
      body: { agent, provider_id },
    });
  },
  fetchProviderModels(payload) {
    return request('/api/providers/models', { method: 'POST', body: payload });
  },
  testProvider(payload) {
    return request('/api/providers/test', { method: 'POST', body: payload });
  },
  async getUsage(projectId = '') {
    const q = projectId ? `?project_id=${enc(projectId)}` : '';
    const data = await request(`/api/usage${q}`);
    if (Array.isArray(data)) return data;
    return data?.usage || data?.rows || [];
  },

  // ---- Memory & Manager Settings ----
  getMemory(projectId, { filename = '', query = '' } = {}) {
    const params = new URLSearchParams();
    if (filename) params.set('filename', filename);
    if (query) params.set('query', query);
    const qs = params.toString();
    return request(`/api/projects/${enc(projectId)}/memory${qs ? `?${qs}` : ''}`);
  },
  saveMemory(projectId, { filename, content, mode = 'replace' }) {
    return request(`/api/projects/${enc(projectId)}/memory`, {
      method: 'PUT',
      body: { filename, content, mode },
    });
  },
  async getManagerSettings(projectId) {
    const data = await request(`/api/projects/${enc(projectId)}/manager-settings`);
    return data?.settings || data;
  },
  async saveManagerSettings(projectId, payload) {
    const data = await request(`/api/projects/${enc(projectId)}/manager-settings`, {
      method: 'PUT',
      body: payload,
    });
    return data?.settings || data;
  },
};
