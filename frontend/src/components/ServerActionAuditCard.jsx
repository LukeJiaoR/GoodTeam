export default function ServerActionAuditCard({ audit }) {
  if (!audit || audit.available !== true) return null;

  const records = Array.isArray(audit.records) ? audit.records : [];
  const warnings = Array.isArray(audit.warnings) ? audit.warnings : [];

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-xs font-extrabold text-text-secondary uppercase tracking-wider">Server Action Audit</h3>
          <p className="text-2xs text-text-muted mt-1">Recent guarded action records. Read-only audit visibility.</p>
        </div>
        <span className="inline-flex items-center px-2 py-0.5 rounded-full border text-2xs font-extrabold uppercase bg-surface-sunken text-text-muted border-border-subtle">
          {records.length} recent
        </span>
      </div>

      {warnings.length > 0 && (
        <div className="bg-status-warning-bg border border-status-warning/30 rounded-xl p-3 text-status-warning-text text-2xs font-semibold">
          <div className="font-extrabold uppercase tracking-wider">Audit warnings</div>
          <ul className="list-disc pl-4 mt-1 space-y-0.5">
            {warnings.map((warning, idx) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {records.length > 0 ? (
        <div className="space-y-2">
          {records.map((record, idx) => {
            const statusLabel = record.timed_out ? 'timeout' : record.returncode === 0 ? 'passed' : 'failed';
            const statusClass = record.timed_out || record.returncode !== 0
              ? 'bg-status-danger-bg text-status-danger-text border-status-danger/30'
              : 'bg-status-success-bg text-status-success-text border-status-success/30';
            return (
              <article key={record.id || idx} className="bg-surface-sunken border border-border-subtle rounded-xl p-3 min-w-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-2xs font-extrabold uppercase tracking-wider text-text-secondary truncate">
                      {record.action || 'server action'}
                    </div>
                    <div className="mt-0.5 text-2xs font-mono text-text-muted truncate" title={record.timestamp || ''}>
                      {record.timestamp || 'timestamp unavailable'}
                    </div>
                  </div>
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-2xs font-extrabold uppercase ${statusClass}`}>
                    {statusLabel}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2 text-2xs font-semibold text-text-muted">
                  <div className="truncate" title={String(record.returncode ?? '')}>
                    returncode: <span className="text-text-secondary">{record.returncode ?? 'n/a'}</span>
                  </div>
                  <div className="truncate" title={record.audit_path || ''}>
                    audit: <span className="font-mono text-text-secondary">{record.audit_path || 'n/a'}</span>
                  </div>
                </div>
                {(record.stdout_tail || record.stderr_tail) && (
                  <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {record.stdout_tail && (
                      <code className="block bg-surface-raised border border-border-subtle rounded-lg p-2 text-2xs text-text-secondary font-mono whitespace-pre-wrap break-words max-h-20 overflow-y-auto">
                        {record.stdout_tail}
                      </code>
                    )}
                    {record.stderr_tail && (
                      <code className="block bg-status-danger-bg border border-status-danger/30 rounded-lg p-2 text-2xs text-status-danger-text font-mono whitespace-pre-wrap break-words max-h-20 overflow-y-auto">
                        {record.stderr_tail}
                      </code>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="bg-surface-sunken border border-border-subtle rounded-xl p-4 text-center text-2xs font-semibold text-text-muted">
          No server action audit records.
        </div>
      )}
    </div>
  );
}
