import { useEffect, useMemo, useRef, useState } from 'react';
import { settingsApi } from '../settings/settingsApi';
import WorkerDialog from './WorkerDialog';
import { buildTeamModel } from './teamModel';
import { saveAssistantRuntime } from './saveAssistantRuntime';

export default function AssistantRuntimePanel({ data, projectKey, onClose, refresh, t, api = settingsApi }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const pending = useRef(false);
  const team = useMemo(() => buildTeamModel(data), [data]);
  useEffect(() => {
    const controller = new AbortController();
    api.getSettingsAgents({ signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setAgents(payload.agents || []); })
      .catch((caught) => { if (!controller.signal.aborted) setError(caught); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, revision]);
  const reloadAgents = () => { setLoading(true); setError(null); setRevision((value) => value + 1); };
  const save = async (commands) => {
    if (pending.current) return;
    pending.current = true;
    setSaving(true);
    try { return await saveAssistantRuntime({ api, projectKey, commands, refresh }); }
    finally { pending.current = false; setSaving(false); }
  };
  return <WorkerDialog presentation="sheet" manager agents={agents} agentsLoading={loading}
    backendOptions={[...new Set([...(data?.start_context?.worker_backend_options || []), ...agents.filter((agent) => agent.available !== false).map((agent) => agent.backend)])]}
    defaultProvider={data?.start_context?.worker_backend?.mode || 'codex'} managerRuntime={team?.manager}
    assistantSession={data?.task_assistant_session} onRefresh={reloadAgents} error={error}
    onClose={() => { if (!pending.current) onClose(); }} onSubmit={save} saving={saving} t={t}>
    {data?.project_assistant?.activity?.length ? <details className="team-runtime-activity">
      <summary>{t('assistant.project.activity')}</summary>
      <ul>{data.project_assistant.activity.map((item) => <li key={item.id}><code>{item.id}</code> · {item.status}</li>)}</ul>
    </details> : null}
  </WorkerDialog>;
}
