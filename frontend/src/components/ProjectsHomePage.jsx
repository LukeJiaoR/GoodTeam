import {
  AlertTriangle,
  Check,
  Folder,
  LayoutDashboard,
  Lock,
  Moon,
  Sun,
  Trash2,
} from 'lucide-react';
import FilePath from './FilePath';
import Spinner from './Spinner';
import { getBadgeStyle } from '../utils/status';
import { formatHumanTime } from '../utils/time';

export default function ProjectsHomePage({
  actionLoading,
  darkMode,
  data,
  directoryPickerModal,
  error,
  handleDeleteProject,
  handleTerminate,
  loading,
  message,
  newProjectModal,
  renderLanguageSwitcher,
  setDarkMode,
  setNewProjectModalOpen,
  setStartMode,
  setTheme,
  t,
  theme,
  updateUrl,
}) {
  const projectItems = data?.project_items || [];
  const legacyState = data?.legacy_state;
  const openNewProject = () => {
    setStartMode('new_project');
    setNewProjectModalOpen(true);
  };

  return (
    <>
      <div className="min-h-screen bg-surface-base text-text-primary transition-colors flex flex-col font-sans antialiased">
        {/* Header - 60px height, white background, bottom border */}
        <header className="h-[60px] border-b-2 border-border-subtle bg-surface-raised px-6 flex items-center justify-between shadow-sm shrink-0">
          <div className="flex items-center space-x-3">
            <div className="p-1.5 bg-accent border-2 border-accent-text text-text-inverse rounded-xl shadow-button">
              <LayoutDashboard className="h-5 w-5" />
            </div>
            <div>
              <span className="text-2xs uppercase tracking-wider font-bold text-text-muted">{t('chrome.multiProjectEyebrow')}</span>
              <h1 className="text-base font-bold tracking-tight text-text-primary">{t('chrome.goodteamProjects')}</h1>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={openNewProject}
              className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-4 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics hover:opacity-90 cursor-pointer"
            >
              {t('chrome.newProject')}
            </button>
            <div className="flex bg-surface-sunken p-1 rounded-xl border border-border-subtle select-none">
              <button
                type="button"
                onClick={() => setTheme('vivid')}
                className={`px-3 py-1 rounded-lg text-2xs font-bold transition-all cursor-pointer ${theme === 'vivid' ? 'bg-surface-raised shadow-sm text-accent-text border border-border-subtle' : 'text-text-muted hover:text-text-secondary'}`}
              >
                {t('theme.vivid')}
              </button>
              <button
                type="button"
                onClick={() => setTheme('minimal')}
                className={`px-3 py-1 rounded-lg text-2xs font-bold transition-all cursor-pointer ${theme === 'minimal' ? 'bg-surface-raised shadow-sm text-accent-text border border-border-subtle' : 'text-text-muted hover:text-text-secondary'}`}
              >
                {t('theme.minimal')}
              </button>
            </div>
            {renderLanguageSwitcher('wide')}
            <button
              onClick={() => setDarkMode(!darkMode)}
              className="bg-surface-raised p-2 border-2 border-border-subtle shadow-button text-text-secondary rounded-xl transition-all duo-button-physics"
              aria-label={t('theme.toggle')}
            >
              {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </header>

        {/* Global Toast */}
        {message && (
          <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-xl text-xs font-bold border-2 flex items-center space-x-2 animate-fade-in-down ${message.isError ? 'bg-status-danger-bg text-status-danger-text border-status-danger' : 'bg-status-success-bg text-accent-text border-accent'}`}>
            {message.isError ? <AlertTriangle className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            <span>{message.text}</span>
          </div>
        )}

        <main className="max-w-6xl mx-auto w-full px-6 py-8 flex-grow space-y-8">
          {/* Legacy notice */}
          {legacyState && (
            <div className="bg-status-warning-bg dark:bg-amber-950/20 border-2 border-status-warning/30 p-5 rounded-2xl flex flex-col md:flex-row md:items-center justify-between text-status-warning-text dark:text-amber-300 text-xs gap-4 shadow-card dark:shadow-none">
              <div>
                <span className="font-extrabold text-sm block mb-1">Legacy Active Run Detected</span>
                <span>Active path: <code className="bg-white/60 dark:bg-amber-900/40 px-1.5 py-0.5 rounded-lg border border-amber-250 dark:border-amber-900 font-mono">{legacyState.project_key}</code> currently at <code className="bg-white/60 dark:bg-amber-900/40 px-1.5 py-0.5 rounded-lg border border-amber-250 dark:border-amber-900 font-mono">{legacyState.next_node || legacyState.current_node}</code></span>
              </div>
              <button
                onClick={() => updateUrl(legacyState.project_key)}
                className="bg-status-warning border-2 border-status-warning-text shadow-button text-white font-bold px-4 py-1.5 rounded-2xl text-2xs uppercase tracking-wider transition-all duo-button-physics shrink-0 self-start md:self-auto"
              >
                Resume Run
              </button>
            </div>
          )}

          {/* Project List */}
          {loading ? (
            <Spinner label={t('chrome.loading')} />
          ) : error ? (
            <div className="bg-status-danger-bg border-2 border-status-danger p-6 rounded-xl text-status-danger flex items-center space-x-3">
              <AlertTriangle className="h-6 w-6 shrink-0" />
              <span className="font-bold text-sm">{error}</span>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="flex justify-between items-center border-b-2 border-border-subtle pb-4">
                <h2 className="text-sm font-bold uppercase tracking-widest text-text-muted">Registered Projects ({projectItems.length})</h2>
                <button
                  onClick={openNewProject}
                  className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-4 py-2 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics flex items-center gap-1.5 hover:opacity-90 cursor-pointer"
                >
                  <span>{t('chrome.newProject')}</span>
                </button>
              </div>

              {projectItems.length === 0 ? (
                <div className="bg-surface-raised border-2 border-border-subtle shadow-card p-12 text-center rounded-xl max-w-2xl mx-auto flex flex-col items-center">
                  <Folder className="h-16 w-16 mx-auto mb-4 text-text-muted" />
                  <h3 className="font-bold text-lg text-text-primary mb-2">No Projects Configured</h3>
                  <p className="text-xs text-text-secondary max-w-md mb-6 leading-relaxed">
                    Create a new project config to get started, adopt an existing repository, or drop a yaml config into <code className="bg-surface-sunken px-1.5 py-0.5 rounded border border-border-subtle text-xs font-mono">projects/*.local.yaml</code>.
                  </p>
                  <button
                    onClick={openNewProject}
                    className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-6 py-3 rounded-xl text-xs uppercase tracking-wider transition-all duo-button-physics hover:opacity-90 cursor-pointer"
                  >
                    + Create a Project
                  </button>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {projectItems.map((item) => {
                    const isActive = item.status === 'active';
                    const activeBadge = getBadgeStyle(item.status);
                    return (
                      <article
                        key={item.project_key}
                        className={`bg-surface-raised border-2 rounded-xl shadow-card overflow-hidden flex flex-col h-full group transition-all duration-200 theme-card-hover ${isActive ? 'border-accent' : 'border-border-subtle'}`}
                      >
                        <div className="p-6 flex-grow">
                          <div className="flex items-start justify-between">
                            <div>
                              <h3 className="text-base font-bold text-text-primary group-hover:text-accent transition-colors">
                                {item.project_name}
                              </h3>
                              <p className="text-2xs font-mono text-text-muted mt-0.5 font-bold uppercase">
                                {item.project_key}
                              </p>
                            </div>
                            <span className={`inline-flex items-center space-x-1.5 px-3 py-1 rounded-full text-2xs font-bold uppercase border ${activeBadge.bg} ${activeBadge.text} ${activeBadge.border}`}>
                              {isActive && <span className="h-1.5 w-1.5 bg-status-info rounded-full animate-pulse" />}
                              <span>{item.status}</span>
                            </span>
                          </div>

                          <dl className="mt-6 grid grid-cols-2 gap-x-4 gap-y-3 text-2xs border-t-2 border-border-subtle pt-4 font-semibold">
                            <div>
                              <dt className="text-text-muted uppercase tracking-wider text-2xs mb-0.5">Model Profile</dt>
                              <dd className="text-text-secondary capitalize font-bold">{item.model_profile}</dd>
                            </div>
                            <div>
                              <dt className="text-text-muted uppercase tracking-wider text-2xs mb-0.5">Delivery Mode</dt>
                              <dd className="text-accent font-mono font-bold">{item.delivery_mode}</dd>
                            </div>
                            <div>
                              <dt className="text-text-muted uppercase tracking-wider text-2xs mb-0.5">Next Action</dt>
                              <dd className="text-text-secondary font-mono truncate font-bold flex items-center gap-1">
                                {item.next_node && (item.next_node.includes('approval') || item.next_node.includes('gate') || item.next_node.startsWith('await_')) ? (
                                  <span className="inline-flex items-center gap-0.5 bg-status-warning-bg text-status-warning-text border border-status-warning/30 px-1 rounded text-2xs uppercase font-bold shrink-0">
                                    <Lock className="h-2.5 w-2.5" /> gate
                                  </span>
                                ) : null}
                                <span className="truncate">{item.next_node || 'no active run'}</span>
                              </dd>
                            </div>
                            <div>
                              <dt className="text-text-muted uppercase tracking-wider text-2xs mb-0.5">Last Active</dt>
                              <dd className="text-text-secondary truncate" title={item.last_action || 'none'}>
                                {item.last_action ? formatHumanTime(item.last_action) : 'none'}
                              </dd>
                            </div>
                            <div className="col-span-2">
                              <dt className="text-text-muted uppercase tracking-wider text-2xs mb-0.5">Config Path</dt>
                              <dd className="truncate text-text-muted font-mono text-2xs" title={item.project_config}><FilePath path={item.project_config} segments={2} /></dd>
                            </div>
                          </dl>
                        </div>

                        <div className="bg-surface-sunken px-6 py-4 border-t-2 border-border-subtle flex justify-between items-center">
                          <button
                            onClick={() => handleDeleteProject(item.project_key)}
                            disabled={actionLoading}
                            className="bg-surface-raised hover:bg-status-danger-bg border-2 border-border-subtle hover:border-status-danger/30 shadow-button text-status-danger p-2 rounded-xl transition-all duo-button-physics shrink-0 cursor-pointer"
                            title="Delete project config"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>

                          <div className="flex space-x-2">
                            {isActive && (
                              <button
                                onClick={() => handleTerminate(item.project_key)}
                                disabled={actionLoading}
                                className="bg-surface-raised hover:bg-status-warning-bg border-2 border-border-subtle hover:border-status-warning/30 shadow-button text-status-warning font-bold px-3 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer"
                              >
                                Terminate
                              </button>
                            )}
                            <button
                              onClick={() => updateUrl(item.project_key)}
                              className="bg-surface-raised border-2 border-border-subtle hover:border-border-strong shadow-button text-text-secondary font-bold px-4 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer"
                            >
                              {isActive ? 'Resume' : 'Start'}
                            </button>
                            <button
                              onClick={() => updateUrl(item.project_key)}
                              className="bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-4 py-1.5 rounded-xl text-2xs uppercase tracking-wider transition-all duo-button-physics cursor-pointer hover:opacity-90"
                            >
                              Open
                            </button>
                          </div>
                        </div>
                      </article>
                    );
                  })}

                  {/* "+" Tile Card */}
                  <button
                    onClick={openNewProject}
                    className="bg-surface-sunken border-2 border-dashed border-border-subtle rounded-xl p-6 flex flex-col items-center justify-center min-h-[200px] hover:border-accent hover:bg-surface-raised transition-all duration-200 group cursor-pointer theme-card-hover"
                  >
                    <div className="w-12 h-12 rounded-full border-2 border-dashed border-border-strong group-hover:border-accent flex items-center justify-center text-text-muted group-hover:text-accent transition-colors mb-3">
                      <span className="text-2xl font-bold font-sans">+</span>
                    </div>
                    <span className="text-xs font-bold text-text-secondary group-hover:text-accent-text uppercase tracking-wider">New Project</span>
                  </button>
                </div>
              )}
            </div>
          )}
        </main>
      </div>
      {newProjectModal}
      {directoryPickerModal}
    </>
  );
}
