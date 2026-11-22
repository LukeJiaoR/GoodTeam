/**
 * [INPUT]: 接收项目键。
 * [OUTPUT]: 生成项目列表和项目页面的站内地址。
 * [POS]: 为导航组件统一编码项目键。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
const segment = (value) => encodeURIComponent(String(value || '').trim());

export const routes = Object.freeze({
  projects: () => '/projects',
  projectTeam: (projectKey) => `/projects/${segment(projectKey)}/team`,
  projectWork: (projectKey) => `/projects/${segment(projectKey)}/work`,
  projectKnowledge: (projectKey) => `/projects/${segment(projectKey)}/knowledge`,
});
