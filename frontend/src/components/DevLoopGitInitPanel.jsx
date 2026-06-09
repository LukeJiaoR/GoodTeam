import {
  AlertTriangle,
  GitBranch,
  Info,
  RefreshCw,
} from 'lucide-react';

export default function DevLoopGitInitPanel({
  createRemote,
  setCreateRemote,
  remotePrivate,
  setRemotePrivate,
  gitInitLoading,
  gitInitWarnings,
  onInitializeGit,
}) {
  const warnings = Array.isArray(gitInitWarnings) ? gitInitWarnings : [];

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-6 space-y-5 max-w-2xl">
      <div className="flex items-start gap-3">
        <div className="bg-surface-sunken border border-border-subtle rounded-xl p-2 text-accent-text shrink-0">
          <GitBranch className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-extrabold text-text-primary uppercase tracking-wider">
            This project is not a Git repository yet
          </h3>
          <p className="text-xs text-text-muted mt-1 leading-relaxed font-semibold">
            Initialize Git before using worktrees, worker dispatch, pull-request tracking, and release readiness from Dev Loop.
          </p>
        </div>
      </div>

      {warnings.length > 0 && (
        <div className="bg-status-warning-bg border-2 border-status-warning/30 p-4 rounded-xl text-status-warning-text text-xs font-semibold space-y-1">
          <div className="flex items-center space-x-1.5">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="font-extrabold uppercase text-2xs tracking-wider">Warnings during Git Init</span>
          </div>
          <ul className="list-disc pl-5 font-medium space-y-1 mt-1 text-status-warning-text">
            {warnings.map((warning, idx) => (
              <li key={idx}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="bg-surface-sunken border border-border-subtle rounded-xl p-4 space-y-4">
        <label className="flex items-center gap-2.5 cursor-pointer text-xs font-bold text-text-secondary">
          <input
            type="checkbox"
            checked={createRemote}
            onChange={(event) => setCreateRemote(event.target.checked)}
            disabled={gitInitLoading}
            className="h-4 w-4 rounded border-border-subtle text-accent focus:ring-accent disabled:opacity-50"
          />
          <span>Create GitHub repository & push</span>
        </label>

        {createRemote && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">Repository Visibility</span>
              <div className="bg-surface-raised border border-border-subtle rounded-xl p-1 flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setRemotePrivate(true)}
                  disabled={gitInitLoading}
                  className={`px-3 py-1.5 rounded-lg text-2xs font-extrabold uppercase transition-all duo-button-physics ${
                    remotePrivate
                      ? 'bg-accent text-text-inverse border border-accent-text shadow-button'
                      : 'text-text-muted hover:text-text-primary'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  Private
                </button>
                <button
                  type="button"
                  onClick={() => setRemotePrivate(false)}
                  disabled={gitInitLoading}
                  className={`px-3 py-1.5 rounded-lg text-2xs font-extrabold uppercase transition-all duo-button-physics ${
                    !remotePrivate
                      ? 'bg-accent text-text-inverse border border-accent-text shadow-button'
                      : 'text-text-muted hover:text-text-primary'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  Public
                </button>
              </div>
            </div>

            <div className="bg-status-info-bg border border-status-info/30 rounded-xl p-3 text-status-info-text text-2xs leading-relaxed font-semibold space-y-1.5">
              <p className="font-extrabold flex items-center gap-1 text-xs">
                <Info className="h-3.5 w-3.5 shrink-0" />
                GitHub Authentication Requirement
              </p>
              <p>
                Requires a GitHub Personal Access Token in the environment as <strong className="font-bold font-mono">GITHUB_TOKEN</strong> or <strong className="font-bold font-mono">GH_TOKEN</strong>.
              </p>
              <p>
                Classic tokens need <strong className="font-bold font-mono">repo</strong>. Fine-grained tokens need repository administration and contents write access.
              </p>
            </div>
          </div>
        )}
      </div>

      <button
        type="button"
        disabled={gitInitLoading}
        onClick={onInitializeGit}
        className="w-full bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold py-2.5 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center space-x-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {gitInitLoading ? (
          <>
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            <span>Initializing Git...</span>
          </>
        ) : (
          <>
            <GitBranch className="h-3.5 w-3.5" />
            <span>Initialize Git repository</span>
          </>
        )}
      </button>
    </div>
  );
}
