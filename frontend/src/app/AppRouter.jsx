/* eslint-disable react-refresh/only-export-components -- route factories are exported for router tests. */
import { Navigate, useLocation, useOutletContext, useParams } from 'react-router-dom';
import ActivityPage from '../features/activity/ActivityPage';
import InboxPage from '../features/inbox/InboxPage';
import KnowledgePage from '../features/knowledge/KnowledgePage';
import ProjectHomePage from '../features/projects/ProjectHomePage';
import ProjectsPage from '../features/projects/ProjectsPage';
import ReviewPage from '../features/reviews/ReviewPage';
import WorkPage from '../features/runs/WorkPage';
import ProjectSettingsPage from '../features/settings/ProjectSettingsPage';
import SettingsPage from '../features/settings/SettingsPage';
import { legacyDestination, routes } from '../lib/routing/routes';
import NotFoundPage from './NotFoundPage';
import ProductLayout from './ProductLayout';

function RootRedirect() {
  const { search } = useLocation();
  const projectKey = new URLSearchParams(search).get('project')?.trim();
  return <Navigate to={projectKey ? routes.projectHome(projectKey) : routes.projects()} replace />;
}

function LegacyRedirect() {
  const { search } = useLocation();
  return <Navigate to={legacyDestination(search)} replace />;
}

function ProjectHomeRoute({ Component }) {
  const { projectKey = '' } = useParams();
  const { language } = useOutletContext();

  return <Component language={language} projectKey={projectKey} />;
}

export function createProductRoutes({
  ProjectsComponent = ProjectsPage,
  ProjectHomeComponent = ProjectHomePage,
  WorkComponent = WorkPage,
  ReviewComponent = ReviewPage,
  KnowledgeComponent = KnowledgePage,
  InboxComponent = InboxPage,
  ActivityComponent = ActivityPage,
  SettingsComponent = SettingsPage,
  ProjectSettingsComponent = ProjectSettingsPage,
} = {}) {
  return [
    { path: '/', element: <RootRedirect /> },
    { path: '/legacy', element: <LegacyRedirect /> },
    {
      element: <ProductLayout />,
      children: [
        { path: '/projects', element: <ProjectsComponent /> },
        { path: '/inbox', element: <InboxComponent /> },
        { path: '/activity', element: <ActivityComponent /> },
        { path: '/settings', element: <SettingsComponent /> },
        {
          path: '/projects/:projectKey/home',
          element: <ProjectHomeRoute Component={ProjectHomeComponent} />,
        },
        { path: '/projects/:projectKey/work', element: <WorkComponent /> },
        { path: '/projects/:projectKey/work/runs/:runId', element: <WorkComponent /> },
        { path: '/projects/:projectKey/review', element: <ReviewComponent /> },
        { path: '/projects/:projectKey/review/:reviewId', element: <ReviewComponent /> },
        { path: '/projects/:projectKey/knowledge', element: <KnowledgeComponent /> },
        { path: '/projects/:projectKey/settings/:section', element: <ProjectSettingsComponent /> },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}
