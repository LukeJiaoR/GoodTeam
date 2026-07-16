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

async function readDirectory(path) {
  const url = path ? `/api/fs/list?path=${encodeURIComponent(path)}` : '/api/fs/list';
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || data.message || `HTTP error! status: ${response.status}`);
  }
  return data;
}

const DEFAULT_STRINGS = {
  browserLabel: 'Directory browser',
  directoryTitle: 'Select project root directory',
  fileTitle: 'Select context file',
  directoryDescription: 'Browse local directories and select a project root.',
  fileDescription: 'Browse local directories and select a context file.',
  close: 'Close directory browser',
  up: 'Up',
  newFolderLabel: 'New folder name',
  newFolderPlaceholder: 'New folder name',
  newFolderAction: 'New folder',
  newFolderRequired: 'Folder name is required.',
  loading: 'Loading directory',
  errorTitle: 'Cannot load folder',
  emptyDirectory: 'This directory is empty.',
  noSelectableFiles: 'No selectable files in this directory.',
  cancel: 'Cancel',
  selectFolder: 'Select this folder',
  gitRepository: 'Git repository',
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
      setPickerPath(data.path);
      setPickerParent(data.parent);
      setPickerEntries(data.entries || []);
    } catch (err) {
      setPickerError(err.message || 'Failed to read directory');
    } finally {
      setPickerLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;

    const loadInitialDirectory = async () => {
      try {
        const data = await readDirectory(initialPath || '');
        if (cancelled) return;
        setPickerPath(data.path);
        setPickerParent(data.parent);
        setPickerEntries(data.entries || []);
        setPickerError(null);
      } catch (err) {
        if (cancelled) return;
        setPickerError(err.message || 'Failed to read directory');
      } finally {
        if (!cancelled) {
          setPickerLoading(false);
        }
      }
    };

    loadInitialDirectory();
    return () => {
      cancelled = true;
    };
  }, [initialPath]);

  const handleCreateFolder = async (e) => {
    if (e?.preventDefault) {
      e.preventDefault();
    }

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
        throw new Error(payload.error || payload.message || `Failed to create folder (HTTP ${response.status})`);
      }

      setNewFolderName('');
      await fetchDirectory(payload.path || pickerPath);
    } catch (err) {
      setNewFolderError(err.message || 'Failed to create folder.');
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
      .filter((element) => !element.classList.contains('hidden'));
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
    <div className="fixed inset-0 z-45 flex items-center justify-center bg-zinc-950/50 px-4 py-6 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        onKeyDown={handleDialogKeyDown}
        className="w-full max-w-2xl bg-white dark:bg-zinc-950 border-2 border-border-subtle dark:border-zinc-800 shadow-card dark:shadow-card rounded-2xl overflow-hidden flex flex-col max-h-[85vh]"
      >
        {/* Header */}
        <div className="px-5 py-4 bg-surface-base dark:bg-zinc-900/80 border-b-2 border-border-subtle dark:border-zinc-800 flex items-start justify-between gap-4 shrink-0">
          <div>
            <p className="text-2xs font-extrabold uppercase tracking-wider text-text-muted">{copy.browserLabel}</p>
            <h3 id={titleId} className="font-mono text-xs font-extrabold text-accent-text dark:text-emerald-400 mt-1">
              {pickerTitle}
            </h3>
            <p id={descriptionId} className="sr-only">{pickerDescription}</p>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="bg-white dark:bg-zinc-800 hover:bg-status-danger-bg border-2 border-border-subtle dark:border-zinc-800 shadow-button dark:shadow-button text-zinc-550 hover:text-status-danger p-2 rounded-2xl transition-all duo-button-physics cursor-pointer"
            aria-label={copy.close}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation Bar / Breadcrumb */}
        <div className="px-5 py-3 bg-surface-base dark:bg-zinc-900/40 border-b border-border-subtle dark:border-zinc-800 flex items-center gap-3 shrink-0">
          <button
            type="button"
            disabled={pickerParent === null || pickerLoading}
            onClick={() => fetchDirectory(pickerParent)}
            className="flex items-center gap-1 bg-white dark:bg-zinc-800 border-2 border-border-subtle dark:border-zinc-800 disabled:opacity-50 disabled:pointer-events-none hover:bg-zinc-50 dark:hover:bg-zinc-800 shadow-button dark:shadow-button text-zinc-700 dark:text-zinc-300 font-bold px-3 py-1.5 rounded-xl text-xs transition-all duo-button-physics cursor-pointer shrink-0"
          >
            <ArrowUp className="h-3 w-3" /> {copy.up}
          </button>
          <div className="flex-grow font-mono text-2xs text-zinc-600 dark:text-zinc-400 bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 px-3 py-1.5 rounded-xl overflow-x-auto whitespace-nowrap scrollbar-thin select-all">
            {pickerPath || '/'}
          </div>
        </div>

        {/* New Folder */}
        <form
          onSubmit={handleCreateFolder}
          className="px-5 py-3 bg-white dark:bg-zinc-950 border-b border-border-subtle dark:border-zinc-800 shrink-0"
        >
          <div className="flex flex-col sm:flex-row gap-2">
            <label className="sr-only" htmlFor={newFolderId}>{copy.newFolderLabel}</label>
            <input
              id={newFolderId}
              type="text"
              value={newFolderName}
              onChange={(e) => {
                setNewFolderName(e.target.value);
                if (newFolderError) setNewFolderError('');
              }}
              placeholder={copy.newFolderPlaceholder}
              disabled={pickerLoading || creatingFolder}
              className="flex-1 min-w-0 bg-zinc-50 dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 border-2 border-border-subtle dark:border-zinc-800 rounded-xl px-3 py-2 text-xs font-mono focus:outline-none focus:border-accent disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={pickerLoading || creatingFolder || !newFolderName.trim()}
              className="bg-white dark:bg-zinc-800 hover:bg-status-success-bg hover:text-accent-text border-2 border-border-subtle dark:border-zinc-800 shadow-button dark:shadow-button text-zinc-700 dark:text-zinc-300 font-bold px-3 py-2 rounded-xl text-xs transition-all duo-button-physics disabled:opacity-50 disabled:pointer-events-none cursor-pointer shrink-0 inline-flex items-center justify-center gap-1.5"
            >
              {creatingFolder ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Folder className="h-3.5 w-3.5" />}
              <span>{copy.newFolderAction}</span>
            </button>
          </div>
          {newFolderError && (
            <div className="mt-2 bg-status-warning-bg dark:bg-amber-950/20 border border-status-warning/40 px-3 py-2 rounded-xl text-status-warning-text dark:text-amber-300 text-2xs font-bold flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>{newFolderError}</span>
            </div>
          )}
        </form>

        {/* Body */}
        <div className="p-5 overflow-y-auto flex-grow min-h-0 bg-white dark:bg-zinc-950">
          {pickerLoading ? (
            <div className="flex items-center justify-center p-10 text-zinc-500 font-bold">
              <RefreshCw className="h-5 w-5 animate-spin mr-2 text-accent" />
              <span>{copy.loading}</span>
            </div>
          ) : pickerError ? (
            <div className="bg-status-warning-bg dark:bg-amber-950/20 border-2 border-status-warning/30 p-4 rounded-2xl text-status-warning-text dark:text-amber-300 text-xs font-bold flex items-center space-x-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <div className="flex-grow">
                <p className="font-bold">{copy.errorTitle}</p>
                <p className="font-normal font-mono text-2xs mt-0.5">{pickerError}</p>
              </div>
            </div>
          ) : visibleEntries.length === 0 ? (
            <div className="text-center py-10 text-zinc-400 dark:text-zinc-500 font-bold text-xs">
              {mode === 'file' ? copy.noSelectableFiles : copy.emptyDirectory}
            </div>
          ) : (
            <div className="space-y-1">
              {visibleEntries.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  onClick={() => handleEntrySelect(entry)}
                  className="w-full flex items-center justify-between text-left px-3 py-2 rounded-xl hover:bg-zinc-50 dark:hover:bg-zinc-900/40 border border-transparent hover:border-zinc-200 dark:hover:border-zinc-800 transition-colors cursor-pointer group"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {entry.is_dir ? (
                      <Folder className="h-4 w-4 text-accent dark:text-[#78D639] group-hover:scale-110 transition-transform shrink-0" />
                    ) : (
                      <File className="h-4 w-4 text-zinc-500 dark:text-zinc-400 group-hover:text-accent transition-colors shrink-0" />
                    )}
                    <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300 truncate select-all">{entry.name}</span>
                  </div>
                  {entry.is_git_repo && (
                    <span className="flex items-center gap-0.5 bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400 text-2xs font-extrabold uppercase px-1.5 py-0.5 rounded-md border border-blue-100 dark:border-blue-900/40 shrink-0" aria-label={copy.gitRepository}>
                      <GitBranch className="h-3 w-3 inline" /> git
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 bg-surface-base dark:bg-zinc-900/80 border-t-2 border-border-subtle dark:border-zinc-800 flex justify-between items-center gap-3 shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="bg-white dark:bg-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-800 border-2 border-border-subtle dark:border-zinc-800 shadow-button dark:shadow-button text-zinc-700 dark:text-zinc-300 font-bold px-4 py-2 rounded-2xl text-xs transition-all duo-button-physics cursor-pointer"
          >
            {copy.cancel}
          </button>
          <button
            type="button"
            disabled={pickerLoading || !!pickerError}
            onClick={handleSelectFolder}
            className={`bg-accent hover:bg-accent border-2 border-accent-text shadow-button text-text-inverse font-bold px-5 py-2 rounded-2xl text-xs disabled:opacity-50 disabled:pointer-events-none transition-all duo-button-physics cursor-pointer ${mode === 'file' ? 'hidden' : ''}`}
          >
            {copy.selectFolder}
          </button>
        </div>
      </div>
    </div>
  );
}
