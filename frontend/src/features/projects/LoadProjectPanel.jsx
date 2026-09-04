import { useEffect, useId, useRef, useState } from 'react';
import { ArrowRight, FolderOpen, ShieldCheck, X } from 'lucide-react';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import { useDirectoryPicker } from '../../hooks/useDirectoryPicker';
import { translate } from '../../i18n';
import {
  projectErrorMessageKey,
  projectRecoveryPresentation,
} from './projectPresentation';

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function isAbortError(error) {
  return error?.name === 'AbortError';
}

function defaultReloadPage() {
  globalThis.location?.reload();
}

function pickerStrings(t) {
  return {
    browserLabel: t('projects.directoryPicker.browserLabel'),
    directoryTitle: t('projects.directoryPicker.directoryTitle'),
    fileTitle: t('projects.directoryPicker.fileTitle'),
    directoryDescription: t('projects.directoryPicker.directoryDescription'),
    fileDescription: t('projects.directoryPicker.fileDescription'),
    close: t('projects.directoryPicker.close'),
    up: t('projects.directoryPicker.up'),
    newFolderLabel: t('projects.directoryPicker.newFolderLabel'),
    newFolderPlaceholder: t('projects.directoryPicker.newFolderPlaceholder'),
    newFolderAction: t('projects.directoryPicker.newFolderAction'),
    newFolderRequired: t('projects.directoryPicker.newFolderRequired'),
    loading: t('projects.directoryPicker.loading'),
    errorTitle: t('projects.directoryPicker.errorTitle'),
    emptyDirectory: t('projects.directoryPicker.emptyDirectory'),
    noSelectableFiles: t('projects.directoryPicker.noSelectableFiles'),
    cancel: t('projects.directoryPicker.cancel'),
    selectFolder: t('projects.directoryPicker.selectFolder'),
    gitRepository: t('projects.directoryPicker.gitRepository'),
  };
}

export default function LoadProjectPanel({
  api,
  language,
  onClose,
  onImported,
  reloadPage = defaultReloadPage,
}) {
  const inputId = useId();
  const dialogRef = useRef(null);
  const inputRef = useRef(null);
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const t = (key, params) => translate(language, key, params);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { chooseLocalPath, directoryPickerModal } = useDirectoryPicker(
    (path) => setRoot(path),
    () => setError({ code: 'internal_error', recovery: ['choose_another_directory'] }),
    { strings: pickerStrings(t) },
  );

  const browse = () => {
    setError(null);
    void chooseLocalPath({
      mode: 'directory',
      initialPath: root,
      onSelected: (path) => {
        setRoot(path);
        setError(null);
        inputRef.current?.focus();
      },
    });
  };

  const returnToProjects = () => {
    setError(null);
    inputRef.current?.focus();
  };

  const submit = async (event) => {
    event?.preventDefault();
    if (busy) return;
    const trimmedRoot = root.trim();
    if (!trimmedRoot.startsWith('/')) {
      setError({ code: 'invalid_project_root', recovery: ['choose_another_directory'] });
      inputRef.current?.focus();
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const payload = await api.importProject({ root: trimmedRoot });
      if (typeof payload?.project?.project_key !== 'string' || !payload.project.project_key.trim()) {
        throw Object.assign(new Error('Invalid import response.'), {
          code: 'invalid_response',
          recovery: ['retry'],
        });
      }
      onImported?.(payload);
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(caught);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDialogKeyDown = (event) => {
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR)];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const recovery = Array.isArray(error?.recovery) ? error.recovery : [];
  const guidance = [...new Set(recovery.filter(
    (item) => projectRecoveryPresentation(item) === 'guidance',
  ))];
  const recoveryActions = [];
  const actionKinds = new Set();
  for (const item of recovery) {
    const presentation = projectRecoveryPresentation(item);
    if (!['choose', 'retry', 'reload_page', 'return'].includes(presentation)) continue;
    if (actionKinds.has(presentation)) continue;
    actionKinds.add(presentation);
    recoveryActions.push({ item, presentation });
  }

  return (
    <div className="projects-import-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose?.();
    }}>
      <form
        ref={dialogRef}
        className="projects-import-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="projects-load-title"
        aria-describedby="projects-load-description"
        onKeyDown={handleDialogKeyDown}
        onSubmit={submit}
        noValidate
      >
        <header className="projects-import-dialog__header">
          <div>
            <p className="projects-load-eyebrow">{t('projects.load.eyebrow')}</p>
            <h2 id="projects-load-title">{t('projects.load.title')}</h2>
            <p id="projects-load-description">{t('projects.load.description')}</p>
          </div>
          <button
            type="button"
            className="projects-icon-button"
            onClick={onClose}
            aria-label={t('projects.load.close')}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="projects-import-dialog__body">
          <div className="projects-path-field">
            <label htmlFor={inputId}>{t('projects.directory.label')}</label>
            <div className="projects-path-controls">
              <div className="projects-path-input" data-invalid={error ? 'true' : undefined}>
                <FolderOpen aria-hidden="true" size={17} strokeWidth={1.8} />
                <input
                  ref={inputRef}
                  id={inputId}
                  type="text"
                  inputMode="url"
                  autoCapitalize="none"
                  autoComplete="off"
                  spellCheck="false"
                  value={root}
                  placeholder={t('projects.directory.placeholder')}
                  onChange={(event) => {
                    setRoot(event.target.value);
                    if (error) setError(null);
                  }}
                  required
                  aria-invalid={error ? 'true' : undefined}
                  aria-describedby={error ? `${inputId}-error` : `${inputId}-help`}
                />
              </div>
              <Button
                className="projects-browse-button"
                type="button"
                variant="secondary"
                onClick={browse}
              >
                <FolderOpen aria-hidden="true" size={16} />
                <span>{t('projects.directory.browse')}</span>
              </Button>
            </div>
            <div id={`${inputId}-help`} className="projects-field-help" aria-live="polite">
              <ShieldCheck aria-hidden="true" size={14} />
              <span>{root.trim() ? t('projects.directory.selected') : t('projects.load.boundary')}</span>
            </div>
          </div>

          {error ? (
            <InlineNotice
              id={`${inputId}-error`}
              tone="error"
              title={t(projectErrorMessageKey(error?.code))}
              action={recoveryActions.length > 0 ? (
                <div className="projects-recovery-actions">
                  {recoveryActions.map(({ item, presentation }) => (
                    <Button
                      type="button"
                      variant="secondary"
                      key={presentation}
                      onClick={presentation === 'choose'
                        ? browse
                        : presentation === 'reload_page'
                          ? reloadPage
                          : presentation === 'return'
                            ? returnToProjects
                            : submit}
                    >
                      {t(`projects.recovery.${item}`)}
                    </Button>
                  ))}
                </div>
              ) : null}
            >
              {guidance.map((item) => (
                <span className="projects-recovery-guidance" key={item}>
                  {t(`projects.recovery.${item}`)}
                </span>
              ))}
              {error?.requestId ? (
                <span className="projects-request-id" data-monospace>
                  {t('projects.requestId', { requestId: error.requestId })}
                </span>
              ) : null}
            </InlineNotice>
          ) : null}

        </div>
        <footer className="projects-import-dialog__footer">
          <Button type="button" variant="secondary" onClick={onClose}>
            {t('projects.load.cancel')}
          </Button>
          <Button className="projects-load-button" type="submit" busy={busy} disabled={!root.trim()}>
            <span>{t('projects.load.action')}</span>
            <ArrowRight aria-hidden="true" size={16} />
          </Button>
        </footer>
        {directoryPickerModal}
      </form>
    </div>
  );
}
