/* eslint-disable react-refresh/only-export-components -- route factories are exported for router tests. */
import { useOutletContext, useParams } from 'react-router-dom';
import App from '../App';
import ProjectHomePage from '../features/projects/ProjectHomePage';
import ProjectsPage from '../features/projects/ProjectsPage';
import NotFoundPage from './NotFoundPage';
import ProductLayout from './ProductLayout';

function LegacyApp() {
  return (
    <div data-testid="legacy-app-root">
      <App />
    </div>
  );
}

function ProjectHomeRoute({ Component }) {
  const { projectKey = '' } = useParams();
  const { language } = useOutletContext();

  return <Component language={language} projectKey={projectKey} />;
}

export function createProductRoutes({
  ProjectsComponent = ProjectsPage,
  ProjectHomeComponent = ProjectHomePage,
} = {}) {
  return [
    { path: '/', element: <LegacyApp /> },
    {
      element: <ProductLayout />,
      children: [
        { path: '/projects', element: <ProjectsComponent /> },
        {
          path: '/projects/:projectKey/home',
          element: <ProjectHomeRoute Component={ProjectHomeComponent} />,
        },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}
