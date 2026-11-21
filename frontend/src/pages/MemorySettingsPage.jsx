import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useWorkspace } from '../context/WorkspaceContext';
import MemoryPanel from '../components/MemoryPanel';
import ProvidersPanel from '../components/ProvidersPanel';
import ManagerUsagePanel from '../components/ManagerUsagePanel';

export default function MemorySettingsPage() {
  const { projectId: routeProjectId } = useParams();
  const { activeProjectId, setActiveProjectId } = useWorkspace();
  const projectId = routeProjectId || activeProjectId;
  const [providerRevision, setProviderRevision] = useState(0);

  useEffect(() => {
    if (routeProjectId && routeProjectId !== activeProjectId) {
      setActiveProjectId(routeProjectId);
    }
  }, [routeProjectId, activeProjectId, setActiveProjectId]);

  return (
    <div className="v2-page">
      <header className="v2-page-header">
        <div>
          <p className="v2-eyebrow">MEMORY &amp; SETTINGS</p>
          <h1>项目记忆与运行设置</h1>
          <p>管理本地 Markdown 记忆、Codex / Claude API Provider 连接、Manager 参数以及 Token 用量。</p>
        </div>
      </header>

      <MemoryPanel projectId={projectId} />

      <ProvidersPanel onChanged={() => setProviderRevision((n) => n + 1)} />

      <ManagerUsagePanel projectId={projectId} providerRevision={providerRevision} />
    </div>
  );
}
