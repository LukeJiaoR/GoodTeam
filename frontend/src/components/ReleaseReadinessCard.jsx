import {
  Check,
  CheckCircle,
  Clock,
  Copy,
  X,
} from 'lucide-react';

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

function CommandRow({ command, label, copyLandCommand, copiedText, copyFailedText }) {
  if (!command) return null;

  return (
    <div className="flex items-center gap-2 min-w-0">
      <code
        className="flex-1 min-w-0 bg-surface-sunken border border-border-subtle px-2 py-1 rounded text-text-secondary font-mono text-2xs truncate"
        title={command}
      >
        {command}
      </code>
      <CommandCopyButton
        command={command}
        label={label}
        copyLandCommand={copyLandCommand}
        copiedText={copiedText}
        copyFailedText={copyFailedText}
      />
    </div>
  );
}

export default function ReleaseReadinessCard({
  releaseReadiness,
  copyLandCommand,
  copiedText,
  copyFailedText,
}) {
  if (releaseReadiness?.available !== true) return null;

  const isCurrent = releaseReadiness.up_to_date === true;

  return (
    <div className="bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-xs font-extrabold text-text-secondary uppercase tracking-wider">Release Readiness</h3>
          <p className="text-2xs text-text-muted mt-1">Pinned release metadata only. Commands are copied for manual execution.</p>
        </div>
        <span
          className={`inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-2xs font-extrabold uppercase border ${
            isCurrent
              ? 'bg-status-success-bg text-status-success-text border-status-success/30'
              : 'bg-status-info-bg text-status-info-text border-status-info/30'
          }`}
        >
          {isCurrent ? <CheckCircle className="h-3 w-3 shrink-0" /> : <Clock className="h-3 w-3 shrink-0" />}
          <span>{isCurrent ? 'release current' : 'ready to graduate'}</span>
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-2xs font-semibold text-text-secondary">
        <div className="bg-surface-sunken border border-border-subtle rounded-xl p-3">
          <div className="uppercase tracking-wider text-text-muted font-extrabold">Dev HEAD</div>
          <code className="block mt-1 font-mono text-text-primary truncate" title={releaseReadiness.dev_head || ''}>
            {releaseReadiness.dev_head ? releaseReadiness.dev_head.slice(0, 12) : 'unknown'}
          </code>
        </div>
        <div className="bg-surface-sunken border border-border-subtle rounded-xl p-3">
          <div className="uppercase tracking-wider text-text-muted font-extrabold">Release HEAD</div>
          <code className="block mt-1 font-mono text-text-primary truncate" title={releaseReadiness.release_head || ''}>
            {releaseReadiness.release_head ? releaseReadiness.release_head.slice(0, 12) : releaseReadiness.release_exists ? 'unknown' : 'not created'}
          </code>
        </div>
      </div>

      <div className="space-y-2">
        <CommandRow
          command={releaseReadiness.release_up_command}
          label="Copy release-up command"
          copyLandCommand={copyLandCommand}
          copiedText={copiedText}
          copyFailedText={copyFailedText}
        />
        <CommandRow
          command={releaseReadiness.graduate_command}
          label="Copy graduate command"
          copyLandCommand={copyLandCommand}
          copiedText={copiedText}
          copyFailedText={copyFailedText}
        />
      </div>
    </div>
  );
}
