function FieldRow({ label, value }) {
  return (
    <div className="flex justify-between items-center gap-4 border-b border-border-subtle pb-1.5 last:border-b-0">
      <dt className="text-text-muted uppercase text-2xs tracking-wider shrink-0">{label}</dt>
      <dd className="font-mono text-2xs truncate max-w-[200px] text-right font-bold text-text-primary">{value}</dd>
    </div>
  );
}

function InfoCard({ title, meta, children }) {
  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 space-y-4">
      <div className="border-b border-border-subtle pb-2 flex justify-between items-center gap-4">
        <h3 className="font-extrabold text-sm text-text-primary">{title}</h3>
        <span className="text-2xs text-text-muted uppercase tracking-wider shrink-0">{meta}</span>
      </div>
      {children}
    </div>
  );
}

export default function SystemTab({ state, startContext, FilePath }) {
  const stateFields = [
    { label: 'Run ID', value: state?.run_id },
    { label: 'Project Key', value: state?.project_key },
    { label: 'Current Node', value: state?.current_node },
    { label: 'Next Node', value: state?.next_node },
    { label: 'Model Profile', value: state?.model_profile },
    { label: 'Delivery Mode', value: state?.delivery_mode },
    { label: 'Source Type', value: state?.source_type },
    { label: 'Source Ref', value: <FilePath path={state?.source_ref} segments={2} /> },
    { label: 'Context Memory Path', value: <FilePath path={state?.context_packet_path} segments={2} /> },
    { label: 'Task Ledger File', value: <FilePath path={state?.artifacts?.task_ledger} segments={2} /> },
  ];

  const targetFields = [
    { label: 'Workspace Root', value: <FilePath path={startContext.workspace} segments={2} /> },
    { label: 'Model Config', value: <FilePath path={startContext.model_config} segments={2} /> },
    { label: 'Project Config', value: <FilePath path={startContext.project_config} segments={2} /> },
    { label: 'Project Base Folder', value: <FilePath path={startContext.project_base} segments={2} /> },
    { label: 'Task Backend Mode', value: startContext.task_backend?.mode },
    { label: 'Task Sync Destinations', value: startContext.task_backend?.sync?.join(', ') || 'Local only' },
    { label: 'Worker Backend Mode', value: startContext.worker_backend?.mode },
    { label: 'Deployment Backend', value: startContext.deployment_backend?.mode || 'None' },
    { label: 'Delivery Contract Req', value: startContext.delivery_contract?.required ? 'Yes' : 'No' },
    { label: 'Contract Min Gate', value: startContext.delivery_contract?.minimum_evidence || 'n/a' },
  ];

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">System Information</h2>
        <p className="text-xs text-text-muted">Live JSON configuration files, workspaces parameters, and target variables.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start font-semibold text-xs">
        <InfoCard title="Active Workflow State" meta="state/current.json">
          {state ? (
            <dl className="space-y-3 font-semibold text-text-secondary">
              {stateFields.map((field) => (
                <FieldRow key={field.label} label={field.label} value={field.value} />
              ))}
            </dl>
          ) : (
            <p className="bg-surface-sunken border border-border-subtle rounded-xl text-text-muted italic text-center py-6">
              No run state loaded.
            </p>
          )}
        </InfoCard>

        <InfoCard title="Project Target Config" meta="Projects environment">
          <dl className="space-y-3 font-semibold text-text-secondary">
            {targetFields.map((field) => (
              <FieldRow key={field.label} label={field.label} value={field.value} />
            ))}
          </dl>
        </InfoCard>
      </div>
    </div>
  );
}
