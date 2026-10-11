import { act, render, screen, waitFor } from '@testing-library/react';
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

test('does not claim an empty library when its first read fails', async () => {
  const api = { getSkillLibrary: vi.fn().mockRejectedValue(new Error('Library is unavailable')) };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);
  await screen.findByText('Library is unavailable');
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
});

function pending() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}

const savedSkill = { skill_id: 'saved', name: 'Saved method', versions: [] };
const staleMessage = 'Showing the last successfully read library. Its current contents have not been verified.';

test('retries a failed first read without changing the import draft', async () => {
  const user = userEvent.setup();
  const api = { getSkillLibrary: vi.fn().mockRejectedValueOnce(new Error('Read failed')).mockResolvedValueOnce({ skills: [] }) };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);
  await screen.findByText('Read failed');
  await user.type(screen.getByLabelText('Server directory'), '/srv/unfinished');
  await user.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findByText('No Skills installed yet.');
  expect(screen.getByLabelText('Server directory')).toHaveValue('/srv/unfinished');
  expect(screen.queryByText('Read failed')).not.toBeInTheDocument();
  expect(api.getSkillLibrary).toHaveBeenCalledTimes(2);
});

test.each([undefined, {}, { skills: null }, { skills: 'invalid' }])('invalid library response remains unknown, not empty: %j', async payload => {
  render(<SkillLibraryPanel api={{ getSkillLibrary: vi.fn().mockResolvedValue(payload) }} onClose={vi.fn()} t={t} />);
  await screen.findByText('invalid_response');
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
});

test('failed refresh labels retained Skills as stale and preserves the draft until a successful retry', async () => {
  const user = userEvent.setup();
  const api = { getSkillLibrary: vi.fn().mockResolvedValueOnce({ skills: [savedSkill] }).mockRejectedValueOnce(new Error('Refresh failed')).mockResolvedValueOnce({ skills: [] }) };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);
  await screen.findByText('Saved method');
  await user.type(screen.getByLabelText('Server directory'), '/srv/unfinished');
  await user.click(screen.getByRole('button', { name: 'Refresh Skills' }));
  await screen.findByText('Refresh failed');
  expect(screen.getByText('Saved method')).toBeVisible();
  expect(screen.getByText(staleMessage)).toBeVisible();
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findByText('No Skills installed yet.');
  expect(screen.queryByText('Saved method')).not.toBeInTheDocument();
  expect(screen.queryByText(staleMessage)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Server directory')).toHaveValue('/srv/unfinished');
});

test('an earlier empty snapshot cannot imply the library is still empty during or after a failed refresh', async () => {
  const user = userEvent.setup();
  const read = pending();
  const api = { getSkillLibrary: vi.fn().mockResolvedValueOnce({ skills: [] }).mockReturnValueOnce(read.promise) };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);
  await screen.findByText('No Skills installed yet.');
  await user.click(screen.getByRole('button', { name: 'Refresh Skills' }));
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
  expect(screen.getByText(staleMessage)).toBeVisible();
  await act(async () => read.resolve({ skills: 'malformed' }));
  await screen.findByText('invalid_response');
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
});

test('late old-API reads cannot replace a newer library', async () => {
  const read = pending();
  const oldApi = { getSkillLibrary: vi.fn().mockReturnValue(read.promise) };
  const nextApi = { getSkillLibrary: vi.fn().mockResolvedValue({ skills: [savedSkill] }) };
  const { rerender } = render(<SkillLibraryPanel api={oldApi} onClose={vi.fn()} t={t} />);
  rerender(<SkillLibraryPanel api={nextApi} onClose={vi.fn()} t={t} />);
  await screen.findByText('Saved method');
  expect(oldApi.getSkillLibrary.mock.calls[0][0].signal.aborted).toBe(true);
  await act(async () => read.resolve({ skills: [{ ...savedSkill, name: 'Wrong old library' }] }));
  expect(screen.getByText('Saved method')).toBeVisible();
  expect(screen.queryByText('Wrong old library')).not.toBeInTheDocument();
});

test('successful install stays confirmed when the follow-up read fails; retry only reads', async () => {
  const user = userEvent.setup();
  const api = {
    getSkillLibrary: vi.fn().mockResolvedValueOnce({ skills: [] }).mockRejectedValueOnce(new Error('Read after install failed')).mockResolvedValueOnce({ skills: [savedSkill] }),
    previewLocalSkill: vi.fn().mockResolvedValue(previewFixture()),
    installLocalSkill: vi.fn().mockResolvedValue(previewFixture('installed')),
  };
  render(<SkillLibraryPanel api={api} onClose={vi.fn()} t={t} />);
  await screen.findByText('No Skills installed yet.');
  await user.type(screen.getByLabelText('Server directory'), '/srv/methods/my-review');
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await user.click(await screen.findByRole('button', { name: 'Install Skill' }));
  await screen.findByText('Read after install failed');
  expect(screen.getByText('Installed. This Skill is not enabled for any role yet.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Install Skill' })).not.toBeInTheDocument();
  expect(screen.queryByText('No Skills installed yet.')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findByText('Saved method');
  expect(api.installLocalSkill).toHaveBeenCalledTimes(1);
  expect(api.previewLocalSkill).toHaveBeenCalledTimes(1);
  expect(api.getSkillLibrary).toHaveBeenCalledTimes(3);
});
