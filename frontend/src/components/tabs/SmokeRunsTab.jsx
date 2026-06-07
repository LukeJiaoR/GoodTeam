import { PlaySquare } from 'lucide-react';

function getSmokeBorder(status) {
  if (status === 'pass') return 'border-accent bg-status-success-bg/25';
  if (status === 'fail') return 'border-status-danger bg-status-danger-bg/30';
  return 'border-border-subtle bg-status-info-bg/20';
}

function getSmokeDot(status) {
  if (status === 'pass') return 'bg-status-success text-status-success-text';
  if (status === 'fail') return 'bg-status-danger animate-pulse';
  return 'bg-status-info text-status-info-text';
}

export default function SmokeRunsTab({
  smokeRunItems,
  smokeRunLoading,
  handleSmokeRun,
  getBadgeStyle,
  getStatusLabel,
  FilePath,
}) {
  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-3 flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <h2 className="text-base font-extrabold text-text-primary">Smoke Test Reports</h2>
          <p className="text-xs text-text-muted">Verify codebase status and health against configured suites.</p>
        </div>
        <button
          type="button"
          onClick={handleSmokeRun}
          disabled={smokeRunLoading}
          className="bg-accent hover:bg-accent disabled:bg-surface-sunken disabled:text-text-muted text-text-inverse border-b-4 border-accent-text disabled:border-border-strong px-4 py-2 rounded-xl text-xs font-extrabold tracking-wide uppercase transition-all flex items-center justify-center gap-2 min-w-[9rem]"
        >
          <PlaySquare className="h-4 w-4" />
          {smokeRunLoading ? 'Starting...' : 'Run Dry Smoke'}
        </button>
      </div>

      <div className="space-y-4 max-w-4xl">
        {smokeRunItems.map((item, idx) => {
          const badge = getBadgeStyle(item.status);

          return (
            <article
              key={idx}
              className={`bg-surface-raised border-2 shadow-card rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-card transition-all duration-200 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 ${getSmokeBorder(item.status)}`}
            >
              <div className="space-y-2">
                <div className="flex items-center space-x-2.5">
                  <span className={`h-2.5 w-2.5 rounded-full shrink-0 ${getSmokeDot(item.status)}`} />
                  <h3 className="font-extrabold text-sm text-text-primary">Cycle {item.cycle} - {item.project_key}</h3>
                  <span className={`inline-block text-2xs px-2 py-0.5 rounded-full font-extrabold border uppercase tracking-wider leading-none ${badge.bg} ${badge.text} ${badge.border}`}>
                    {getStatusLabel(item.status)}
                  </span>
                </div>

                <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-2xs text-text-secondary font-bold uppercase tracking-wider pt-1.5">
                  <div>
                    <dt className="text-text-muted mb-0.5">Test Suite</dt>
                    <dd className="font-mono text-text-primary font-extrabold text-2xs">{item.suite}</dd>
                  </div>
                  {item.classification && (
                    <div>
                      <dt className="text-text-muted mb-0.5">Classification</dt>
                      <dd className="font-mono text-status-danger-text font-extrabold">{item.classification}</dd>
                    </div>
                  )}
                  {item.next_task && (
                    <div className="col-span-2">
                      <dt className="text-text-muted mb-0.5">Recovery Task</dt>
                      <dd className="text-text-primary truncate max-w-xs font-sans normal-case text-xs font-semibold" title={item.next_task}>{item.next_task}</dd>
                    </div>
                  )}
                </dl>

                {item.report_path && (
                  <div className="pt-2 flex items-center space-x-2 text-2xs font-bold">
                    <span className="text-text-muted uppercase tracking-wider">Report:</span>
                    <FilePath path={item.report_path} segments={1} />
                  </div>
                )}
              </div>
            </article>
          );
        })}

        {smokeRunItems.length === 0 && (
          <div className="bg-surface-raised border-2 border-border-subtle p-12 text-center rounded-2xl text-text-muted italic text-xs">
            No smoke run test reports recorded yet.
          </div>
        )}
      </div>
    </div>
  );
}
