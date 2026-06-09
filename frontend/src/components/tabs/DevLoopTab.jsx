import {
  AlertTriangle,
  Check,
  CheckCircle,
  Clock,
  Copy,
  ExternalLink,
  Play,
  RefreshCw,
  X,
} from 'lucide-react';

import DevLoopWarningsPanel from '../DevLoopWarningsPanel';
import DevLoopWorktreesPanel from '../DevLoopWorktreesPanel';
import ReleaseReadinessCard from '../ReleaseReadinessCard';
import ServerActionAuditCard from '../ServerActionAuditCard';
import ServerActionsCard from '../ServerActionsCard';

const textInputClass =
  'w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl px-3 py-2 focus:outline-none focus:border-accent font-mono transition-colors';

const textAreaClass =
  'w-full bg-surface-raised text-text-primary border-2 border-border-subtle rounded-xl p-3 focus:outline-none focus:border-accent font-mono leading-relaxed transition-colors resize-none';

function EmptyState({ children }) {
  return (
    <div className="bg-surface-raised border-2 border-border-subtle p-10 text-center rounded-2xl text-text-muted italic text-xs font-semibold">
      {children}
    </div>
  );
}

function CommandCopyButton({ command, label, copyLandCommand, copiedText, copyFailedText }) {
  const wasCopied = copiedText === command;
  const copyFailed = copyFailedText === command;
  const stateClass = wasCopied
    ? 'bg-status-success-bg text-status-success-text border-status-success/30 hover:bg-status-success-bg'
    : copyFailed
      ? 'bg-status-danger-bg text-status-danger-text border-status-danger/30 hover:bg-status-danger-bg'
      : 'bg-surface-raised hover:bg-status-info-bg hover:text-status-info-text border-border-subtle text-text-secondary';

  return (
    <button
      type="button"
      onClick={() => copyLandCommand(command)}
      className={`border-2 shadow-button font-bold p-1.5 rounded-xl text-xs transition-all duo-button-physics inline-flex items-center justify-center shrink-0 cursor-pointer gap-1 ${stateClass}`}
      title={`${label}: ${command}`}
    >
      {wasCopied ? (
        <>
          <Check className="h-3.5 w-3.5" />
          <span className="text-2xs font-extrabold uppercase">Copied!</span>
        </>
      ) : copyFailed ? (
        <>
          <X className="h-3.5 w-3.5" />
          <span className="text-2xs font-extrabold uppercase">Copy failed</span>
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          <span className="text-2xs font-extrabold uppercase">{label}</span>
        </>
      )}
    </button>
  );
}

function PullRequestsPanel({
  devLoopData,
  getBadgeStyle,
  copyLandCommand,
  copiedText,
  copyFailedText,
}) {
  const pullRequests = Array.isArray(devLoopData.pull_requests) ? devLoopData.pull_requests : [];

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-extrabold text-text-secondary uppercase tracking-wider">Open PRs & CI Status</h3>
      {pullRequests.length > 0 ? (
        <div className="grid grid-cols-1 gap-4">
          {pullRequests.map((pr, idx) => {
            const prNumber = pr?.number;
            const isPreReviewed =
              prNumber !== undefined &&
              Array.isArray(devLoopData.pre_reviews) &&
              devLoopData.pre_reviews.some((review) => review && review.pr === prNumber && review.present);

            let ciStatus = String(pr?.ci || 'none').toLowerCase().trim();
            if (ciStatus === 'pass') ciStatus = 'complete';
            if (ciStatus === 'fail') ciStatus = 'failed';
            const ciStyle = getBadgeStyle(ciStatus);
            const CIIcon = ciStyle.icon;

            return (
              <article
                key={prNumber !== undefined ? prNumber : idx}
                className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 hover:-translate-y-0.5 hover:shadow-card transition-all duration-200 flex flex-col sm:flex-row justify-between sm:items-center gap-4"
              >
                <div className="space-y-1.5 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    {prNumber !== undefined && (
                      <span className="font-mono text-xs font-bold text-text-muted">#{prNumber}</span>
                    )}
                    <h4 className="font-extrabold text-sm text-text-primary">
                      {pr?.title || 'Untitled Pull Request'}
                    </h4>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-2xs font-bold text-text-muted">
                    <span className="bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded text-text-secondary font-mono text-2xs">
                      {pr?.head_branch || 'no branch'}
                    </span>
                    {pr?.land_command && (
                      <code
                        className="bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded text-text-secondary font-mono text-2xs truncate max-w-[200px] sm:max-w-xs md:max-w-md lg:max-w-lg"
                        title={pr.land_command}
                      >
                        {pr.land_command}
                      </code>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2 shrink-0">
                  <span className={`inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-2xs font-extrabold uppercase border ${ciStyle.bg} ${ciStyle.text} ${ciStyle.border}`}>
                    <CIIcon className="h-3 w-3 shrink-0" />
                    <span>CI: {pr?.ci || 'none'}</span>
                  </span>

                  {isPreReviewed && (
                    <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-2xs font-extrabold uppercase border bg-status-info-bg text-status-info-text border-status-info/30">
                      pre-reviewed
                    </span>
                  )}

                  {pr?.land_ready === true && (
                    <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-2xs font-extrabold uppercase border bg-status-success-bg text-status-success-text border-status-success/30">
                      <CheckCircle className="h-3 w-3 shrink-0" />
                      <span>Ready to land</span>
                    </span>
                  )}
                  {pr?.land_ready === false && (
                    <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-2xs font-extrabold uppercase border bg-surface-sunken text-text-secondary border-border-subtle">
                      <Clock className="h-3 w-3 shrink-0" />
                      <span>not ready</span>
                    </span>
                  )}

                  {pr?.land_command && (
                    <CommandCopyButton
                      command={pr.land_command}
                      label="Copy land command"
                      copyLandCommand={copyLandCommand}
                      copiedText={copiedText}
                      copyFailedText={copyFailedText}
                    />
                  )}

                  {pr?.url && (
                    <a
                      href={pr.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="bg-surface-raised hover:bg-status-success-bg hover:text-status-success-text border-2 border-border-subtle shadow-button text-text-secondary font-bold p-1.5 rounded-xl text-xs transition-all duo-button-physics inline-flex items-center justify-center shrink-0 cursor-pointer"
                      title="View Pull Request on GitHub"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState>No open pull requests.</EmptyState>
      )}
    </div>
  );
}

function DispatchForm({
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

export default function DevLoopTab({
  devLoopData,
  loadingDevLoop,
  devLoopError,
  formatHumanTime,
  Spinner,
  getBadgeStyle,
  copyLandCommand,
  copiedText,
  copyFailedText,
  FilePath,
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
  const hasVisibleDevLoopData =
    devLoopData &&
    (
      (Array.isArray(devLoopData.worktrees) && devLoopData.worktrees.length > 0) ||
      (Array.isArray(devLoopData.pull_requests) && devLoopData.pull_requests.length > 0) ||
      devLoopData.release_readiness?.available === true ||
      Object.keys(devLoopData.server_actions?.actions || {}).length > 0 ||
      devLoopData.server_action_audit?.available === true ||
      (Array.isArray(devLoopData.warnings) && devLoopData.warnings.length > 0)
    );

  return (
    <div className="space-y-6">
      <div className="border-b-2 border-border-subtle pb-2 flex justify-between items-end flex-wrap gap-2">
        <div>
          <h2 className="text-base font-extrabold text-text-primary">Dev Loop</h2>
          <p className="text-xs text-text-muted">Observability view of in-flight dev loops, including active worktrees, open PRs, CI statuses, and pre-reviews.</p>
        </div>
        {devLoopData?.generated_at && (
          <span className="text-2xs font-mono font-bold text-text-muted uppercase bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded-lg">
            Freshness: {formatHumanTime(devLoopData.generated_at)}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        <div className="lg:col-span-2 space-y-6">
          {loadingDevLoop && <Spinner />}

          {devLoopError && (
            <div className="bg-status-danger-bg border-2 border-status-danger/30 p-4 rounded-xl text-status-danger-text text-xs font-semibold flex items-start gap-2 max-w-2xl">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <h4 className="font-extrabold uppercase tracking-wide text-2xs">Error loading Dev Loop status</h4>
                <p className="mt-1">{devLoopError}</p>
              </div>
            </div>
          )}

          {!loadingDevLoop && !devLoopError && !hasVisibleDevLoopData && (
            <EmptyState>No active Dev Loop details recorded.</EmptyState>
          )}

          {!loadingDevLoop && !devLoopError && devLoopData && (
            <div className="space-y-6 max-w-4xl">
              <ReleaseReadinessCard
                releaseReadiness={devLoopData.release_readiness}
                copyLandCommand={copyLandCommand}
                copiedText={copiedText}
                copyFailedText={copyFailedText}
              />
              <ServerActionsCard serverActions={devLoopData.server_actions} />
              <ServerActionAuditCard audit={devLoopData.server_action_audit} />
              <DevLoopWarningsPanel warnings={devLoopData.warnings} />
              <PullRequestsPanel
                devLoopData={devLoopData}
                getBadgeStyle={getBadgeStyle}
                copyLandCommand={copyLandCommand}
                copiedText={copiedText}
                copyFailedText={copyFailedText}
              />
              <DevLoopWorktreesPanel worktrees={devLoopData.worktrees} FilePath={FilePath} />
            </div>
          )}
        </div>

        <div className="lg:col-span-1">
          <DispatchForm
            dispatchSuccess={dispatchSuccess}
            dispatchError={dispatchError}
            handleDispatchTrack={handleDispatchTrack}
            dispatchName={dispatchName}
            setDispatchName={setDispatchName}
            dispatchRole={dispatchRole}
            setDispatchRole={setDispatchRole}
            dispatchCustomRole={dispatchCustomRole}
            setDispatchCustomRole={setDispatchCustomRole}
            dispatchAdapter={dispatchAdapter}
            setDispatchAdapter={setDispatchAdapter}
            dispatchCustomAdapter={dispatchCustomAdapter}
            setDispatchCustomAdapter={setDispatchCustomAdapter}
            dispatchTask={dispatchTask}
            setDispatchTask={setDispatchTask}
            dispatchConstraints={dispatchConstraints}
            setDispatchConstraints={setDispatchConstraints}
            dispatchVerify={dispatchVerify}
            setDispatchVerify={setDispatchVerify}
            dispatchLoading={dispatchLoading}
            workerRoles={workerRoles}
            roleBackendOptions={roleBackendOptions}
          />
        </div>
      </div>
    </div>
  );
}
