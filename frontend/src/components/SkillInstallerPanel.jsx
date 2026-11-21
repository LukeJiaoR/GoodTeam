import { useRef, useState } from 'react';
import { FolderOpen, GitBranch, Radar, Upload } from 'lucide-react';
import { api, fileToBase64 } from '../api';
import DirectoryPickerModal from './DirectoryPickerModal';
import Button from './ui/Button';
import InlineNotice from './ui/InlineNotice';
import { useToast } from './ui/ToastProvider';

export default function SkillInstallerPanel({ projectId, onInstalled }) {
  const toast = useToast();

  // GitHub installer state
  const [ghUrl, setGhUrl] = useState('');
  const [ghPreview, setGhPreview] = useState(null);
  const [ghSelected, setGhSelected] = useState([]);
  const [ghBusy, setGhBusy] = useState(false);
  const [ghError, setGhError] = useState('');

  // Zip installer state
  const zipInputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [zipPayload, setZipPayload] = useState(null);
  const [zipPreview, setZipPreview] = useState(null);
  const [zipSelected, setZipSelected] = useState([]);
  const [zipBusy, setZipBusy] = useState(false);
  const [zipError, setZipError] = useState('');

  // Local folder & discovery state
  const [localPath, setLocalPath] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [includeUserDirs, setIncludeUserDirs] = useState(false);
  const [discoverBusy, setDiscoverBusy] = useState(false);

  const toggleItem = (list, setList, relPath) => {
    setList(list.includes(relPath) ? list.filter((p) => p !== relPath) : [...list, relPath]);
  };

  const handleGitHubPreview = async (e) => {
    e.preventDefault();
    if (!ghUrl.trim()) return;
    setGhBusy(true);
    setGhError('');
    try {
      const data = await api.previewGitHubSkills(ghUrl.trim());
      setGhPreview(data);
      setGhSelected((data?.skills || []).map((s) => s.rel_path));
    } catch (err) {
      setGhError(err.message || '预览 GitHub Skill 失败');
      setGhPreview(null);
    } finally {
      setGhBusy(false);
    }
  };

  const handleGitHubInstall = async () => {
    if (!ghUrl.trim() || ghSelected.length === 0) return;
    setGhBusy(true);
    setGhError('');
    try {
      await api.installGitHubSkills(ghUrl.trim(), ghSelected);
      toast.success(`已从 GitHub 安装 ${ghSelected.length} 个 Skill`);
      setGhUrl('');
      setGhPreview(null);
      setGhSelected([]);
      onInstalled?.();
    } catch (err) {
      setGhError(err.message || '安装 GitHub Skill 失败');
    } finally {
      setGhBusy(false);
    }
  };

  const processZipFile = async (file) => {
    if (!file) return;
    setZipBusy(true);
    setZipError('');
    try {
      const content_base64 = await fileToBase64(file);
      const payload = { filename: file.name || 'skills.zip', content_base64 };
      const preview = await api.previewZipSkills(payload);
      setZipPayload(payload);
      setZipPreview(preview);
      setZipSelected((preview?.skills || []).map((s) => s.rel_path));
    } catch (err) {
      setZipError(err.message || '解析 .zip 技能包失败');
      setZipPayload(null);
      setZipPreview(null);
    } finally {
      setZipBusy(false);
    }
  };

  const handleZipInstall = async () => {
    if (!zipPayload || zipSelected.length === 0) return;
    setZipBusy(true);
    setZipError('');
    try {
      await api.installZipSkills({ ...zipPayload, selected_paths: zipSelected });
      toast.success(`已从压缩包安装 ${zipSelected.length} 个 Skill`);
      setZipPayload(null);
      setZipPreview(null);
      setZipSelected([]);
      onInstalled?.();
    } catch (err) {
      setZipError(err.message || '安装 .zip Skill 失败');
    } finally {
      setZipBusy(false);
    }
  };

  const handleInstallLocal = async (e) => {
    e?.preventDefault?.();
    if (!localPath.trim()) return;
    setLocalBusy(true);
    try {
      await api.installLocalSkill(localPath.trim());
      toast.success('已导入本地 Skill 目录');
      setLocalPath('');
      onInstalled?.();
    } catch (err) {
      toast.error(err.message || '导入本地 Skill 失败');
    } finally {
      setLocalBusy(false);
    }
  };

  const handleDiscover = async () => {
    setDiscoverBusy(true);
    try {
      const res = await api.discoverSkills({
        project_id: projectId || null,
        include_user_dirs: includeUserDirs,
      });
      const count = Array.isArray(res?.skills) ? res.skills.length : Array.isArray(res?.discovered) ? res.discovered.length : 0;
      toast.success(`自动扫描完成，发现 ${count} 个可用 Skill`);
      onInstalled?.();
    } catch (err) {
      toast.error(err.message || '自动扫描 Skill 失败');
    } finally {
      setDiscoverBusy(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '0.9rem' }}>
      {/* 1. GitHub URL Installer */}
      <section className="v2-card" style={{ display: 'grid', gap: '0.75rem', alignContent: 'start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
          <GitBranch size={16} aria-hidden="true" />
          <h3 className="v2-card__title">GitHub 链接安装</h3>
        </div>
        <form onSubmit={handleGitHubPreview} style={{ display: 'flex', gap: '0.45rem' }}>
          <input
            type="text"
            className="v2-input v2-input--mono"
            aria-label="GitHub 仓库链接或 owner/repo"
            placeholder="owner/repo 或 https://github.com/..."
            value={ghUrl}
            onChange={(e) => setGhUrl(e.target.value)}
          />
          <Button type="submit" variant="secondary" className="v2-btn-sm" busy={ghBusy}>
            预览
          </Button>
        </form>
        {ghError ? <InlineNotice tone="danger" title="GitHub 安装失败">{ghError}</InlineNotice> : null}
        {ghPreview?.skills?.length ? (
          <PreviewSkillList
            skills={ghPreview.skills}
            selected={ghSelected}
            onToggle={(p) => toggleItem(ghSelected, setGhSelected, p)}
            onInstall={handleGitHubInstall}
            busy={ghBusy}
            commitSha={ghPreview.commit_sha}
          />
        ) : null}
      </section>

      {/* 2. Drag & Drop Zip Installer */}
      <section className="v2-card" style={{ display: 'grid', gap: '0.75rem', alignContent: 'start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
          <Upload size={16} aria-hidden="true" />
          <h3 className="v2-card__title">上传 .zip 技能包</h3>
        </div>
        <div
          className={`v2-dropzone${dragging ? ' is-dragging' : ''}`}
          role="button"
          tabIndex={0}
          aria-label="拖拽或点击选择 .zip 技能包"
          onClick={() => zipInputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              zipInputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer?.files?.[0];
            if (file) processZipFile(file);
          }}
        >
          <input
            ref={zipInputRef}
            type="file"
            accept=".zip,application/zip"
            hidden
            data-testid="zip-upload-input"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) processZipFile(file);
            }}
          />
          <p style={{ margin: 0, fontWeight: 620, fontSize: '0.86rem' }}>
            {zipBusy ? '正在解析压缩包...' : '拖拽 .zip 到此处，或点击选择文件'}
          </p>
          <p style={{ margin: '0.25rem 0 0', fontSize: '0.76rem', color: 'var(--product-text-muted)' }}>
            内置 Zip Slip 路径穿越与软链安全校验，预览确认后再安装。
          </p>
        </div>
        {zipError ? <InlineNotice tone="danger" title="Zip 解析失败">{zipError}</InlineNotice> : null}
        {zipPreview?.skills?.length ? (
          <PreviewSkillList
            skills={zipPreview.skills}
            selected={zipSelected}
            onToggle={(p) => toggleItem(zipSelected, setZipSelected, p)}
            onInstall={handleZipInstall}
            busy={zipBusy}
          />
        ) : null}
      </section>

      {/* 3. Local Folder & Auto Discovery */}
      <section className="v2-card" style={{ display: 'grid', gap: '0.85rem', alignContent: 'start' }}>
        <div style={{ display: 'grid', gap: '0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
            <FolderOpen size={16} aria-hidden="true" />
            <h3 className="v2-card__title">本地目录导入</h3>
          </div>
          <form onSubmit={handleInstallLocal} style={{ display: 'flex', gap: '0.4rem' }}>
            <input
              type="text"
              className="v2-input v2-input--mono"
              aria-label="本地 Skill 目录路径"
              placeholder="/path/to/skill-folder (含 SKILL.md)"
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
            />
            <Button type="button" variant="secondary" className="v2-btn-sm" onClick={() => setPickerOpen(true)}>
              浏览
            </Button>
            <Button type="submit" variant="primary" className="v2-btn-sm" busy={localBusy} disabled={!localPath.trim()}>
              导入
            </Button>
          </form>
        </div>

        <div style={{ borderTop: '1px solid var(--product-border)', paddingTop: '0.75rem', display: 'grid', gap: '0.55rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
            <Radar size={16} aria-hidden="true" />
            <h3 className="v2-card__title">自动扫描发现</h3>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={includeUserDirs}
              onChange={(e) => setIncludeUserDirs(e.target.checked)}
              style={{ width: '15px', height: '15px' }}
            />
            <span>包含本机 ~/.claude/skills 与 ~/.codex/skills</span>
          </label>
          <div>
            <Button variant="secondary" className="v2-btn-sm v2-btn-icon" busy={discoverBusy} onClick={handleDiscover}>
              <Radar size={14} aria-hidden="true" />
              立即扫描
            </Button>
          </div>
        </div>
      </section>

      {pickerOpen ? (
        <DirectoryPickerModal
          initialPath={localPath}
          onClose={() => setPickerOpen(false)}
          onSelect={(selectedPath) => setLocalPath(selectedPath)}
          strings={{ directoryTitle: '选择包含 SKILL.md 的本地目录' }}
        />
      ) : null}
    </div>
  );
}

function PreviewSkillList({ skills, selected, onToggle, onInstall, busy, commitSha = '' }) {
  return (
    <div style={{ display: 'grid', gap: '0.5rem', paddingTop: '0.35rem', borderTop: '1px solid var(--product-border)' }}>
      {commitSha ? (
        <span style={{ fontSize: '0.74rem', color: 'var(--product-text-muted)', fontFamily: 'var(--font-mono)' }}>
          Commit: {commitSha.slice(0, 10)}
        </span>
      ) : null}
      {skills.map((item) => (
        <label
          key={item.rel_path}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.5rem',
            padding: '0.45rem 0.6rem',
            borderRadius: '6px',
            border: '1px solid var(--product-border)',
            background: 'var(--product-bg)',
            cursor: 'pointer',
          }}
        >
          <input
            type="checkbox"
            checked={selected.includes(item.rel_path)}
            onChange={() => onToggle(item.rel_path)}
            style={{ marginTop: '3px' }}
          />
          <span style={{ display: 'grid', gap: '0.15rem' }}>
            <strong style={{ fontSize: '0.84rem' }}>
              {item.name} <code style={{ fontWeight: 400, fontSize: '0.72rem' }}>({item.rel_path})</code>
            </strong>
            {item.description ? (
              <span style={{ fontSize: '0.76rem', color: 'var(--product-text-muted)' }}>
                {item.description}
              </span>
            ) : null}
          </span>
        </label>
      ))}
      <Button
        type="button"
        variant="primary"
        className="v2-btn-sm"
        busy={busy}
        disabled={selected.length === 0}
        onClick={onInstall}
      >
        安装已选 ({selected.length})
      </Button>
    </div>
  );
}
