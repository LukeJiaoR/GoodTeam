import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Cpu,
  FileCode2,
  GitPullRequestArrow,
  Square,
  Terminal,
} from 'lucide-react';
import { api } from '../api';
import Button from './ui/Button';
import StatusBadge from './ui/StatusBadge';

const STATUS_META = {
  queued: { label: '排队中', tone: 'pending' },
  running: { label: '执行中', tone: 'active' },
  completed: { label: '已完成', tone: 'complete' },
  failed: { label: '失败', tone: 'danger' },
  stopped: { label: '已停止', tone: 'warning' },
};

function formatEventLine(ev) {
  const payload = ev?.payload || {};
  const text =
    payload.text ||
    payload.message ||
    payload.summary ||
    payload.command ||
    (typeof payload === 'string' ? payload : JSON.stringify(payload));
  return `#${ev.seq ?? '-'} [${ev.kind || 'event'}] ${String(text || '').slice(0, 320)}`;
}

export default function RunCard({
  run,
  projectId,
  liveEvents = [],
  onStopped,
}) {
  const [expanded, setExpanded] = useState(false);
  const [fetchedEvents, setFetchedEvents] = useState(null);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [stopping, setStopping] = useState(false);

  const meta = STATUS_META[run.status] || { label: run.status || '未知', tone: 'neutral' };
  const isActive = run.status === 'running' || run.status === 'queued';
  const changedCount = Array.isArray(run.changed_files) ? run.changed_files.length : 0;
  const scopeViolated = run.scope_ok === false;

  const mergedEvents = (() => {
    const map = new Map();
    for (const ev of fetchedEvents || []) {
      map.set(ev.seq ?? `f-${map.size}`, ev);
    }
    for (const ev of liveEvents || []) {
      map.set(ev.seq ?? `l-${map.size}`, ev);
    }
    return [...map.values()].sort((a, b) => (a.seq || 0) - (b.seq || 0));
  })();

  const handleToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && fetchedEvents === null) {
      setLoadingEvents(true);
      try {
        const detail = await api.getRun(run.id);
        setFetchedEvents(detail?.events || []);
      } catch {
        setFetchedEvents([]);
      } finally {
        setLoadingEvents(false);
      }
    }
  };

  const handleStop = async () => {
    setStopping(true);
    try {
      const updated = await api.stopRun(run.id);
      onStopped?.(updated?.run || updated);
    } finally {
      setStopping(false);
    }
  };

  return (
    <article
      className="v2-run-card"
      data-status={run.status}
      data-scope-ok={String(run.scope_ok !== false)}
      data-testid={`run-card-${run.id}`}
    >
      <div className="v2-run-card__head">
        <div className="v2-run-card__meta">
          <button
            type="button"
            onClick={handleToggle}
            aria-expanded={expanded}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              border: 0,
              background: 'transparent',
              padding: 0,
              color: 'var(--product-text)',
              font: 'inherit',
              fontWeight: 680,
              cursor: 'pointer',
            }}
          >
            {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            <span>Sub-agent · {run.role_key}</span>
          </button>
          <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
          <span className="v2-chip">
            <Cpu size={12} aria-hidden="true" />
            {run.adapter}
            {run.model ? ` / ${run.model}` : ''}
          </span>
          {changedCount > 0 ? (
            <span className="v2-chip">
              <FileCode2 size={12} aria-hidden="true" />
              {changedCount} 个改动文件
            </span>
          ) : null}
          {scopeViolated ? (
            <StatusBadge tone="danger">
              <AlertTriangle size={12} style={{ marginRight: '4px' }} />
              超出写域限制
            </StatusBadge>
          ) : null}
        </div>

        <div className="v2-actions">
          {isActive ? (
            <Button
              variant="danger"
              className="v2-btn-sm v2-btn-icon"
              busy={stopping}
              onClick={handleStop}
            >
              <Square size={12} aria-hidden="true" />
              停止运行
            </Button>
          ) : null}
          {changedCount > 0 && projectId ? (
            <Link
              to={`/projects/${encodeURIComponent(projectId)}/delivery`}
              className="product-button product-button--secondary v2-btn-sm v2-btn-icon"
            >
              <GitPullRequestArrow size={13} aria-hidden="true" />
              前往交付审阅
            </Link>
          ) : null}
        </div>
      </div>

      {run.summary || run.error ? (
        <div style={{ padding: '0.55rem 0.95rem', fontSize: '0.84rem', borderTop: '1px solid var(--product-border)' }}>
          {run.error ? (
            <p style={{ margin: 0, color: 'var(--product-danger)' }}>{run.error}</p>
          ) : null}
          {run.summary ? (
            <p style={{ margin: run.error ? '0.35rem 0 0' : 0, color: 'var(--product-text-muted)' }}>
              {run.summary}
            </p>
          ) : null}
        </div>
      ) : null}

      {expanded ? (
        <div className="v2-run-card__body">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem', fontSize: '0.76rem', color: 'var(--product-text-muted)' }}>
            <span>Run ID: <code>{run.id}</code></span>
            {run.branch_name ? <span>分支: <code>{run.branch_name}</code></span> : null}
            <span>策略: <code>{run.execution_policy}</code></span>
          </div>

          {changedCount > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
              {run.changed_files.map((file) => (
                <span key={file} className="v2-chip">{file}</span>
              ))}
            </div>
          ) : null}

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.76rem', fontWeight: 640, marginBottom: '0.35rem', color: 'var(--product-text-muted)' }}>
              <Terminal size={13} aria-hidden="true" />
              实时日志与事件 ({mergedEvents.length})
            </div>
            <div className="v2-run-events">
              {loadingEvents ? (
                <span>加载执行日志...</span>
              ) : mergedEvents.length === 0 ? (
                <span>暂无执行事件日志。</span>
              ) : (
                mergedEvents.map((ev, index) => (
                  <div key={`${ev.seq ?? index}`}>{formatEventLine(ev)}</div>
                ))
              )}
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}
