export default function ServerActionsCard({ serverActions }) {
  const actions = serverActions?.actions && typeof serverActions.actions === 'object'
    ? Object.entries(serverActions.actions)
    : [];
  if (actions.length === 0) return null;

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4">
      <div>
        <h3 className="text-xs font-extrabold text-text-secondary uppercase tracking-wider">Server Action Capabilities</h3>
        <p className="text-2xs text-text-muted mt-1">Read-only capability report. Destructive actions stay human-command-only.</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {actions.map(([name, capability]) => {
          const isAvailable = capability?.available === true;
          const mode = capability?.mode || 'disabled';
          return (
            <div key={name} className="bg-surface-sunken border border-border-subtle rounded-xl p-3 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-2xs font-extrabold uppercase tracking-wider text-text-secondary truncate">
                  {name.replaceAll('_', ' ')}
                </span>
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full border text-2xs font-extrabold uppercase ${
                    isAvailable
                      ? 'bg-status-success-bg text-status-success-text border-status-success/30'
                      : 'bg-surface-raised text-text-muted border-border-subtle'
                  }`}
                >
                  {isAvailable ? 'available' : 'disabled'}
                </span>
              </div>
              <div className="mt-2 text-2xs font-semibold text-text-muted truncate" title={mode}>
                {mode}
              </div>
              {capability?.reason && (
                <div className="mt-1 text-2xs font-semibold text-text-muted truncate" title={capability.reason}>
                  {capability.reason}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
