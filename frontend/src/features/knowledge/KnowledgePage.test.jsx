import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Link, MemoryRouter, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
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
  const location = useLocation();
  const navigate = useNavigate();
  return <><output data-testid="query-location">{location.search}</output><button onClick={() => navigate(-1)}>Browser back</button><button onClick={() => navigate(1)}>Browser forward</button><Link to="/projects/SECOND/knowledge?view=memory">Switch project</Link>
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

test('opens a scoped cross-project memory source without leaving the query', async () => {
  vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockResolvedValue({ results: [{
    id: `OTHER:${contract.detail.id}`, mem_id: contract.detail.id, project_key: 'OTHER',
    text: 'Retrieved source text', source: 'other-source', timestamp: contract.detail.timestamp,
  }] });
  open('&cross_q=database&scope=OTHER');
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' }));
  await screen.findByText('Retrieved source text');
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.memory.openSource' }));
  await waitFor(() => expect(knowledgeApi.getMemoryEvent).toHaveBeenCalledWith('OTHER', contract.detail.id, expect.objectContaining({ signal: expect.any(AbortSignal) })));
  expect(screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' })).toHaveValue('database');
  expect(screen.getByRole('checkbox', { name: 'OTHER' })).toBeChecked();
});

test('presents readable memory provenance and its read-only correction boundary', async () => {
  open(`&role=architect&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('knowledge.memory.inspector');
  expect(screen.getByText('knowledge.memory.readOnly')).toBeVisible();
  expect(screen.getByText('knowledge.memory.source')).toBeVisible();
  expect(screen.getByText('contract-fixture')).toBeVisible();
  expect(screen.getByRole('button', { name: contract.detail.supersedes })).toBeEnabled();
});


test('follows a correction chain and returns to the exact source query with browser history', async () => {
  const previous = { ...contract.detail, id: contract.detail.supersedes, text: 'Earlier source wording', is_superseded: true, supersedes: '', superseded_by: [contract.detail.id] };
  knowledgeApi.getMemoryEvent.mockImplementation(async (_project, id) => id === previous.id ? previous : contract.detail);
  open(`&role=architect&memory_q=database&cross_q=backup&scope=OTHER&event=${encodeURIComponent(contract.detail.id)}`);
  const inspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  fireEvent.click(within(inspector).getByRole('button', { name: previous.id }));
  let currentInspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  expect(within(currentInspector).getByText('Earlier source wording')).toBeVisible();
  expect(within(currentInspector).getByText('knowledge.memory.superseded')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  currentInspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  expect(within(currentInspector).getByText(contract.detail.text)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Browser forward' }));
  currentInspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  expect(within(currentInspector).getByText('Earlier source wording')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Browser back' }));
  currentInspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  expect(within(currentInspector).getByText(contract.detail.text)).toBeVisible();
  expect(screen.getByRole('searchbox', { name: 'knowledge.memory.search' })).toHaveValue('database');
  expect(screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' })).toHaveValue('backup');
  expect(screen.getByRole('checkbox', { name: 'OTHER' })).toBeChecked();
  fireEvent.click(within(currentInspector).getByRole('button', { name: 'knowledge.memory.closeSource' }));
  expect(screen.queryByRole('complementary', { name: 'knowledge.memory.inspector' })).not.toBeInTheDocument();
  expect(screen.getByTestId('query-location')).toHaveTextContent('memory_q=database');
  expect(screen.getByTestId('query-location')).not.toHaveTextContent('event=');
});

test('closing a cross-project source preserves pending query text and returns focus', async () => {
  vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockResolvedValue({ results: [{
    id: `OTHER:${contract.detail.id}`, mem_id: contract.detail.id, project_key: 'OTHER', text: 'Cross source',
  }] });
  open('&role=architect&cross_q=backup&scope=OTHER');
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' }));
  const source = await screen.findByRole('button', { name: 'knowledge.memory.openSource' });
  const input = screen.getByRole('searchbox', { name: 'knowledge.memory.search' });
  fireEvent.change(input, { target: { value: 'Keep this pending query' } });
  fireEvent.click(source);
  const inspector = await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  const local = screen.getByText(contract.detail.id, { selector: '.knowledge-memory-workspace > ol code' }).closest('button');
  expect(local).toHaveAttribute('aria-pressed', 'false');
  await new Promise(resolve => setTimeout(resolve, 300));
  expect(inspector).toBeInTheDocument();
  fireEvent.click(within(inspector).getByRole('button', { name: 'knowledge.memory.closeSource' }));
  expect(input).toHaveValue('Keep this pending query');
  await waitFor(() => expect(source).toHaveFocus());
  expect(screen.getByTestId('query-location')).toHaveTextContent('memory_q=Keep+this+pending+query');
});

test.each([{ id: 'mem:other' }, { role: 'qa' }, { project_key: 'FOREIGN' }])('rejects a mismatched detail identity %j', async (change) => {
  knowledgeApi.getMemoryEvent.mockResolvedValue({ ...contract.detail, ...change, text: 'WRONG_SOURCE_CONTENT' });
  open(`&role=architect&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('knowledge.memory.identityMismatch');
  expect(screen.queryByText('WRONG_SOURCE_CONTENT')).not.toBeInTheDocument();
  expect(screen.queryByRole('complementary', { name: 'knowledge.memory.inspector' })).not.toBeInTheDocument();
});

test('cannot read a cross-project source outside the selected scope', async () => {
  open(`&role=architect&scope=OTHER&event_project=UNSELECTED&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('knowledge.memory.sourceUnavailable');
  expect(knowledgeApi.getMemoryEvent).not.toHaveBeenCalled();
});

test('retries a failed exact source in place without losing query or scope', async () => {
  knowledgeApi.getMemoryEvent.mockRejectedValueOnce(new Error('Source temporarily unavailable')).mockResolvedValueOnce(contract.detail);
  open(`&role=architect&memory_q=database&scope=OTHER&event_project=OTHER&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('Source temporarily unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'product.actions.retry' }));
  await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  expect(knowledgeApi.getMemoryEvent).toHaveBeenLastCalledWith('OTHER', contract.detail.id, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByRole('searchbox', { name: 'knowledge.memory.search' })).toHaveValue('database');
  expect(screen.getByRole('checkbox', { name: 'OTHER' })).toBeChecked();
});

test('loading and partial memory remain distinct from an empty store', async () => {
  const read = pending();
  knowledgeApi.getMemory.mockReturnValue(read.promise);
  open();
  expect(screen.getByText('product.states.loading')).toBeVisible();
  expect(screen.queryByText('knowledge.memory.empty')).not.toBeInTheDocument();
  await act(async () => read.resolve({ roles: [{ role: 'architect', event_count: 2, context_status: 'partial', context_reasons: ['retrieval_unavailable'] }] }));
  await screen.findByText('knowledge.memory.contextUnavailable');
  expect(screen.getByText(/retrieval_unavailable/)).toBeVisible();
  expect(screen.queryByText('knowledge.memory.empty')).not.toBeInTheDocument();
});

test('cross-query retry reissues the failed search rather than only reloading local events', async () => {
  const search = vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockRejectedValueOnce(new Error('Cross search unavailable')).mockResolvedValueOnce({ results: [] });
  open('&cross_q=backup&scope=OTHER');
  fireEvent.click(await screen.findByRole('button', { name: 'knowledge.memory.crossSearch' }));
  await screen.findByText('Cross search unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'product.actions.retry' }));
  await screen.findByText('knowledge.memory.crossEmpty');
  expect(search).toHaveBeenCalledTimes(2);
});


test('debounced local query preserves newer cross-query navigation', async () => {
  open('&role=architect&cross_q=before&scope=OTHER');
  const local = await screen.findByRole('searchbox', { name: 'knowledge.memory.search' });
  fireEvent.change(local, { target: { value: 'Local draft' } });
  fireEvent.change(screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' }), { target: { value: 'Newest cross query' } });
  await waitFor(() => expect(screen.getByTestId('query-location')).toHaveTextContent('memory_q=Local+draft'));
  expect(screen.getByRole('searchbox', { name: 'knowledge.memory.crossQuery' })).toHaveValue('Newest cross query');
  expect(screen.getByRole('checkbox', { name: 'OTHER' })).toBeChecked();
});

test('source remains open after opening with a whitespace-padded query', async () => {
  open('&role=architect');
  const input = await screen.findByRole('searchbox', { name: 'knowledge.memory.search' });
  fireEvent.change(input, { target: { value: 'database ' } });
  fireEvent.click(screen.getByText(contract.detail.id));
  await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 650)); });
  expect(screen.getByRole('complementary', { name: 'knowledge.memory.inspector' })).toBeInTheDocument();
});

test('cross search preserves an unresolved source failure and recovery', async () => {
  knowledgeApi.getMemoryEvent.mockRejectedValue(new Error('Source failure must remain recoverable'));
  vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockResolvedValue({ results: [] });
  open(`&role=architect&cross_q=backup&scope=OTHER&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('Source failure must remain recoverable');
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.memory.crossSearch' }));
  await screen.findByText('knowledge.memory.crossEmpty');
  expect(knowledgeApi.getMemoryEvent).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Source failure must remain recoverable')).toBeVisible();
  expect(screen.getByRole('button', { name: 'product.actions.retry' })).toBeEnabled();
});


test('separate failed source and cross-query reads both remain recoverable', async () => {
  knowledgeApi.getMemoryEvent.mockRejectedValueOnce(new Error('Source read failed')).mockResolvedValue(contract.detail);
  const search = vi.spyOn(knowledgeApi, 'getCrossProjectMemory').mockRejectedValueOnce(new Error('Cross read failed')).mockResolvedValue({ results: [] });
  open(`&role=architect&cross_q=backup&scope=OTHER&event=${encodeURIComponent(contract.detail.id)}`);
  await screen.findByText('Source read failed');
  fireEvent.click(screen.getByRole('button', { name: 'knowledge.memory.crossSearch' }));
  await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: 'product.actions.retry' }));
  await screen.findByRole('complementary', { name: 'knowledge.memory.inspector' });
  await screen.findByText('Cross read failed');
  fireEvent.click(screen.getByRole('button', { name: 'product.actions.retry' }));
  await screen.findByText('knowledge.memory.crossEmpty');
  expect(search).toHaveBeenCalledTimes(2);
  expect(screen.queryByText('Source read failed')).not.toBeInTheDocument();
  expect(screen.queryByText('Cross read failed')).not.toBeInTheDocument();
});
