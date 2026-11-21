import { useCallback, useEffect, useState } from 'react';
import { Eye, FilePlus2, FileText, Pencil, Save, Search } from 'lucide-react';
import { api } from '../api';
import Button from './ui/Button';
import InlineNotice from './ui/InlineNotice';
import MarkdownView from './ui/MarkdownView';
import { useToast } from './ui/ToastProvider';

export default function MemoryPanel({ projectId }) {
  const toast = useToast();
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState('project.md');
  const [content, setContent] = useState('');
  const [mode, setMode] = useState('preview');
  const [loadingFile, setLoadingFile] = useState(false);
  const [saving, setSaving] = useState(false);

  const [newFilename, setNewFilename] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchHits, setSearchHits] = useState(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');

  const loadFiles = useCallback(async () => {
    if (!projectId) return;
    setError('');
    try {
      const data = await api.getMemory(projectId);
      const list = data?.files || [];
      setFiles(list);
      setSelectedFile((curr) => (curr && list.some((f) => f.filename === curr) ? curr : list[0]?.filename || 'project.md'));
    } catch (err) {
      setError(err.message || '加载记忆目录失败');
    }
  }, [projectId]);

  useEffect(() => {
    const id = setTimeout(() => {
      loadFiles();
    }, 0);
    return () => clearTimeout(id);
  }, [loadFiles]);

  const loadFileContent = useCallback(async (filename) => {
    if (!projectId || !filename) return;
    setLoadingFile(true);
    setError('');
    try {
      const data = await api.getMemory(projectId, { filename });
      setContent(data?.content || '');
    } catch (err) {
      setError(err.message || `读取 ${filename} 失败`);
    } finally {
      setLoadingFile(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !selectedFile) return undefined;
    const id = setTimeout(() => {
      loadFileContent(selectedFile);
    }, 0);
    return () => clearTimeout(id);
  }, [projectId, selectedFile, loadFileContent]);

  const handleSave = async () => {
    if (!projectId || !selectedFile) return;
    setSaving(true);
    try {
      await api.saveMemory(projectId, { filename: selectedFile, content, mode: 'replace' });
      toast.success(`已保存记忆文档 ${selectedFile}`);
      await loadFiles();
    } catch (err) {
      toast.error(err.message || '保存记忆文档失败');
    } finally {
      setSaving(false);
    }
  };

  const handleCreateFile = async (e) => {
    e.preventDefault();
    let name = newFilename.trim().toLowerCase();
    if (!name) return;
    if (!name.endsWith('.md')) name = `${name}.md`;
    if (!/^[a-z0-9][a-z0-9._-]{0,60}\.md$/.test(name)) {
      toast.error('文件名仅支持小写字母、数字、点、短横线、下划线，并以 .md 结尾。');
      return;
    }
    try {
      await api.saveMemory(projectId, {
        filename: name,
        content: `# ${name.replace(/\.md$/, '')}\n\n`,
        mode: 'replace',
      });
      setNewFilename('');
      await loadFiles();
      setSelectedFile(name);
      setMode('edit');
      toast.success(`已创建记忆文档 ${name}`);
    } catch (err) {
      toast.error(err.message || '新建记忆文档失败');
    }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    const q = searchQuery.trim();
    if (!q) {
      setSearchHits(null);
      return;
    }
    setSearching(true);
    try {
      const data = await api.getMemory(projectId, { query: q });
      setSearchHits(data?.hits || []);
    } catch (err) {
      toast.error(err.message || '搜索记忆失败');
    } finally {
      setSearching(false);
    }
  };

  if (!projectId) {
    return (
      <section className="v2-card">
        <h2 className="v2-card__title">项目记忆 (.goodteam/memory/*.md)</h2>
        <p className="v2-card__subtitle">请先在左侧选择项目，再浏览和编辑项目记忆文档。</p>
      </section>
    );
  }

  return (
    <section className="v2-card" style={{ display: 'grid', gap: '1rem' }} aria-label="项目记忆管理">
      <div className="v2-card__header" style={{ marginBottom: 0 }}>
        <div>
          <h2 className="v2-card__title">项目记忆（.goodteam/memory/*.md）</h2>
          <p className="v2-card__subtitle">
            沉淀架构约束、关键决策与避坑经验，Manager 与 Sub-agent 执行时可按需读取。
          </p>
        </div>

        <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.4rem', minWidth: '240px' }}>
          <input
            type="search"
            className="v2-input"
            aria-label="搜索项目记忆"
            placeholder="全文搜索记忆..."
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              if (!e.target.value.trim()) setSearchHits(null);
            }}
          />
          <Button type="submit" variant="secondary" className="v2-btn-sm v2-btn-icon" busy={searching}>
            <Search size={14} aria-hidden="true" />
            搜索
          </Button>
        </form>
      </div>

      {error ? <InlineNotice tone="danger" title="记忆读取失败">{error}</InlineNotice> : null}

      {searchHits !== null ? (
        <div className="v2-card v2-card--muted" style={{ padding: '0.75rem', display: 'grid', gap: '0.45rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <strong style={{ fontSize: '0.84rem' }}>搜索结果 ({searchHits.length} 个文档命中)</strong>
            <Button variant="secondary" className="v2-btn-sm" onClick={() => setSearchHits(null)}>
              清除搜索
            </Button>
          </div>
          {searchHits.length === 0 ? (
            <span style={{ fontSize: '0.82rem', color: 'var(--product-text-muted)' }}>未匹配到相关内容。</span>
          ) : (
            searchHits.map((hit) => (
              <div key={hit.filename} style={{ display: 'grid', gap: '0.2rem' }}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedFile(hit.filename);
                    setSearchHits(null);
                  }}
                  style={{
                    border: 0,
                    background: 'transparent',
                    padding: 0,
                    textAlign: 'left',
                    color: 'var(--product-accent-strong)',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 650,
                    cursor: 'pointer',
                  }}
                >
                  {hit.filename}
                </button>
                {(hit.matches || []).slice(0, 4).map((line, i) => (
                  <code key={i} style={{ fontSize: '0.75rem', color: 'var(--product-text-muted)' }}>
                    {line}
                  </code>
                ))}
              </div>
            ))
          )}
        </div>
      ) : null}

      <div className="v2-memory-grid">
        <div style={{ display: 'grid', gap: '0.6rem' }}>
          <div className="v2-memory-list">
            {files.map((file) => (
              <button
                key={file.filename}
                type="button"
                className={selectedFile === file.filename ? 'is-active' : ''}
                onClick={() => setSelectedFile(file.filename)}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', minWidth: 0 }}>
                  <FileText size={14} aria-hidden="true" />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {file.filename}
                  </span>
                </span>
                {!file.exists ? (
                  <span style={{ fontSize: '0.68rem', color: 'var(--product-text-muted)' }}>未创建</span>
                ) : null}
              </button>
            ))}
          </div>

          <form onSubmit={handleCreateFile} style={{ display: 'flex', gap: '0.35rem' }}>
            <input
              type="text"
              className="v2-input v2-input--mono"
              aria-label="新建记忆文件名"
              placeholder="新建如 api.md"
              value={newFilename}
              onChange={(e) => setNewFilename(e.target.value)}
            />
            <Button type="submit" variant="secondary" className="v2-btn-sm v2-btn-icon" disabled={!newFilename.trim()}>
              <FilePlus2 size={14} aria-hidden="true" />
            </Button>
          </form>
        </div>

        <div style={{ display: 'grid', gap: '0.75rem', minWidth: 0 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
            <code style={{ fontWeight: 700, fontSize: '0.88rem' }}>{selectedFile}</code>
            <div className="v2-actions">
              <div className="v2-tabs" role="group" aria-label="文档模式切换">
                <button
                  type="button"
                  className={mode === 'preview' ? 'is-active' : ''}
                  onClick={() => setMode('preview')}
                >
                  <Eye size={14} aria-hidden="true" />
                  预览
                </button>
                <button
                  type="button"
                  className={mode === 'edit' ? 'is-active' : ''}
                  onClick={() => setMode('edit')}
                >
                  <Pencil size={14} aria-hidden="true" />
                  编辑
                </button>
              </div>
              <Button variant="primary" className="v2-btn-sm v2-btn-icon" busy={saving} onClick={handleSave}>
                <Save size={14} aria-hidden="true" />
                保存文档
              </Button>
            </div>
          </div>

          {loadingFile ? (
            <div className="v2-card v2-card--muted" style={{ color: 'var(--product-text-muted)' }}>
              正在读取文档...
            </div>
          ) : mode === 'edit' ? (
            <textarea
              className="v2-textarea v2-textarea--mono"
              aria-label="编辑记忆 Markdown 内容"
              rows={12}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="# 项目背景与约定..."
            />
          ) : (
            <div
              style={{
                minHeight: '220px',
                padding: '1rem 1.15rem',
                borderRadius: '7px',
                border: '1px solid var(--product-border)',
                background: 'var(--product-bg)',
              }}
            >
              {content.trim() ? (
                <MarkdownView content={content} />
              ) : (
                <p style={{ margin: 0, color: 'var(--product-text-muted)', fontSize: '0.86rem' }}>
                  该文档暂无内容，切换到「编辑」即可编写并保存。
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
