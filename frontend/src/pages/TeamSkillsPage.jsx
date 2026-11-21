import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  BookOpen,
  Edit3,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { api } from '../api';
import { useWorkspace } from '../context/WorkspaceContext';
import ConfirmModal from '../components/ConfirmModal';
import RoleEditorModal from '../components/RoleEditorModal';
import SkillBindingsEditor from '../components/SkillBindingsEditor';
import SkillInstallerPanel from '../components/SkillInstallerPanel';
import Button from '../components/ui/Button';
import MarkdownView from '../components/ui/MarkdownView';
import StatusBadge from '../components/ui/StatusBadge';
import { useToast } from '../components/ui/ToastProvider';

export default function TeamSkillsPage() {
  const { projectId: routeProjectId } = useParams();
  const toast = useToast();
  const { activeProjectId, setActiveProjectId } = useWorkspace();
  const projectId = routeProjectId || activeProjectId;

  const [tab, setTab] = useState('roles');
  const [roles, setRoles] = useState([]);
  const [skills, setSkills] = useState([]);
  const [providers, setProviders] = useState([]);
  const [managerSettings, setManagerSettings] = useState(null);
  const [savingManagerSkills, setSavingManagerSkills] = useState(false);

  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRole, setEditingRole] = useState(null);
  const [savingRole, setSavingRole] = useState(false);
  const [deleteRoleTarget, setDeleteRoleTarget] = useState(null);
  const [resettingRoles, setResettingRoles] = useState(false);

  const [viewingSkill, setViewingSkill] = useState(null);
  const [skillContent, setSkillContent] = useState('');
  const [loadingSkillContent, setLoadingSkillContent] = useState(false);
  const [uninstallTarget, setUninstallTarget] = useState(null);

  useEffect(() => {
    if (routeProjectId && routeProjectId !== activeProjectId) {
      setActiveProjectId(routeProjectId);
    }
  }, [routeProjectId, activeProjectId, setActiveProjectId]);

  const loadData = useCallback(async () => {
    try {
      const [skillList, provView] = await Promise.all([
        api.listSkills(),
        api.getProviders().catch(() => ({ providers: [], active: {} })),
      ]);
      setSkills(skillList);
      setProviders(provView?.providers || []);

      if (projectId) {
        const [roleList, mgr] = await Promise.all([
          api.listRoles(projectId),
          api.getManagerSettings(projectId),
        ]);
        setRoles(roleList);
        setManagerSettings(mgr);
      }
    } catch (err) {
      toast.error(err.message || '加载团队与能力配置失败');
    }
  }, [projectId, toast]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadData();
    }, 0);
    return () => clearTimeout(id);
  }, [loadData]);

  const handleSaveRole = async (payload, isEdit) => {
    setSavingRole(true);
    try {
      if (isEdit) {
        await api.updateRole(projectId, payload.key, payload);
        toast.success(`已更新角色「${payload.name}」`);
      } else {
        await api.createRole(projectId, payload);
        toast.success(`已创建自定义角色「${payload.name}」`);
      }
      setEditorOpen(false);
      setEditingRole(null);
      await loadData();
    } finally {
      setSavingRole(false);
    }
  };

  const handleDeleteRole = async () => {
    if (!deleteRoleTarget) return;
    try {
      await api.deleteRole(projectId, deleteRoleTarget.key);
      toast.info(`已删除角色「${deleteRoleTarget.name}」`);
      setDeleteRoleTarget(null);
      await loadData();
    } catch (err) {
      toast.error(err.message || '删除角色失败');
    }
  };

  const handleResetRoles = async () => {
    setResettingRoles(true);
    try {
      const list = await api.resetRoles(projectId);
      setRoles(list);
      toast.success('已恢复默认预置角色');
    } catch (err) {
      toast.error(err.message || '恢复默认角色失败');
    } finally {
      setResettingRoles(false);
    }
  };

  const handleSaveManagerSkills = async () => {
    if (!projectId || !managerSettings) return;
    setSavingManagerSkills(true);
    try {
      const saved = await api.saveManagerSettings(projectId, managerSettings);
      setManagerSettings(saved);
      toast.success('已保存 Manager 技能绑定');
    } catch (err) {
      toast.error(err.message || '保存 Manager 技能绑定失败');
    } finally {
      setSavingManagerSkills(false);
    }
  };

  const handleViewSkill = async (skill) => {
    setViewingSkill(skill);
    setSkillContent('');
    setLoadingSkillContent(true);
    try {
      const data = await api.getSkillContent(skill.id);
      setSkillContent(data?.content || data?.raw || '');
    } catch (err) {
      setSkillContent(`无法读取 SKILL.md：${err.message}`);
    } finally {
      setLoadingSkillContent(false);
    }
  };

  const handleUninstallSkill = async () => {
    if (!uninstallTarget) return;
    try {
      await api.deleteSkill(uninstallTarget.id);
      toast.info(`已卸载 Skill「${uninstallTarget.name}」`);
      setUninstallTarget(null);
      await loadData();
    } catch (err) {
      toast.error(err.message || '卸载 Skill 失败');
    }
  };

  const providerNameMap = Object.fromEntries(providers.map((p) => [p.id, p.name]));

  return (
    <div className="v2-page">
      <header className="v2-page-header">
        <div>
          <p className="v2-eyebrow">TEAM &amp; SKILLS</p>
          <h1>团队角色与 Skill 能力库</h1>
          <p>配置预置或自定义 Sub-agent 角色、绑定标准 SKILL.md 能力包，并为 Manager 注入项目级方法论。</p>
        </div>

        <div className="v2-tabs" role="tablist" aria-label="团队与能力标签页">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'roles'}
            className={tab === 'roles' ? 'is-active' : ''}
            onClick={() => setTab('roles')}
          >
            <Users size={15} aria-hidden="true" />
            Agent 角色
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'skills'}
            className={tab === 'skills' ? 'is-active' : ''}
            onClick={() => setTab('skills')}
          >
            <Sparkles size={15} aria-hidden="true" />
            Skill 能力库 ({skills.length})
          </button>
        </div>
      </header>

      {tab === 'roles' ? (
        !projectId ? (
          <div className="v2-card">
            <h2 className="v2-card__title">请先选择项目以配置团队角色</h2>
            <p className="v2-card__subtitle">角色配置按项目存储，前往项目列表选择后即可编辑。</p>
            <div style={{ marginTop: '0.85rem' }}>
              <Link to="/projects" className="product-button product-button--primary">
                前往项目列表
              </Link>
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: '1.25rem' }}>
            {/* Manager Skill Bindings Section */}
            <section className="v2-card" aria-label="Manager 自身绑定的 Skills">
              <div className="v2-card__header">
                <div>
                  <h2 className="v2-card__title">Manager 自身绑定的 Skills</h2>
                  <p className="v2-card__subtitle">
                    Manager 对话会话启动时将自动注入以下技能（required 全文注入，on_demand 按需读取）。
                  </p>
                </div>
                <Button
                  variant="primary"
                  className="v2-btn-sm v2-btn-icon"
                  busy={savingManagerSkills}
                  onClick={handleSaveManagerSkills}
                >
                  <Save size={14} aria-hidden="true" />
                  保存 Manager Skills
                </Button>
              </div>
              <SkillBindingsEditor
                skills={skills}
                bindings={managerSettings?.skill_bindings || []}
                onChange={(next) =>
                  setManagerSettings((prev) => ({ ...(prev || {}), skill_bindings: next }))
                }
                idPrefix="mgr-skill"
              />
            </section>

            {/* Sub-agent Roles */}
            <section style={{ display: 'grid', gap: '0.85rem' }} aria-label="Sub-agent 角色列表">
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '0.6rem' }}>
                <h2 className="v2-card__title">Sub-agent 角色 ({roles.length})</h2>
                <div className="v2-actions">
                  <Button
                    variant="secondary"
                    className="v2-btn-sm v2-btn-icon"
                    busy={resettingRoles}
                    onClick={handleResetRoles}
                  >
                    <RotateCcw size={14} aria-hidden="true" />
                    恢复默认角色
                  </Button>
                  <Button
                    variant="primary"
                    className="v2-btn-sm v2-btn-icon"
                    onClick={() => {
                      setEditingRole(null);
                      setEditorOpen(true);
                    }}
                  >
                    <Plus size={14} aria-hidden="true" />
                    新建自定义角色
                  </Button>
                </div>
              </div>

              <div className="v2-cards-grid">
                {roles.map((role) => {
                  const providerLabel =
                    role.provider_id === 'native'
                      ? '本机 CLI 登录态'
                      : role.provider_id
                        ? providerNameMap[role.provider_id] || role.provider_id
                        : '跟随全局默认';
                  return (
                    <article key={role.key} className="v2-card" style={{ display: 'grid', gap: '0.65rem', alignContent: 'start' }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.5rem' }}>
                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', flexWrap: 'wrap' }}>
                            <strong style={{ fontSize: '0.98rem' }}>{role.name}</strong>
                            <code style={{ fontSize: '0.76rem', color: 'var(--product-text-muted)' }}>{role.key}</code>
                            {role.is_builtin ? <StatusBadge tone="neutral">预置</StatusBadge> : <StatusBadge tone="accent">自定义</StatusBadge>}
                          </div>
                          {role.description ? (
                            <p style={{ margin: '0.3rem 0 0', fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>
                              {role.description}
                            </p>
                          ) : null}
                        </div>
                      </div>

                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                        <StatusBadge tone={role.execution_policy === 'workspace_write' ? 'warning' : 'success'}>
                          {role.execution_policy === 'workspace_write' ? '工作区写入' : '只读'}
                        </StatusBadge>
                        <StatusBadge tone="neutral">{role.adapter}</StatusBadge>
                        <StatusBadge tone="neutral">{providerLabel}</StatusBadge>
                        {role.model ? <StatusBadge tone="neutral">{role.model}</StatusBadge> : null}
                      </div>

                      {role.write_domains?.length ? (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', fontSize: '0.74rem' }}>
                          {role.write_domains.map((d) => (
                            <span key={d} className="v2-chip">{d}</span>
                          ))}
                        </div>
                      ) : null}

                      {role.skill_bindings?.length ? (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                          {role.skill_bindings.map((b) => (
                            <span key={b.skill_id} className="v2-chip">
                              {b.skill_id} ({b.mode === 'on_demand' ? '按需' : '必用'})
                            </span>
                          ))}
                        </div>
                      ) : null}

                      <div className="v2-actions" style={{ marginTop: '0.25rem' }}>
                        <Button
                          variant="secondary"
                          className="v2-btn-sm v2-btn-icon"
                          onClick={() => {
                            setEditingRole(role);
                            setEditorOpen(true);
                          }}
                        >
                          <Edit3 size={13} aria-hidden="true" />
                          编辑
                        </Button>
                        {!role.is_builtin ? (
                          <Button
                            variant="danger"
                            className="v2-btn-sm v2-btn-icon"
                            onClick={() => setDeleteRoleTarget(role)}
                          >
                            <Trash2 size={13} aria-hidden="true" />
                            删除
                          </Button>
                        ) : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          </div>
        )
      ) : (
        <div style={{ display: 'grid', gap: '1.25rem' }}>
          <SkillInstallerPanel projectId={projectId} onInstalled={loadData} />

          <section style={{ display: 'grid', gap: '0.75rem' }} aria-label="已安装 Skills 列表">
            <h2 className="v2-card__title">已安装 Skills ({skills.length})</h2>
            <div className="v2-cards-grid">
              {skills.map((skill) => (
                <article key={skill.id} className="v2-card" style={{ display: 'grid', gap: '0.55rem', alignContent: 'start' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <strong style={{ fontSize: '0.96rem' }}>{skill.name}</strong>
                    <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
                      <StatusBadge tone={skill.source_type === 'builtin' ? 'neutral' : 'accent'}>
                        {skill.source_type}
                      </StatusBadge>
                      {skill.commit_sha ? (
                        <code className="v2-chip">{skill.commit_sha.slice(0, 8)}</code>
                      ) : null}
                    </div>
                  </div>
                  {skill.description ? (
                    <p style={{ margin: 0, fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>
                      {skill.description}
                    </p>
                  ) : null}
                  <code style={{ fontSize: '0.72rem', color: 'var(--product-text-muted)', wordBreak: 'break-all' }}>
                    {skill.id}
                  </code>
                  <div className="v2-actions">
                    <Button
                      variant="secondary"
                      className="v2-btn-sm v2-btn-icon"
                      onClick={() => handleViewSkill(skill)}
                    >
                      <BookOpen size={13} aria-hidden="true" />
                      查看 SKILL.md
                    </Button>
                    {skill.source_type !== 'builtin' ? (
                      <Button
                        variant="danger"
                        className="v2-btn-sm v2-btn-icon"
                        onClick={() => setUninstallTarget(skill)}
                      >
                        <Trash2 size={13} aria-hidden="true" />
                        卸载
                      </Button>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}

      <RoleEditorModal
        open={editorOpen}
        initialRole={editingRole}
        providers={providers}
        skills={skills}
        saving={savingRole}
        onClose={() => {
          setEditorOpen(false);
          setEditingRole(null);
        }}
        onSave={handleSaveRole}
      />

      <ConfirmModal
        open={Boolean(deleteRoleTarget)}
        title={`删除自定义角色「${deleteRoleTarget?.name || ''}」？`}
        description="删除后该角色将从当前项目中移除。"
        confirmLabel="确认删除"
        onCancel={() => setDeleteRoleTarget(null)}
        onConfirm={handleDeleteRole}
      />

      <ConfirmModal
        open={Boolean(uninstallTarget)}
        title={`卸载 Skill「${uninstallTarget?.name || ''}」？`}
        description="卸载后将自动清理该技能在各角色中的绑定关系。"
        confirmLabel="确认卸载"
        onCancel={() => setUninstallTarget(null)}
        onConfirm={handleUninstallSkill}
      />

      {viewingSkill ? (
        <div className="v2-modal-backdrop" role="presentation">
          <div role="dialog" aria-modal="true" aria-label={`查看 ${viewingSkill.name}`} className="v2-modal">
            <div className="v2-modal__header">
              <h2>{viewingSkill.name} — SKILL.md</h2>
              <button
                type="button"
                className="projects-icon-button"
                onClick={() => setViewingSkill(null)}
                aria-label="关闭预览"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="v2-modal__body">
              {loadingSkillContent ? (
                <p style={{ margin: 0, color: 'var(--product-text-muted)' }}>正在加载 SKILL.md...</p>
              ) : (
                <MarkdownView content={skillContent} />
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
