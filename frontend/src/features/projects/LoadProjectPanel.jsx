import { useId, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import { useDirectoryPicker } from '../../hooks/useDirectoryPicker';
import { translate } from '../../i18n';

const RETRY_RECOVERY_ACTIONS = new Set(['retry', 'retry_request']);

function isAbortError(error) {
  return error?.name === 'AbortError';
}

export default function LoadProjectPanel({ api, language, onImported }) {
  const inputId = useId();
  const inputRef = useRef(null);
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const t = (key, params) => translate(language, key, params);

  const { chooseLocalPath, directoryPickerModal } = useDirectoryPicker(
    (path) => setRoot(path),
    () => setError({ code: 'internal_error', recovery: ['choose_another_directory'] }),
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

  const submit = async (event) => {
    event?.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await api.importProject({ root: root.trim() });
      onImported(payload);
    } catch (caught) {
      if (!isAbortError(caught)) {
        setError(caught);
      }
    } finally {
      setBusy(false);
    }
  };

  const errorCode = error?.code || 'internal_error';
  const recovery = Array.isArray(error?.recovery) ? error.recovery : [];

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
            <Button
              type="button"
              variant="secondary"
              onClick={browse}
              aria-label={t('projects.directory.browse')}
            >
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
            title={t(`projects.errors.${errorCode}`)}
            action={recovery.includes('choose_another_directory') ? (
              <Button type="button" variant="secondary" onClick={browse}>
                {t('projects.recovery.choose_another_directory')}
              </Button>
            ) : recovery.some((item) => RETRY_RECOVERY_ACTIONS.has(item)) ? (
              <Button type="button" variant="secondary" onClick={submit}>
                {t('projects.recovery.retry')}
              </Button>
            ) : null}
          >
            {recovery
              .filter((item) => item !== 'choose_another_directory' && !RETRY_RECOVERY_ACTIONS.has(item))
              .map((item) => (
                <span className="projects-recovery-guidance" key={item}>
                  {t(`projects.recovery.${item}`)}
                </span>
              ))}
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
