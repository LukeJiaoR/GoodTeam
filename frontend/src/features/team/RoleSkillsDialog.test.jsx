import { render, screen } from '@testing-library/react';
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

function renderDialog(overrides = {}) {
  const props = {
    api: { getSkillLibrary: vi.fn().mockResolvedValue({ skills: [skill] }) },
    currentTeamRevision: 7,
    error: null,
    onClose: vi.fn(),
    onRefresh: vi.fn().mockResolvedValue({}),
    onSubmit: vi.fn(),
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
  await screen.findByText('custom-review');

  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
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
  await screen.findByText('custom-review');

  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
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
  await screen.findByText('custom-review');

  await user.click(screen.getByRole('radio', { name: /GoodTeam default methods/i }));
  await user.click(screen.getByRole('button', { name: 'Restore default methods' }));
  expect(props.onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    mode: 'default',
    bindings: [],
    expected_binding_revision: 4,
  }), { expectedTeamRevision: 12 });
});

test('refreshing current truth preserves the local draft and exposes a changed baseline', async () => {
  const user = userEvent.setup();
  const initial = role();
  const onRefresh = vi.fn().mockResolvedValue({});
  const { rerender, props } = renderDialog({ role: initial, onRefresh });
  await screen.findByText('custom-review');

  await user.click(screen.getByRole('radio', { name: /Custom Skills/i }));
  await user.click(screen.getByRole('checkbox', { name: /custom-review/i }));
  await user.click(screen.getByRole('button', { name: 'Refresh current' }));
  expect(onRefresh).toHaveBeenCalledOnce();

  rerender(<RoleSkillsDialog {...props} role={role({ bindingRevision: 1 })} currentTeamRevision={8} />);
  expect(screen.getByText(/changed while this editor was open/i)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: /custom-review/i })).toBeChecked();

  await user.click(screen.getByRole('button', { name: 'Save methods' }));
  expect(props.onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
    expected_binding_revision: 1,
    bindings: [expect.objectContaining({ skill_id: skill.skill_id })],
  }), { expectedTeamRevision: 8 });
});
