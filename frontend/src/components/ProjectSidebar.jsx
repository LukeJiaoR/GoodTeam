import { Folder, Moon, Sun } from 'lucide-react';
import LanguageSwitcher from './LanguageSwitcher';

export default function ProjectSidebar({
  activeTab,
  actionableCount,
  darkMode,
  languageOptions,
  languagePreference,
  mobileMenuOpen,
  navGroups,
  projectKey,
  setActiveTab,
  setAgentsMemorySubTab,
  setDarkMode,
  setLanguagePreference,
  setMobileMenuOpen,
  setTheme,
  startContext,
  state,
  t,
  theme,
  updateUrl,
}) {
  return (
    <aside className={`w-full lg:w-[220px] shrink-0 bg-surface-raised text-text-secondary flex flex-col border-r-2 border-border-subtle lg:min-h-screen ${mobileMenuOpen ? 'block' : 'hidden lg:flex'}`}>
      <div className="p-5 border-b-2 border-border-subtle flex justify-between items-center bg-surface-sunken">
        <div>
          <span className="text-2xs uppercase tracking-widest text-accent-text font-bold">GoodTeam OS</span>
          <h2 className="text-sm font-bold tracking-tight text-text-primary mt-0.5">{state?.project_name || startContext.project_name || t('chrome.dashboard')}</h2>
        </div>
        <button
          onClick={() => updateUrl('')}
          className="text-2xs text-text-secondary hover:text-accent border-2 border-border-subtle hover:border-accent px-2.5 py-0.5 rounded-lg transition-all font-bold uppercase cursor-pointer"
        >
          {t('chrome.exit')}
        </button>
      </div>

      {/* Project Selector Indicator */}
      <div className="px-4 py-3 bg-surface-sunken border-b-2 border-border-subtle flex items-center space-x-2 text-xs font-bold text-text-secondary">
        <Folder className="h-4 w-4 text-accent shrink-0" />
        <span className="font-mono text-text-primary select-all">{projectKey}</span>
      </div>

      {/* Tab buttons - grouped by priority */}
      <nav className="flex-grow py-4 space-y-4 overflow-y-auto">
        {navGroups.map((group) => (
          <div key={group.title} className="space-y-1">
            <div className="px-4 py-1 text-2xs font-bold tracking-widest text-text-muted uppercase">
              {group.title}
            </div>
            {group.items.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              const showBadge = tab.id === 'approvals' && actionableCount > 0;
              return (
                <button
                  key={tab.id}
                  onClick={() => {
                    if (tab.id === 'agents_memory') {
                      setAgentsMemorySubTab('roles');
                    }
                    setActiveTab(tab.id);
                    setMobileMenuOpen(false);
                  }}
                  className={`w-full h-11 flex items-center justify-between px-4 transition-all cursor-pointer ${isActive ? 'bg-accent-bg border-l-[4px] border-accent text-accent-text font-bold' : 'hover:bg-surface-sunken border-l-[4px] border-transparent font-semibold text-text-secondary'}`}
                >
                  <div className="flex items-center space-x-3 min-w-0">
                    <Icon className={`h-4.5 w-4.5 shrink-0 ${isActive ? 'text-accent-text' : 'text-text-muted'}`} />
                    <span className="text-2xs uppercase tracking-wider truncate">{tab.label}</span>
                  </div>
                  {showBadge && (
                    <span className="bg-status-danger-bg text-status-danger-text text-2xs font-bold px-1.5 py-0.5 rounded-full border border-status-danger/20 shrink-0">
                      {actionableCount}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Sidebar Footer */}
      <div className="p-4 border-t-2 border-border-subtle flex flex-col gap-2 text-xs bg-surface-sunken font-bold text-text-muted">
        <div className="flex items-center justify-between gap-2">
          <div className="flex bg-surface-raised p-0.5 rounded-lg border border-border-subtle select-none">
            <button
              type="button"
              onClick={() => setTheme('vivid')}
              className={`px-2 py-0.5 rounded text-2xs font-bold transition-all cursor-pointer ${theme === 'vivid' ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
            >
              {t('theme.vivid')}
            </button>
            <button
              type="button"
              onClick={() => setTheme('minimal')}
              className={`px-2 py-0.5 rounded text-2xs font-bold transition-all cursor-pointer ${theme === 'minimal' ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
            >
              {t('theme.minimal')}
            </button>
          </div>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className="p-1.5 rounded-lg border-2 border-border-subtle hover:bg-surface-sunken transition-colors text-text-secondary cursor-pointer"
            aria-label={t('theme.toggle')}
          >
            {darkMode ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
        <LanguageSwitcher
          activeValue={languagePreference}
          onChange={setLanguagePreference}
          options={languageOptions}
        />
      </div>
    </aside>
  );
}
