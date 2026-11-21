import { useState } from 'react';
import { GitPullRequest, ListTodo, Plus, RefreshCw, X } from 'lucide-react';
import { api } from '../api';
import Button from './ui/Button';
import StatusBadge from './ui/StatusBadge';
import { useToast } from './ui/ToastProvider';

const TASK_STATUSES = [
  { value: 'todo', label: '待办' },
  { value: 'in_progress', label: '进行中' },
  { value: 'review', label: '待审阅' },
  { value: 'done', label: '已完成' },
  { value: 'blocked', label: '已阻塞' },
];

export default function BacklogSidebar({
  projectId,
  tasks = [],
  onTasksChange,
  onClose,
}) {
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [creating, setCreating] = useState(false);

  const [githubOpen, setGithubOpen] = useState(false);
  const [issues, setIssues] = useState([]);
  const [issuesLoading, setIssuesLoading] = useState(false);
  const [issuesError, setIssuesError] = useState('');
  const [importingIssue, setImportingIssue] = useState(null);

  const handleCreateTask = async (e) => {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    setCreating(true);
    try {
      const created = await api.createTask(projectId, { title: trimmed, status: 'todo' });
      onTasksChange((prev) =>
        prev.some((t) => t.id === created.id)
          ? prev.map((t) => (t.id === created.id ? created : t))
          : [...prev, created],
      );
      setTitle('');
    } catch (err) {
      toast.error(err.message || '创建任务失败');
    } finally {
      setCreating(false);
    }
  };

  const handleStatusChange = async (taskId, status) => {
    try {
      const updated = await api.updateTask(projectId, taskId, { status });
      onTasksChange((prev) => prev.map((t) => (t.id === taskId ? updated : t)));
    } catch (err) {
      toast.error(err.message || '更新任务状态失败');
    }
  };

  const loadGitHubIssues = async () => {
    setIssuesLoading(true);
    setIssuesError('');
    try {
      const list = await api.listGitHubIssues(projectId, { state: 'open', limit: 30 });
      setIssues(list);
    } catch (err) {
      setIssuesError(err.message || '读取 GitHub Issues 失败，请确认 gh CLI 已认证。');
    } finally {
      setIssuesLoading(false);
    }
  };

  const handleToggleGithub = () => {
    const next = !githubOpen;
    setGithubOpen(next);
    if (next && issues.length === 0 && !issuesLoading) {
      loadGitHubIssues();
    }
  };

  const handleImportIssue = async (issueNumber) => {
    setImportingIssue(issueNumber);
    try {
      const task = await api.importGitHubIssue(projectId, issueNumber);
      onTasksChange((prev) => (prev.some((t) => t.id === task.id) ? prev : [...prev, task]));
      toast.success(`已导入 Issue #${issueNumber}`);
    } catch (err) {
      toast.error(err.message || `导入 Issue #${issueNumber} 失败`);
    } finally {
      setImportingIssue(null);
    }
  };

  return (
    <aside className="v2-backlog" aria-label="项目任务 Backlog">
      <div className="v2-backlog__header">
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.45rem' }}>
          <ListTodo size={16} aria-hidden="true" />
          <strong style={{ fontSize: '0.92rem' }}>任务清单 ({tasks.length})</strong>
        </div>
        <button
          type="button"
          className="projects-icon-button"
          onClick={onClose}
          aria-label="收起任务栏"
        >
          <X size={15} aria-hidden="true" />
        </button>
      </div>

      <div className="v2-backlog__body">
        <form onSubmit={handleCreateTask} style={{ display: 'flex', gap: '0.4rem' }}>
          <input
            type="text"
            className="v2-input"
            placeholder="新建待办任务..."
            aria-label="新建待办任务"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Button type="submit" variant="primary" className="v2-btn-sm v2-btn-icon" busy={creating}>
            <Plus size={14} aria-hidden="true" />
          </Button>
        </form>

        <div>
          <Button
            variant="secondary"
            className="v2-btn-sm v2-btn-icon"
            style={{ width: '100%' }}
            onClick={handleToggleGithub}
          >
            <GitPullRequest size={14} aria-hidden="true" />
            {githubOpen ? '收起 GitHub Issues 导入' : '从 GitHub Issues 导入'}
          </Button>
        </div>

        {githubOpen ? (
          <div className="v2-card v2-card--muted" style={{ padding: '0.7rem', display: 'grid', gap: '0.55rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <strong style={{ fontSize: '0.8rem' }}>Open GitHub Issues</strong>
              <button
                type="button"
                className="projects-icon-button"
                onClick={loadGitHubIssues}
                aria-label="刷新 GitHub Issues"
              >
                <RefreshCw size={13} className={issuesLoading ? 'product-button__spinner' : ''} />
              </button>
            </div>
            {issuesError ? (
              <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--product-danger)' }}>{issuesError}</p>
            ) : issuesLoading ? (
              <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--product-text-muted)' }}>正在读取 Issues...</p>
            ) : issues.length === 0 ? (
              <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--product-text-muted)' }}>当前无开放的 GitHub Issues。</p>
            ) : (
              <div style={{ display: 'grid', gap: '0.45rem', maxHeight: '220px', overflowY: 'auto' }}>
                {issues.map((issue) => (
                  <div
                    key={issue.number}
                    style={{
                      padding: '0.5rem',
                      borderRadius: '6px',
                      background: 'var(--product-surface)',
                      border: '1px solid var(--product-border)',
                      display: 'grid',
                      gap: '0.35rem',
                    }}
                  >
                    <div style={{ fontSize: '0.8rem', fontWeight: 600 }}>
                      #{issue.number} {issue.title}
                    </div>
                    <Button
                      variant="secondary"
                      className="v2-btn-sm"
                      busy={importingIssue === issue.number}
                      onClick={() => handleImportIssue(issue.number)}
                    >
                      导入任务
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}

        <div style={{ display: 'grid', gap: '0.5rem' }}>
          {tasks.length === 0 ? (
            <p style={{ margin: '0.5rem 0', fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>
              暂无任务，可手动添加或由 Manager 拆解。
            </p>
          ) : (
            tasks.map((task) => (
              <div key={task.id} className="v2-task-item">
                <div className="v2-task-item__top">
                  <span
                    style={{
                      fontSize: '0.84rem',
                      fontWeight: 600,
                      textDecoration: task.status === 'done' ? 'line-through' : 'none',
                      color: task.status === 'done' ? 'var(--product-text-muted)' : 'var(--product-text)',
                    }}
                  >
                    {task.title}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.4rem' }}>
                  <select
                    aria-label={`任务 ${task.title} 状态`}
                    className="v2-select"
                    style={{ minHeight: '28px', padding: '0.15rem 0.4rem', fontSize: '0.76rem', width: 'auto' }}
                    value={task.status}
                    onChange={(e) => handleStatusChange(task.id, e.target.value)}
                  >
                    {TASK_STATUSES.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  {task.assigned_role ? (
                    <StatusBadge tone="neutral">{task.assigned_role}</StatusBadge>
                  ) : null}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
