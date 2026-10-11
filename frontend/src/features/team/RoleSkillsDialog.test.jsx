import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import RoleSkillsDialog from './RoleSkillsDialog';

const t = (key, params) => translate('en', key, params);

const version = {
  version_id: 'version-' + '1'.repeat(64),
  package_digest: 'sha256:' + '2'.repeat(64),
  metadata: { description: 'Review persistence boundaries.' },
  diagnostics: [],
};
const skill = {
  skill_id: 'skill-' + '3'.repeat(64),
  name: 'custom-review',
  source: { kind: 'local', label: 'custom-review' },
  versions: [version],
};

function role(binding = {}) {
  return {
    role: 'code_review',
    label: 'Code review',
    skillBinding: {
      subject: 'role:code_review',
      bindingRevision: 0,
      mode: 'default',
      status: 'default',
      items: [],
      executionStatus: 'not_connected',
      ...binding,
    },
  };
}

function refreshedState(bindingRevision = 1, teamRevision = 8) {
  return {
    team: {
      project_key: 'DEMO',
      revision: teamRevision,
      roles: [{ role: 'code_review', skill_binding: {
        subject: 'role:code_review', binding_revision: bindingRevision,
      } }],
    },
  };
}

function renderDialog(overrides = {}) {
  const props = {
    api: { getSkillLibrary: vi.fn().mockResolvedValue({ skills: [skill] }) },
    currentTeamRevision: 7,
    error: null,
    onClose: vi.fn(),
    onRefresh: vi.fn().mockResolvedValue({}),
    onSubmit: vi.fn(),
    projectKey: 'DEMO',
    role: role(),
    saving: false,
    t,
    ...overrides,
  };
  const rendered = render(<RoleSkillsDialog {...props} />);
  return { ...rendered, props };
}

test('replaces default methods with an exact installed Skill binding', async () => {
  const user = userEvent.setup();
  const { props } = renderDialog();
  expect(screen.getByText('Project DEMO')).toBeVisible();
  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
  await screen.findByRole('checkbox', { name: /custom-review/i });

  await user.click(screen.getByRole('checkbox', { name: /custom-review/i }));
  await user.selectOptions(screen.getByLabelText('Loading'), 'on_demand');
  await user.click(screen.getByRole('button', { name: 'Save methods' }));

  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith({
    kind: 'set_role_skills',
    subject: 'role:code_review',
    mode: 'custom',
    bindings: [{
      skill_id: skill.skill_id,
      version_id: version.version_id,
      package_digest: version.package_digest,
      activation: 'on_demand',
      enabled: true,
    }],
    expected_binding_revision: 0,
  }, { expectedTeamRevision: 7 });
  expect(screen.getByText(/not connected to Worker execution yet/i)).toBeVisible();
});

test('custom mode can intentionally save an empty unconfigured binding', async () => {
  const user = userEvent.setup();
  const { props } = renderDialog();
  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
  await screen.findByRole('checkbox', { name: /custom-review/i });
  expect(screen.getByText(/defaults will not be restored automatically/i)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));

  expect(props.onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    kind: 'set_role_skills',
    mode: 'custom',
    bindings: [],
    expected_binding_revision: 0,
  }), { expectedTeamRevision: 7 });
});

test('explicit default mode restores the default preset rather than silently falling back', async () => {
  const user = userEvent.setup();
  const customRole = role({
    bindingRevision: 4,
    mode: 'custom',
    status: 'configured',
    items: [{
      skill_id: skill.skill_id,
      version_id: version.version_id,
      package_digest: version.package_digest,
      activation: 'required',
      enabled: true,
    }],
  });
  const { props } = renderDialog({ role: customRole, currentTeamRevision: 12 });
  await screen.findByRole('checkbox', { name: /custom-review/i });

  await user.click(screen.getByRole('radio', { name: /GoodTeam default methods/i }));
  await user.click(screen.getByRole('button', { name: 'Restore default methods' }));
  expect(props.onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    mode: 'default',
    bindings: [],
    expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
});

test('explicit refresh preserves the draft and adopts only the returned baseline', async () => {
  const user = userEvent.setup();
  const onRefresh = vi.fn().mockResolvedValue(refreshedState());
  const { rerender, props } = renderDialog({ onRefresh });
  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
  await screen.findByRole('checkbox', { name: /custom-review/i });
  await user.click(screen.getByRole('checkbox', { name: /custom-review/i }));
  await user.click(screen.getByRole('button', { name: 'Refresh current' }));
  expect(onRefresh).toHaveBeenCalledOnce();

  // A later background poll must not rebase past the explicit refresh response.
  rerender(<RoleSkillsDialog {...props} role={role({ bindingRevision: 2 })} currentTeamRevision={9} />);
  expect(screen.getByText(/changed while this editor was open/i)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();

  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
    expected_binding_revision: 1,
    bindings: [expect.objectContaining({ skill_id: skill.skill_id })],
  }), { expectedTeamRevision: 8 });
});

test('background polling does not rebase an old draft or an uncertain-result retry', async () => {
  const user = userEvent.setup();
  const { rerender, props } = renderDialog();
  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
  await user.click(await screen.findByRole('checkbox', { name: /custom-review/i }));
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  const first = props.onSubmit.mock.calls[0];

  rerender(<RoleSkillsDialog {...props} role={role({ bindingRevision: 1 })} currentTeamRevision={8} />);
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));

  expect(props.onSubmit).toHaveBeenCalledTimes(2);
  expect(props.onSubmit.mock.calls[1]).toEqual(first);
  expect(first[0].expected_binding_revision).toBe(0);
  expect(first[1].expectedTeamRevision).toBe(7);
  expect(props.onRefresh).not.toHaveBeenCalled();
});

test.each([null, {}, { team: { ...refreshedState().team, project_key: 'OTHER' } }])(
  'an unavailable or wrong-project refresh does not adopt a polled revision: %j',
  async (payload) => {
    const user = userEvent.setup();
    const { rerender, props } = renderDialog({ onRefresh: vi.fn().mockResolvedValue(payload) });
    await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
    await screen.findByRole('checkbox', { name: /custom-review/i });
    rerender(<RoleSkillsDialog {...props} role={role({ bindingRevision: 1 })} currentTeamRevision={8} />);
    await user.click(screen.getByRole('button', { name: 'Refresh current' }));
    await user.click(screen.getByRole('button', { name: 'Save methods' }));
    expect(props.onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
      expected_binding_revision: 0,
    }), { expectedTeamRevision: 7 });
  },
);

test('a missing custom version blocks custom save but not explicit default restoration', async () => {
  const user = userEvent.setup();
  const customRole = role({
    bindingRevision: 4,
    mode: 'custom',
    status: 'configured',
    items: [{
      skill_id: skill.skill_id,
      version_id: 'version-' + '9'.repeat(64),
      package_digest: version.package_digest,
      activation: 'required',
      enabled: true,
    }],
  });
  const { props } = renderDialog({ role: customRole, currentTeamRevision: 12 });
  await screen.findByRole('checkbox', { name: /custom-review/i });
  expect(screen.getByRole('button', { name: 'Save methods' })).toBeDisabled();
  await user.click(screen.getByRole('radio', { name: /GoodTeam default methods/i }));
  expect(screen.getByRole('button', { name: 'Restore default methods' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: 'Restore default methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    mode: 'default', bindings: [], expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function configuredRole() {
  return role({
    bindingRevision: 4,
    mode: 'custom',
    status: 'configured',
    items: [{
      skill_id: skill.skill_id,
      version_id: version.version_id,
      package_digest: version.package_digest,
      activation: 'on_demand',
      enabled: false,
    }],
  });
}

function expectLibraryUnavailable() {
  expect(screen.getByRole('button', { name: 'Save methods' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
  expect(screen.queryByText(/No Skills are installed yet/)).not.toBeInTheDocument();
  expect(screen.queryByText(/configured Skill version is no longer available/)).not.toBeInTheDocument();
}

test('a pending library read cannot remove configured versions or submit a custom draft', async () => {
  const user = userEvent.setup();
  const read = deferred();
  const { props } = renderDialog({
    api: { getSkillLibrary: vi.fn().mockReturnValue(read.promise) },
    role: configuredRole(),
    currentTeamRevision: 12,
  });

  expectLibraryUnavailable();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).not.toHaveBeenCalled();
  await act(async () => read.resolve({ skills: [skill] }));
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith({
    kind: 'set_role_skills', subject: 'role:code_review', mode: 'custom',
    bindings: configuredRole().skillBinding.items, expected_binding_revision: 4,
  }, { expectedTeamRevision: 12 });
});

test('library-only retry preserves pinned selections and the original CAS baseline across polling', async () => {
  const user = userEvent.setup();
  const retry = deferred();
  const api = { getSkillLibrary: vi.fn()
    .mockRejectedValueOnce(new Error('Library offline'))
    .mockReturnValueOnce(retry.promise) };
  const originalRole = configuredRole();
  const { props, rerender } = renderDialog({ api, role: originalRole, currentTeamRevision: 12 });
  await screen.findByRole('button', { name: 'Retry Skill Library' });
  expectLibraryUnavailable();

  rerender(<RoleSkillsDialog {...props} role={role({ bindingRevision: 9 })} currentTeamRevision={20} />);
  await user.dblClick(screen.getByRole('button', { name: 'Retry Skill Library' }));
  expect(api.getSkillLibrary).toHaveBeenCalledTimes(2);
  expect(props.onRefresh).not.toHaveBeenCalled();
  expect(screen.queryByText('Library offline')).not.toBeInTheDocument();
  expectLibraryUnavailable();

  // A newer installed version must not silently replace the pinned version or digest.
  await act(async () => retry.resolve({ skills: [{ ...skill, versions: [version, {
    ...version, version_id: 'version-' + '4'.repeat(64), package_digest: 'sha256:' + '5'.repeat(64),
  }] }] }));
  expect(screen.getByRole('radio', { name: /Custom Skills/i })).toBeChecked();
  expect(screen.getByLabelText('Version')).toHaveValue(version.version_id);
  expect(screen.getByLabelText('Loading')).toHaveValue('on_demand');
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith({
    kind: 'set_role_skills', subject: 'role:code_review', mode: 'custom',
    bindings: originalRole.skillBinding.items, expected_binding_revision: 4,
  }, { expectedTeamRevision: 12 });
  expect(props.onRefresh).not.toHaveBeenCalled();
});

test.each([null, {}, { skills: null }, { skills: {} }, { skills: 'unavailable' }])(
  'a malformed successful response is unavailable and can be retried: %j', async (payload) => {
    const user = userEvent.setup();
    const api = { getSkillLibrary: vi.fn()
      .mockResolvedValueOnce(payload)
      .mockResolvedValueOnce({ skills: [skill] }) };
    const { props } = renderDialog({ api, role: configuredRole(), currentTeamRevision: 12 });
    await screen.findByRole('button', { name: 'Retry Skill Library' });
    expectLibraryUnavailable();
    await user.click(screen.getByRole('button', { name: 'Retry Skill Library' }));
    await screen.findByRole('checkbox', { name: /custom-review/i });
    await user.click(screen.getByRole('button', { name: 'Save methods' }));
    expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      bindings: configuredRole().skillBinding.items, expected_binding_revision: 4,
    }), { expectedTeamRevision: 12 });
    expect(props.onRefresh).not.toHaveBeenCalled();
  },
);

test('only a valid empty library permits removal of a confirmed missing binding', async () => {
  const user = userEvent.setup();
  const read = deferred();
  const { props } = renderDialog({
    api: { getSkillLibrary: vi.fn().mockReturnValue(read.promise) },
    role: configuredRole(), currentTeamRevision: 12,
  });
  expectLibraryUnavailable();
  await act(async () => read.resolve({ skills: [] }));
  expect(screen.getByRole('button', { name: 'Save methods' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Remove' }));
  expect(props.onSubmit).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    mode: 'custom', bindings: [], expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
});

test.each(['pending', 'failed'])('default restoration is available with a %s library read', async (state) => {
  const user = userEvent.setup();
  const read = deferred();
  const api = { getSkillLibrary: vi.fn().mockReturnValue(read.promise) };
  const { props } = renderDialog({ api, role: configuredRole(), currentTeamRevision: 12 });
  if (state === 'failed') await act(async () => read.reject(new Error('Library offline')));
  expectLibraryUnavailable();
  await user.click(screen.getByRole('radio', { name: /GoodTeam default methods/i }));
  await user.click(screen.getByRole('button', { name: 'Restore default methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    mode: 'default', bindings: [], expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
  expect(api.getSkillLibrary).toHaveBeenCalledOnce();
  expect(props.onRefresh).not.toHaveBeenCalled();
});

test('refresh current configuration does not retry the library; a library retry retains that explicit baseline', async () => {
  const user = userEvent.setup();
  const api = { getSkillLibrary: vi.fn()
    .mockRejectedValueOnce(new Error('Library offline'))
    .mockResolvedValueOnce({ skills: [skill] }) };
  const onRefresh = vi.fn().mockResolvedValue(refreshedState(5, 13));
  const { props } = renderDialog({ api, onRefresh, role: configuredRole(), currentTeamRevision: 12 });
  await screen.findByRole('button', { name: 'Retry Skill Library' });
  await user.click(screen.getByRole('button', { name: 'Refresh current' }));
  expect(api.getSkillLibrary).toHaveBeenCalledOnce();
  expectLibraryUnavailable();
  await user.click(screen.getByRole('button', { name: 'Retry Skill Library' }));
  await screen.findByRole('checkbox', { name: /custom-review/i });
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(onRefresh).toHaveBeenCalledOnce();
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    bindings: configuredRole().skillBinding.items, expected_binding_revision: 5,
  }), { expectedTeamRevision: 13 });
});

test.each(['resolve', 'reject'])('an obsolete library request cannot %s over a newer read', async (settle) => {
  const user = userEvent.setup();
  const oldRead = deferred();
  const oldApi = { getSkillLibrary: vi.fn().mockReturnValue(oldRead.promise) };
  const newRead = deferred();
  const newApi = { getSkillLibrary: vi.fn().mockReturnValue(newRead.promise) };
  const { props, rerender } = renderDialog({ api: oldApi, role: configuredRole(), currentTeamRevision: 12 });
  const oldSignal = oldApi.getSkillLibrary.mock.calls[0][0].signal;
  rerender(<RoleSkillsDialog {...props} api={newApi} />);
  expect(oldSignal.aborted).toBe(true);
  await act(async () => newRead.resolve({ skills: [skill] }));
  await act(async () => oldRead[settle](settle === 'resolve' ? { skills: [] } : new Error('Late failure')));
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Retry Skill Library' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    bindings: configuredRole().skillBinding.items, expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
});

test('changing the library client invalidates a previous ready result until its new read succeeds', async () => {
  const nextRead = deferred();
  const { props, rerender } = renderDialog({ role: configuredRole() });
  await screen.findByRole('checkbox', { name: /custom-review/i });
  const api = { getSkillLibrary: vi.fn().mockReturnValue(nextRead.promise) };
  rerender(<RoleSkillsDialog {...props} api={api} />);
  expectLibraryUnavailable();
  expect(screen.queryByRole('checkbox', { name: /custom-review/i })).not.toBeInTheDocument();
  await act(async () => nextRead.reject(new Error('New library unavailable')));
  expectLibraryUnavailable();
  expect(props.onSubmit).not.toHaveBeenCalled();
});

test('closing aborts a pending retry and reopening ignores its late response', async () => {
  const user = userEvent.setup();
  const retry = deferred();
  const api = { getSkillLibrary: vi.fn()
    .mockRejectedValueOnce(new Error('Library offline'))
    .mockReturnValueOnce(retry.promise)
    .mockResolvedValueOnce({ skills: [skill] }) };
  const first = renderDialog({ api, role: configuredRole() });
  await user.click(await screen.findByRole('button', { name: 'Retry Skill Library' }));
  const retrySignal = api.getSkillLibrary.mock.calls[1][0].signal;
  await user.click(screen.getByRole('button', { name: 'Close role methods' }));
  expect(first.props.onClose).toHaveBeenCalledOnce();
  first.unmount();
  expect(retrySignal.aborted).toBe(true);
  const reopened = renderDialog({ api, role: configuredRole() });
  await screen.findByRole('checkbox', { name: /custom-review/i });
  await act(async () => retry.resolve({ skills: [] }));
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument();
  expect(first.props.onSubmit).not.toHaveBeenCalled();
  expect(reopened.props.onSubmit).not.toHaveBeenCalled();
});
