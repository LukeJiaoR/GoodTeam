import {
  Check,
  CheckCircle,
  Clock,
  Copy,
  ExternalLink,
  X,
} from 'lucide-react';

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

export default function DevLoopPullRequestsPanel({
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
