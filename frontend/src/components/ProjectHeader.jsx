import { Menu, X } from 'lucide-react';

export default function ProjectHeader({
  activeRun,
  getStatusLabel,
  headBadge,
  headerStatus,
  mobileMenuOpen,
  mobileStatusClass,
  setMobileMenuOpen,
  startContext,
  state,
  t,
  updateUrl,
}) {
  return (
    <>
      {/* Top Bar for Mobile viewports */}
      <header className="lg:hidden h-[60px] shrink-0 bg-surface-raised text-text-primary flex items-center justify-between px-4 border-b-2 border-border-subtle shadow-sm">
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="p-1.5 rounded-xl border-2 border-border-subtle hover:bg-surface-sunken transition-colors cursor-pointer"
        >
          {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <span className="font-bold text-sm tracking-tight text-text-primary">{state?.project_name || t('chrome.dashboard')}</span>
        {headerStatus.toLowerCase() === 'complete' ? (
          <div className="bg-status-success-bg text-status-success-text font-bold text-xs px-2.5 py-1 rounded-full border-2 border-status-success/20 flex items-center space-x-1">
            <span>✅</span>
            <span>{t('status.complete')}</span>
          </div>
        ) : (
          <div className={`px-2.5 py-0.5 rounded-full text-2xs uppercase font-bold border ${activeRun ? 'bg-status-info-bg text-status-info-text border-status-info/30' : mobileStatusClass}`}>
            {getStatusLabel(headerStatus, t)}
          </div>
        )}
      </header>

      {/* Top Header Bar on Desktop - 60px high, white background, bottom border */}
      <header className="hidden lg:flex h-[60px] shrink-0 border-b-2 border-border-subtle bg-surface-raised items-center justify-between px-6 shadow-sm">
        <div className="flex items-center space-x-3">
          <div
            onClick={() => updateUrl('')}
            className="w-8 h-8 rounded-lg bg-accent border-2 border-accent-text flex items-center justify-center text-text-inverse font-bold text-lg shadow-button shrink-0 cursor-pointer hover:opacity-90"
          >
            G
          </div>
          <span onClick={() => updateUrl('')} className="font-bold text-lg tracking-tight text-text-primary cursor-pointer hover:text-accent transition-colors">GoodTeam</span>
          <span className="text-border-strong font-bold text-sm">|</span>
          <span className="text-text-secondary font-bold text-xs uppercase tracking-wider">
            {state?.project_name || startContext.project_name || t('chrome.dashboard')}
          </span>
        </div>

        <div className="flex items-center space-x-4">
          <span className="text-2xs font-mono font-bold text-text-muted uppercase hidden sm:inline">{t('chrome.runId')}: <code className="bg-surface-sunken border border-border-subtle px-2 py-0.5 rounded-lg text-text-secondary select-all font-mono">{state?.run_id || t('chrome.none')}</code></span>

          {headerStatus.toLowerCase() === 'complete' ? (
            <div className="bg-status-success-bg text-status-success-text font-bold text-base px-5 py-2.5 rounded-full border-2 border-status-success/30 flex items-center space-x-1.5 shadow-sm">
              <span>✅</span>
              <span>{t('status.complete')}</span>
            </div>
          ) : (
            <div className={`inline-flex items-center space-x-1.5 px-4 py-2 rounded-full text-xs font-semibold uppercase border ${headBadge.bg} ${headBadge.text} ${headBadge.border}`}>
              {activeRun && <span className="h-1.5 w-1.5 bg-status-info rounded-full animate-pulse" />}
              <span>{getStatusLabel(headerStatus, t)}</span>
            </div>
          )}
        </div>
      </header>
    </>
  );
}
