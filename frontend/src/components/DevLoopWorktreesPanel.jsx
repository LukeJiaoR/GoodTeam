function EmptyState({ children }) {
  return (
    <div className="bg-surface-raised border-2 border-border-subtle p-10 text-center rounded-2xl text-text-muted italic text-xs font-semibold">
      {children}
    </div>
  );
}

export default function DevLoopWorktreesPanel({ worktrees, FilePath }) {
  const activeWorktrees = Array.isArray(worktrees) ? worktrees : [];

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-extrabold text-text-secondary uppercase tracking-wider">Active Worktrees</h3>
      {activeWorktrees.length > 0 ? (
        <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden font-semibold text-xs">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-surface-sunken border-b-2 border-border-subtle text-2xs font-extrabold uppercase tracking-wider text-text-muted">
                  <th className="px-5 py-3">Path</th>
                  <th className="px-5 py-3">Branch</th>
                  <th className="px-5 py-3">Commit Head</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {activeWorktrees.map((worktree, idx) => (
                  <tr key={idx} className="hover:bg-surface-sunken text-text-secondary">
                    <td className="px-5 py-3 font-mono text-2xs truncate max-w-xs" title={worktree?.path}>
                      <FilePath path={worktree?.path} segments={2} />
                    </td>
                    <td className="px-5 py-3 font-mono text-2xs text-text-primary font-bold">
                      {worktree?.branch || 'no branch'}
                    </td>
                    <td className="px-5 py-3 font-mono text-2xs">
                      {worktree?.head ? (
                        <code className="bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded text-text-secondary font-mono text-2xs">
                          {worktree.head.slice(0, 7)}
                        </code>
                      ) : (
                        <span className="text-text-muted italic">n/a</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <EmptyState>No active git worktrees.</EmptyState>
      )}
    </div>
  );
}
