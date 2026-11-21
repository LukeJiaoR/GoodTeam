import { useCallback, useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { api } from '../api';
import DoctorStatusCard from './DoctorStatusCard';
import Button from './ui/Button';
import { useToast } from './ui/ToastProvider';

export default function ManagerUsagePanel({ projectId, providerRevision = 0 }) {
  const toast = useToast();
  const [settings, setSettings] = useState({
    model: '',
    reasoning_effort: 'high',
    additional_prompt: '',
    provider_id: null,
    skill_bindings: [],
  });
  const [codexProviders, setCodexProviders] = useState([]);
  const [saving, setSaving] = useState(false);

  const [usageRows, setUsageRows] = useState([]);
  const [doctor, setDoctor] = useState(null);
  const [doctorLoading, setDoctorLoading] = useState(false);

  const loadAll = useCallback(async () => {
    setDoctorLoading(true);
    try {
      const [provData, usage, doc] = await Promise.all([
        api.getProviders().catch(() => ({ providers: [] })),
        api.getUsage(projectId || '').catch(() => []),
        api.getDoctor().catch(() => null),
      ]);
      setCodexProviders((provData?.providers || []).filter((p) => p.agent === 'codex'));
      setUsageRows(usage);
      setDoctor(doc);

      if (projectId) {
        const mgr = await api.getManagerSettings(projectId);
        if (mgr) setSettings(mgr);
      }
    } finally {
      setDoctorLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadAll();
    }, 0);
    return () => clearTimeout(id);
  }, [loadAll, providerRevision]);

  const handleSaveManager = async (e) => {
    e.preventDefault();
    if (!projectId) return;
    setSaving(true);
    try {
      const saved = await api.saveManagerSettings(projectId, settings);
      setSettings(saved);
      toast.success('已保存 Manager 会话配置');
    } catch (err) {
      toast.error(err.message || '保存 Manager 配置失败');
    } finally {
      setSaving(false);
    }
  };

  const totals = usageRows.reduce(
    (acc, row) => ({
      calls: acc.calls + Number(row.calls || 0),
      input: acc.input + Number(row.input_tokens || 0),
      cached: acc.cached + Number(row.cached_input_tokens || 0),
      output: acc.output + Number(row.output_tokens || 0),
    }),
    { calls: 0, input: 0, cached: 0, output: 0 },
  );

  return (
    <div style={{ display: 'grid', gap: '1.25rem' }}>
      {/* Manager Settings */}
      {projectId ? (
        <section className="v2-card" aria-label="Manager 设置">
          <div className="v2-card__header">
            <div>
              <h2 className="v2-card__title">Manager 会话设置</h2>
              <p className="v2-card__subtitle">
                控制当前项目 Manager 的模型、推理强度、Provider 与附加项目级指令。
              </p>
            </div>
          </div>

          <form onSubmit={handleSaveManager} style={{ display: 'grid', gap: '0.85rem' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: '0.7rem' }}>
              <div className="v2-field">
                <label htmlFor="mgr-provider">Manager Provider</label>
                <select
                  id="mgr-provider"
                  className="v2-select"
                  value={settings.provider_id || ''}
                  onChange={(e) => setSettings((s) => ({ ...s, provider_id: e.target.value || null }))}
                >
                  <option value="">跟随 Codex 全局默认</option>
                  <option value="native">本机 CLI 登录态</option>
                  {codexProviders.map((prov) => (
                    <option key={prov.id} value={prov.id}>
                      {prov.name} ({prov.base_url})
                    </option>
                  ))}
                </select>
              </div>
              <div className="v2-field">
                <label htmlFor="mgr-model">模型 (Model)</label>
                <input
                  id="mgr-model"
                  className="v2-input v2-input--mono"
                  placeholder="留空继承默认"
                  value={settings.model || ''}
                  onChange={(e) => setSettings((s) => ({ ...s, model: e.target.value }))}
                />
              </div>
              <div className="v2-field">
                <label htmlFor="mgr-effort">推理强度 (Reasoning Effort)</label>
                <select
                  id="mgr-effort"
                  className="v2-select"
                  value={settings.reasoning_effort || 'high'}
                  onChange={(e) => setSettings((s) => ({ ...s, reasoning_effort: e.target.value }))}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="xhigh">xhigh</option>
                </select>
              </div>
            </div>

            <div className="v2-field">
              <label htmlFor="mgr-prompt">附加项目指令 (Additional Prompt)</label>
              <textarea
                id="mgr-prompt"
                rows={4}
                className="v2-textarea"
                placeholder="例如：优先保持最小改动，所有前端更改必须通过 vitest 与 vite build..."
                value={settings.additional_prompt || ''}
                onChange={(e) => setSettings((s) => ({ ...s, additional_prompt: e.target.value }))}
              />
            </div>

            <div>
              <Button type="submit" variant="primary" className="v2-btn-sm v2-btn-icon" busy={saving}>
                <Save size={14} aria-hidden="true" />
                保存 Manager 设置
              </Button>
            </div>
          </form>
        </section>
      ) : null}

      {/* Token Usage Summary */}
      <section className="v2-card" aria-label="Token 用量统计">
        <div className="v2-card__header">
          <div>
            <h2 className="v2-card__title">Token 用量汇总</h2>
            <p className="v2-card__subtitle">
              {projectId ? '当前项目按 Adapter / Model / Provider 分组统计。' : '全部项目用量统计。'}
            </p>
          </div>
        </div>

        {usageRows.length === 0 ? (
          <p style={{ margin: 0, fontSize: '0.86rem', color: 'var(--product-text-muted)' }}>
            暂无 Token 消耗记录。
          </p>
        ) : (
          <div className="v2-table-wrap">
            <table className="v2-table">
              <thead>
                <tr>
                  <th>Adapter</th>
                  <th>Model</th>
                  <th>Provider</th>
                  <th>调用次数</th>
                  <th>Input Tokens</th>
                  <th>Cached Tokens</th>
                  <th>Output Tokens</th>
                </tr>
              </thead>
              <tbody>
                {usageRows.map((row, idx) => (
                  <tr key={`${row.adapter}-${row.model}-${row.provider_id}-${idx}`}>
                    <td><code>{row.adapter}</code></td>
                    <td><code>{row.model || 'default'}</code></td>
                    <td><code>{row.provider_id || 'native'}</code></td>
                    <td>{Number(row.calls || 0).toLocaleString()}</td>
                    <td>{Number(row.input_tokens || 0).toLocaleString()}</td>
                    <td>{Number(row.cached_input_tokens || 0).toLocaleString()}</td>
                    <td>{Number(row.output_tokens || 0).toLocaleString()}</td>
                  </tr>
                ))}
                <tr style={{ fontWeight: 700, background: 'var(--product-surface-muted)' }}>
                  <td colSpan={3}>合计</td>
                  <td>{totals.calls.toLocaleString()}</td>
                  <td>{totals.input.toLocaleString()}</td>
                  <td>{totals.cached.toLocaleString()}</td>
                  <td>{totals.output.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Doctor Status */}
      <DoctorStatusCard doctor={doctor} loading={doctorLoading} onRefresh={loadAll} />
    </div>
  );
}
