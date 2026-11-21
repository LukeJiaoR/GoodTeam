import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  GitBranch,
  GitMerge,
  GitPullRequest,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { api } from '../api';
import { useWorkspace } from '../context/WorkspaceContext';
import ConfirmModal from '../components/ConfirmModal';
import DiffViewer from '../components/DiffViewer';
import Button from '../components/ui/Button';
import InlineNotice from '../components/ui/InlineNotice';
import StatusBadge from '../components/ui/StatusBadge';
import { useToast } from '../components/ui/ToastProvider';

const STATUS_LABEL = {
  pending: { label: '待审阅交付', tone: 'warning' },
  merged: { label: '已合入主分支', tone: 'success' },
  pr_open: { label: '已创建 PR', tone: 'accent' },
  pr_opened: { label: '已创建 PR', tone: 'accent' },
  discarded: { label: '已丢弃', tone: 'neutral' },
};

export default function DeliveryPage() {
  const { projectId: routeProjectId } = useParams();
  const toast = useToast();
  const { activeProjectId, setActiveProjectId, setPendingDeliveries } = useWorkspace();
  const projectId = routeProjectId || activeProjectId;

  const [deliveries, setDeliveries] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [merging, setMerging] = useState(false);
  const [prFormOpen, setPrFormOpen] = useState(false);
  const [prTitle, setPrTitle] = useState('');
  const [prBody, setPrBody] = useState('');
  const [creatingPr, setCreatingPr] = useState(false);
  const [discardTarget, setDiscardTarget] = useState(null);
  const [discarding, setDiscarding] = useState(false);

  const [checksData, setChecksData] = useState(null);
  const [checksLoading, setChecksLoading] = useState(false);
  const [checksError, setChecksError] = useState('');

  useEffect(() => {
    if (routeProjectId && routeProjectId !== activeProjectId) {
      setActiveProjectId(routeProjectId);
    }
  }, [routeProjectId, activeProjectId, setActiveProjectId]);

  const loadDeliveries = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError('');
    try {
      const list = await api.listDeliveries(projectId);
      setDeliveries(list);
      setPendingDeliveries(list.filter((d) => d.status === 'pending').length);
      setSelectedId((curr) => (curr && list.some((d) => d.id === curr) ? curr : list[0]?.id || ''));
    } catch (err) {
      setError(err.message || '加载交付队列失败');
    } finally {
      setLoading(false);
    }
  }, [projectId, setPendingDeliveries]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadDeliveries();
    }, 0);
    return () => clearTimeout(id);
  }, [loadDeliveries]);

  const selected = useMemo(
    () => deliveries.find((d) => d.id === selectedId) || null,
    [deliveries, selectedId],
  );

  const selectDelivery = (item) => {
    setSelectedId(item.id);
    setPrFormOpen(false);
    setChecksData(null);
    setChecksError('');
    setPrTitle(
      item.summary
        ? item.summary.split('\n')[0].slice(0, 120)
        : `chore: deliver ${item.branch_name}`,
    );
    setPrBody(item.summary || '');
  };

  const togglePrForm = () => {
    if (!prFormOpen && selected && !prTitle) {
      setPrTitle(
        selected.summary
          ? selected.summary.split('\n')[0].slice(0, 120)
          : `chore: deliver ${selected.branch_name}`,
      );
      setPrBody(selected.summary || '');
    }
    setPrFormOpen((v) => !v);
  };

  const handleMerge = async () => {
    if (!selected) return;
    setMerging(true);
    try {
      await api.mergeDelivery(selected.id);
      toast.success(`已合入分支 ${selected.branch_name} 至 ${selected.base_ref}`);
      await loadDeliveries();
    } catch (err) {
      toast.error(err.message || '合入本地主分支失败');
    } finally {
      setMerging(false);
    }
  };

  const handleCreatePr = async (e) => {
    e.preventDefault();
    if (!selected) return;
    setCreatingPr(true);
    try {
      const res = await api.createDeliveryPr(selected.id, {
        title: prTitle.trim() || undefined,
        body: prBody.trim() || undefined,
      });
      const url = res?.pr?.url || res?.delivery?.pr_url || res?.pr_url || res?.url;
      toast.success(url ? `已创建 Pull Request：${url}` : '已推送分支并创建 PR');
      setPrFormOpen(false);
      await loadDeliveries();
    } catch (err) {
      toast.error(err.message || '推分支并创建 PR 失败');
    } finally {
      setCreatingPr(false);
    }
  };

  const handleDiscard = async () => {
    if (!discardTarget) return;
    setDiscarding(true);
    try {
      await api.discardDelivery(discardTarget.id);
      toast.info(`已丢弃交付 ${discardTarget.branch_name}`);
      setDiscardTarget(null);
      await loadDeliveries();
    } catch (err) {
      toast.error(err.message || '丢弃交付失败');
    } finally {
      setDiscarding(false);
    }
  };

  const handleLoadChecks = async () => {
    if (!selected) return;
    setChecksLoading(true);
    setChecksError('');
    try {
      const data = await api.getDeliveryChecks(selected.id);
      setChecksData(data?.checks || data);
    } catch (err) {
      setChecksError(err.message || '获取 CI 检查状态失败');
    } finally {
      setChecksLoading(false);
    }
  };

  if (!projectId) {
    return (
      <div className="v2-page">
        <div className="v2-card">
          <h2 className="v2-card__title">请先选择项目</h2>
          <p className="v2-card__subtitle">请先在「项目」页面导入并选择一个本地仓库。</p>
          <div style={{ marginTop: '1rem' }}>
            <Link to="/projects" className="product-button product-button--primary">
              前往项目列表
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const pendingItems = deliveries.filter((d) => d.status === 'pending');
  const historyItems = deliveries.filter((d) => d.status !== 'pending');
  const rollup = checksData?.statusCheckRollup || checksData?.checks || [];

  return (
    <div className="v2-page">
      <header className="v2-page-header">
        <div>
          <p className="v2-eyebrow">HUMAN DELIVERY GATE</p>
          <h1>交付审阅与合并门禁</h1>
          <p>Sub-agent 永不直接修改主分支；在此逐行审阅 Unified Diff 后，由你显式合入本地主分支、创建 PR 或丢弃。</p>
        </div>
        <Button variant="secondary" className="v2-btn-sm v2-btn-icon" busy={loading} onClick={loadDeliveries}>
          {!loading ? <RefreshCw size={14} aria-hidden="true" /> : null}
          刷新交付队列
        </Button>
      </header>

      {error ? (
        <InlineNotice tone="danger" title="加载失败">
          {error}
        </InlineNotice>
      ) : null}

      {!loading && deliveries.length === 0 ? (
        <div className="v2-card" style={{ textAlign: 'center', padding: '2.5rem 1rem' }}>
          <p style={{ margin: 0, fontWeight: 620 }}>暂无待审阅或历史交付</p>
          <p style={{ margin: '0.35rem 0 0', color: 'var(--product-text-muted)', fontSize: '0.86rem' }}>
            当写权限 Sub-agent 或多轮自主续跑产出代码变更时，将自动在此生成交付审阅项。
          </p>
        </div>
      ) : (
        <div className="v2-delivery-grid">
          {/* Left Queue */}
          <aside style={{ display: 'grid', gap: '1rem' }} aria-label="交付列表">
            <div>
              <h2 className="v2-card__title" style={{ fontSize: '0.88rem', marginBottom: '0.5rem' }}>
                待审阅 ({pendingItems.length})
              </h2>
              <div className="v2-delivery-list">
                {pendingItems.length === 0 ? (
                  <p style={{ margin: 0, fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>
                    当前无待审阅项。
                  </p>
                ) : (
                  pendingItems.map((item) => (
                    <DeliveryQueueItem
                      key={item.id}
                      item={item}
                      active={item.id === selectedId}
                      onSelect={() => selectDelivery(item)}
                    />
                  ))
                )}
              </div>
            </div>

            {historyItems.length > 0 ? (
              <div>
                <h2 className="v2-card__title" style={{ fontSize: '0.88rem', marginBottom: '0.5rem' }}>
                  历史记录 ({historyItems.length})
                </h2>
                <div className="v2-delivery-list">
                  {historyItems.map((item) => (
                    <DeliveryQueueItem
                      key={item.id}
                      item={item}
                      active={item.id === selectedId}
                      onSelect={() => selectDelivery(item)}
                    />
                  ))}
                </div>
              </div>
            ) : null}
          </aside>

          {/* Right Diff & Action Inspector */}
          {selected ? (
            <section style={{ display: 'grid', gap: '1rem', minWidth: 0 }} aria-label="交付详情与操作">
              <div className="v2-card" style={{ display: 'grid', gap: '0.85rem' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '0.6rem' }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' }}>
                    <code style={{ fontWeight: 700, fontSize: '0.9rem' }}>{selected.branch_name}</code>
                    <span style={{ color: 'var(--product-text-muted)', fontSize: '0.82rem' }}>
                      → {selected.base_ref}
                    </span>
                    <StatusBadge tone={STATUS_LABEL[selected.status]?.tone || 'neutral'}>
                      {STATUS_LABEL[selected.status]?.label || selected.status}
                    </StatusBadge>
                    {selected.role_key ? <StatusBadge tone="neutral">{selected.role_key}</StatusBadge> : null}
                    {selected.scope_ok === false ? (
                      <StatusBadge tone="danger">写域越界 (scope_ok: false)</StatusBadge>
                    ) : (
                      <StatusBadge tone="success">
                        <ShieldCheck size={12} style={{ marginRight: '4px' }} />
                        写域校验通过
                      </StatusBadge>
                    )}
                  </div>
                </div>

                {selected.scope_ok === false ? (
                  <InlineNotice tone="danger" title="严重警告：检测到超出允许写域的文件改动！">
                    <div style={{ display: 'flex', gap: '0.45rem', alignItems: 'flex-start' }}>
                      <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
                      <span>
                        该 Sub-agent 修改了角色 Write Domains 范围之外的文件。请仔细逐行检查下方 Diff 后再决定是否放行，否则建议直接丢弃。
                      </span>
                    </div>
                  </InlineNotice>
                ) : null}

                {selected.summary ? (
                  <p style={{ margin: 0, fontSize: '0.88rem', color: 'var(--product-text-muted)' }}>
                    {selected.summary}
                  </p>
                ) : null}

                {Array.isArray(selected.changed_files) && selected.changed_files.length > 0 ? (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                    {selected.changed_files.map((file) => (
                      <span key={file} className="v2-chip">{file}</span>
                    ))}
                  </div>
                ) : null}

                {selected.merge_commit ? (
                  <div style={{ fontSize: '0.82rem', color: 'var(--product-success)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    <CheckCircle2 size={15} />
                    已合入提交：<code>{selected.merge_commit}</code>
                  </div>
                ) : null}

                {selected.pr_url ? (
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.6rem' }}>
                    <a
                      href={selected.pr_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="product-button product-button--secondary v2-btn-sm v2-btn-icon"
                    >
                      <ExternalLink size={14} />
                      查看 PR ({selected.pr_url})
                    </a>
                    <Button
                      variant="secondary"
                      className="v2-btn-sm v2-btn-icon"
                      busy={checksLoading}
                      onClick={handleLoadChecks}
                    >
                      {!checksLoading ? <RefreshCw size={13} /> : null}
                      查看 CI 检查
                    </Button>
                  </div>
                ) : null}

                {checksError ? (
                  <InlineNotice tone="danger" title="CI 检查读取失败">
                    {checksError}
                  </InlineNotice>
                ) : null}

                {checksData ? (
                  <div className="v2-card v2-card--muted" style={{ padding: '0.75rem', display: 'grid', gap: '0.45rem' }}>
                    <div style={{ fontSize: '0.82rem', fontWeight: 660 }}>
                      PR #{checksData.number || '-'} · 状态：{checksData.state || 'OPEN'}
                    </div>
                    {Array.isArray(rollup) && rollup.length > 0 ? (
                      <div style={{ display: 'grid', gap: '0.3rem' }}>
                        {rollup.map((chk, idx) => (
                          <div
                            key={chk.name || chk.context || idx}
                            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.8rem' }}
                          >
                            <span>{chk.name || chk.context || `Check #${idx + 1}`}</span>
                            <StatusBadge
                              tone={
                                chk.conclusion === 'SUCCESS' || chk.state === 'SUCCESS'
                                  ? 'success'
                                  : chk.conclusion === 'FAILURE' || chk.state === 'FAILURE'
                                    ? 'danger'
                                    : 'warning'
                              }
                            >
                              {chk.conclusion || chk.status || chk.state || 'PENDING'}
                            </StatusBadge>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <span style={{ fontSize: '0.8rem', color: 'var(--product-text-muted)' }}>
                        暂无关联 CI 检查项。
                      </span>
                    )}
                  </div>
                ) : null}

                {/* Explicit Human Delivery Gate Buttons */}
                {selected.status === 'pending' ? (
                  <div style={{ display: 'grid', gap: '0.75rem', paddingTop: '0.4rem', borderTop: '1px solid var(--product-border)' }}>
                    <div className="v2-actions">
                      <Button
                        variant="primary"
                        className="v2-btn-icon"
                        busy={merging}
                        onClick={handleMerge}
                      >
                        <GitMerge size={15} aria-hidden="true" />
                        合入本地主分支
                      </Button>
                      <Button
                        variant="secondary"
                        className="v2-btn-icon"
                        onClick={togglePrForm}
                      >
                        <GitPullRequest size={15} aria-hidden="true" />
                        推分支并创建 PR
                      </Button>
                      <Button
                        variant="danger"
                        className="v2-btn-icon"
                        onClick={() => setDiscardTarget(selected)}
                      >
                        <Trash2 size={15} aria-hidden="true" />
                        丢弃
                      </Button>
                    </div>

                    {prFormOpen ? (
                      <form
                        onSubmit={handleCreatePr}
                        className="v2-card v2-card--muted"
                        style={{ display: 'grid', gap: '0.7rem' }}
                      >
                        <div className="v2-field">
                          <label htmlFor="pr-title-input">PR 标题</label>
                          <input
                            id="pr-title-input"
                            type="text"
                            className="v2-input"
                            value={prTitle}
                            onChange={(e) => setPrTitle(e.target.value)}
                            required
                          />
                        </div>
                        <div className="v2-field">
                          <label htmlFor="pr-body-input">PR 说明</label>
                          <textarea
                            id="pr-body-input"
                            rows={3}
                            className="v2-textarea"
                            value={prBody}
                            onChange={(e) => setPrBody(e.target.value)}
                          />
                        </div>
                        <div className="v2-actions">
                          <Button type="submit" variant="primary" busy={creatingPr}>
                            确认推送并创建 PR
                          </Button>
                          <Button type="button" variant="secondary" onClick={() => setPrFormOpen(false)}>
                            取消
                          </Button>
                        </div>
                      </form>
                    ) : null}
                  </div>
                ) : null}
              </div>

              <DiffViewer diffText={selected.diff_text || ''} />
            </section>
          ) : null}
        </div>
      )}

      <ConfirmModal
        open={Boolean(discardTarget)}
        title="确认丢弃该交付？"
        description={`将清理交付分支 ${discardTarget?.branch_name || ''} 及其隔离 worktree 变更，且不会影响本地主分支。`}
        confirmLabel="确认丢弃"
        confirmVariant="danger"
        busy={discarding}
        onCancel={() => setDiscardTarget(null)}
        onConfirm={handleDiscard}
      />
    </div>
  );
}

function DeliveryQueueItem({ item, active, onSelect }) {
  const statusInfo = STATUS_LABEL[item.status] || { label: item.status, tone: 'neutral' };
  const fileCount = Array.isArray(item.changed_files) ? item.changed_files.length : 0;
  return (
    <button
      type="button"
      className={`v2-delivery-item${active ? ' is-active' : ''}`}
      onClick={onSelect}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.4rem' }}>
        <strong style={{ fontSize: '0.84rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
          <GitBranch size={13} aria-hidden="true" />
          {item.role_key || 'iteration'}
        </strong>
        <StatusBadge tone={statusInfo.tone}>{statusInfo.label}</StatusBadge>
      </div>
      <code style={{ fontSize: '0.73rem', color: 'var(--product-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {item.branch_name}
      </code>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--product-text-muted)' }}>
        <span>{fileCount} 个变更文件</span>
        {item.scope_ok === false ? (
          <span style={{ color: 'var(--product-danger)', fontWeight: 650 }}>写域越界</span>
        ) : null}
      </div>
    </button>
  );
}
