/* eslint-disable react-refresh/only-export-components -- route factories are exported for router tests. */
import { Navigate, useLocation, useParams } from 'react-router-dom';
import KnowledgePage from '../features/knowledge/KnowledgePage';
import ProjectsPage from '../features/projects/ProjectsPage';
import ReviewPage from '../features/reviews/ReviewPage';
import WorkPage from '../features/runs/WorkPage';
import WorkWorkspaceRoute from '../features/runs/workspace/WorkWorkspaceShell';
import TeamPage from '../features/team/TeamPage';
import { legacyDestination, routes } from '../lib/routing/routes';
import NotFoundPage from './NotFoundPage';
import ProductLayout from './ProductLayout';
import SettingsPage from '../features/settings/SettingsPage';

function RootRedirect() {
  const { search } = useLocation();
  const projectKey = new URLSearchParams(search).get('project')?.trim();
  return <Navigate to={projectKey ? routes.projectWork(projectKey) : routes.projects()} replace />;
}

function LegacyRedirect() {
  const { search } = useLocation();
  return <Navigate to={legacyDestination(search)} replace />;
}

function ProjectTeamRedirect() {
  const { projectKey = '' } = useParams();
  return <Navigate to={routes.projectTeam(projectKey)} replace />;
}

function ProjectWorkRedirect() {
  const { projectKey = '' } = useParams();
  return <Navigate to={routes.projectWork(projectKey)} replace />;
}

export function createProductRoutes({
  ProjectsComponent = ProjectsPage,
  WorkComponent = WorkPage,
  ReviewComponent = ReviewPage,
  KnowledgeComponent = KnowledgePage,
  TeamComponent = TeamPage,
} = {}) {
  return [
    { path: '/', element: <RootRedirect /> },
    { path: '/legacy', element: <LegacyRedirect /> },
    {
      element: <ProductLayout />,
      children: [
        { path: '/projects', element: <ProjectsComponent /> },
        { path: '/inbox', element: <Navigate to={routes.projects()} replace /> },
        { path: '/activity', element: <Navigate to={routes.projects()} replace /> },
        { path: '/settings', element: <SettingsPage /> },
        { path: '/projects/:projectKey/home', element: <ProjectWorkRedirect /> },
        { path: '/projects/:projectKey/team', element: <TeamComponent /> },
        {
          path: '/projects/:projectKey/work',
          element: <WorkWorkspaceRoute Component={WorkComponent} />,
        },
        {
          path: '/projects/:projectKey/work/runs/:runId',
          element: <WorkWorkspaceRoute Component={WorkComponent} />,
        },
        { path: '/projects/:projectKey/review', element: <ReviewComponent /> },
        { path: '/projects/:projectKey/review/:reviewId', element: <ReviewComponent /> },
        { path: '/projects/:projectKey/knowledge', element: <KnowledgeComponent /> },
        { path: '/projects/:projectKey/settings', element: <SettingsPage /> },
        { path: '/projects/:projectKey/settings/:section', element: <ProjectTeamRedirect /> },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}
