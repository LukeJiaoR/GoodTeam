/**
 * [INPUT]: 项目标识、目录与共享的 JSON 客户端。
 * [OUTPUT]: 查询、导入和移除项目的 API 请求。
 * [POS]: 项目目录与项目侧栏共用的登记 API。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { requestJson } from '../../lib/api/client';

export const listProjects = ({ signal } = {}) => requestJson('/api/projects', { signal });

export const importProject = ({ root }) => requestJson('/api/projects/import', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ root }),
});

export const removeProject = ({ project_key, root }) => requestJson(`/api/projects/${encodeURIComponent(project_key)}/remove`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ root }),
});

export const projectApi = {
  listProjects,
  importProject,
  removeProject,
};
