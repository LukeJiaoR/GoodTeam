import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Link, MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import KnowledgePage from './KnowledgePage';
import { knowledgeApi } from './knowledgeApi';
import contract from './fixtures/memory-events.json';

vi.mock('../runs/useProjectState', () => ({ default: () => ({ data: { state: { artifacts: {} } }, status: 'ready' }) }));

function pending() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function Layout() {
  return <><Link to="/projects/SECOND/knowledge?view=memory">Switch project</Link>
    <Link to="/projects/AUDIT/knowledge?view=memory&role=qa">Go QA</Link>
    <Link to="/projects/AUDIT/knowledge?view=memory&role=architect">Go architect</Link>
    <Link to={`/projects/AUDIT/knowledge?view=memory&role=architect&event=${encodeURIComponent(contract.detail.id)}`}>Open selected memory</Link>
    <Outlet context={{ language: 'en', t: (key) => key }} /></>;
}

function open(query = '') {
  return render(<MemoryRouter initialEntries={[`/projects/AUDIT/knowledge?view=memory${query}`]}>
    <Routes><Route element={<Layout />}><Route path="/projects/:projectKey/knowledge" element={<KnowledgePage />} /></Route></Routes>
  </MemoryRouter>);
}

beforeEach(() => {
  vi.spyOn(knowledgeApi, 'getKnowledgeHome').mockResolvedValue({});
  vi.spyOn(knowledgeApi, 'getMemory').mockResolvedValue({ roles: [{ role: 'architect', event_count: 2 }, { role: 'qa', event_count: 1 }] });
  vi.spyOn(knowledgeApi, 'getKnowledgeProjects').mockResolvedValue({ projects: [{ project: { project_key: 'OTHER' } }] });
  vi.spyOn(knowledgeApi, 'getMemoryEvents').mockImplementation(async (_project, role) => contract[role]);
  vi.spyOn(knowledgeApi, 'getMemoryEvent').mockResolvedValue(contract.detail);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('renders the backend-protected browse contract and preserves superseded audit entries', async () => {
  open();
  for (const event of contract.architect.events) await screen.findByText(event.body);
  expect(screen.getByText('knowledge.memory.superseded')).toBeVisible();
  const current = contract.architect.events.find((event) => !event.is_superseded);
  fireEvent.click(screen.getByText(current.id));
  await screen.findByText('knowledge.memory.inspector');
  expect(knowledgeApi.getMemoryEvent).toHaveBeenCalledWith('AUDIT', current.id, expect.objectContaining({ signal: expect.any(AbortSignal) }));
});

test('a late role response cannot replace current role events', async () => {
  const old = pending();
  let oldSignal;
  knowledgeApi.getMemoryEvents.mockImplementation((_project, role, _query, { signal }) => {
    if (role === 'architect') { oldSignal = signal; return old.promise; }
    return Promise.resolve(contract.qa);
  });
  open();
  fireEvent.click(await screen.findByRole('button', { name: /qa/ }));
  await screen.findByText(contract.qa.events[0].body);
  expect(oldSignal.aborted).toBe(true);
  await act(async () => old.resolve(contract.architect));
  expect(screen.queryByText(contract.architect.events[0].body)).not.toBeInTheDocument();
  expect(screen.getByText(contract.qa.events[0].body)).toBeVisible();
});

test('changing project clears the old memory view even if its request completes late', async () => {
  const old = pending();
  knowledgeApi.getMemoryEvents.mockImplementation((project) => project === 'AUDIT' ? old.promise : Promise.resolve(contract.qa));
  open();
  await waitFor(() => expect(knowledgeApi.getMemoryEvents).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('link', { name: 'Switch project' }));
  await screen.findByText(contract.qa.events[0].body);
  await act(async () => old.resolve(contract.architect));
  expect(screen.queryByText(contract.architect.events[0].body)).not.toBeInTheDocument();
});

test.each(['query', 'scope', 'role'])('late cross-project results are not shown after changing %s', async (change) => {
  const old = pending();
  const search = vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockReturnValue(old.promise);
  open('&cross_q=database&scope=OTHER');
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' }));
  expect(search).toHaveBeenCalledWith('architect', 'database', ['OTHER'], expect.objectContaining({ signal: expect.any(AbortSignal) }));
  if (change === 'query') fireEvent.change(screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' }), { target: { value: 'new query' } });
  if (change === 'scope') fireEvent.click(screen.getByRole('checkbox', { name: 'OTHER' }));
  if (change === 'role') fireEvent.click(screen.getByRole('button', { name: /qa/ }));
  await waitFor(() => expect(search.mock.calls[0][3].signal.aborted).toBe(true));
  await act(async () => old.resolve({ results: [{ id: 'OTHER:mem:late', text: 'OLD_QUERY_RESULT' }] }));
  expect(screen.queryByText('OLD_QUERY_RESULT')).not.toBeInTheDocument();
});

test('returning to an interrupted cross-query permits a new explicit search', async () => {
  const first = pending();
  const second = pending();
  const search = vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  open('&cross_q=database&scope=OTHER');
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' }));
  const input = screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' });
  fireEvent.change(input, { target: { value: 'temporary query' } });
  fireEvent.change(input, { target: { value: 'database' } });
  const button = await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await act(async () => first.resolve({ results: [{ id: 'OTHER:old', text: 'OLD_QUERY_RESULT' }] }));
  expect(screen.queryByText('OLD_QUERY_RESULT')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'product.states.loading' })).toBeDisabled();
  await act(async () => second.resolve({ results: [{ id: 'OTHER:new', text: 'NEW_QUERY_RESULT' }] }));
  await screen.findByText('NEW_QUERY_RESULT');
  expect(search).toHaveBeenCalledTimes(2);
});

test('a late event detail cannot remain under another selected role', async () => {
  const old = pending();
  knowledgeApi.getMemoryEvent.mockReturnValue(old.promise);
  open(`&event=${encodeURIComponent(contract.detail.id)}`);
  fireEvent.click(await screen.findByRole('button', { name: /qa/ }));
  await screen.findByText(contract.qa.events[0].body);
  await act(async () => old.resolve(contract.detail));
  expect(screen.queryByText('knowledge.memory.inspector')).not.toBeInTheDocument();
});

test.each(['events', 'detail'])('successful %s retry clears the previous error for the same selection', async (kind) => {
  const failure = new Error('Previous memory request failed');
  if (kind === 'events') knowledgeApi.getMemoryEvents.mockRejectedValueOnce(failure);
  else knowledgeApi.getMemoryEvent.mockRejectedValueOnce(failure);
  open(`&role=architect${kind === 'detail' ? `&event=${encodeURIComponent(contract.detail.id)}` : ''}`);
  await screen.findByText(failure.message);
  fireEvent.click(screen.getByRole('link', { name: 'Go QA' }));
  await screen.findByText(contract.qa.events[0].body);
  const retry = pending();
  const api = kind === 'events' ? knowledgeApi.getMemoryEvents : knowledgeApi.getMemoryEvent;
  api.mockReturnValueOnce(retry.promise);
  fireEvent.click(screen.getByRole('link', { name: kind === 'detail' ? 'Open selected memory' : 'Go architect' }));
  expect(screen.queryByText(failure.message)).not.toBeInTheDocument();
  await act(async () => retry.resolve(kind === 'events' ? contract.architect : contract.detail));
  await screen.findByText(contract.architect.events[0].body);
  if (kind === 'detail') await screen.findByText('knowledge.memory.inspector');
  expect(screen.queryByText(failure.message)).not.toBeInTheDocument();
});
