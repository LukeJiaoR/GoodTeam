import { useEffect, useId, useState } from 'react';
import { X } from 'lucide-react';
import SkillBindingsEditor from './SkillBindingsEditor';
import Button from './ui/Button';
import InlineNotice from './ui/InlineNotice';

const EMPTY_ROLE = {
  key: '',
  name: '',
  description: '',
  system_prompt: '',
  execution_policy: 'read_only',
  write_domains: [],
  allowed_tools: [],
  adapter: 'codex',
  provider_id: null,
  model: '',
  reasoning_effort: 'high',
  skill_bindings: [],
};

function parseListInput(raw) {
  return String(raw || '')
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function RoleEditorModal(props) {
  if (!props.open) return null;
  const formKey = props.initialRole?.key || 'new';
  return <RoleEditorModalContent key={formKey} {...props} />;
}

function RoleEditorModalContent({
  initialRole = null,
  providers = [],
  skills = [],
  saving = false,
  onClose,
  onSave,
}) {
  const titleId = useId();
  const isEdit = Boolean(initialRole?.key);
  const initialBase = initialRole ? { ...EMPTY_ROLE, ...initialRole } : { ...EMPTY_ROLE };
  const [form, setForm] = useState(initialBase);
  const [domainsText, setDomainsText] = useState((initialBase.write_domains || []).join(', '));
  const [toolsText, setToolsText] = useState((initialBase.allowed_tools || []).join(', '));
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !saving) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [saving, onClose]);

  const matchingProviders = providers.filter((p) => p.agent === form.adapter);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const key = form.key.trim().toLowerCase();
    if (!/^[a-z][a-z0-9_-]{1,48}$/.test(key)) {
      setError('角色 Key 须以小写字母开头，仅包含小写字母、数字、下划线或短横线（2-49 位）。');
      return;
    }
    if (!form.name.trim()) {
      setError('请输入角色名称。');
      return;
    }
    setError('');
    const payload = {
      key,
      name: form.name.trim(),
      description: form.description.trim(),
      system_prompt: form.system_prompt.trim(),
      execution_policy: form.execution_policy,
      write_domains: form.execution_policy === 'workspace_write' ? parseListInput(domainsText) : [],
      allowed_tools: parseListInput(toolsText),
      adapter: form.adapter,
      provider_id: form.provider_id || null,
      model: form.model.trim(),
      reasoning_effort: form.reasoning_effort,
      skill_bindings: form.skill_bindings || [],
    };
    try {
      await onSave(payload, isEdit);
    } catch (err) {
      setError(err.message || '保存角色失败');
    }
  };

  return (
    <div className="v2-modal-backdrop" role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="v2-modal">
        <div className="v2-modal__header">
          <h2 id={titleId}>{isEdit ? `编辑角色 · ${initialRole.name}` : '新建自定义 Agent 角色'}</h2>
          <button type="button" className="projects-icon-button" onClick={onClose} aria-label="关闭弹窗">
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'contents' }}>
          <div className="v2-modal__body">
            {error ? <InlineNotice tone="danger" title="表单校验失败">{error}</InlineNotice> : null}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.75rem' }}>
              <div className="v2-field">
                <label htmlFor="role-name">角色名称</label>
                <input
                  id="role-name"
                  className="v2-input"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="例如：Frontend Engineer"
                  required
                />
              </div>
              <div className="v2-field">
                <label htmlFor="role-key">角色标识 (Key)</label>
                <input
                  id="role-key"
                  className="v2-input v2-input--mono"
                  value={form.key}
                  disabled={isEdit}
                  onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))}
                  placeholder="例如：frontend_dev"
                  required
                />
              </div>
            </div>

            <div className="v2-field">
              <label htmlFor="role-desc">职责说明</label>
              <input
                id="role-desc"
                className="v2-input"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="简述该角色何时由 Manager 调度以及擅长方向"
              />
            </div>

            <div className="v2-field">
              <label htmlFor="role-prompt">系统提示词 (System Prompt)</label>
              <textarea
                id="role-prompt"
                rows={4}
                className="v2-textarea"
                value={form.system_prompt}
                onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                placeholder="定义该 Sub-agent 的工作原则、输出格式与约束..."
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.75rem' }}>
              <div className="v2-field">
                <label htmlFor="role-policy">执行策略 (Execution Policy)</label>
                <select
                  id="role-policy"
                  className="v2-select"
                  value={form.execution_policy}
                  onChange={(e) => setForm((f) => ({ ...f, execution_policy: e.target.value }))}
                >
                  <option value="read_only">只读调研 (read_only)</option>
                  <option value="workspace_write">隔离 Worktree 写入 (workspace_write)</option>
                </select>
              </div>
              <div className="v2-field">
                <label htmlFor="role-adapter">CLI Adapter</label>
                <select
                  id="role-adapter"
                  className="v2-select"
                  value={form.adapter}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      adapter: e.target.value,
                      provider_id: null,
                    }))
                  }
                >
                  <option value="codex">Codex CLI</option>
                  <option value="claude">Claude CLI</option>
                </select>
              </div>
            </div>

            {form.execution_policy === 'workspace_write' ? (
              <div className="v2-field">
                <label htmlFor="role-domains">允许写入目录 (Write Domains，逗号分隔，留空表示全仓)</label>
                <input
                  id="role-domains"
                  className="v2-input v2-input--mono"
                  value={domainsText}
                  onChange={(e) => setDomainsText(e.target.value)}
                  placeholder="例如：frontend/, docs/"
                />
              </div>
            ) : null}

            <div className="v2-field">
              <label htmlFor="role-tools">允许工具 (Allowed Tools，逗号分隔，留空按策略默认)</label>
              <input
                id="role-tools"
                className="v2-input v2-input--mono"
                value={toolsText}
                onChange={(e) => setToolsText(e.target.value)}
                placeholder="例如：Read, Glob, Grep"
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '0.75rem' }}>
              <div className="v2-field">
                <label htmlFor="role-provider">Provider</label>
                <select
                  id="role-provider"
                  className="v2-select"
                  value={form.provider_id || ''}
                  onChange={(e) => setForm((f) => ({ ...f, provider_id: e.target.value || null }))}
                >
                  <option value="">跟随全局默认</option>
                  <option value="native">本机 CLI 登录态</option>
                  {matchingProviders.map((prov) => (
                    <option key={prov.id} value={prov.id}>
                      {prov.name} ({prov.base_url})
                    </option>
                  ))}
                </select>
              </div>
              <div className="v2-field">
                <label htmlFor="role-model">模型 (Model)</label>
                <input
                  id="role-model"
                  className="v2-input v2-input--mono"
                  value={form.model}
                  onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                  placeholder="留空继承默认"
                />
              </div>
              <div className="v2-field">
                <label htmlFor="role-effort">推理强度 (Reasoning Effort)</label>
                <select
                  id="role-effort"
                  className="v2-select"
                  value={form.reasoning_effort}
                  onChange={(e) => setForm((f) => ({ ...f, reasoning_effort: e.target.value }))}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="xhigh">xhigh</option>
                </select>
              </div>
            </div>

            <div className="v2-field">
              <label>绑定 Skills（可配置必用 / 按需）</label>
              <SkillBindingsEditor
                skills={skills}
                bindings={form.skill_bindings}
                onChange={(next) => setForm((f) => ({ ...f, skill_bindings: next }))}
                idPrefix="role-skill"
              />
            </div>
          </div>

          <div className="v2-modal__footer">
            <Button type="button" variant="secondary" disabled={saving} onClick={onClose}>
              取消
            </Button>
            <Button type="submit" variant="primary" busy={saving}>
              保存角色配置
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
