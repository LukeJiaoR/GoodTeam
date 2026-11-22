/**
 * [INPUT]: 依赖 React Router 和当前产品页面。
 * [OUTPUT]: 提供浏览器路由和未找到页面。
 * [POS]: 连接统一项目入口与工作台、团队和知识库。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { Navigate } from 'react-router-dom';
import KnowledgePage from '../features/knowledge/KnowledgePage';
import ProjectsPage from '../features/projects/ProjectsPage';
import WorkPage from '../features/runs/WorkPage';
import WorkWorkspaceRoute from '../features/runs/workspace/WorkWorkspaceShell';
import TeamPage from '../features/team/TeamPage';
import { routes } from '../lib/routing/routes';
import NotFoundPage from './NotFoundPage';
import ProductLayout from './ProductLayout';
import SettingsPage from '../features/settings/SettingsPage';

export function createProductRoutes({
  ProjectsComponent = ProjectsPage,
  WorkComponent = WorkPage,
  KnowledgeComponent = KnowledgePage,
  TeamComponent = TeamPage,
} = {}) {
  return [
    { path: '/', element: <Navigate to={routes.projects()} replace /> },
    {
      element: <ProductLayout />,
      children: [
        { path: '/projects', element: <ProjectsComponent /> },
        { path: '/settings', element: <SettingsPage /> },
        { path: '/projects/:projectKey/team', element: <TeamComponent /> },
        {
          path: '/projects/:projectKey/work',
          element: <WorkWorkspaceRoute Component={WorkComponent} />,
        },
        { path: '/projects/:projectKey/knowledge', element: <KnowledgeComponent /> },
        { path: '/projects/:projectKey/settings', element: <SettingsPage /> },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}
