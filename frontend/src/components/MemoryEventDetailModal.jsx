import { AlertTriangle, RefreshCw, X } from 'lucide-react';

export default function MemoryEventDetailModal({
  hasMemorySupersedeInfo,
  memoryEventDetail,
  memorySupersededBy,
  memorySupersedes,
  renderMemoryEventLink,
  setMemoryEventDetail,
}) {
  if (!memoryEventDetail) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="memory-event-detail-title"
        className="w-full max-w-2xl bg-white dark:bg-zinc-950 border-2 border-border-subtle dark:border-zinc-800 shadow-card dark:shadow-card rounded-2xl overflow-hidden"
      >
        <div className="px-5 py-4 bg-surface-base dark:bg-zinc-900/80 border-b-2 border-border-subtle dark:border-zinc-800 flex items-start justify-between gap-4">
          <div>
            <p className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">Memory Event</p>
            <h3 id="memory-event-detail-title" className="font-mono text-sm font-extrabold text-accent-text dark:text-emerald-400 mt-1 select-all">
              {memoryEventDetail.id}
            </h3>
          </div>
          <button
            type="button"
            onClick={() => setMemoryEventDetail(null)}
            className="bg-white dark:bg-zinc-800 hover:bg-status-danger-bg border-2 border-border-subtle dark:border-zinc-800 shadow-button dark:shadow-button text-zinc-550 hover:text-status-danger p-2 rounded-2xl transition-all duo-button-physics"
            aria-label="Close memory event detail"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 max-h-[70vh] overflow-y-auto">
          {memoryEventDetail.loading ? (
            <div className="flex items-center justify-center p-10 text-zinc-500 font-bold">
              <RefreshCw className="h-5 w-5 animate-spin mr-2 text-accent" />
              <span>Loading full memory event...</span>
            </div>
          ) : memoryEventDetail.error ? (
            <div className="bg-status-warning-bg dark:bg-amber-950/20 border-2 border-status-warning/30 p-4 rounded-2xl text-status-warning-text dark:text-amber-300 text-xs font-bold flex items-center space-x-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>{memoryEventDetail.error}</span>
            </div>
          ) : memoryEventDetail.data ? (
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-2xs font-bold">
                <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                  <span className="block uppercase tracking-wider text-text-muted mb-1">Role</span>
                  <span className="text-zinc-800 dark:text-zinc-200 uppercase">{memoryEventDetail.data.role || 'not recorded'}</span>
                </div>
                <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-100 dark:border-blue-900/50 rounded-xl p-3">
                  <span className="block uppercase tracking-wider text-blue-400 mb-1">Horizon</span>
                  <span className="text-blue-700 dark:text-blue-300 uppercase">{memoryEventDetail.data.horizon || 'not recorded'}</span>
                </div>
                <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3">
                  <span className="block uppercase tracking-wider text-text-muted mb-1">Timestamp</span>
                  <span className="font-mono text-zinc-700 dark:text-zinc-300">{memoryEventDetail.data.timestamp || 'not recorded'}</span>
                </div>
                <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 min-w-0">
                  <span className="block uppercase tracking-wider text-text-muted mb-1">Source</span>
                  <span className="font-mono text-zinc-700 dark:text-zinc-300 break-all">{memoryEventDetail.data.source || 'not recorded'}</span>
                </div>
              </div>
              {hasMemorySupersedeInfo && (
                <div className="bg-zinc-50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded-xl p-3 text-2xs font-bold space-y-2">
                  {memoryEventDetail.data.is_superseded && (
                    <span className="inline-flex items-center bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded uppercase text-2xs border border-amber-100 dark:border-amber-900/50">
                      Superseded
                    </span>
                  )}
                  {memorySupersedes && (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="uppercase tracking-wider text-text-muted">Supersedes:</span>
                      {renderMemoryEventLink(memorySupersedes)}
                    </div>
                  )}
                  {memorySupersededBy.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="uppercase tracking-wider text-text-muted">Superseded by:</span>
                      {memorySupersededBy.map((eventId) => (
                        <span key={eventId} className="flex items-center gap-1">
                          {renderMemoryEventLink(eventId)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div>
                <span className="block text-2xs font-extrabold uppercase tracking-wider text-text-muted mb-2">Full Event Text</span>
                <p className="text-xs font-mono leading-relaxed text-zinc-800 dark:text-zinc-200 bg-zinc-50/70 dark:bg-zinc-900/50 p-4 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-800 whitespace-pre-wrap select-all">
                  {memoryEventDetail.data.text || 'No event text recorded.'}
                </p>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
