import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import SkillLibraryPanel from './SkillLibraryPanel';

const t = (key, params) => translate('en', key, params);

function previewFixture(status = 'awaiting_confirmation') {
  return {
    schema_version: 'skill_install_proposal.v1',
    proposal_id: 'proposal-0123456789abcdef0123456789abcdef',
    status,
    source: { kind: 'local', label: 'my-review' },
    metadata: { name: 'my-review', description: 'Review persistence boundaries.', license: 'MIT' },
    package_digest: 'sha256:' + 'a'.repeat(64),
    manifest: {
      files: [
        { path: 'SKILL.md', size: 80 },
        { path: 'references/rules.md', size: 20 },
      ],
    },
    diagnostics: [],
    installed: status === 'installed'
      ? { skill_id: 'skill-' + 'b'.repeat(64), version_id: 'version-' + 'c'.repeat(64) }
      : {},
  };
}

afterEach(() => vi.clearAllMocks());

test('previews and installs a local Skill without implying Role binding', async () => {
  const user = userEvent.setup();
  const installed = {
    skill_id: 'skill-' + 'b'.repeat(64),
    name: 'my-review',
    status: 'installed',
    source: { kind: 'local', label: 'my-review' },
    versions: [{
      version_id: 'version-' + 'c'.repeat(64),
      package_digest: 'sha256:' + 'a'.repeat(64),
      metadata: { name: 'my-review', description: 'Review persistence boundaries.' },
      diagnostics: [],
      created_at: '2026-09-21T00:00:00Z',
      source: { kind: 'local', label: 'my-review' },
    }],
  };
  const api = {
    getSkillLibrary: vi.fn()
      .mockResolvedValueOnce({ schema_version: 'skill_library.v1', skills: [] })
      .mockResolvedValueOnce({ schema_version: 'skill_library.v1', skills: [installed] }),
    previewLocalSkill: vi.fn().mockResolvedValue(previewFixture()),
    installLocalSkill: vi.fn().mockResolvedValue(previewFixture('installed')),
  };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);

  await screen.findByText('No Skills installed yet.');
  await user.type(screen.getByLabelText('Server directory'), '/srv/methods/my-review');
  await user.click(screen.getByRole('button', { name: 'Preview' }));

  expect(api.previewLocalSkill).toHaveBeenCalledWith(
    '/srv/methods/my-review',
    { commandKey: expect.any(String) },
  );
  expect(await screen.findByText('Review persistence boundaries.')).toBeVisible();
  expect(screen.getAllByText('my-review').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('MIT')).toBeVisible();
  expect(screen.getByText('SKILL.md')).toBeVisible();
  expect(screen.getByText('references/rules.md')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Install Skill' }));
  expect(api.installLocalSkill).toHaveBeenCalledWith('proposal-0123456789abcdef0123456789abcdef');
  expect(await screen.findByText('Installed. This Skill is not enabled for any role yet.')).toBeVisible();
  await waitFor(() => expect(api.getSkillLibrary).toHaveBeenCalledTimes(2));
  expect(screen.getAllByText('my-review').length).toBeGreaterThan(1);
});

test('preview retry keeps the same command key and source input', async () => {
  const user = userEvent.setup();
  const api = {
    getSkillLibrary: vi.fn().mockResolvedValue({ skills: [] }),
    previewLocalSkill: vi.fn()
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(previewFixture()),
    installLocalSkill: vi.fn(),
  };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);

  await screen.findByText('No Skills installed yet.');
  const input = screen.getByLabelText('Server directory');
  await user.type(input, '/srv/methods/my-review');
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByText(/connection lost/i);

  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByText('Review persistence boundaries.');

  expect(api.previewLocalSkill).toHaveBeenCalledTimes(2);
  expect(api.previewLocalSkill.mock.calls[0][1].commandKey).toBe(
    api.previewLocalSkill.mock.calls[1][1].commandKey,
  );
  expect(input).toHaveValue('/srv/methods/my-review');
});
