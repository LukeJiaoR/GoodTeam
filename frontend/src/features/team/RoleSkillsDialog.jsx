import { AlertTriangle, RefreshCw, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import useTeamDialog from './useTeamDialog';

function findVersion(skill, versionId) {
  return (skill?.versions || []).find((version) => version.version_id === versionId) || null;
}

function latestVersion(skill) {
  const versions = skill?.versions || [];
  return versions.length ? versions[versions.length - 1] : null;
}

function initialSelections(role) {
  return Object.fromEntries((role?.skillBinding?.items || []).map((item) => [
    item.skill_id,
    {
      skillId: item.skill_id,
      versionId: item.version_id,
      activation: item.activation || 'required',
      enabled: item.enabled !== false,
      packageDigest: item.package_digest || '',
    },
  ]));
}

export default function RoleSkillsDialog({
  api,
  currentTeamRevision,
  error,
  onClose,
  onRefresh,
  onSubmit,
  projectKey,
  role,
  saving,
  t,
}) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const [openedBindingRevision] = useState(() => role.skillBinding.bindingRevision);
  // Polling may update the displayed truth, but must not rebase this draft or
  // change the semantic payload of an uncertain-result retry.
  const [submissionBaseline, setSubmissionBaseline] = useState(() => ({
    teamRevision: currentTeamRevision,
    bindingRevision: role.skillBinding.bindingRevision,
  }));
  const [mode, setMode] = useState(role.skillBinding.mode || 'default');
  const [selections, setSelections] = useState(() => initialSelections(role));
  const [library, setLibrary] = useState({ skills: [] });
  const [loadError, setLoadError] = useState(null);
  const [refreshError, setRefreshError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const handleKeyDown = useTeamDialog(dialogRef, closeRef, () => {
    if (!saving && !refreshing) onClose();
  });

  useEffect(() => {
    const controller = new AbortController();
    api.getSkillLibrary({ signal: controller.signal })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setLibrary({ ...payload, skills: Array.isArray(payload?.skills) ? payload.skills : [] });
        }
      })
      .catch((caught) => { if (!controller.signal.aborted) setLoadError(caught); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api]);

  const installedById = useMemo(
    () => new Map(library.skills.map((skill) => [skill.skill_id, skill])),
    [library.skills],
  );
  const staleBaseline = openedBindingRevision !== role.skillBinding.bindingRevision;

  const toggleSkill = (skill, checked) => {
    setSelections((current) => {
      const next = { ...current };
      if (!checked) {
        delete next[skill.skill_id];
        return next;
      }
      const version = latestVersion(skill);
      if (!version) return next;
      next[skill.skill_id] = {
        skillId: skill.skill_id,
        versionId: version.version_id,
        activation: 'required',
        enabled: true,
        packageDigest: version.package_digest,
      };
      return next;
    });
  };

  const updateSelection = (skillId, patch) => {
    setSelections((current) => {
      const existing = current[skillId];
      if (!existing) return current;
      const skill = installedById.get(skillId);
      const next = { ...existing, ...patch };
      if (patch.versionId) {
        const version = findVersion(skill, patch.versionId);
        next.packageDigest = version?.package_digest || '';
      }
      return { ...current, [skillId]: next };
    });
  };

  const selectedIds = new Set(Object.keys(selections));
  const missingSelections = Object.values(selections).filter((item) => {
    const skill = installedById.get(item.skillId);
    return !skill || !findVersion(skill, item.versionId);
  });
  const submitBlocked = saving || refreshing
    || (mode === 'custom' && (loading || Boolean(loadError) || missingSelections.length > 0));

  const submit = (event) => {
    event.preventDefault();
    if (submitBlocked) return;
    const bindings = mode === 'custom'
      ? Object.values(selections).map((selection) => ({
        skill_id: selection.skillId,
        version_id: selection.versionId,
        package_digest: selection.packageDigest,
        activation: selection.activation,
        enabled: selection.enabled,
      }))
      : [];
    onSubmit({
      kind: 'set_role_skills',
      subject: role.skillBinding.subject || `role:${role.role}`,
      mode,
      bindings,
      expected_binding_revision: submissionBaseline.bindingRevision,
    }, { expectedTeamRevision: submissionBaseline.teamRevision });
  };

  const refreshCurrent = async () => {
    if (refreshing || saving) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      // Only this explicit action may adopt a new CAS baseline. Read the
      // returned snapshot, not props captured before the request or a later poll.
      const payload = await onRefresh();
      const team = payload?.team;
      const binding = team?.roles?.find((entry) => entry.role === role.role)?.skill_binding;
      if (
        team?.project_key === projectKey
        && Number.isInteger(team.revision)
        && binding?.subject === (role.skillBinding.subject || `role:${role.role}`)
        && Number.isInteger(binding.binding_revision)
      ) {
        setSubmissionBaseline({
          teamRevision: team.revision,
          bindingRevision: binding.binding_revision,
        });
      }
    } catch (caught) {
      setRefreshError(caught);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div
      className="team-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving && !refreshing) onClose();
      }}
    >
      <form
        ref={dialogRef}
        className="team-dialog team-skills-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={handleKeyDown}
        onSubmit={submit}
      >
        <header className="team-dialog__header">
          <div>
            <p className="team-dialog__eyebrow">{t('team.roleSkills.eyebrow')}</p>
            <h2 id={titleId}>{t('team.roleSkills.title', { role: role.label })}</h2>
            <p>{t('team.roleSkills.description')}</p>
          </div>
          <button
            ref={closeRef}
            className="team-icon-button"
            type="button"
            disabled={saving || refreshing}
            onClick={onClose}
            aria-label={t('team.roleSkills.close')}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="team-skills-dialog__body">
          {error ? <ProductError error={error} t={t} /> : null}
          {loadError ? <ProductError error={loadError} t={t} /> : null}
          {refreshError ? <ProductError error={refreshError} t={t} /> : null}

          <div className="team-skills-dialog__status">
            <span>{t('team.roleSkills.project', { project: projectKey || role.skillBinding.projectKey || '—' })}</span>
            <span>{t(`team.roleSkills.status.${role.skillBinding.status}`)}</span>
            <code>r{role.skillBinding.bindingRevision}</code>
            <span>{t('team.roleSkills.futureOnly')}</span>
          </div>

          {staleBaseline ? (
            <div className="team-skills-dialog__stale" role="status">
              <AlertTriangle aria-hidden="true" size={16} />
              <div>
                <span>{t('team.roleSkills.changedWhileEditing')}</span>
                <p>{t(`team.roleSkills.status.${role.skillBinding.status}`)} · r{role.skillBinding.bindingRevision}</p>
                <ul>
                  {(role.skillBinding.items || []).map((item) => (
                    <li key={item.skill_id}>
                      {installedById.get(item.skill_id)?.name || item.skill_id}
                      {' · '}<code>{item.version_id}</code>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

          <fieldset className="team-skills-dialog__modes">
            <legend>{t('team.roleSkills.methodMode')}</legend>
            <label>
              <input
                type="radio"
                name="role-skill-mode"
                value="default"
                checked={mode === 'default'}
                onChange={() => setMode('default')}
                disabled={saving}
              />
              <span>
                <strong>{t('team.roleSkills.defaultMode')}</strong>
                <small>{t('team.roleSkills.defaultModeHint')}</small>
              </span>
            </label>
            <label>
              <input
                type="radio"
                name="role-skill-mode"
                value="custom"
                checked={mode === 'custom'}
                onChange={() => setMode('custom')}
                disabled={saving}
              />
              <span>
                <strong>{t('team.roleSkills.customMode')}</strong>
                <small>{t('team.roleSkills.customModeHint')}</small>
              </span>
            </label>
          </fieldset>

          {mode === 'custom' ? (
            <section className="team-skills-dialog__library" aria-labelledby="role-skills-library-title">
              <div className="team-skills-dialog__section-heading">
                <div>
                  <h3 id="role-skills-library-title">{t('team.roleSkills.library')}</h3>
                  <p>{t('team.roleSkills.libraryHint')}</p>
                </div>
              </div>

              {loading ? <p role="status">{t('product.states.loading')}</p> : null}
              {!loading && !library.skills.length ? <p>{t('team.roleSkills.noInstalled')}</p> : null}

              {missingSelections.length ? (
                <div className="team-skills-dialog__missing" role="alert">
                  <div className="team-skills-dialog__stale">
                    <AlertTriangle aria-hidden="true" size={16} />
                    <span>{t('team.roleSkills.missingVersion')}</span>
                  </div>
                  <ul>
                    {missingSelections.map((item) => (
                      <li key={item.skillId}>
                        <code>{item.skillId}</code>
                        <button
                          type="button"
                          className="team-text-button"
                          onClick={() => setSelections((current) => {
                            const next = { ...current };
                            delete next[item.skillId];
                            return next;
                          })}
                          disabled={saving}
                        >
                          {t('team.roleSkills.remove')}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <ul className="team-skills-dialog__list">
                {library.skills.map((skill) => {
                  const selected = selections[skill.skill_id];
                  const version = selected ? findVersion(skill, selected.versionId) : latestVersion(skill);
                  return (
                    <li key={skill.skill_id} data-selected={selectedIds.has(skill.skill_id)}>
                      <label className="team-skills-dialog__select">
                        <input
                          type="checkbox"
                          checked={Boolean(selected)}
                          onChange={(event) => toggleSkill(skill, event.target.checked)}
                          disabled={saving || !(skill.versions || []).length}
                        />
                        <span>
                          <strong>{skill.name}</strong>
                          <small>{version?.metadata?.description || ''}</small>
                          <em>{skill.source?.label || skill.source?.kind}</em>
                        </span>
                      </label>
                      {selected ? (
                        <div className="team-skills-dialog__options">
                          <label>
                            <span>{t('team.roleSkills.version')}</span>
                            <select
                              value={selected.versionId}
                              onChange={(event) => updateSelection(skill.skill_id, { versionId: event.target.value })}
                              disabled={saving}
                            >
                              {(skill.versions || []).map((item) => (
                                <option key={item.version_id} value={item.version_id}>
                                  {item.version_id.slice(0, 20)}…
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            <span>{t('team.roleSkills.activation')}</span>
                            <select
                              value={selected.activation}
                              onChange={(event) => updateSelection(skill.skill_id, { activation: event.target.value })}
                              disabled={saving}
                            >
                              <option value="required">{t('team.roleSkills.required')}</option>
                              <option value="on_demand">{t('team.roleSkills.onDemand')}</option>
                            </select>
                          </label>
                          {version?.diagnostics?.length ? (
                            <ul className="team-skills-dialog__diagnostics">
                              {version.diagnostics.map((item, index) => (
                                <li key={`${item.code}-${index}`}>{item.message}</li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>

              {!Object.keys(selections).length && !loading ? (
                <p className="team-skills-dialog__empty-warning">{t('team.roleSkills.emptyCustom')}</p>
              ) : null}
            </section>
          ) : null}

          <div className="team-skills-dialog__execution">
            <AlertTriangle aria-hidden="true" size={16} />
            <span>{t('team.roleSkills.executionNotConnected')}</span>
          </div>
        </div>

        <footer className="team-dialog__footer">
          <Button variant="secondary" type="button" disabled={saving || refreshing} onClick={refreshCurrent}>
            <RefreshCw aria-hidden="true" size={14} />
            {t('team.roleSkills.refresh')}
          </Button>
          <Button variant="secondary" type="button" disabled={saving || refreshing} onClick={onClose}>
            {t('team.dialog.cancel')}
          </Button>
          <Button type="submit" disabled={submitBlocked} busy={saving}>
            {mode === 'default' ? t('team.roleSkills.restoreDefault') : t('team.roleSkills.save')}
          </Button>
        </footer>
      </form>
    </div>
  );
}
