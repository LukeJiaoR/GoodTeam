import { PackageOpen, RefreshCw, X } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import useTeamDialog from './useTeamDialog';

function commandKey() {
  return globalThis.crypto?.randomUUID?.() || `skill-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function fileSummary(manifest = {}) {
  const files = Array.isArray(manifest.files) ? manifest.files : [];
  return {
    count: files.length,
    bytes: files.reduce((total, item) => total + Number(item?.size || 0), 0),
  };
}

export default function SkillLibraryPanel({ api, onClose, t }) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const previewAttempt = useRef(null);
  const [library, setLibrary] = useState({ skills: [] });
  const [sourcePath, setSourcePath] = useState('');
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');
  const [installedNotice, setInstalledNotice] = useState(false);
  const handleKeyDown = useTeamDialog(dialogRef, closeRef, () => { if (!busy) onClose(); });

  const load = useCallback(async ({ signal } = {}) => {
    const payload = await api.getSkillLibrary({ signal });
    setLibrary({ ...payload, skills: Array.isArray(payload?.skills) ? payload.skills : [] });
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    setBusy('loading');
    load({ signal: controller.signal })
      .catch((caught) => { if (!controller.signal.aborted) setError(caught); })
      .finally(() => { if (!controller.signal.aborted) setBusy(''); });
    return () => controller.abort();
  }, [load]);

  const submitPreview = async (event) => {
    event.preventDefault();
    const path = sourcePath.trim();
    if (!path || busy) return;
    setError(null);
    setInstalledNotice(false);
    setBusy('preview');
    if (previewAttempt.current?.path !== path) {
      previewAttempt.current = { path, key: commandKey() };
    }
    try {
      const result = await api.previewLocalSkill(path, { commandKey: previewAttempt.current.key });
      setPreview(result);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy('');
    }
  };

  const install = async () => {
    if (!preview?.proposal_id || busy) return;
    setBusy('install');
    setError(null);
    try {
      const result = await api.installLocalSkill(preview.proposal_id);
      setPreview(result);
      setInstalledNotice(true);
      await load();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy('');
    }
  };

  const summary = fileSummary(preview?.manifest);

  return (
    <div className="team-dialog-backdrop" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <section
        ref={dialogRef}
        className="team-dialog skill-library"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={handleKeyDown}
      >
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{t('team.skills.eyebrow')}</p>
            <h2 id={titleId}>{t('team.skills.title')}</h2>
            <p>{t('team.skills.description')}</p>
          </div>
          <button ref={closeRef} className="team-icon-button" type="button" disabled={Boolean(busy)} onClick={onClose} aria-label={t('team.skills.close')}>
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="skill-library__body">
          {error ? <ProductError error={error} onRetry={() => setError(null)} t={t} /> : null}

          <form className="skill-library__import" onSubmit={submitPreview}>
            <div>
              <h3>{t('team.skills.importTitle')}</h3>
              <p>{t('team.skills.importDescription')}</p>
            </div>
            <label>
              <span>{t('team.skills.path')}</span>
              <input
                value={sourcePath}
                onChange={(event) => {
                  setSourcePath(event.target.value);
                  setPreview(null);
                  setInstalledNotice(false);
                  if (previewAttempt.current?.path !== event.target.value.trim()) previewAttempt.current = null;
                }}
                placeholder={t('team.skills.pathPlaceholder')}
                disabled={Boolean(busy)}
                autoComplete="off"
              />
            </label>
            <Button type="submit" disabled={!sourcePath.trim() || Boolean(busy)} busy={busy === 'preview'}>
              {t('team.skills.preview')}
            </Button>
          </form>

          {preview ? (
            <section className="skill-library__preview" aria-label={t('team.skills.previewTitle')}>
              <div className="skill-library__preview-header">
                <div>
                  <h3>{preview.metadata?.name || t('team.skills.unnamed')}</h3>
                  <p>{preview.metadata?.description || ''}</p>
                </div>
                <code>{String(preview.package_digest || '').slice(0, 19)}…</code>
              </div>
              <dl>
                <div><dt>{t('team.skills.source')}</dt><dd>{preview.source?.label || preview.source?.kind}</dd></div>
                <div><dt>{t('team.skills.files')}</dt><dd>{summary.count}</dd></div>
                <div><dt>{t('team.skills.size')}</dt><dd>{summary.bytes} B</dd></div>
                {preview.metadata?.license ? <div><dt>{t('team.skills.license')}</dt><dd>{String(preview.metadata.license)}</dd></div> : null}
              </dl>
              {preview.diagnostics?.length ? (
                <ul className="skill-library__diagnostics">
                  {preview.diagnostics.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}
                </ul>
              ) : null}
              {preview.status === 'installed' ? (
                <p className="skill-library__installed" role="status">{t('team.skills.installedNotBound')}</p>
              ) : (
                <Button onClick={install} disabled={Boolean(busy)} busy={busy === 'install'}>{t('team.skills.install')}</Button>
              )}
              {installedNotice ? <p className="skill-library__installed" role="status">{t('team.skills.installedNotBound')}</p> : null}
            </section>
          ) : null}

          <section className="skill-library__list" aria-labelledby="skill-library-installed">
            <div className="skill-library__list-heading">
              <div>
                <h3 id="skill-library-installed">{t('team.skills.installedTitle')}</h3>
                <p>{t('team.skills.installedDescription')}</p>
              </div>
              <button className="team-icon-button" type="button" disabled={Boolean(busy)} onClick={() => {
                setBusy('loading');
                setError(null);
                load().catch(setError).finally(() => setBusy(''));
              }} aria-label={t('team.skills.refresh')}>
                <RefreshCw aria-hidden="true" size={15} />
              </button>
            </div>
            {busy === 'loading' && !library.skills.length ? <p role="status">{t('product.states.loading')}</p> : null}
            {!busy && !library.skills.length ? <p className="skill-library__empty">{t('team.skills.empty')}</p> : null}
            <ul>
              {library.skills.map((skill) => {
                const version = skill.versions?.at(-1);
                return (
                  <li key={skill.skill_id}>
                    <span className="skill-library__icon"><PackageOpen aria-hidden="true" size={16} /></span>
                    <div>
                      <strong>{skill.name}</strong>
                      <p>{version?.metadata?.description || ''}</p>
                      <small>{skill.source?.label || skill.source?.kind} · {skill.versions?.length || 0} {t('team.skills.versions')}</small>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        </div>

        <footer className="team-dialog__footer">
          <Button variant="secondary" disabled={Boolean(busy)} onClick={onClose}>{t('team.skills.close')}</Button>
        </footer>
      </section>
    </div>
  );
}
