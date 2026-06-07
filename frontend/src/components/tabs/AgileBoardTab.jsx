import { Info } from 'lucide-react';

const BOARD_COLUMNS = [
  {
    key: 'backlog',
    label: 'Backlog',
    headStyle: 'bg-surface-sunken text-text-secondary border-2 border-border-strong/20',
  },
  {
    key: 'doing',
    label: 'Doing',
    headStyle: 'bg-status-info-bg text-status-info-text border-2 border-status-info/20',
  },
  {
    key: 'blocked',
    label: 'Blocked',
    headStyle: 'bg-status-warning-bg text-status-warning-text border-2 border-status-warning/20',
  },
  {
    key: 'done',
    label: 'Done',
    headStyle: 'bg-status-success-bg text-status-success-text border-2 border-status-success/20',
  },
];

function formatTaskDate(timestamp) {
  return timestamp ? new Date(timestamp).toLocaleDateString() : '';
}

export default function AgileBoardTab({ agileBoard }) {
  const columns = agileBoard.columns || {};
  const counts = agileBoard.counts || {};

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2">
        <h2 className="text-base font-extrabold text-text-primary">Agile Board</h2>
        <p className="text-xs text-text-muted">Real-time projection of task ledger task items.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 items-start font-semibold">
        {BOARD_COLUMNS.map((column) => {
          const columnTasks = columns[column.key] || [];

          return (
            <section
              key={column.key}
              className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-4 flex flex-col min-h-[420px]"
            >
              <div className={`flex justify-between items-center px-3 py-2 rounded-xl mb-4 font-extrabold text-xs uppercase tracking-wider leading-none ${column.headStyle}`}>
                <span>{column.label}</span>
                <span className="text-2xs leading-none px-2 py-0.5 rounded-full bg-surface-raised/70 font-extrabold">
                  {counts[column.key] || 0}
                </span>
              </div>

              <div className="flex-grow space-y-3">
                {columnTasks.map((task) => (
                  <article
                    key={task.id}
                    className="bg-surface-sunken border-2 border-border-subtle rounded-xl p-3.5 shadow-card space-y-3 hover:-translate-y-0.5 hover:shadow-card transition-all group"
                  >
                    <div className="flex justify-between items-start gap-1">
                      <span className="font-mono text-2xs text-text-muted uppercase tracking-wide font-extrabold select-all leading-none">{task.id}</span>
                      <span className={`inline-block px-1.5 py-0.5 rounded-md text-2xs font-extrabold uppercase leading-none ${task.priority === 'high' ? 'bg-status-danger-bg text-status-danger-text' : 'bg-surface-raised text-text-secondary'}`}>
                        {task.priority}
                      </span>
                    </div>

                    <div>
                      <h4 className="font-bold text-xs text-text-primary leading-snug">{task.title}</h4>
                      <p className="text-2xs font-semibold text-text-secondary mt-1 leading-relaxed">{task.description}</p>
                    </div>

                    {task.acceptance_criteria && task.acceptance_criteria.length > 0 && (
                      <div className="pt-2 border-t border-border-subtle text-2xs space-y-1">
                        <span className="font-bold text-text-muted block uppercase tracking-wider">Criteria</span>
                        <ul className="list-disc pl-3 text-text-secondary space-y-0.5 leading-snug">
                          {task.acceptance_criteria.map((criterion, cIdx) => <li key={cIdx}>{criterion}</li>)}
                        </ul>
                      </div>
                    )}

                    <div className="flex justify-between items-center text-2xs pt-1 leading-none font-bold">
                      <span className="text-text-muted uppercase">Actor: <span className="text-text-primary font-extrabold">{task.role}</span></span>
                      <span className="text-text-muted font-mono">{formatTaskDate(task.updated_at)}</span>
                    </div>
                  </article>
                ))}

                {columnTasks.length === 0 && (
                  <div className="border-2 border-dashed border-border-subtle rounded-xl p-6 text-center text-text-muted text-2xs flex flex-col items-center justify-center space-y-1 select-none">
                    <Info className="h-4 w-4 text-text-muted" />
                    <span>No tasks</span>
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
