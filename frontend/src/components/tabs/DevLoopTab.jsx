import {
  AlertTriangle,
} from 'lucide-react';

import DevLoopDispatchForm from '../DevLoopDispatchForm';
import DevLoopPullRequestsPanel from '../DevLoopPullRequestsPanel';
import DevLoopWarningsPanel from '../DevLoopWarningsPanel';
import DevLoopWorktreesPanel from '../DevLoopWorktreesPanel';
import ReleaseReadinessCard from '../ReleaseReadinessCard';
import ServerActionAuditCard from '../ServerActionAuditCard';
import ServerActionsCard from '../ServerActionsCard';

function EmptyState({ children }) {
  return (
    <div className="bg-surface-raised border-2 border-border-subtle p-10 text-center rounded-2xl text-text-muted italic text-xs font-semibold">
      {children}
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
              <DevLoopPullRequestsPanel
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
          <DevLoopDispatchForm
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
