function getActorTone(actor) {
  if (actor === 'human') {
    return {
      dot: 'bg-accent border-accent-text',
      badge: 'bg-status-success-bg text-status-success-text border-status-success/30',
    };
  }

  if (actor === 'system') {
    return {
      dot: 'bg-status-info border-status-info-text',
      badge: 'bg-status-info-bg text-status-info-text border-status-info/30',
    };
  }

  return {
    dot: 'bg-status-danger border-status-danger-text',
    badge: 'bg-status-danger-bg text-status-danger-text border-status-danger/30',
  };
}

export default function ActionsTab({ actionItems, formatHumanTime, FilePath }) {
  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Execution Logs</h2>
        <p className="text-xs text-text-muted">Chronological list of all worker dispatches, approvals, and commits.</p>
      </div>

      <div className="relative border-l-2 border-border-subtle ml-4 pl-6 space-y-6">
        {actionItems.map((action, idx) => {
          const tone = getActorTone(action.actor);

          return (
            <div key={idx} className="relative group">
              <span className={`absolute -left-[31px] top-1.5 h-3.5 w-3.5 rounded-full border-2 ${tone.dot}`} />

              <article className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-card transition-all duration-200 space-y-2.5 max-w-3xl">
                <div className="flex justify-between items-center gap-2">
                  <h3 className="font-extrabold text-sm text-text-primary leading-tight">{action.title}</h3>
                  <span className={`inline-block text-2xs px-2 py-0.5 rounded-full font-extrabold border uppercase tracking-wider leading-none ${tone.badge}`}>
                    {action.actor}
                  </span>
                </div>

                <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-2xs text-text-secondary font-semibold uppercase tracking-wider">
                  <span>Action: <span className="font-bold text-text-primary font-mono text-2xs">{action.action}</span></span>
                  {action.gate && <span>Gate: <span className="font-bold text-text-primary">{action.gate}</span></span>}
                  {action.decision && <span>Decision: <span className="font-bold text-text-primary">{action.decision}</span></span>}
                  {action.node_transition && <span>Node: <span className="font-bold text-text-primary font-mono text-2xs">{action.node_transition}</span></span>}
                </div>

                {action.artifact_path && (
                  <div className="pt-1.5 border-t border-border-subtle flex items-center space-x-2 text-2xs font-bold">
                    <span className="text-text-muted uppercase tracking-wider text-2xs">{action.artifact || 'Artifact'}</span>
                    <FilePath path={action.artifact_path} segments={1} />
                  </div>
                )}

                <div className="text-2xs font-mono font-bold text-text-muted text-right">
                  {action.timestamp ? formatHumanTime(action.timestamp) : ''}
                </div>
              </article>
            </div>
          );
        })}

        {actionItems.length === 0 && (
          <div className="border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs max-w-3xl">
            No actions logged in this run.
          </div>
        )}
      </div>
    </div>
  );
}
