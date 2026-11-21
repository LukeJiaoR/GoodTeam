import { useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  FolderKanban,
  GitBranch,
  GitPullRequestArrow,
  Menu,
  MessageSquareCode,
  SlidersHorizontal,
  Users,
  X,
} from 'lucide-react';
import { WorkspaceProvider, useWorkspace } from '../context/WorkspaceContext';
import { ToastProvider } from '../components/ui/ToastProvider';

const SSE_LABELS = {
  idle: '未建立实时流',
  connecting: '正在连接 SSE...',
  live: 'SSE 实时连接中',
  reconnecting: 'SSE 重连中...',
};

function AppShellInner() {
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const {
    projects,
    activeProjectId,
    activeProject,
    setActiveProjectId,
    pendingDeliveries,
    sseStatus,
  } = useWorkspace();

  const pid = activeProjectId ? encodeURIComponent(activeProjectId) : '';
  const navItems = [
    { to: '/projects', label: '项目', icon: FolderKanban, end: true },
    { to: pid ? `/projects/${pid}/chat` : '/chat', label: '协作对话', icon: MessageSquareCode },
    {
      to: pid ? `/projects/${pid}/delivery` : '/delivery',
      label: '交付审阅',
      icon: GitPullRequestArrow,
      badge: pendingDeliveries > 0 ? pendingDeliveries : null,
    },
    { to: pid ? `/projects/${pid}/team` : '/team', label: '团队与能力', icon: Users },
    { to: pid ? `/projects/${pid}/settings` : '/settings', label: '记忆与设置', icon: SlidersHorizontal },
  ];

  const handleProjectSwitch = (nextId) => {
    setActiveProjectId(nextId);
    if (!nextId) return;
    const match = location.pathname.match(/\/(chat|delivery|team|settings)$/);
    const section = match ? match[1] : 'chat';
    navigate(`/projects/${encodeURIComponent(nextId)}/${section}`);
  };

  return (
    <div className="product-shell v2-shell">
      <a className="product-skip-link" href="#main-content">
        跳至主内容
      </a>

      {/* Mobile Top Bar */}
      <div className="v2-mobile-bar">
        <Link to="/projects" className="v2-brand__title">
          <span className="v2-brand__mark">GT</span>
          <span>GoodTeam</span>
        </Link>
        <button
          type="button"
          className="projects-icon-button"
          onClick={() => setMobileOpen((v) => !v)}
          aria-label={mobileOpen ? '关闭导航菜单' : '打开导航菜单'}
        >
          {mobileOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>

      {/* Left Rail */}
      <aside className={`v2-rail${mobileOpen ? ' is-open' : ''}`} aria-label="主导航">
        <div className="v2-brand">
          <Link to="/projects" className="v2-brand__title" onClick={() => setMobileOpen(false)}>
            <span className="v2-brand__mark">GT</span>
            <span>GoodTeam</span>
          </Link>
          <span className="v2-brand__tag">v2</span>
        </div>

        <div className="v2-project-switcher">
          <span className="v2-project-switcher__label">
            <span>当前项目</span>
            {activeProject?.default_branch ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', fontFamily: 'var(--font-mono)' }}>
                <GitBranch size={11} />
                {activeProject.default_branch}
              </span>
            ) : null}
          </span>
          {projects.length > 0 ? (
            <select
              aria-label="切换当前项目"
              value={activeProjectId}
              onChange={(e) => handleProjectSwitch(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : (
            <Link
              to="/projects"
              style={{ fontSize: '0.82rem', color: 'var(--product-accent-strong)', textDecoration: 'none', fontWeight: 620 }}
            >
              + 导入第一个本地项目
            </Link>
          )}
          {activeProject?.path ? (
            <span className="v2-project-switcher__meta" title={activeProject.path}>
              {activeProject.path}
            </span>
          ) : null}
        </div>

        <nav className="v2-nav" aria-label="功能页面">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.label}
                to={item.to}
                end={item.end}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `v2-nav__link${isActive ? ' is-active' : ''}`}
              >
                <Icon size={16} aria-hidden="true" />
                <span>{item.label}</span>
                {item.badge ? <span className="v2-nav__pill">{item.badge}</span> : null}
              </NavLink>
            );
          })}
        </nav>

        <div className="v2-rail__footer">
          <div className="v2-sse-pill" data-status={sseStatus} role="status" aria-live="polite">
            <span className="v2-sse-dot" aria-hidden="true" />
            <span>{SSE_LABELS[sseStatus] || SSE_LABELS.idle}</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main id="main-content" className="v2-main">
        <Outlet />
      </main>
    </div>
  );
}

export default function Layout() {
  return (
    <ToastProvider>
      <WorkspaceProvider>
        <AppShellInner />
      </WorkspaceProvider>
    </ToastProvider>
  );
}
