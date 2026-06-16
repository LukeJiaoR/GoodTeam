import { Bug, RefreshCw, X } from 'lucide-react';

const SEVERITY_OPTIONS = [
  { value: 'p1', label: 'P1' },
  { value: 'p2', label: 'P2' },
  { value: 'p3', label: 'P3' },
];

export default function ReportBugModal({
  description,
  onClose,
  onSubmit,
  projectKey,
  reportBugError,
  setDescription,
  setSeverity,
  setTitle,
  severity,
  submitting,
  title,
}) {
  const canSubmit = description.trim().length > 0 && !submitting;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="report-bug-title"
        className="w-full max-w-lg bg-surface-overlay border-2 border-border-subtle shadow-card rounded-xl overflow-hidden flex flex-col max-h-[90vh]"
      >
        {/* Header */}
        <div className="px-5 py-4 bg-surface-sunken border-b-2 border-border-subtle flex items-start justify-between gap-4 shrink-0">
          <div>
            <p className="text-2xs font-bold uppercase tracking-wider text-text-muted">Quality</p>
            <h3 id="report-bug-title" className="text-sm font-bold text-accent-text mt-1 flex items-center gap-1.5">
              <Bug className="h-4 w-4" />
              <span>Report a Bug</span>
              {projectKey && (
                <code className="bg-surface-raised border border-border-subtle px-1.5 py-0.5 rounded text-2xs font-mono text-text-secondary">{projectKey}</code>
              )}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle hover:border-status-danger/30 shadow-button text-text-secondary hover:text-status-danger p-2 rounded-xl transition-all duo-button-physics cursor-pointer shrink-0"
            aria-label="Close report bug dialog"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Modal Body */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) onSubmit();
          }}
          className="p-6 overflow-y-auto space-y-5 flex-grow text-left"
        >
          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Short summary (optional)"
              className="w-full bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl px-4 py-2.5 text-xs focus:outline-none focus:border-accent transition-colors"
            />
          </div>

          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What went wrong? Steps to reproduce, expected vs. actual behavior."
              className="w-full h-28 bg-surface-sunken text-text-primary border-2 border-border-subtle rounded-xl p-4 text-xs font-mono leading-relaxed focus:outline-none focus:border-accent transition-colors resize-none"
            />
          </div>

          <div>
            <label className="block text-2xs text-text-muted uppercase tracking-wider mb-1.5">Severity</label>
            <div className="flex bg-surface-sunken p-1 rounded-lg w-fit border border-border-subtle select-none">
              {SEVERITY_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setSeverity(option.value)}
                  className={`px-4 py-1.5 rounded-md text-2xs font-bold transition-all cursor-pointer ${severity === option.value ? 'bg-accent text-text-inverse shadow-sm' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {reportBugError && (
            <div className="bg-status-danger-bg text-status-danger-text border-2 border-status-danger/30 rounded-xl px-4 py-2.5 text-xs font-semibold">
              {reportBugError}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold px-4 py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-lg text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center justify-center gap-2 cursor-pointer hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Bug className="h-3.5 w-3.5" />
              )}
              <span>{submitting ? 'Reporting…' : 'Report Bug'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
