import { FileText } from 'lucide-react';

function getRoleBorder(status) {
  if (status === 'Waiting for approval') return 'border-status-warning';
  if (status === 'Ready') return 'border-accent';
  return 'border-border-subtle';
}

export default function RolesTab({
  roleObservations,
  getBadgeStyle,
  getStatusLabel,
  FilePath,
}) {
  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Role Observability</h2>
        <p className="text-xs text-text-muted">Overview of AI agents configured for this workflow execution loop.</p>
      </div>

      {roleObservations.length === 0 ? (
        <div className="bg-surface-raised border-2 border-dashed border-border-subtle rounded-2xl p-12 text-center text-text-muted italic text-xs">
          No role observations recorded for this workflow yet.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {roleObservations.map((role) => {
            const badge = getBadgeStyle(role.status);

            return (
              <div
                key={role.name}
                className={`bg-surface-raised border-2 rounded-2xl shadow-card p-5 space-y-4 hover:-translate-y-0.5 hover:shadow-card transition-all duration-200 flex flex-col justify-between ${getRoleBorder(role.status)}`}
              >
                <div className="space-y-3">
                  <div className="flex justify-between items-start gap-2">
                    <h3 className="font-extrabold text-sm text-text-primary leading-tight">{role.name}</h3>
                    <span className={`inline-block px-2.5 py-0.5 rounded-full text-2xs font-extrabold border uppercase tracking-wider shrink-0 ${badge.bg} ${badge.text} ${badge.border}`}>
                      {getStatusLabel(role.status)}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-text-secondary leading-relaxed h-12 overflow-y-auto">{role.description}</p>
                </div>

                <div className="border-t-2 border-border-subtle pt-3 space-y-2 text-2xs font-bold">
                  <div className="grid grid-cols-2 gap-x-2">
                    <div>
                      <span className="text-text-muted uppercase tracking-wider text-2xs block mb-0.5">Model</span>
                      <span className="font-mono text-text-primary truncate block">{role.model}</span>
                    </div>
                    <div>
                      <span className="text-text-muted uppercase tracking-wider text-2xs block mb-0.5">Mode</span>
                      <span className="text-text-primary capitalize">{role.mode}</span>
                    </div>
                  </div>

                  {role.artifact && (
                    <div className="pt-1 flex justify-between items-center gap-2">
                      <div>
                        <span className="text-text-muted uppercase tracking-wider text-2xs block">Target Artifact</span>
                        <span className="text-accent uppercase font-extrabold text-2xs">{role.artifact}</span>
                      </div>
                      <FilePath path={role.artifact_path} segments={1} />
                    </div>
                  )}

                  {!role.artifact && (
                    <div className="pt-1 flex items-center gap-1.5 text-text-muted">
                      <FileText className="h-3 w-3 shrink-0" />
                      <span>No target artifact recorded.</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
