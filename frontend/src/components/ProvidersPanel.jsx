import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, KeyRound, Plus, PlugZap, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../api';
import Button from './ui/Button';
import InlineNotice from './ui/InlineNotice';
import StatusBadge from './ui/StatusBadge';
import { useToast } from './ui/ToastProvider';

const EMPTY_FORM = {
  id: '',
  agent: 'codex',
  name: '',
  base_url: '',
  model: '',
  api_key: '',
  has_api_key: false,
};

export default function ProvidersPanel({ onChanged }) {
  const toast = useToast();
  const [providers, setProviders] = useState([]);
  const [activeMap, setActiveMap] = useState({});
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [fetchedModels, setFetchedModels] = useState([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [formError, setFormError] = useState('');

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getProviders();
      setProviders(data?.providers || []);
      setActiveMap(data?.active || {});
    } catch (err) {
      toast.error(err.message || '加载 Provider 配置失败');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadProviders();
    }, 0);
    return () => clearTimeout(id);
  }, [loadProviders]);

  const buildPayload = () => {
    const payload = {
      ...(form.id ? { id: form.id, provider_id: form.id } : {}),
      agent: form.agent,
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
    };
    if (form.api_key.trim()) {
      payload.api_key = form.api_key.trim();
    }
    return payload;
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true);
    setFormError('');
    try {
      await api.saveProvider(buildPayload());
      toast.success(form.id ? '已更新 Provider 配置' : '已添加 Provider 配置');
      setForm(EMPTY_FORM);
      setFetchedModels([]);
      setTestResult(null);
      await loadProviders();
      onChanged?.();
    } catch (err) {
      setFormError(err.message || '保存 Provider 失败');
    } finally {
      setSaving(false);
    }
  };

  const handleFetchModels = async () => {
    setFetchingModels(true);
    setFormError('');
    try {
      const res = await api.fetchProviderModels(buildPayload());
      const list = res?.models || [];
      setFetchedModels(list);
      toast.success(`成功拉取 ${list.length} 个可用模型`);
    } catch (err) {
      setFormError(err.message || '拉取模型列表失败');
    } finally {
      setFetchingModels(false);
    }
  };

  const handleTest = async (targetPayload = null) => {
    setTesting(true);
    setFormError('');
    setTestResult(null);
    try {
      const res = await api.testProvider(targetPayload || buildPayload());
      setTestResult({
        ok: true,
        message: `连通正常，检测到 ${res?.model_count ?? 0} 个模型${
          res?.sample_models?.length ? `（示例：${res.sample_models.slice(0, 3).join(', ')}）` : ''
        }`,
      });
    } catch (err) {
      setTestResult({ ok: false, message: err.message || '连通性测试失败' });
    } finally {
      setTesting(false);
    }
  };

  const handleActivate = async (agent, providerId) => {
    try {
      const data = await api.activateProvider(agent, providerId);
      setProviders(data?.providers || providers);
      setActiveMap(data?.active || {});
      toast.success(
        providerId
          ? `已切换 ${agent.toUpperCase()} 全局默认 Provider`
          : `已将 ${agent.toUpperCase()} 切回本机 CLI 登录态`,
      );
      onChanged?.();
    } catch (err) {
      toast.error(err.message || '切换默认 Provider 失败');
    }
  };

  const handleDelete = async (providerId) => {
    try {
      await api.deleteProvider(providerId);
      toast.info('已删除 Provider 配置');
      if (form.id === providerId) setForm(EMPTY_FORM);
      await loadProviders();
      onChanged?.();
    } catch (err) {
      toast.error(err.message || '删除 Provider 失败');
    }
  };

  return (
    <section className="v2-card" style={{ display: 'grid', gap: '1.1rem' }} aria-label="Provider 配置管理">
      <div className="v2-card__header" style={{ marginBottom: 0 }}>
        <div>
          <h2 className="v2-card__title">Provider 连接配置（Codex / Claude）</h2>
          <p className="v2-card__subtitle">
            API Key 仅以 0600 权限保存在本地并通过进程环境变量注入，绝不返回前端明文或写入日志。
          </p>
        </div>
      </div>

      {/* Global Active Status for Codex & Claude */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '0.75rem' }}>
        {['codex', 'claude'].map((agent) => {
          const activeId = activeMap?.[agent] || null;
          const activeObj = providers.find((p) => p.id === activeId);
          return (
            <div key={agent} className="v2-doctor-item">
              <div className="v2-doctor-item__head">
                <strong style={{ textTransform: 'uppercase', fontSize: '0.84rem' }}>{agent} 全局默认</strong>
                <StatusBadge tone={activeObj ? 'accent' : 'success'}>
                  {activeObj ? activeObj.name : '本机 CLI 登录态'}
                </StatusBadge>
              </div>
              {activeObj ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
                  <code className="v2-doctor-item__ver">{activeObj.base_url}</code>
                  <Button
                    variant="secondary"
                    className="v2-btn-sm"
                    onClick={() => handleActivate(agent, null)}
                  >
                    切回本机 CLI 登录态
                  </Button>
                </div>
              ) : (
                <span style={{ fontSize: '0.78rem', color: 'var(--product-text-muted)' }}>
                  当前直接使用本机已登录的 {agent} CLI 凭证。
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Configured providers list */}
      {loading ? (
        <p style={{ margin: 0, color: 'var(--product-text-muted)', fontSize: '0.86rem' }}>正在加载 Provider...</p>
      ) : providers.length > 0 ? (
        <div className="v2-cards-grid">
          {providers.map((prov) => {
            const isActive = activeMap?.[prov.agent] === prov.id;
            return (
              <article key={prov.id} className="v2-card v2-card--muted" style={{ display: 'grid', gap: '0.55rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.4rem', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: '0.94rem' }}>{prov.name}</strong>
                  <div style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
                    <StatusBadge tone="neutral">{prov.agent}</StatusBadge>
                    {isActive ? <StatusBadge tone="success">全局默认</StatusBadge> : null}
                  </div>
                </div>
                <code style={{ fontSize: '0.76rem', color: 'var(--product-text-muted)', wordBreak: 'break-all' }}>
                  {prov.base_url}
                </code>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', alignItems: 'center' }}>
                  {prov.model ? <span className="v2-chip">{prov.model}</span> : null}
                  {prov.has_api_key ? (
                    <StatusBadge tone="success">
                      <KeyRound size={11} style={{ marginRight: '4px' }} />
                      已配置密钥
                    </StatusBadge>
                  ) : (
                    <StatusBadge tone="neutral">未配置密钥</StatusBadge>
                  )}
                </div>
                <div className="v2-actions" style={{ marginTop: '0.2rem' }}>
                  {!isActive ? (
                    <Button
                      variant="primary"
                      className="v2-btn-sm"
                      onClick={() => handleActivate(prov.agent, prov.id)}
                    >
                      设为全局默认
                    </Button>
                  ) : null}
                  <Button
                    variant="secondary"
                    className="v2-btn-sm"
                    onClick={() =>
                      setForm({
                        id: prov.id,
                        agent: prov.agent,
                        name: prov.name,
                        base_url: prov.base_url,
                        model: prov.model || '',
                        api_key: '',
                        has_api_key: Boolean(prov.has_api_key),
                      })
                    }
                  >
                    编辑
                  </Button>
                  <Button
                    variant="secondary"
                    className="v2-btn-sm"
                    busy={testing}
                    onClick={() => handleTest({ provider_id: prov.id })}
                  >
                    测试连通
                  </Button>
                  <Button
                    variant="danger"
                    className="v2-btn-sm v2-btn-icon"
                    onClick={() => handleDelete(prov.id)}
                    aria-label={`删除 Provider ${prov.name}`}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}

      {/* Add / Edit Provider Form */}
      <form
        onSubmit={handleSave}
        style={{
          display: 'grid',
          gap: '0.75rem',
          paddingTop: '0.9rem',
          borderTop: '1px solid var(--product-border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h3 className="v2-card__title" style={{ fontSize: '0.92rem' }}>
            {form.id ? `编辑 Provider · ${form.name}` : '新增自定义 Provider'}
          </h3>
          {form.id ? (
            <Button type="button" variant="secondary" className="v2-btn-sm" onClick={() => setForm(EMPTY_FORM)}>
              取消编辑
            </Button>
          ) : null}
        </div>

        {formError ? <InlineNotice tone="danger" title="操作失败">{formError}</InlineNotice> : null}
        {testResult ? (
          <InlineNotice tone={testResult.ok ? 'success' : 'danger'} title={testResult.ok ? '连通测试通过' : '连通测试失败'}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
              {testResult.ok ? <CheckCircle2 size={14} /> : null}
              {testResult.message}
            </span>
          </InlineNotice>
        ) : null}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.65rem' }}>
          <div className="v2-field">
            <label htmlFor="prov-agent">CLI 类型</label>
            <select
              id="prov-agent"
              className="v2-select"
              value={form.agent}
              onChange={(e) => setForm((f) => ({ ...f, agent: e.target.value }))}
            >
              <option value="codex">Codex</option>
              <option value="claude">Claude</option>
            </select>
          </div>
          <div className="v2-field">
            <label htmlFor="prov-name">连接名称</label>
            <input
              id="prov-name"
              className="v2-input"
              placeholder="例如：OpenAI Proxy"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              required
            />
          </div>
          <div className="v2-field">
            <label htmlFor="prov-url">Base URL</label>
            <input
              id="prov-url"
              className="v2-input v2-input--mono"
              placeholder="https://api.openai.com/v1"
              value={form.base_url}
              onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
              required
            />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.65rem' }}>
          <div className="v2-field">
            <label htmlFor="prov-key">
              API Key（只写不回显{form.has_api_key ? ' · 已配置密钥，留空保持不变' : ''}）
            </label>
            <input
              id="prov-key"
              type="password"
              autoComplete="new-password"
              className="v2-input v2-input--mono"
              placeholder={form.has_api_key ? '已配置密钥（输入新密钥可覆盖）' : 'sk-...'}
              value={form.api_key}
              onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
            />
          </div>
          <div className="v2-field">
            <label htmlFor="prov-model">默认模型（可选）</label>
            <input
              id="prov-model"
              list="provider-fetched-models"
              className="v2-input v2-input--mono"
              placeholder="例如：gpt-5.4 或 claude-sonnet-4-5"
              value={form.model}
              onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
            />
            <datalist id="provider-fetched-models">
              {fetchedModels.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </div>

        <div className="v2-actions">
          <Button type="submit" variant="primary" className="v2-btn-sm v2-btn-icon" busy={saving}>
            <Plus size={14} aria-hidden="true" />
            {form.id ? '保存修改' : '保存 Provider'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="v2-btn-sm v2-btn-icon"
            busy={fetchingModels}
            disabled={!form.base_url.trim()}
            onClick={handleFetchModels}
          >
            {!fetchingModels ? <RefreshCw size={14} aria-hidden="true" /> : null}
            拉取模型
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="v2-btn-sm v2-btn-icon"
            busy={testing}
            disabled={!form.base_url.trim()}
            onClick={() => handleTest()}
          >
            <PlugZap size={14} aria-hidden="true" />
            测试连通
          </Button>
        </div>
      </form>
    </section>
  );
}
