/* eslint-disable react-refresh/only-export-components */
import { Link, Navigate, createBrowserRouter } from 'react-router-dom';
import Layout from './Layout';
import ProjectsPage from '../pages/ProjectsPage';
import ChatPage from '../pages/ChatPage';
import DeliveryPage from '../pages/DeliveryPage';
import TeamSkillsPage from '../pages/TeamSkillsPage';
import MemorySettingsPage from '../pages/MemorySettingsPage';

function NotFoundPage() {
  return (
    <div className="product-not-found">
      <p className="product-not-found__code">404</p>
      <h1>页面不存在</h1>
      <p>请求的路由未找到，请返回项目工作区继续操作。</p>
      <Link to="/projects" className="product-button product-button--primary">
        返回项目工作区
      </Link>
    </div>
  );
}

export function createV2Routes() {
  return [
    {
      path: '/',
      element: <Layout />,
      children: [
        { index: true, element: <Navigate to="/projects" replace /> },
        { path: 'projects', element: <ProjectsPage /> },
        { path: 'projects/:projectId/chat', element: <ChatPage /> },
        { path: 'projects/:projectId/delivery', element: <DeliveryPage /> },
        { path: 'projects/:projectId/team', element: <TeamSkillsPage /> },
        { path: 'projects/:projectId/settings', element: <MemorySettingsPage /> },
        { path: 'chat', element: <ChatPage /> },
        { path: 'delivery', element: <DeliveryPage /> },
        { path: 'team', element: <TeamSkillsPage /> },
        { path: 'settings', element: <MemorySettingsPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ];
}

export function createV2Router() {
  return createBrowserRouter(createV2Routes());
}
