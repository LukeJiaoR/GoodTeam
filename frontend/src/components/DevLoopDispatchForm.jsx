import {
  AlertTriangle,
  CheckCircle,
  Play,
  RefreshCw,
} from 'lucide-react';

const textInputClass =
  'w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 focus:outline-none focus:border-accent font-mono transition-colors';

const textAreaClass =
  'w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl p-3 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none';

export default function DevLoopDispatchForm({
  dispatchSuccess,
  dispatchError,
  handleDispatchTrack,
  dispatchName,
  setDispatchName,
  dispatchRole,
  setDispatchRole,
  dispatchCustomRole,
  setDispatchCustomRole,
  dispatchAdapter,
  setDispatchAdapter,
  dispatchCustomAdapter,
  setDispatchCustomAdapter,
  dispatchTask,
  setDispatchTask,
  dispatchConstraints,
  setDispatchConstraints,
  dispatchVerify,
  setDispatchVerify,
  dispatchLoading,
  workerRoles,
  roleBackendOptions,
}) {
  const visibleRoles = ['backend', 'frontend', ...workerRoles.filter((role) => role !== 'backend' && role !== 'frontend')];

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card p-5 rounded-2xl space-y-4">
      <div className="border-b border-border-subtle pb-2">
        <h3 className="font-extrabold text-sm text-text-primary">Dispatch a Track</h3>
        <p className="text-2xs text-text-muted mt-0.5">Start a new parallel development track.</p>
      </div>

      {dispatchSuccess && (
        <div className="bg-status-success-bg border-2 border-status-success/30 p-3 rounded-xl text-status-success-text text-xs font-semibold space-y-1">
          <div className="flex items-center gap-1 font-bold">
            <CheckCircle className="h-4 w-4 shrink-0" />
            <span>Track started asynchronously!</span>
          </div>
          <ul className="list-disc pl-4 font-mono text-2xs space-y-0.5 mt-1">
            <li>Run ID: {dispatchSuccess.run_id}</li>
            <li>Branch: {dispatchSuccess.branch}</li>
            <li>Worktree: {dispatchSuccess.worktree}</li>
          </ul>
        </div>
      )}

      {dispatchError && (
        <div className="bg-status-danger-bg border-2 border-status-danger/30 p-3 rounded-xl text-status-danger-text text-xs font-semibold flex items-start gap-1.5">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <div className="break-all">{dispatchError}</div>
        </div>
      )}

      <form onSubmit={handleDispatchTrack} className="space-y-3.5 text-xs font-semibold">
        <div>
          <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Track Name</label>
          <input
            type="text"
            value={dispatchName}
            onChange={(event) => setDispatchName(event.target.value)}
            placeholder="e.g. feat-login-page"
            disabled={dispatchLoading}
            className={textInputClass}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Role</label>
            <select
              value={dispatchRole}
              onChange={(event) => setDispatchRole(event.target.value)}
              disabled={dispatchLoading}
              className={`${textInputClass} px-2.5`}
            >
              {visibleRoles.map((role) => (
                <option key={role} value={role}>{role}</option>
              ))}
              <option value="custom">Custom...</option>
            </select>
          </div>

          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Adapter</label>
            <select
              value={dispatchAdapter}
              onChange={(event) => setDispatchAdapter(event.target.value)}
              disabled={dispatchLoading}
              className={`${textInputClass} px-2.5`}
            >
              {roleBackendOptions.map((option) => (
                <option key={option} value={option}>
                  {option === 'codex' ? 'Codex CLI' : option === 'anthropic' || option === 'claude' ? 'Claude CLI' : option}
                </option>
              ))}
              <option value="custom">Custom...</option>
            </select>
          </div>
        </div>

        {dispatchRole === 'custom' && (
          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Custom Role Name</label>
            <input
              type="text"
              value={dispatchCustomRole}
              onChange={(event) => setDispatchCustomRole(event.target.value)}
              placeholder="e.g. backend_audit"
              disabled={dispatchLoading}
              className={textInputClass}
            />
          </div>
        )}

        {dispatchAdapter === 'custom' && (
          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Custom Adapter Name</label>
            <input
              type="text"
              value={dispatchCustomAdapter}
              onChange={(event) => setDispatchCustomAdapter(event.target.value)}
              placeholder="e.g. openai"
              disabled={dispatchLoading}
              className={textInputClass}
            />
          </div>
        )}

        <div>
          <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Task Details</label>
          <textarea
            value={dispatchTask}
            onChange={(event) => setDispatchTask(event.target.value)}
            placeholder="Describe the task for this worker..."
            disabled={dispatchLoading}
            className={`${textAreaClass} h-24`}
          />
        </div>

        <div>
          <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Constraints (Optional)</label>
          <textarea
            value={dispatchConstraints}
            onChange={(event) => setDispatchConstraints(event.target.value)}
            placeholder="Optional constraints to restrict the worker..."
            disabled={dispatchLoading}
            className={`${textAreaClass} h-16`}
          />
        </div>

        <div>
          <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1">Verification Command (Optional)</label>
          <input
            type="text"
            value={dispatchVerify}
            onChange={(event) => setDispatchVerify(event.target.value)}
            placeholder="e.g. npm run test"
            disabled={dispatchLoading}
            className={textInputClass}
          />
        </div>

        <button
          type="submit"
          disabled={dispatchLoading}
          className="w-full bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold py-2.5 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {dispatchLoading ? (
            <>
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              <span>Dispatching...</span>
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>Dispatch Track</span>
            </>
          )}
        </button>
      </form>
    </div>
  );
}
