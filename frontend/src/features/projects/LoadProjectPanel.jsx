import { useId, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import { useDirectoryPicker } from '../../hooks/useDirectoryPicker';
import { translate } from '../../i18n';
import {
  projectErrorMessageKey,
  projectRecoveryPresentation,
} from './projectPresentation';

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
  onImported,
  reloadPage = defaultReloadPage,
}) {
  const inputId = useId();
  const inputRef = useRef(null);
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const t = (key, params) => translate(language, key, params);

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
      if (!payload || typeof payload.home_url !== 'string' || !payload.home_url) {
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
    <section className="projects-load-panel" aria-labelledby="projects-load-title">
      <div className="projects-section-heading">
        <h2 id="projects-load-title">{t('projects.load.title')}</h2>
        <p>{t('projects.load.description')}</p>
      </div>

      <form className="projects-load-form" onSubmit={submit} noValidate>
        <div className="projects-path-field">
          <label htmlFor={inputId}>{t('projects.directory.label')}</label>
          <div className="projects-path-controls">
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
            <Button type="button" variant="secondary" onClick={browse}>
              {t('projects.directory.browse')}
            </Button>
          </div>
          <span id={`${inputId}-help`} className="projects-field-help">
            {t('projects.directory.placeholder')}
          </span>
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

        <Button type="submit" busy={busy} disabled={!root.trim()}>
          {t('projects.load.action')}
        </Button>
      </form>
      {directoryPickerModal}
    </section>
  );
}
