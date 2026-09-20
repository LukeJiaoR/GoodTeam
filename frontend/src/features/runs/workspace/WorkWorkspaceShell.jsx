import { useParams } from 'react-router-dom';
import './workspace.css';
import './projectConversation.css';
import './workspaceResponsive.css';
import './workspaceTaskDetail.css';

export function WorkWorkspaceShell({ children }) {
  return (
    <div className="work-workspace-shell" data-workspace="manager-centered">
      {children}
    </div>
  );
}

export default function WorkWorkspaceRoute({ Component }) {
  const { projectKey } = useParams();
  return (
    <WorkWorkspaceShell>
      <Component key={projectKey} />
    </WorkWorkspaceShell>
  );
}
