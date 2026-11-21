import { useEffect, useId, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowUp,
  File,
  Folder,
  GitBranch,
  RefreshCw,
  X,
} from 'lucide-react';
import Button from './ui/Button';

async function readDirectory(path) {
  const url = path ? `/api/fs/list?path=${encodeURIComponent(path)}` : '/api/fs/list';
  const response = await fetch(url);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || data.message || `HTTP ${response.status}`);
  }
  return data;
}

const DEFAULT_STRINGS = {
  browserLabel: '本地目录浏览器',
  directoryTitle: '选择本地项目目录',
  fileTitle: '选择本地文件',
  directoryDescription: '浏览本机目录并选择要导入的目录。',
  fileDescription: '浏览本机目录并选择文件。',
  close: '关闭目录浏览器',
  up: '上一级',
  newFolderLabel: '新建目录名',
  newFolderPlaceholder: '输入新建文件夹名称',
  newFolderAction: '新建文件夹',
  newFolderRequired: '请输入文件夹名称。',
  loading: '正在读取目录...',
  errorTitle: '无法读取该目录',
  emptyDirectory: '当前目录为空。',
  noSelectableFiles: '当前目录下没有可选文件。',
  cancel: '取消',
  selectFolder: '选择当前目录',
  gitRepository: 'Git 仓库',
};

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export default function DirectoryPickerModal({
  initialPath = '',
  mode = 'directory',
  allowCreateFolder = true,
  onClose,
  onSelect,
  strings,
}) {
  const copy = { ...DEFAULT_STRINGS, ...strings };
  const titleId = useId();
  const descriptionId = useId();
  const newFolderId = useId();
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const [pickerPath, setPickerPath] = useState('');
  const [pickerParent, setPickerParent] = useState(null);
  const [pickerEntries, setPickerEntries] = useState([]);
  const [pickerLoading, setPickerLoading] = useState(true);
  const [pickerError, setPickerError] = useState(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderError, setNewFolderError] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);

  useEffect(() => {
    const previouslyFocused = globalThis.document?.activeElement;
    closeButtonRef.current?.focus();
    return () => {
      if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) {
        previouslyFocused.focus();
      }
    };
  }, []);

  const fetchDirectory = async (path) => {
    setPickerLoading(true);
    setPickerError(null);
    try {
      const data = await readDirectory(path);
      setPickerPath(data.path || '');
      setPickerParent(data.parent ?? null);
      setPickerEntries(data.entries || []);
    } catch (err) {
      setPickerError(err.message || '读取目录失败');
    } finally {
      setPickerLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const loadInitial = async () => {
      try {
        const data = await readDirectory(initialPath || '');
        if (cancelled) return;
        setPickerPath(data.path || '');
        setPickerParent(data.parent ?? null);
        setPickerEntries(data.entries || []);
        setPickerError(null);
      } catch (err) {
        if (cancelled) return;
        setPickerError(err.message || '读取目录失败');
      } finally {
        if (!cancelled) setPickerLoading(false);
      }
    };
    loadInitial();
    return () => {
      cancelled = true;
    };
  }, [initialPath]);

  const handleCreateFolder = async (e) => {
    e?.preventDefault?.();
    const name = newFolderName.trim();
    if (!name) {
      setNewFolderError(copy.newFolderRequired);
      return;
    }
    setCreatingFolder(true);
    setNewFolderError('');
    try {
      const response = await fetch('/api/fs/mkdir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent: pickerPath, name }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || payload.message || `创建目录失败 (HTTP ${response.status})`);
      }
      setNewFolderName('');
      await fetchDirectory(payload.path || pickerPath);
    } catch (err) {
      setNewFolderError(err.message || '创建目录失败');
    } finally {
      setCreatingFolder(false);
    }
  };

  const handleSelectFolder = () => {
    if (!onSelect) return;
    onSelect(pickerPath);
    onClose();
  };

  const handleEntrySelect = (entry) => {
    if (entry.is_dir) {
      fetchDirectory(entry.path);
      return;
    }
    if (mode === 'file' && onSelect) {
      onSelect(entry.path);
      onClose();
    }
  };

  const visibleEntries = mode === 'file'
    ? pickerEntries
    : pickerEntries.filter((entry) => entry.is_dir);
  const pickerTitle = mode === 'file' ? copy.fileTitle : copy.directoryTitle;
  const pickerDescription = mode === 'file' ? copy.fileDescription : copy.directoryDescription;

  const handleDialogKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR)]
      .filter((el) => !el.classList.contains('hidden'));
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && globalThis.document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && globalThis.document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="v2-modal-backdrop" role="presentation">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        onKeyDown={handleDialogKeyDown}
        className="v2-modal"
      >
        <div className="v2-modal__header">
          <div>
            <p className="v2-eyebrow">{copy.browserLabel}</p>
            <h2 id={titleId}>{pickerTitle}</h2>
            <p id={descriptionId} className="sr-only">{pickerDescription}</p>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="projects-icon-button"
            aria-label={copy.close}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="v2-modal__body">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Button
              variant="secondary"
              className="v2-btn-sm v2-btn-icon"
              disabled={pickerParent === null || pickerLoading}
              onClick={() => fetchDirectory(pickerParent)}
            >
              <ArrowUp size={14} aria-hidden="true" />
              {copy.up}
            </Button>
            <code
              className="v2-input v2-input--mono"
              style={{ flex: 1, overflowX: 'auto', whiteSpace: 'nowrap' }}
            >
              {pickerPath || '/'}
            </code>
          </div>

          {allowCreateFolder ? (
            <form onSubmit={handleCreateFolder} style={{ display: 'grid', gap: '0.45rem' }}>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <label className="sr-only" htmlFor={newFolderId}>{copy.newFolderLabel}</label>
                <input
                  id={newFolderId}
                  type="text"
                  className="v2-input v2-input--mono"
                  value={newFolderName}
                  onChange={(e) => {
                    setNewFolderName(e.target.value);
                    if (newFolderError) setNewFolderError('');
                  }}
                  placeholder={copy.newFolderPlaceholder}
                  disabled={pickerLoading || creatingFolder}
                />
                <Button
                  type="submit"
                  variant="secondary"
                  className="v2-btn-sm v2-btn-icon"
                  disabled={pickerLoading || creatingFolder || !newFolderName.trim()}
                >
                  {creatingFolder ? <RefreshCw size={14} className="product-button__spinner" /> : <Folder size={14} />}
                  {copy.newFolderAction}
                </Button>
              </div>
              {newFolderError ? (
                <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--product-danger)' }}>
                  {newFolderError}
                </p>
              ) : null}
            </form>
          ) : null}

          <div
            style={{
              minHeight: '220px',
              maxHeight: '360px',
              overflowY: 'auto',
              border: '1px solid var(--product-border)',
              borderRadius: '7px',
              background: 'var(--product-bg)',
            }}
          >
            {pickerLoading ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--product-text-muted)' }}>
                <RefreshCw size={16} className="product-button__spinner" style={{ display: 'inline' }} />
                {copy.loading}
              </div>
            ) : pickerError ? (
              <div style={{ padding: '1rem', color: 'var(--product-danger)', display: 'flex', gap: '0.5rem' }}>
                <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
                <div>
                  <strong>{copy.errorTitle}</strong>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}>{pickerError}</div>
                </div>
              </div>
            ) : visibleEntries.length === 0 ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--product-text-muted)', fontSize: '0.86rem' }}>
                {mode === 'file' ? copy.noSelectableFiles : copy.emptyDirectory}
              </div>
            ) : (
              <div>
                {visibleEntries.map((entry) => (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => handleEntrySelect(entry)}
                    style={{
                      width: '100%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '0.6rem',
                      padding: '0.55rem 0.8rem',
                      border: 0,
                      borderBottom: '1px solid var(--product-border)',
                      background: 'transparent',
                      color: 'var(--product-text)',
                      textAlign: 'left',
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
                      {entry.is_dir ? (
                        <Folder size={15} style={{ color: 'var(--product-accent-strong)', flexShrink: 0 }} />
                      ) : (
                        <File size={15} style={{ color: 'var(--product-text-muted)', flexShrink: 0 }} />
                      )}
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {entry.name}
                      </span>
                    </span>
                    {entry.is_git_repo ? (
                      <span
                        className="product-status-badge"
                        data-tone="success"
                        aria-label={copy.gitRepository}
                        style={{ gap: '0.25rem', fontSize: '0.7rem' }}
                      >
                        <GitBranch size={11} aria-hidden="true" /> git
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="v2-modal__footer">
          <Button variant="secondary" onClick={onClose}>
            {copy.cancel}
          </Button>
          {mode !== 'file' ? (
            <Button
              variant="primary"
              disabled={pickerLoading || Boolean(pickerError) || !pickerPath}
              onClick={handleSelectFolder}
            >
              {copy.selectFolder}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
