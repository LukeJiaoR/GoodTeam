import './workspace.css';
import './workspacePresentation.css';
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
  return (
    <WorkWorkspaceShell>
      <Component />
    </WorkWorkspaceShell>
  );
}
