import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  FolderOpen,
  FolderPlus,
  GitBranch,
  PlayCircle,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { api } from '../api';
import { useWorkspace } from '../context/WorkspaceContext';
import DirectoryPickerModal from '../components/DirectoryPickerModal';
import ConfirmModal from '../components/ConfirmModal';
import DoctorStatusCard from '../components/DoctorStatusCard';
import Button from '../components/ui/Button';
import InlineNotice from '../components/ui/InlineNotice';
import StatusBadge from '../components/ui/StatusBadge';
import { useToast } from '../components/ui/ToastProvider';

export const SAFE_REMOVE_WARNING =
  '仅从 GoodTeam 移除记录并清理临时 worktree，绝不删除本地源码目录';

export default function ProjectsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { projects, projectsLoading, projectsError, refreshProjects, setActiveProjectId } =
    useWorkspace();

  const [manualPath, setManualPath] = useState('');
  const [customName, setCustomName] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);

  const [removeTarget, setRemoveTarget] = useState(null);
  const [removing, setRemoving] = useState(false);

  const [doctor, setDoctor] = useState(null);
  const [doctorLoading, setDoctorLoading] = useState(true);
  const [doctorError, setDoctorError] = useState('');

  const loadDoctor = useCallback(async () => {
    setDoctorLoading(true);
    setDoctorError('');
    try {
      const data = await api.getDoctor();
      setDoctor(data);
    } catch (err) {
      setDoctorError(err.message || '环境检查失败');
    } finally {
      setDoctorLoading(false);
    }
  }, []);

  useEffect(() => {
    const id = setTimeout(() => {
      loadDoctor();
    }, 0);
    return () => clearTimeout(id);
  }, [loadDoctor]);

  const submitImport = async (pathToImport) => {
    const trimmed = (pathToImport ?? manualPath).trim();
    if (!trimmed) {
      setImportError('请选择或输入有效的本地项目目录路径。');
      return;
    }
    setImporting(true);
    setImportError('');
    try {
      const created = await api.createProject({
        path: trimmed,
        ...(customName.trim() ? { name: customName.trim() } : {}),
      });
      await refreshProjects();
      setManualPath('');
      setCustomName('');
      toast.success(`已导入项目「${created?.name || trimmed}」`);
      if (created?.id) {
        setActiveProjectId(created.id);
        navigate(`/projects/${encodeURIComponent(created.id)}/chat`);
      }
    } catch (err) {
      setImportError(err.message || '导入项目失败');
    } finally {
      setImporting(false);
    }
  };

  const handleOpenProject = (project) => {
    setActiveProjectId(project.id);
    navigate(`/projects/${encodeURIComponent(project.id)}/chat`);
  };

  const handleConfirmRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      await api.deleteProject(removeTarget.id);
      toast.success(`已安全移除项目「${removeTarget.name}」的记录`);
      setRemoveTarget(null);
      await refreshProjects();
    } catch (err) {
      toast.error(err.message || '移除项目失败');
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className="v2-page">
      <header className="v2-page-header">
        <div>
          <p className="v2-eyebrow">WORKSPACES</p>
          <h1>本地项目工作区</h1>
          <p>导入本地 Git 仓库，由 Manager 协调带 Skill 的 Sub-agent 在隔离 worktree 中协作开发。</p>
        </div>
        <div className="v2-actions">
          <Button
            variant="primary"
            className="v2-btn-icon"
            onClick={() => setPickerOpen(true)}
          >
            <FolderPlus size={16} aria-hidden="true" />
            导入本地项目
          </Button>
        </div>
      </header>

      {/* Manual path input fallback & quick import card */}
      <section className="v2-card" aria-label="导入本地项目">
        <div className="v2-card__header">
          <div>
            <h2 className="v2-card__title">选择或输入本地项目路径</h2>
            <p className="v2-card__subtitle">
              支持通过目录浏览器选择，或直接粘贴绝对路径；GoodTeam 仅建立索引与隔离 worktree，不改动原始目录结构。
            </p>
          </div>
          <Button
            variant="secondary"
            className="v2-btn-sm v2-btn-icon"
            onClick={() => setPickerOpen(true)}
          >
            <FolderOpen size={14} aria-hidden="true" />
            浏览目录
          </Button>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submitImport(manualPath);
          }}
          style={{ display: 'grid', gap: '0.75rem' }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.8fr) minmax(0, 0.9fr) auto', gap: '0.55rem', alignItems: 'end' }}>
            <div className="v2-field">
              <label htmlFor="project-path-input">项目目录路径</label>
              <input
                id="project-path-input"
                type="text"
                className="v2-input v2-input--mono"
                placeholder="/Users/you/workspace/my-repo"
                value={manualPath}
                onChange={(e) => {
                  setManualPath(e.target.value);
                  if (importError) setImportError('');
                }}
              />
            </div>
            <div className="v2-field">
              <label htmlFor="project-name-input">显示名称（可选）</label>
              <input
                id="project-name-input"
                type="text"
                className="v2-input"
                placeholder="留空默认使用目录名"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
              />
            </div>
            <Button type="submit" variant="primary" busy={importing}>
              导入并进入
            </Button>
          </div>

          {importError ? (
            <InlineNotice tone="danger" title="导入失败">
              {importError}
            </InlineNotice>
          ) : null}
        </form>
      </section>

      {/* Imported projects list */}
      <section style={{ display: 'grid', gap: '0.75rem' }} aria-label="已导入项目列表">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h2 className="v2-card__title">已导入项目（{projects.length}）</h2>
        </div>

        {projectsError ? (
          <InlineNotice
            tone="danger"
            title="加载项目列表失败"
            action={
              <Button variant="secondary" className="v2-btn-sm" onClick={refreshProjects}>
                重试
              </Button>
            }
          >
            {projectsError}
          </InlineNotice>
        ) : projectsLoading && projects.length === 0 ? (
          <div className="v2-card" style={{ color: 'var(--product-text-muted)' }}>
            正在加载项目列表...
          </div>
        ) : projects.length === 0 ? (
          <div className="v2-card" style={{ textAlign: 'center', padding: '2.5rem 1.25rem' }}>
            <p style={{ margin: 0, fontWeight: 620 }}>尚未导入任何本地项目</p>
            <p style={{ margin: '0.4rem 0 1rem', color: 'var(--product-text-muted)', fontSize: '0.88rem' }}>
              点击「导入本地项目」选择本地仓库，即可开始与 Manager 对话并委派 Sub-agent。
            </p>
            <Button variant="primary" className="v2-btn-icon" onClick={() => setPickerOpen(true)}>
              <FolderPlus size={15} aria-hidden="true" />
              导入本地项目
            </Button>
          </div>
        ) : (
          <div className="v2-project-list">
            {projects.map((project) => {
              const activeRuns = Number(project.active_runs ?? project.running_runs ?? 0);
              return (
                <article key={project.id} className="v2-project-row">
                  <div className="v2-project-row__main">
                    <div className="v2-project-row__title">
                      <h3 className="v2-project-row__name">{project.name}</h3>
                      <StatusBadge tone="neutral">
                        <GitBranch size={12} style={{ marginRight: '4px' }} aria-hidden="true" />
                        {project.default_branch || 'main'}
                      </StatusBadge>
                      {activeRuns > 0 ? (
                        <StatusBadge tone="active">
                          <PlayCircle size={12} style={{ marginRight: '4px' }} aria-hidden="true" />
                          {activeRuns} 个运行中
                        </StatusBadge>
                      ) : null}
                      {project.autopilot_enabled ? (
                        <StatusBadge tone="accent">多轮续跑中</StatusBadge>
                      ) : null}
                    </div>
                    <code className="v2-project-row__path" title={project.path}>
                      {project.path}
                    </code>
                  </div>

                  <div className="v2-actions">
                    <Button
                      variant="primary"
                      className="v2-btn-sm v2-btn-icon"
                      onClick={() => handleOpenProject(project)}
                    >
                      进入协作对话
                      <ArrowRight size={14} aria-hidden="true" />
                    </Button>
                    <Button
                      variant="secondary"
                      className="v2-btn-sm v2-btn-icon"
                      onClick={() => setRemoveTarget(project)}
                      aria-label={`移除项目 ${project.name}`}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      移除项目
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {/* Doctor status */}
      <DoctorStatusCard
        doctor={doctor}
        loading={doctorLoading}
        error={doctorError}
        onRefresh={loadDoctor}
      />

      {pickerOpen ? (
        <DirectoryPickerModal
          initialPath={manualPath}
          onClose={() => setPickerOpen(false)}
          onSelect={(selectedPath) => {
            setManualPath(selectedPath);
            submitImport(selectedPath);
          }}
        />
      ) : null}

      <ConfirmModal
        open={Boolean(removeTarget)}
        title={`移除项目「${removeTarget?.name || ''}」`}
        confirmLabel="确认移除记录"
        confirmVariant="danger"
        busy={removing}
        onCancel={() => setRemoveTarget(null)}
        onConfirm={handleConfirmRemove}
      >
        <InlineNotice tone="warning" title="安全移除说明">
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.45rem' }}>
            <ShieldCheck size={16} style={{ flexShrink: 0, marginTop: '2px', color: 'var(--product-success)' }} />
            <span>{SAFE_REMOVE_WARNING}</span>
          </div>
        </InlineNotice>
        {removeTarget?.path ? (
          <div style={{ fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>
            保留的本地源码目录：
            <code style={{ display: 'block', marginTop: '0.25rem', wordBreak: 'break-all' }}>
              {removeTarget.path}
            </code>
          </div>
        ) : null}
      </ConfirmModal>
    </div>
  );
}
