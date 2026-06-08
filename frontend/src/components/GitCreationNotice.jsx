import { AlertTriangle, CheckCircle } from 'lucide-react';

export default function GitCreationNotice({
  gitRemoteCreated,
  gitWarnings,
  setShowGitNotice,
  showGitNotice,
}) {
  if (!showGitNotice) {
    return null;
  }

  return (
    <div className="bg-surface-raised border-b-2 border-border-subtle px-6 py-4 flex flex-col md:flex-row md:items-start justify-between gap-4 animate-fade-in shrink-0">
      <div className="flex-grow space-y-1">
        <div className="flex items-center space-x-2">
          <span className="p-1 bg-status-success-bg dark:bg-emerald-950/20 text-accent-text dark:text-emerald-400 rounded-lg">
            <CheckCircle className="h-4 w-4" />
          </span>
          <span className="font-extrabold text-sm text-text-primary">Git Repository Initialized</span>
        </div>
        {gitRemoteCreated && (
          <p className="text-xs text-text-secondary pl-8">
            GitHub remote repository was successfully created and initial commit was pushed!
          </p>
        )}
        {gitWarnings.length > 0 && (
          <div className="space-y-1 mt-2 pl-8">
            <p className="text-xs font-bold text-status-warning-text flex items-center gap-1">
              <AlertTriangle className="h-3.5 w-3.5" />
              Git Warnings:
            </p>
            <ul className="list-disc pl-5 text-2xs text-text-muted space-y-0.5">
              {gitWarnings.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <button
        onClick={() => setShowGitNotice(false)}
        className="bg-surface-raised hover:bg-surface-sunken border-2 border-border-subtle shadow-button text-text-secondary font-bold px-3 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer shrink-0"
      >
        Dismiss
      </button>
    </div>
  );
}
